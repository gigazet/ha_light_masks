import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.light_masks.endpoint import native_record, validate_endpoint, validate_zones
from tests.light_masks.conftest import NativeGroupLight
from tests.light_masks.test_integration import call, entities, options_step, settle

pytestmark = pytest.mark.parametrize("z2m_output", [6], indirect=True)


@pytest.fixture
async def native_zones(hass, z2m_output):
    groups = []
    for index in range(3):
        group = NativeGroupLight(z2m_output.members[index * 2 : index * 2 + 2])
        group._attr_name = f"Native zone {index}"
        group._attr_unique_id = f"native_zone_{index}"
        group._attr_device_info = {
            "identifiers": {("mqtt", f"zigbee2mqtt_testbridge_{index + 10}")},
            "via_device_id": z2m_output.bridge.id,
            "manufacturer": "Zigbee2MQTT",
            "model": "Group",
        }
        groups.append(group)
    await z2m_output.platform.async_add_entities(groups)
    await hass.async_block_till_done()
    return SimpleNamespace(
        groups=groups,
        aggregate=z2m_output.group,
        members=z2m_output.members,
        bridge=z2m_output.bridge,
        entry=z2m_output.entry,
    )


async def begin_zones(hass, native):
    result = await hass.config_entries.flow.async_init("light_masks", context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"multi_zone": True, "name": "Room"}
    )
    for index, group in enumerate(native.groups):
        assert result["step_id"] == "zone", result
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "name": f"Zone {index}",
                "output": group.entity_id,
                "add_another": index < len(native.groups) - 1,
            },
        )
    assert result["step_id"] == "aggregate", result
    return result


@pytest.fixture
async def multi_entry(hass, native_zones, monkeypatch, request):
    from custom_components.light_masks import controller

    if getattr(request, "param", None) != "production":
        monkeypatch.setattr(controller, "MULTI_ZONE_ACK_TIMEOUT", 0.04)
        monkeypatch.setattr(controller, "SETTLE_SECONDS", 0.01)
    result = await begin_zones(hass, native_zones)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"aggregate": native_zones.aggregate.entity_id, "confirm": True}
    )
    assert result["type"] == "create_entry", result
    entry = result["result"]
    await hass.async_block_till_done()
    assert hasattr(entry, "runtime_data"), entry
    yield entry
    if hasattr(entry, "runtime_data"):
        await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def add_mask(
    hass, entry, *, name="Alarm", power=True, priority=100, duration=0, restore=False
):
    result = await options_step(hass, entry, "add_mask")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "name": name,
            "priority": priority,
            "power": power,
            "brightness": power,
            "appearance": True,
            "duration": duration,
            "restore": restore,
        },
    )
    assert result["type"] == "create_entry", result
    await hass.async_block_till_done()
    return next(s.subentry_id for s in entry.subentries.values() if s.title == name)


async def test_multi_native_exact_aggregate_and_zone_dispatch(hass, native_zones, multi_entry):
    c = multi_entry.runtime_data
    ids = entities(hass, multi_entry)
    zones = list(c.outputs)
    assert not c.engine.apply
    assert len(ids) == 4
    await call(hass, ids["normal"], brightness=90, color_temp_kelvin=3500)
    await settle(hass, c)
    assert not native_zones.aggregate.calls
    await c.set_apply(True)
    await settle(hass, c)
    assert c.status == "in_sync"
    assert len(native_zones.aggregate.calls) == 1
    assert not any(g.calls for g in native_zones.groups)
    await call(hass, ids[zones[0]], "turn_off")
    await settle(hass, c)
    assert c.status == "in_sync"
    assert len(native_zones.groups[0].calls) == 1
    assert len(native_zones.aggregate.calls) == 1
    whole = hass.states.get(ids["normal"])
    assert whole.state == "on"
    assert whole.attributes["mixed_power"]
    assert whole.attributes["on_zone_count"] == 2
    assert whole.attributes["zone_count"] == 3
    await call(hass, ids["normal"], "toggle")
    await settle(hass, c)
    assert all(not member.is_on for member in native_zones.members)
    assert len(native_zones.aggregate.calls) == 1
    assert [len(g.calls) for g in native_zones.groups] == [1, 1, 1]
    await call(hass, ids["normal"], "toggle")
    await settle(hass, c)
    assert len(native_zones.aggregate.calls) == 2
    assert all(not m.calls for m in native_zones.members)
    assert all(z["status"] == "in_sync" for z in c.diagnostics()["zones"].values())


async def test_multi_alarm_hides_latest_zone_intent_and_shared_circadian(
    hass, native_zones, multi_entry
):
    sun = await add_mask(hass, multi_entry, name="Sun", power=False, priority=10, restore=True)
    alarm = await add_mask(hass, multi_entry)
    c = multi_entry.runtime_data
    ids = entities(hass, multi_entry)
    zones = list(c.outputs)
    await call(hass, ids[zones[0]], brightness=80)
    await c.set_apply(True)
    await settle(hass, c)
    await call(hass, ids[sun], color_temp_kelvin=5000)
    await settle(hass, c)
    assert [m.is_on for m in native_zones.members] == [True, True, False, False, False, False]
    await call(hass, ids[alarm], brightness=200, rgb_color=[255, 0, 0])
    await settle(hass, c)
    assert all(m.is_on and m.brightness == 200 for m in native_zones.members)
    assert len(native_zones.aggregate.calls) == 1
    calls = [len(g.calls) for g in [*native_zones.groups, native_zones.aggregate]]
    await call(hass, ids[zones[0]], "turn_off")
    await call(hass, ids[zones[1]], brightness=60, color_temp_kelvin=3000)
    await call(hass, ids[sun], color_temp_kelvin=4500)
    await settle(hass, c)
    assert calls == [len(g.calls) for g in [*native_zones.groups, native_zones.aggregate]]
    assert hass.states.is_state(ids[zones[0]], "off")
    assert all(m.is_on for m in native_zones.members)
    await call(hass, ids[alarm], "turn_off")
    await settle(hass, c)
    assert c.status == "in_sync"
    assert [m.is_on for m in native_zones.members] == [False, False, True, True, False, False]
    assert native_zones.members[2].brightness == 60
    assert native_zones.members[2].color_temp_kelvin == 4500
    assert all(not m.calls for m in native_zones.members)


async def test_multi_mixed_values_unset_and_whole_omissions_preserve(
    hass, native_zones, multi_entry
):
    c = multi_entry.runtime_data
    ids = entities(hass, multi_entry)
    zones = list(c.outputs)
    await call(hass, ids[zones[0]], brightness=90, xy_color=[0.2, 0.3])
    whole = hass.states.get(ids["normal"])
    assert whole.attributes["mixed_brightness"] and whole.attributes["mixed_appearance"]
    assert whole.attributes.get("brightness") is None
    assert whole.attributes.get("color_mode") == "unknown"
    assert whole.attributes.get("xy_color") is None
    await call(hass, ids["normal"])
    assert c.engine.normals[zones[0]].brightness == 90
    assert c.engine.normals[zones[1]].brightness == 128
    await c.set_apply(True)
    await settle(hass, c)
    assert not native_zones.aggregate.calls
    assert [len(g.calls) for g in native_zones.groups] == [1, 1, 1]
    await call(hass, ids["normal"], brightness=70, color_temp_kelvin=3300)
    await settle(hass, c)
    assert len(native_zones.aggregate.calls) == 1


async def test_multi_partial_ack_retries_only_failed_zone(hass, native_zones, multi_entry):
    c = multi_entry.runtime_data
    ids = entities(hass, multi_entry)
    native_zones.members[-1].report = False
    await call(hass, ids["normal"], brightness=90)
    await c.set_apply(True)
    await settle(hass, c)
    assert c.status in ("failed", "unverified")
    assert len(native_zones.aggregate.calls) == 1
    assert [len(g.calls) for g in native_zones.groups[:2]] == [0, 0]
    assert 1 <= len(native_zones.groups[-1].calls) <= 2
    assert list(c.zone_status.values())[:2] == ["in_sync", "in_sync"]
    assert not c.engine.suspended
    assert all(not m.calls for m in native_zones.members)


async def test_multi_same_state_alarm_off_intent_survives_reload(hass, native_zones, multi_entry):
    alarm = await add_mask(hass, multi_entry, restore=True)
    c = multi_entry.runtime_data
    ids = entities(hass, multi_entry)
    zone = next(iter(c.outputs))
    await call(hass, ids["normal"], brightness=100)
    await call(hass, ids[alarm], brightness=200)
    await c.set_apply(True)
    await settle(hass, c)
    await call(hass, ids[zone], "turn_off")
    await call(hass, ids[zone], "turn_off")
    await settle(hass, c)
    assert len(native_zones.aggregate.calls) == 1
    before = c.engine.snapshot()
    assert before["schema"] == "multi_zone_v1"
    assert await hass.config_entries.async_reload(multi_entry.entry_id)
    await hass.async_block_till_done()
    c = multi_entry.runtime_data
    assert c.engine.snapshot() == before
    assert entities(hass, multi_entry) == ids
    await call(hass, ids[alarm], "turn_off")
    await settle(hass, c)
    assert not native_zones.members[0].is_on
    assert native_zones.members[2].is_on


async def test_multi_startup_authorization_is_per_zone(hass, native_zones, multi_entry):
    c = multi_entry.runtime_data
    ids = entities(hass, multi_entry)
    await call(hass, ids["normal"])
    await c.set_apply(True)
    await settle(hass, c)
    # Aggregate still reports On, one zone has lost power while HA is not listening.
    await hass.config_entries.async_unload(multi_entry.entry_id)
    for member in native_zones.groups[-1].members:
        member._attr_is_on = False
        member.async_write_ha_state()
    assert await hass.config_entries.async_setup(multi_entry.entry_id)
    await hass.async_block_till_done()
    c = multi_entry.runtime_data
    await settle(hass, c)
    assert list(c.authorizations.values()) == [True, True, False]
    assert c.status == "awaiting_resume"
    assert len(native_zones.aggregate.calls) == 1
    await c.sync()
    await settle(hass, c)
    assert len(native_zones.aggregate.calls) == 1
    await call(hass, ids[list(c.outputs)[0]])
    await settle(hass, c)
    assert not list(c.authorizations.values())[-1]
    await c.resume()
    await settle(hass, c)
    assert len(native_zones.groups[-1].calls) == 1
    assert c.status == "in_sync"


async def test_multi_unknown_member_isolates_zone_without_intent_adoption(
    hass, native_zones, multi_entry
):
    c = multi_entry.runtime_data
    native_zones.members[-1]._attr_available = False
    native_zones.members[-1].async_write_ha_state()
    await hass.async_block_till_done()
    await c.command("normal", "on", {}, None)
    await c.set_apply(True)
    await settle(hass, c)
    assert c.status == "degraded"
    assert not native_zones.aggregate.calls
    assert [len(g.calls) for g in native_zones.groups] == [1, 1, 0]
    assert list(c.zone_status.values()) == ["in_sync", "in_sync", "unavailable"]
    assert c.engine.normal.on
    native_zones.members[-1]._attr_available = True
    native_zones.members[-1].async_write_ha_state()
    await settle(hass, c)
    assert c.status == "in_sync"


async def test_multi_degraded_alarm_retains_off_and_expires_before_recovery(
    hass, native_zones, multi_entry
):
    from datetime import UTC, datetime

    alarm = await add_mask(hass, multi_entry, duration=60)
    c = multi_entry.runtime_data
    zones = list(c.outputs)
    await c.command("normal", "on", {"brightness": 90}, None)
    await c.set_apply(True)
    await settle(hass, c)
    member = native_zones.members[-1]
    member._attr_available = False
    member.async_write_ha_state()
    await settle(hass, c)
    await c.command(alarm, "on", {"brightness": 200}, None)
    await settle(hass, c)
    assert c.status == "degraded"
    assert all(m.is_on and m.brightness == 200 for m in native_zones.members[:4])
    assert [len(g.calls) for g in native_zones.groups] == [1, 1, 0]
    await c.command(zones[0], "off", {}, None)
    await c.command(zones[-1], "off", {}, None)
    await settle(hass, c)
    assert all(m.is_on and m.brightness == 200 for m in native_zones.members[:4])
    assert not c.engine.normals[zones[-1]].on
    await c._tick(datetime.fromtimestamp(c.engine.intents[alarm].expires_at + 1, UTC))
    await settle(hass, c)
    assert c.status == "degraded"
    assert [m.is_on for m in native_zones.members[:4]] == [False, False, True, True]
    assert native_zones.members[2].brightness == 90
    assert not c.engine.intents[alarm].on
    assert not native_zones.groups[-1].calls
    snapshot = c.engine.snapshot()
    member._attr_available = True
    member.async_write_ha_state()
    await settle(hass, c)
    assert c.status == "in_sync"
    assert c.engine.snapshot() == snapshot
    assert native_zones.groups[-1].calls == [("off", {"transition": 0.0})]
    assert len(native_zones.aggregate.calls) == 1
    assert all(not m.calls for m in native_zones.members)


async def test_multi_recovery_under_alarm_then_release_uses_latest_off(
    hass, native_zones, multi_entry
):
    alarm = await add_mask(hass, multi_entry)
    c = multi_entry.runtime_data
    zone = list(c.outputs)[-1]
    group = native_zones.groups[-1]
    group._attr_available = False
    group.async_write_ha_state()
    await c.command(alarm, "on", {"brightness": 200}, None)
    await c.command(zone, "off", {}, None)
    await c.set_apply(True)
    await settle(hass, c)
    assert c.status == "degraded"
    assert not group.calls
    group._attr_available = True
    group.async_write_ha_state()
    await settle(hass, c)
    assert c.status == "in_sync"
    assert all(m.is_on and m.brightness == 200 for m in native_zones.members)
    assert not c.engine.normals[zone].on
    await c.command(alarm, "off", {}, None)
    await settle(hass, c)
    assert not any(m.is_on for m in native_zones.members)
    assert all(not m.calls for m in native_zones.members)


async def test_multi_restart_unknown_zone_does_not_gain_authorization_on_reconnect(
    hass, native_zones, multi_entry
):
    c = multi_entry.runtime_data
    await c.command("normal", "on", {"brightness": 90}, None)
    await c.set_apply(True)
    await settle(hass, c)
    before = c.engine.snapshot()
    await hass.config_entries.async_unload(multi_entry.entry_id)
    group = native_zones.groups[0]
    group._attr_available = False
    group.async_write_ha_state()
    assert await hass.config_entries.async_setup(multi_entry.entry_id)
    await hass.async_block_till_done()
    c = multi_entry.runtime_data
    await settle(hass, c)
    assert c.engine.snapshot() == before
    assert list(c.authorizations.values()) == [False, True, True]
    assert c.status == "degraded"
    group._attr_available = True
    group._attr_is_on = True
    group.async_write_ha_state()  # Retained On is not a new authorization.
    await settle(hass, c)
    assert c.status == "awaiting_resume"
    assert not list(c.authorizations.values())[0]
    await c.command(list(c.outputs)[1], "on", {"brightness": 60}, None)
    await settle(hass, c)
    assert len(native_zones.groups[1].calls) == 1
    assert not group.calls
    await c.sync()
    await settle(hass, c)
    assert not group.calls
    await c.command(next(iter(c.outputs)), "on", {"brightness": 70}, None)
    await settle(hass, c)
    assert c.status == "in_sync"
    assert len(group.calls) == 1


async def test_multi_degraded_retry_and_supersession_never_use_alias(
    hass, native_zones, multi_entry
):
    c = multi_entry.runtime_data
    member = native_zones.members[-1]
    member._attr_available = False
    member.async_write_ha_state()
    native_zones.members[1].report = False
    gate = native_zones.groups[0].gate = asyncio.Event()
    await c.command("normal", "on", {"brightness": 90}, None)
    await c.set_apply(True)
    async with asyncio.timeout(1):
        await native_zones.groups[0].started.wait()
    await c.command("normal", "on", {"brightness": 160}, None)
    gate.set()
    await settle(hass, c)
    assert c.status == "degraded"
    assert list(c.zone_status.values()) == ["unverified", "in_sync", "unavailable"]
    assert 2 <= len(native_zones.groups[0].calls) <= 4
    assert all(data["brightness"] == 160 for _, data in native_zones.groups[0].calls[1:])
    assert native_zones.members[2].brightness == 160
    assert not native_zones.aggregate.calls
    assert not native_zones.groups[-1].calls
    await c.command("normal", "off", {}, None)
    await settle(hass, c)
    member._attr_available = True
    member.async_write_ha_state()
    await settle(hass, c)
    assert not c.engine.normal.on
    assert not any(m.is_on for m in native_zones.members)
    assert not native_zones.aggregate.calls
    assert all(not m.calls for m in native_zones.members)


async def test_multi_availability_loss_during_aggregate_cancels_aggregate_retries(
    hass, native_zones, multi_entry
):
    c = multi_entry.runtime_data
    gate = native_zones.aggregate.gate = asyncio.Event()
    await c.command("normal", "on", {"brightness": 90}, None)
    await c.set_apply(True)
    async with asyncio.timeout(1):
        await native_zones.aggregate.started.wait()
    member = native_zones.members[-1]
    member._attr_available = False
    member.async_write_ha_state()
    await c.command("normal", "on", {"brightness": 180}, None)
    gate.set()
    await settle(hass, c)
    assert c.status == "degraded"
    assert len(native_zones.aggregate.calls) == 1  # Already in flight cannot be recalled.
    assert [len(g.calls) for g in native_zones.groups] == [1, 1, 0]
    assert all(m.brightness == 180 for m in native_zones.members[:4])
    assert all(not m.calls for m in native_zones.members)


async def test_multi_unavailable_zone_does_not_hide_unsafe_topology(
    hass, native_zones, multi_entry
):
    c = multi_entry.runtime_data
    group = native_zones.groups[-1]
    attributes = dict(hass.states.get(group.entity_id).attributes)
    attributes["group_entities"] = [group.members[0].entity_id]
    hass.states.async_set(group.entity_id, "unavailable", attributes)
    await c.command("normal", "on", {}, None)
    await c.set_apply(True)
    await settle(hass, c)
    assert c.status == "failed"
    assert not native_zones.aggregate.calls
    assert not any(g.calls for g in native_zones.groups)
    assert all(z["status"] == "failed" for z in c.diagnostics()["zones"].values())


@pytest.mark.parametrize("target", ["member", "zone", "aggregate"])
async def test_multi_missing_state_with_intact_registry_allows_isolation(
    hass, native_zones, multi_entry, target
):
    c = multi_entry.runtime_data
    entity = {
        "member": native_zones.members[0],
        "zone": native_zones.groups[0],
        "aggregate": native_zones.aggregate,
    }[target]
    hass.states.async_remove(entity.entity_id)
    await c.command("normal", "on", {}, None)
    await c.set_apply(True)
    await settle(hass, c)
    assert c.status == "degraded"
    assert not native_zones.aggregate.calls
    assert [len(g.calls) for g in native_zones.groups] == (
        [1, 1, 1] if target == "aggregate" else [0, 1, 1]
    )
    entity.async_write_ha_state()
    await settle(hass, c)
    assert c.status == "in_sync"
    assert all(not m.calls for m in native_zones.members)


async def test_multi_real_unavailable_alias_uses_zones_and_reports_degraded(
    hass, native_zones, multi_entry
):
    c = multi_entry.runtime_data
    alias = native_zones.aggregate
    alias._attr_available = False
    alias.async_write_ha_state()
    assert "group_entities" not in hass.states.get(alias.entity_id).attributes
    await c.command("normal", "on", {}, None)
    await c.set_apply(True)
    await settle(hass, c)
    assert c.status == "degraded"
    assert not alias.calls
    assert [len(g.calls) for g in native_zones.groups] == [1, 1, 1]
    result = c.diagnostics()
    assert not result["aggregate_available"]
    assert all(z["status"] == "in_sync" for z in result["zones"].values())
    registry = er.async_get(hass)
    overall = registry.async_get_entity_id(
        "sensor", "light_masks", f"{multi_entry.entry_id}_delivery"
    )
    assert hass.states.is_state(overall, "degraded")
    for zone in c.outputs:
        sensor = registry.async_get_entity_id(
            "sensor", "light_masks", f"{multi_entry.entry_id}_{zone}_delivery"
        )
        assert hass.states.is_state(sensor, "in_sync")
    alias._attr_available = True
    alias._attr_extra_state_attributes = {"group_entities": [alias.members[0].entity_id]}
    alias.async_write_ha_state()
    await settle(hass, c)
    assert c.status == "failed"
    assert not alias.calls


async def test_multi_unavailable_member_provenance_still_checked(hass, native_zones, multi_entry):
    c = multi_entry.runtime_data
    member = native_zones.members[-1]
    member._attr_available = False
    member.async_write_ha_state()
    registered = er.async_get(hass).async_get(member.entity_id)
    dr.async_get(hass).async_update_device(registered.device_id, via_device_id=None)
    await c.command("normal", "on", {}, None)
    await c.set_apply(True)
    await settle(hass, c)
    assert c.status == "failed"
    assert not native_zones.aggregate.calls
    assert not any(g.calls for g in native_zones.groups)


async def test_multi_alias_recovery_does_not_clear_rejected_zone_delivery(
    hass, native_zones, multi_entry, monkeypatch
):
    c = multi_entry.runtime_data
    alias = native_zones.aggregate
    alias._attr_available = False
    alias.async_write_ha_state()
    group = native_zones.groups[0]
    original = group.async_turn_on

    async def reported_failure(**kwargs):
        await original(**kwargs)
        raise HomeAssistantError("rejected after report")

    monkeypatch.setattr(group, "async_turn_on", reported_failure)
    await c.command("normal", "on", {}, None)
    await c.set_apply(True)
    await settle(hass, c)
    assert c.status == "degraded"
    assert list(c.zone_status.values()) == ["failed", "in_sync", "in_sync"]
    alias._attr_available = True
    alias.async_write_ha_state()
    await settle(hass, c)
    assert c.status == "failed"
    assert list(c.zone_status.values()) == ["failed", "in_sync", "in_sync"]
    assert c.diagnostics()["error"] == "rejected after report"
    assert len(group.calls) == 3
    assert not alias.calls


async def test_multi_unknown_without_snapshot_fails_instead_of_reseeding(
    hass, native_zones, multi_entry
):
    c = multi_entry.runtime_data
    await hass.config_entries.async_unload(multi_entry.entry_id)
    await c.store.async_remove()
    native_zones.groups[0]._attr_available = False
    native_zones.groups[0].async_write_ha_state()
    assert not await hass.config_entries.async_setup(multi_entry.entry_id)
    await hass.async_block_till_done()
    assert not hasattr(multi_entry, "runtime_data")
    assert "unavailable_output" in str(multi_entry.reason)
    assert await c.store.async_load() is None
    assert not native_zones.aggregate.calls
    assert not any(g.calls for g in native_zones.groups)


async def test_multi_alias_rename_requires_reload_before_any_native_write(
    hass, native_zones, multi_entry
):
    c = multi_entry.runtime_data
    er.async_get(hass).async_update_entity(
        native_zones.aggregate.entity_id, new_entity_id="light.renamed_aggregate"
    )
    await c.command("normal", "on", {}, None)
    await c.set_apply(True)
    await settle(hass, c)
    assert c.status == "failed"
    assert not native_zones.aggregate.calls
    assert not any(g.calls for g in native_zones.groups)
    assert await hass.config_entries.async_reload(multi_entry.entry_id)
    await hass.async_block_till_done()
    c = multi_entry.runtime_data
    assert c.aggregate == "light.renamed_aggregate"
    await c.resume()
    await settle(hass, c)
    assert c.status == "in_sync"
    assert len(native_zones.aggregate.calls) == 1


async def test_multi_options_display_fixed_targets_even_when_registry_entry_missing(
    hass, native_zones, multi_entry
):
    for group in native_zones.groups:
        er.async_get(hass).async_remove(group.entity_id)
    await hass.async_block_till_done()
    result = await options_step(hass, multi_entry, "base")
    assert result["step_id"] == "base"
    assert result["description_placeholders"]["output"] == ", ".join(
        zone["output"] for zone in multi_entry.data["zones"]
    )
    hass.config_entries.options.async_abort(result["flow_id"])


async def test_multi_external_change_suspends_and_never_adopts(hass, native_zones, multi_entry):
    c = multi_entry.runtime_data
    await c.command("normal", "on", {"brightness": 90}, None)
    await c.set_apply(True)
    await settle(hass, c)
    normals = dict(c.engine.normals)
    native_zones.members[0]._attr_is_on = False
    native_zones.members[0].async_write_ha_state()
    await asyncio.sleep(0.04)
    await hass.async_block_till_done()
    assert c.status == "suspended_external_change"
    assert c.engine.normals == normals
    await c.resume()
    await settle(hass, c)
    assert len(native_zones.groups[0].calls) == 1
    assert len(native_zones.aggregate.calls) == 1


async def test_multi_superseded_aggregate_does_not_retry_stale_on(hass, native_zones, multi_entry):
    c = multi_entry.runtime_data
    gate = native_zones.aggregate.gate = asyncio.Event()
    await c.command("normal", "on", {}, None)
    await c.set_apply(True)
    for _ in range(100):
        if native_zones.aggregate.calls:
            break
        await asyncio.sleep(0.001)
    assert native_zones.aggregate.calls
    zone = next(iter(c.outputs))
    await c.command(zone, "off", {}, None)
    gate.set()
    await settle(hass, c)
    assert c.status == "in_sync"
    assert len(native_zones.aggregate.calls) == 1
    assert [len(g.calls) for g in native_zones.groups] == [1, 0, 0]
    assert not native_zones.members[0].is_on
    assert native_zones.members[2].is_on


async def test_multi_service_error_not_hidden_by_matching_feedback(
    hass, native_zones, multi_entry, monkeypatch
):
    c = multi_entry.runtime_data
    original = native_zones.aggregate.async_turn_on

    async def reported_failure(**kwargs):
        await original(**kwargs)
        raise HomeAssistantError("Reported state but rejected command")

    monkeypatch.setattr(native_zones.aggregate, "async_turn_on", reported_failure)
    await c.command("normal", "on", {}, None)
    await c.set_apply(True)
    await settle(hass, c)
    assert all(m.is_on for m in native_zones.members)
    assert c.status == "failed"
    assert all(status == "failed" for status in c.zone_status.values())
    assert c.error
    await asyncio.sleep(0.05)
    assert c.status == "failed"


async def test_multi_storage_failure_rejects_global_mutation(
    hass, native_zones, multi_entry, monkeypatch
):
    c = multi_entry.runtime_data
    before = c.engine.snapshot()
    monkeypatch.setattr(c.engine, "_save", AsyncMock(side_effect=OSError("disk full")))
    with pytest.raises(HomeAssistantError, match="persist"):
        await c.command("normal", "on", {}, None)
    assert c.engine.snapshot() == before
    assert not native_zones.aggregate.calls


async def test_multi_alias_change_and_ownership_are_fail_closed(hass, native_zones, multi_entry):
    c = multi_entry.runtime_data
    for target in [g.entity_id for g in native_zones.groups] + [native_zones.aggregate.entity_id]:
        with pytest.raises(ValueError, match="already_configured"):
            validate_endpoint(hass, target)
    alias = native_zones.aggregate
    alias._attr_extra_state_attributes = {
        "group_entities": [m.entity_id for m in alias.members[:-1]]
    }
    alias.async_write_ha_state()
    await hass.async_block_till_done()
    await c.command("normal", "on", {}, None)
    await c.set_apply(True)
    await settle(hass, c)
    assert c.status == "failed"
    assert not alias.calls
    assert not any(g.calls for g in native_zones.groups)
    with pytest.raises(ServiceValidationError, match="group_changed"):
        await c.resume()


async def test_multi_rename_keeps_facades_and_config_edit_activation(
    hass, native_zones, multi_entry
):
    alarm = await add_mask(hass, multi_entry, duration=60)
    c = multi_entry.runtime_data
    ids = entities(hass, multi_entry)
    await call(hass, ids[alarm], brightness=200)
    expiry = c.engine.intents[alarm].expires_at
    authorizations = dict(c.authorizations)
    zone = next(iter(c.outputs))
    result = await options_step(hass, multi_entry, "rename_zone")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"zone": zone})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "Worktop"}
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    c = multi_entry.runtime_data
    assert c.engine.intents[alarm].on
    assert c.engine.intents[alarm].expires_at == expiry
    assert c.authorizations == authorizations
    assert entities(hass, multi_entry) == ids
    registered = er.async_get(hass).async_get(ids[zone])
    assert dr.async_get(hass).async_get(registered.device_id).name == "Room Worktop"
    assert not native_zones.aggregate.calls


async def test_multi_without_alias(hass, native_zones):
    result = await begin_zones(hass, native_zones)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"confirm": True})
    assert result["type"] == "create_entry"
    entry = result["result"]
    await hass.async_block_till_done()
    c = entry.runtime_data
    await c.command("normal", "on", {}, None)
    await c.set_apply(True)
    await settle(hass, c)
    assert [len(g.calls) for g in native_zones.groups] == [1, 1, 1]
    assert not native_zones.aggregate.calls
    assert all(not m.calls for m in native_zones.members)
    await hass.config_entries.async_unload(entry.entry_id)


async def test_multi_invalid_topology(hass, native_zones):
    zones = [
        {**native_record(hass, g.entity_id), "id": f"zone_{i}", "name": str(i)}
        for i, g in enumerate(native_zones.groups)
    ]
    alias = native_record(hass, native_zones.aggregate.entity_id)
    assert len(validate_zones(hass, zones, alias, enrolling=True)) == 3
    with pytest.raises(ValueError, match="invalid_zones"):
        validate_zones(hass, zones[:1], None)
    with pytest.raises(ValueError, match="overlapping_zones"):
        validate_zones(hass, [zones[0], {**zones[0], "id": "extra"}], None)
    with pytest.raises(ValueError, match="aggregate_cover"):
        validate_zones(hass, zones[:2], alias)
    with pytest.raises(ValueError, match="aggregate_cover"):
        validate_zones(hass, zones, zones[0])
    native_zones.members[0]._attr_is_on = True
    native_zones.members[0].async_write_ha_state()
    with pytest.raises(ValueError, match="mixed_zone"):
        validate_zones(hass, zones, alias, enrolling=True)
    assert validate_zones(hass, zones, alias)  # A restart must not reseed mixed telemetry.


async def test_multi_requires_native_and_known_roots(hass, native_zones):
    with pytest.raises(ValueError, match="native_zone_required"):
        native_record(hass, native_zones.members[0].entity_id)
    group = native_zones.groups[0]
    state = hass.states.get(group.entity_id)
    hass.states.async_set(group.entity_id, "unknown", state.attributes)
    with pytest.raises(ValueError, match="unavailable_output"):
        native_record(hass, group.entity_id)


@pytest.mark.parametrize("target", ["zone", "aggregate"])
@pytest.mark.parametrize("state", ["unknown", "unavailable"])
async def test_multi_unknown_roots_isolate_delivery(hass, native_zones, multi_entry, target, state):
    c = multi_entry.runtime_data
    group = native_zones.groups[0] if target == "zone" else native_zones.aggregate
    attributes = hass.states.get(group.entity_id).attributes
    hass.states.async_set(group.entity_id, state, attributes)
    await c.command("normal", "on", {}, None)
    await c.set_apply(True)
    await settle(hass, c)
    assert c.status == "degraded"
    statuses = [z["status"] for z in c.diagnostics()["zones"].values()]
    assert statuses == (
        ["unavailable", "in_sync", "in_sync"] if target == "zone" else ["in_sync"] * 3
    )
    assert c.diagnostics()["aggregate_available"] == (target != "aggregate")
    assert not native_zones.aggregate.calls
    assert [len(g.calls) for g in native_zones.groups] == (
        [0, 1, 1] if target == "zone" else [1, 1, 1]
    )
    assert all(i.on for i in c.engine.normals.values())
    group.async_write_ha_state()
    await settle(hass, c)
    assert c.status == "in_sync"


async def test_multi_no_report_is_unverified_not_success(hass, native_zones, multi_entry):
    c = multi_entry.runtime_data
    native_zones.aggregate.report = False
    await c.command("normal", "on", {}, None)
    await c.set_apply(True)
    await settle(hass, c)
    assert c.status == "unverified"
    assert set(c.zone_status.values()) == {"unverified"}
    assert len(native_zones.aggregate.calls) == 1
    assert not any(g.calls for g in native_zones.groups)
    assert all(not m.calls for m in native_zones.members)
    await asyncio.sleep(0.05)
    assert c.status == "unverified"


@pytest.mark.parametrize("late_feedback", ["complete", "partial", "root_only"])
@pytest.mark.parametrize("after_retry_ack", [False, True])
async def test_multi_late_ack_during_other_zone_retry(
    hass, native_zones, multi_entry, monkeypatch, late_feedback, after_retry_ack
):
    c = multi_entry.runtime_data
    zones = list(c.outputs)
    for index, zone in enumerate(zones):
        await c.command(
            zone,
            "on",
            {"brightness": 70 + index, "transition": 0.0 if index == 1 else 0.02},
            None,
        )
    middle, last = native_zones.groups[1:]
    middle.members[-1].report = False
    last.report = False
    original = middle.async_turn_on

    def report_late():
        if after_retry_ack:
            assert c.zone_status[zones[1]] == "in_sync"
        last._attr_is_on = True
        last._attr_brightness = 72
        last.async_write_ha_state()
        for index, member in enumerate(last.members):
            member.report = late_feedback == "complete" or (
                late_feedback == "partial" and index == 0
            )
        last.report_members()

    async def retry_with_late_feedback(**kwargs):
        if middle.calls:
            assert c.zone_status[zones[-1]] == "unverified"
            middle.members[-1].report = True
            if after_retry_ack:
                hass.loop.call_later(0.01, report_late)
            else:
                report_late()
        await original(**kwargs)

    monkeypatch.setattr(middle, "async_turn_on", retry_with_late_feedback)
    await c.set_apply(True)
    await settle(hass, c)
    assert c.transports == [
        *(group.entity_id for group in native_zones.groups),
        middle.entity_id,
    ]
    assert [len(group.calls) for group in native_zones.groups] == [1, 2, 1]
    expected = "in_sync" if late_feedback == "complete" else "unverified"
    assert c.status == expected
    assert list(c.zone_status.values()) == ["in_sync", "in_sync", expected]
    assert c.diagnostics()["zones"][zones[-1]]["status"] == expected
    assert not c.engine.suspended
    assert not native_zones.aggregate.calls
    assert all(not member.calls for member in native_zones.members)


async def test_multi_zone_calls_do_not_wait_for_previous_ack(
    hass, native_zones, multi_entry, monkeypatch
):
    from custom_components.light_masks import controller

    monkeypatch.setattr(controller, "MULTI_ZONE_ACK_TIMEOUT", 0.5)
    c = multi_entry.runtime_data
    for index, zone in enumerate(c.outputs):
        await c.command(zone, "on", {"brightness": 70 + index}, None)
    native_zones.groups[0].report = False
    last_sent = asyncio.Event()
    original = native_zones.groups[-1].async_turn_on

    async def last_call(**kwargs):
        last_sent.set()
        await original(**kwargs)

    monkeypatch.setattr(native_zones.groups[-1], "async_turn_on", last_call)
    await c.set_apply(True)
    async with asyncio.timeout(0.2):
        await last_sent.wait()
    assert not native_zones.aggregate.calls
    assert [len(g.calls) for g in native_zones.groups] == [1, 1, 1]
    await settle(hass, c)
    assert c.status == "unverified"
    assert list(c.zone_status.values()) == ["unverified", "in_sync", "in_sync"]


async def test_multi_divergence_during_other_zone_retry_suspends_after_busy(
    hass, native_zones, multi_entry
):
    c = multi_entry.runtime_data
    native_zones.members[-1].report = False
    gate = native_zones.groups[-1].gate = asyncio.Event()
    await c.command("normal", "on", {}, None)
    await c.set_apply(True)
    async with asyncio.timeout(1):
        await native_zones.groups[-1].started.wait()
    assert list(c.zone_status.values())[:2] == ["in_sync", "in_sync"]
    normals = dict(c.engine.normals)
    native_zones.members[0]._attr_is_on = False
    native_zones.members[0].async_write_ha_state()
    native_zones.members[-1].report = True
    gate.set()
    await settle(hass, c)
    await asyncio.sleep(0.05)
    await hass.async_block_till_done()
    assert c.status == "suspended_external_change"
    assert c.engine.normals == normals
    assert not native_zones.groups[0].calls
    assert all(not m.calls for m in native_zones.members)


async def test_multi_supersession_stops_queued_zone_dispatch(hass, native_zones, multi_entry):
    c = multi_entry.runtime_data
    for index, zone in enumerate(c.outputs):
        await c.command(zone, "on", {"brightness": 70 + index}, None)
    gate = native_zones.groups[0].gate = asyncio.Event()
    await c.set_apply(True)
    async with asyncio.timeout(1):
        await native_zones.groups[0].started.wait()
    await c.command("normal", "off", {}, None)
    gate.set()
    await settle(hass, c)
    assert c.status == "in_sync"
    assert [kind for kind, _ in native_zones.groups[0].calls] == ["on", "off"]
    assert not any(g.calls for g in native_zones.groups[1:])
    assert not native_zones.aggregate.calls
    assert all(not m.is_on and not m.calls for m in native_zones.members)


@pytest.mark.parametrize("multi_entry", [None, "production"], indirect=True)
async def test_multi_transition_supersession_and_duplicate_reports(hass, native_zones, multi_entry):
    c = multi_entry.runtime_data
    await c.command("normal", "on", {"transition": 0.3}, None)
    await c.set_apply(True)
    async with asyncio.timeout(1):
        await native_zones.aggregate.started.wait()
    assert c.status == "pending"
    started = hass.loop.time()
    await c.command("normal", "off", {}, None)
    await settle(hass, c)
    assert hass.loop.time() - started < 1
    assert c.status == "in_sync"
    assert [kind for kind, _ in native_zones.aggregate.calls] == ["on", "off"]
    snapshot = c.engine.snapshot()
    for member in native_zones.members:
        member.async_write_ha_state()
    await asyncio.sleep(0.05)
    assert c.engine.snapshot() == snapshot
    assert len(native_zones.aggregate.calls) == 2


def report_native_group(group, payload):
    group._attr_is_on = True
    group._attr_brightness = payload.get("brightness", 128)
    group._attr_color_temp_kelvin = payload.get("color_temp_kelvin", 4000)
    group.async_write_ha_state()
    group.report_members()


@pytest.mark.parametrize("multi_entry", ["production"], indirect=True)
async def test_multi_production_ack_complete_delayed_feedback(
    hass, native_zones, multi_entry, monkeypatch
):
    from custom_components.light_masks import controller

    assert controller.MULTI_ZONE_ACK_TIMEOUT == 5
    assert controller.ACK_TIMEOUT == 3
    assert controller.MAX_ATTEMPTS == 3
    c = multi_entry.runtime_data
    handles = []
    for zone, group, brightness, delay in zip(
        c.outputs, native_zones.groups, [77, 51, 102], [3.3, 3.53, 3.94], strict=True
    ):
        await c.command(
            zone,
            "on",
            {"brightness": brightness, "color_temp_kelvin": 3000, "transition": 0.2},
            None,
        )
        group.report = False
        original = group.async_turn_on

        async def delayed_call(*, group=group, original=original, delay=delay, **kwargs):
            await original(**kwargs)
            handles.append(hass.loop.call_later(delay, report_native_group, group, kwargs))

        monkeypatch.setattr(group, "async_turn_on", delayed_call)
    try:
        started = hass.loop.time()
        await c.set_apply(True)
        await settle(hass, c, polls=600)
        elapsed = hass.loop.time() - started
        assert 3.94 <= elapsed < 5
        assert c.status == "in_sync"
        assert list(c.zone_status.values()) == ["in_sync"] * 3
        assert list(c.zone_attempts.values()) == [1] * 3
        assert c.transports == [g.entity_id for g in native_zones.groups]
        assert not native_zones.aggregate.calls
        assert not c._delivery_failures
        assert all(not m.calls for m in native_zones.members)
    finally:
        for handle in handles:
            handle.cancel()


@pytest.mark.parametrize("multi_entry", ["production"], indirect=True)
async def test_multi_production_ack_late_terminal_latch(hass, native_zones, multi_entry):
    c = multi_entry.runtime_data
    group = native_zones.aggregate
    group.report = False
    await c.command("normal", "on", {"transition": 0.2}, None)
    started = hass.loop.time()
    await c.set_apply(True)
    await settle(hass, c, polls=600)
    assert 5.2 <= hass.loop.time() - started < 6
    assert c.status == "unverified"
    assert list(c.zone_attempts.values()) == [1] * 3
    assert c._delivery_failures == dict.fromkeys(c.outputs, "unverified")
    report_native_group(group, {})
    await asyncio.sleep(0.8)
    await hass.async_block_till_done()
    assert c.converged(c.desired())
    assert c.status == "unverified"
    assert len(group.calls) == 1
    assert not any(g.calls for g in native_zones.groups)
    assert all(not m.calls for m in native_zones.members)


@pytest.mark.parametrize("multi_entry", ["production"], indirect=True)
async def test_multi_production_ack_partial_selective_retry(
    hass, native_zones, multi_entry, monkeypatch
):
    c = multi_entry.runtime_data
    last = native_zones.groups[-1]
    last.members[-1].report = False
    original = last.async_turn_on
    retry_times = []

    async def complete_on_retry(**kwargs):
        retry_times.append(hass.loop.time())
        last.members[-1].report = True
        await original(**kwargs)

    monkeypatch.setattr(last, "async_turn_on", complete_on_retry)
    await c.command("normal", "on", {"transition": 0.2}, None)
    started = hass.loop.time()
    await c.set_apply(True)
    await settle(hass, c, polls=600)
    assert 5.2 <= retry_times[0] - started < 6
    assert c.status == "in_sync"
    assert list(c.zone_attempts.values()) == [1, 1, 2]
    assert c.transports == [native_zones.aggregate.entity_id, last.entity_id]
    assert all(not m.calls for m in native_zones.members)


@pytest.mark.parametrize("multi_entry", ["production"], indirect=True)
async def test_multi_production_ack_service_errors_still_three_attempts(
    hass, native_zones, multi_entry
):
    c = multi_entry.runtime_data
    group = native_zones.groups[0]
    group.fail_after_report = True
    await c.command(next(iter(c.outputs)), "on", {}, None)
    started = hass.loop.time()
    await c.set_apply(True)
    await settle(hass, c, polls=1800)
    assert 15 <= hass.loop.time() - started < 18
    assert c.status == "failed"
    assert list(c.zone_attempts.values()) == [3, 0, 0]
    assert len(group.calls) == 3
    assert c.zone_errors[next(iter(c.outputs))]
    assert not native_zones.aggregate.calls
    assert not any(g.calls for g in native_zones.groups[1:])
    assert all(not m.calls for m in native_zones.members)


@pytest.mark.parametrize("multi_entry", ["production"], indirect=True)
async def test_multi_production_ack_waits_for_transition(hass, native_zones, multi_entry):
    c = multi_entry.runtime_data
    await c.command("normal", "on", {"transition": 0.2}, None)
    started = hass.loop.time()
    await c.set_apply(True)
    await settle(hass, c)
    assert 0.2 <= hass.loop.time() - started < 1
    assert c.status == "in_sync"
    assert len(native_zones.aggregate.calls) == 1


@pytest.mark.parametrize("z2m_integration", ["production"], indirect=True)
async def test_single_native_production_ack_stays_three_seconds(hass, z2m_output, z2m_integration):
    c = z2m_integration.runtime_data
    z2m_output.group.report = False
    await c.command("normal", "on", {"transition": 0.2}, None)
    started = hass.loop.time()
    await c.set_apply(True)
    await settle(hass, c, polls=500)
    assert 3.2 <= hass.loop.time() - started < 4.5
    assert c.status == "unverified"
    assert c.attempts == 1
    assert len(z2m_output.group.calls) == 1
    assert all(not m.calls for m in z2m_output.members)


async def test_multi_bad_feedback_explain_remains_available(hass, native_zones, multi_entry):
    c = multi_entry.runtime_data
    await c.set_apply(True)
    await settle(hass, c)
    member = native_zones.members[0]
    attrs = dict(hass.states.get(member.entity_id).attributes)
    attrs["brightness"] = "broken"
    hass.states.async_set(member.entity_id, "on", attrs)
    await asyncio.sleep(0.05)
    await hass.async_block_till_done()
    result = c.diagnostics()
    assert result["status"] == "failed"
    assert result["error"]
    assert all(z["status"] == "failed" for z in result["zones"].values())
    assert not native_zones.aggregate.calls
    assert not any(g.calls for g in native_zones.groups)


async def test_multi_final_review_revalidates_topology_and_confirmation(hass, native_zones):
    result = await begin_zones(hass, native_zones)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"confirm": False})
    assert result["errors"] == {"base": "confirm_routing"}
    group = native_zones.groups[0]
    group._attr_extra_state_attributes = {"group_entities": [group.members[0].entity_id]}
    group.async_write_ha_state()
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"confirm": True})
    assert result["errors"] == {"base": "group_changed"}
    assert not hass.config_entries.async_entries("light_masks")
    hass.config_entries.flow.async_abort(result["flow_id"])


async def test_multi_rejects_owned_zone_during_final_review(hass, native_zones):
    result = await begin_zones(hass, native_zones)
    single = await hass.config_entries.flow.async_init("light_masks", context={"source": "user"})
    single = await hass.config_entries.flow.async_configure(
        single["flow_id"], {"output": native_zones.groups[1].entity_id}
    )
    assert single["type"] == "create_entry"
    await hass.async_block_till_done()
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"confirm": True})
    assert result["errors"] == {"base": "already_configured"}
    hass.config_entries.flow.async_abort(result["flow_id"])
    await hass.config_entries.async_unload(single["result"].entry_id)


async def test_multi_flow_one_zone_and_duplicate_can_be_corrected(hass, native_zones):
    result = await hass.config_entries.flow.async_init("light_masks", context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"multi_zone": True})
    one = {"name": "One", "output": native_zones.groups[0].entity_id, "add_another": False}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], one)
    assert result["errors"] == {"base": "invalid_zones"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**one, "add_another": True}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], one)
    assert result["errors"] == {"base": "overlapping_zones"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {**one, "name": "Two", "output": native_zones.groups[1].entity_id},
    )
    assert result["step_id"] == "aggregate"
    hass.config_entries.flow.async_abort(result["flow_id"])


async def test_multi_registry_rename_reloads_by_identity(hass, native_zones, multi_entry):
    c = multi_entry.runtime_data
    ids = entities(hass, multi_entry)
    zone = next(iter(c.outputs))
    registry = er.async_get(hass)
    registry.async_update_entity(c.outputs[zone], new_entity_id="light.renamed_native_zone")
    await hass.async_block_till_done()
    assert await hass.config_entries.async_reload(multi_entry.entry_id)
    await hass.async_block_till_done()
    c = multi_entry.runtime_data
    assert c.outputs[zone] == "light.renamed_native_zone"
    assert entities(hass, multi_entry) == ids
    await c.command(zone, "on", {}, None)
    await c.set_apply(True)
    await settle(hass, c)
    assert c.status == "in_sync"
    assert len(native_zones.groups[0].calls) == 1
    assert all(not m.calls for m in native_zones.members)


async def test_multi_shared_clear_fields_and_manual_reload_policy(hass, native_zones, multi_entry):
    alarm = await add_mask(hass, multi_entry, duration=60)
    c = multi_entry.runtime_data
    ids = entities(hass, multi_entry)
    await call(hass, ids[alarm], brightness=200, xy_color=[0.6, 0.3])
    expiry = c.engine.intents[alarm].expires_at
    await hass.services.async_call(
        "light_masks",
        "clear_fields",
        {
            "config_entry_id": multi_entry.entry_id,
            "entity_id": ids[alarm],
            "fields": ["appearance"],
        },
        blocking=True,
    )
    assert c.engine.intents[alarm].color is None
    assert c.engine.intents[alarm].expires_at == expiry
    assert all(r.intent.color.mode == "color_temp" for r in c.engine.resolutions(0).values())
    with pytest.raises(ServiceValidationError, match="mask light"):
        await hass.services.async_call(
            "light_masks",
            "clear_fields",
            {
                "config_entry_id": multi_entry.entry_id,
                "entity_id": ids[next(iter(c.outputs))],
                "fields": ["appearance"],
            },
            blocking=True,
        )
    assert await hass.config_entries.async_reload(multi_entry.entry_id)
    await hass.async_block_till_done()
    c = multi_entry.runtime_data
    assert not c.engine.intents[alarm].on
    assert c.engine.intents[alarm].expires_at is None
    assert all(not i.on for i in c.engine.normals.values())
    assert not native_zones.aggregate.calls


async def test_multi_corrupt_saved_zone_set_fails_setup(hass, native_zones, multi_entry):
    c = multi_entry.runtime_data
    raw = c.engine.snapshot()
    raw["normals_by_zone"].pop(next(iter(c.outputs)))
    await c.store.async_save(raw)
    assert not await hass.config_entries.async_reload(multi_entry.entry_id)
    await hass.async_block_till_done()
    assert "Stored zone identities" in str(multi_entry.reason)
    assert not hasattr(multi_entry, "runtime_data")
    assert not native_zones.aggregate.calls
    assert not any(g.calls for g in native_zones.groups)


async def test_multi_first_setup_rechecks_uniform_enrollment_after_storage_io(
    hass, native_zones, monkeypatch
):
    from custom_components.light_masks.storage import IntentStore

    async def load_missing(self):
        member = native_zones.members[0]
        member._attr_is_on = True
        member.async_write_ha_state()
        return None

    monkeypatch.setattr(IntentStore, "async_load", load_missing)
    result = await begin_zones(hass, native_zones)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"confirm": True})
    assert result["type"] == "create_entry"
    entry = result["result"]
    await hass.async_block_till_done()
    assert "mixed_zone" in str(entry.reason)
    assert not hasattr(entry, "runtime_data")
    assert not native_zones.aggregate.calls
    assert not any(g.calls for g in native_zones.groups)


@pytest.mark.parametrize("change", ["remove", "bridge", "capabilities"])
async def test_multi_frozen_registry_and_capabilities_block_resume(
    hass, native_zones, multi_entry, change
):
    c = multi_entry.runtime_data
    group = native_zones.groups[-1]
    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    if change == "remove":
        registry.async_remove(group.entity_id)
    elif change == "bridge":
        bridge = devices.async_get_or_create(
            config_entry_id=native_zones.entry.entry_id,
            identifiers={("mqtt", "zigbee2mqtt_bridge_0x00000000000000ab")},
            manufacturer="Zigbee2MQTT",
            model="Bridge",
        )
        for entity in [group, *group.members]:
            devices.async_update_device(
                registry.async_get(entity.entity_id).device_id, via_device_id=bridge.id
            )
    else:
        for entity in [group, *group.members]:
            entity._attr_max_color_temp_kelvin = 6000
            entity.async_write_ha_state()
    await hass.async_block_till_done()
    with pytest.raises(ServiceValidationError, match="group_changed"):
        await c.resume()
    await c.command("normal", "on", {}, None)
    await c.set_apply(True)
    await settle(hass, c)
    assert c.status in ("failed", "unavailable")
    assert not native_zones.aggregate.calls
    assert not any(g.calls for g in native_zones.groups)
