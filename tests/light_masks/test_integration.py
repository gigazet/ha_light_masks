import asyncio
from unittest.mock import AsyncMock

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component


def entities(hass, entry):
    registry = er.async_get(hass)
    return {
        entity.unique_id.removeprefix(entry.entry_id + "_"): entity.entity_id
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
        if entity.domain == "light"
    }


async def call(hass, entity_id, service="turn_on", **kwargs):
    await hass.services.async_call(
        "light", service, {"entity_id": entity_id, **kwargs}, blocking=True
    )
    await asyncio.sleep(0)


async def settle(hass, controller):
    for _ in range(100):
        await asyncio.sleep(0.01)
        await hass.async_block_till_done()
        if not controller._busy and not controller._wake.is_set():
            return
    pytest.fail("Writer did not settle")


async def test_real_services_notification_circadian_switch(hass, integration, output):
    controller = integration.runtime_data
    ids = entities(hass, integration)
    sun, wash = list(integration.subentries)
    assert sun in ids, ids
    await controller.set_apply(True)
    await call(hass, ids[sun], color_temp_kelvin=3500)
    await call(hass, ids[wash], rgb_color=[0, 255, 0], brightness_pct=25)
    await settle(hass, controller)
    assert not output.calls
    assert hass.states.get(ids[wash]).state == "on"
    await call(hass, ids["power"], brightness_pct=25, color_temp_kelvin=2700)
    await settle(hass, controller)
    assert output.is_on
    assert output.brightness == 128
    assert output.color_mode == "xy"
    assert controller.status == "in_sync"
    count = len(output.calls)
    for kelvin in range(4000, 4100):
        await call(hass, ids[sun], color_temp_kelvin=kelvin)
    await settle(hass, controller)
    assert len(output.calls) == count
    await call(hass, ids[wash], "turn_off")
    await settle(hass, controller)
    assert output.color_temp_kelvin == 4099
    await call(hass, ids["power"], "turn_off")
    await settle(hass, controller)
    assert not output.is_on
    count = len(output.calls)
    await call(hass, ids[wash], rgb_color=[255, 0, 0])
    await settle(hass, controller)
    assert len(output.calls) == count


async def test_real_scene_restores_inactive_mask_and_zero_releases(hass, integration, output):
    ids = entities(hass, integration)
    wash = list(integration.subentries)[1]
    assert await async_setup_component(hass, "scene", {"scene": []})
    await hass.async_block_till_done()
    await hass.services.async_call(
        "scene",
        "create",
        {
            "scene_id": "mask_before",
            "snapshot_entities": [ids[wash]],
        },
        blocking=True,
    )
    await call(hass, ids[wash], rgb_color=[0, 255, 0])
    await hass.services.async_call(
        "scene", "turn_on", {"entity_id": "scene.mask_before"}, blocking=True
    )
    assert not integration.runtime_data.engine.intents[wash].on
    await call(hass, ids[wash], brightness=100)
    await call(hass, ids[wash], brightness=0)
    assert not integration.runtime_data.engine.intents[wash].on
    assert not output.calls


async def test_blocked_delivery_coalesces_latest_off(hass, integration, output):
    ids = entities(hass, integration)
    controller = integration.runtime_data
    await controller.set_apply(True)
    output.gate = asyncio.Event()
    await call(hass, ids["normal"])
    for _ in range(50):
        if output.calls:
            break
        await asyncio.sleep(0.01)
    assert len(output.calls) == 1
    for brightness in range(100, 200):
        await call(hass, ids["normal"], brightness=brightness)
    await call(hass, ids["normal"], "turn_off")
    output.gate.set()
    await settle(hass, controller)
    assert [kind for kind, _ in output.calls] == ["on", "off"]
    assert not output.is_on
    assert controller.status == "in_sync"


async def test_external_change_suspends_without_poisoning_normal(hass, integration, output):
    ids = entities(hass, integration)
    controller = integration.runtime_data
    wash = list(integration.subentries)[1]
    await controller.set_apply(True)
    await call(hass, ids["normal"])
    await call(hass, ids[wash], rgb_color=[0, 255, 0])
    await settle(hass, controller)
    normal = controller.engine.normal
    await call(hass, output.entity_id, "turn_off")
    await asyncio.sleep(0.04)
    await hass.async_block_till_done()
    assert controller.engine.suspended
    assert controller.engine.normal == normal
    assert not output.is_on
    await controller.resume()
    await settle(hass, controller)
    assert output.is_on


async def test_reload_never_wakes_off_output(hass, integration, output):
    ids = entities(hass, integration)
    controller = integration.runtime_data
    await call(hass, ids["normal"])
    await controller.set_apply(True)
    await settle(hass, controller)
    await controller.set_apply(False)
    await call(hass, output.entity_id, "turn_off")
    # Persisted Apply enabled, simulating a power loss before the next delivery.
    await controller.engine.set_mode(apply=True)
    await hass.config_entries.async_reload(integration.entry_id)
    controller = integration.runtime_data
    await settle(hass, controller)
    assert not output.is_on
    assert controller.status == "awaiting_resume"
    await controller.resume()
    await settle(hass, controller)
    assert output.is_on


async def test_storage_failure_exposed(hass, integration, output):
    controller = integration.runtime_data
    controller.engine._save = AsyncMock(side_effect=OSError("disk full"))
    with pytest.raises(Exception, match="persist"):
        await call(hass, entities(hass, integration)["normal"])
    assert not controller.engine.normal.on
    assert not output.calls


async def test_config_flow_rejects_group(hass, integration, output):
    from homeassistant import data_entry_flow

    hass.states.async_set(
        "light.group_output",
        "on",
        {
            "supported_color_modes": ["xy"],
            "entity_id": [output.entity_id],
        },
    )
    result = await hass.config_entries.flow.async_init("light_masks", context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "Bad",
            "output": "light.group_output",
            "individual_output": True,
            "power_port": True,
            "fallback_brightness": 128,
            "fallback_kelvin": 4000,
        },
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "aggregate_output"}


async def test_core_toggle_normalizes_rgb_and_relative_brightness_is_local(hass, integration):
    ids = entities(hass, integration)
    wash = list(integration.subentries)[1]
    await call(hass, ids["normal"], brightness=150)
    await call(hass, ids[wash], "toggle", rgb_color=[0, 255, 0], brightness=80)
    engine = integration.runtime_data.engine
    assert engine.intents[wash].color.mode == "xy"
    await call(hass, ids[wash], brightness_step=10)
    assert engine.intents[wash].brightness == 90
    assert engine.normal.brightness == 150
    await asyncio.gather(*(call(hass, ids[wash], "toggle") for _ in range(20)))
    assert engine.intents[wash].on


async def test_no_report_is_unverified_without_retries(hass, integration, output):
    controller = integration.runtime_data
    output.report = False
    await controller.set_apply(True)
    await call(hass, entities(hass, integration)["normal"])
    await settle(hass, controller)
    assert controller.status == "unverified"
    assert len(output.calls) == 1
    await asyncio.sleep(0.15)
    assert len(output.calls) == 1
    output.report = True
    await controller.sync()
    await settle(hass, controller)
    assert controller.status == "in_sync"


async def test_delivery_failure_bounded_and_explicit_retry(hass, integration, output):
    controller = integration.runtime_data
    output.fail = True
    await controller.set_apply(True)
    await call(hass, entities(hass, integration)["normal"])
    await settle(hass, controller)
    assert controller.status == "failed"
    assert len(output.calls) == 3
    await asyncio.sleep(0.15)
    assert len(output.calls) == 3
    output.fail = False
    await controller.sync()
    await settle(hass, controller)
    assert controller.status == "in_sync"
    assert len(output.calls) == 4


async def test_atomic_file_failure_is_not_swallowed_by_core_store(
    hass, integration, output, monkeypatch
):
    from homeassistant.exceptions import HomeAssistantError
    from homeassistant.util.file import WriteError

    controller = integration.runtime_data
    before = controller.engine.snapshot()

    def fail(*args, **kwargs):
        raise WriteError("Disk full")

    monkeypatch.setattr("homeassistant.helpers.storage.write_utf8_file_atomic", fail)
    with pytest.raises(HomeAssistantError, match="persist"):
        await call(hass, entities(hass, integration)["normal"])
    assert controller.engine.snapshot() == before
    assert controller.status == "failed"
    assert not output.calls


async def test_unchanged_explicit_on_authorizes_after_restart(hass, integration, output):
    controller = integration.runtime_data
    await call(hass, entities(hass, integration)["normal"])
    await controller.engine.set_mode(apply=True)
    await hass.config_entries.async_reload(integration.entry_id)
    controller = integration.runtime_data
    await settle(hass, controller)
    assert controller.status == "awaiting_resume"
    await controller.sync()
    await settle(hass, controller)
    assert not output.calls
    await call(hass, entities(hass, integration)["normal"])
    await settle(hass, controller)
    assert output.is_on


async def test_apply_off_during_inflight_does_not_send_queued_commands(hass, integration, output):
    controller = integration.runtime_data
    await controller.set_apply(True)
    output.gate = asyncio.Event()
    await call(hass, entities(hass, integration)["normal"])
    for _ in range(50):
        if output.calls:
            break
        await asyncio.sleep(0.01)
    await controller.set_apply(False)
    await call(hass, entities(hass, integration)["normal"], "turn_off")
    output.gate.set()
    await settle(hass, controller)
    assert len(output.calls) == 1
    assert output.is_on  # An already-dispatched physical command cannot be undone.
    assert controller.status == "shadow"


async def test_unavailable_recovers_latest_intent(hass, integration, output):
    controller = integration.runtime_data
    output._attr_available = False
    output.async_write_ha_state()
    await controller.set_apply(True)
    await call(hass, entities(hass, integration)["normal"], brightness=90)
    await settle(hass, controller)
    assert controller.status == "unavailable"
    assert not output.calls
    output._attr_available = True
    output.async_write_ha_state()
    await settle(hass, controller)
    assert output.is_on
    assert output.brightness == 90


async def test_unsafe_effect_blocks_until_reviewed(hass, integration, output):
    controller = integration.runtime_data
    await controller.set_apply(True)
    await call(hass, entities(hass, integration)["normal"])
    await settle(hass, controller)
    count = len(output.calls)
    output._attr_effect = "rainbow"
    output.async_write_ha_state()
    await asyncio.sleep(0)
    await call(hass, entities(hass, integration)["normal"])
    await settle(hass, controller)
    assert controller.status == "failed"
    assert len(output.calls) == count
    from homeassistant.exceptions import ServiceValidationError

    with pytest.raises(ServiceValidationError, match="active_effect"):
        await controller.resume()
    output._attr_effect = None
    output.async_write_ha_state()
    await controller.resume()
    await settle(hass, controller)
    assert output.is_on


async def test_subentry_duplicate_priority_and_reload_restoration(hass, integration, output):
    from homeassistant import data_entry_flow

    controller = integration.runtime_data
    ids = entities(hass, integration)
    sun, wash = integration.subentries
    await call(hass, ids[sun], color_temp_kelvin=3300)
    await call(hass, ids[wash], rgb_color=[0, 255, 0])
    result = await hass.config_entries.subentries.async_init(
        (integration.entry_id, "mask"), context={"source": "user"}
    )
    data = {
        "name": "Power override",
        "priority": 100,
        "appearance": False,
        "brightness": False,
        "power": True,
        "restore": False,
        "duration": 60,
    }
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], data)
    assert result["errors"] == {"priority": "duplicate_priority"}
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**data, "priority": 200}
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert controller._worker.done()
    controller = integration.runtime_data
    assert controller.engine.intents[sun].on
    assert controller.engine.intents[wash].on
    assert controller.engine.intents[wash].color is not None
    power = list(integration.subentries)[-1]
    await controller.set_apply(True)
    await call(hass, entities(hass, integration)[power])
    await settle(hass, controller)
    assert output.is_on
    await call(hass, entities(hass, integration)[power], "turn_off")
    await settle(hass, controller)
    assert not output.is_on


async def test_explain_and_clear_fields_services(hass, integration, output):
    wash = list(integration.subentries)[1]
    entity_id = entities(hass, integration)[wash]
    await call(hass, entity_id, rgb_color=[0, 255, 0])
    explanation = await hass.services.async_call(
        "light_masks",
        "explain",
        {"config_entry_id": integration.entry_id},
        blocking=True,
        return_response=True,
    )
    assert explanation["status"] == "shadow"
    await hass.services.async_call(
        "light_masks",
        "clear_fields",
        {"config_entry_id": integration.entry_id, "entity_id": entity_id, "fields": ["appearance"]},
        blocking=True,
    )
    assert integration.runtime_data.engine.intents[wash].on
    assert integration.runtime_data.engine.intents[wash].color is None
    assert not output.calls


async def test_mask_reconfigure_expiry_and_removal(hass, integration, output):
    from datetime import UTC, datetime

    wash = list(integration.subentries)[1]
    subentry = integration.subentries[wash]
    hass.config_entries.async_update_subentry(
        integration, subentry, data={**subentry.data, "power": "force_off"}
    )
    await hass.async_block_till_done()
    result = await hass.config_entries.subentries.async_init(
        (integration.entry_id, "mask"),
        context={"source": "reconfigure", "subentry_id": wash},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Blackout",
            "priority": 100,
            "appearance": False,
            "brightness": False,
            "power": True,
            "force_off": True,
            "restore": True,
            "duration": 30,
        },
    )
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    controller = integration.runtime_data
    ids = entities(hass, integration)
    await controller.set_apply(True)
    await call(hass, ids["normal"])
    await call(hass, ids[wash])
    await settle(hass, controller)
    assert controller.engine.normal.on
    assert not output.is_on
    deadline = controller.engine.intents[wash].expires_at
    await controller._tick(datetime.fromtimestamp(deadline + 1, UTC))
    await settle(hass, controller)
    assert output.is_on
    assert not controller.engine.intents[wash].on
    assert hass.config_entries.async_remove_subentry(integration, wash)
    await hass.async_block_till_done()
    assert controller._worker.done()
    assert wash not in integration.runtime_data.engine.intents
    assert hass.states.get(ids[wash]) is None


async def test_repeated_reload_has_one_writer_and_one_set_of_entities(hass, integration):
    expected_ids = entities(hass, integration)
    for _ in range(5):
        previous = integration.runtime_data
        assert await hass.config_entries.async_reload(integration.entry_id)
        await hass.async_block_till_done()
        assert previous._worker.done()
        assert previous._stopped
        assert entities(hass, integration) == expected_ids
        assert not integration.runtime_data._worker.done()


async def test_inactive_external_state_updates_normal_baseline(hass, integration, output):
    controller = integration.runtime_data
    await controller.set_apply(True)
    await settle(hass, controller)
    await call(hass, output.entity_id, brightness=90, color_temp_kelvin=3100)
    await asyncio.sleep(0.04)
    await settle(hass, controller)
    assert controller.engine.normal.on
    assert controller.engine.normal.brightness == 90
    assert controller.engine.normal.color.value == 3100
    assert len(output.calls) == 1
    assert not controller.engine.suspended
    assert controller.status == "in_sync"


async def test_service_exception_cannot_be_hidden_by_matching_state(hass, integration, output):
    controller = integration.runtime_data
    output.fail_after_report = True
    await controller.set_apply(True)
    await call(hass, entities(hass, integration)["normal"])
    await settle(hass, controller)
    assert output.is_on
    assert len(output.calls) == 3
    assert controller.status == "failed"
    assert "rejected" in controller.error


async def test_service_descriptions_and_translation_structure(hass, integration):
    import json
    from pathlib import Path

    from homeassistant.helpers.selector import validate_selector
    from homeassistant.helpers.service import _load_services_file
    from homeassistant.loader import async_get_integration

    descriptions = await hass.async_add_executor_job(
        _load_services_file, await async_get_integration(hass, "light_masks")
    )
    assert set(descriptions) == {"explain", "sync", "resume", "clear_fields"}
    for description in descriptions.values():
        for field in description["fields"].values():
            validate_selector(field["selector"])

    def read_translations():
        root = Path(__file__).parents[2] / "custom_components" / "light_masks"
        source = json.loads((root / "strings.json").read_text(encoding="utf-8"))
        english = json.loads((root / "translations" / "en.json").read_text(encoding="utf-8"))
        ukrainian = json.loads((root / "translations" / "uk.json").read_text(encoding="utf-8"))
        return source, english, ukrainian

    source, english, ukrainian = await hass.async_add_executor_job(read_translations)
    assert english == source

    def paths(value, prefix=""):
        return {
            nested
            for key, child in value.items()
            for nested in (
                paths(child, f"{prefix}.{key}") if isinstance(child, dict) else [f"{prefix}.{key}"]
            )
        }

    assert paths(ukrainian) == paths(source)


async def options_step(hass, entry, step):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "menu"
    assert result["description_placeholders"]["count"] == str(len(entry.subentries))
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step}
    )


@pytest.mark.parametrize(
    ("power", "brightness", "appearance", "modes"),
    [
        (True, False, False, {"onoff"}),
        (False, True, False, {"brightness"}),
        (False, False, True, {"color_temp", "xy"}),
    ],
)
async def test_options_add_exposes_independent_device(
    hass, integration, output, power, brightness, appearance, modes
):
    from homeassistant.helpers import device_registry as dr

    result = await options_step(hass, integration, "add_mask")
    data = result["data_schema"](
        {
            "name": "New mask",
            "priority": 200,
            "power": power,
            "brightness": brightness,
            "appearance": appearance,
        }
    )
    assert type(data["power"]) is bool
    result = await hass.config_entries.options.async_configure(result["flow_id"], data)
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    mask_id = list(integration.subentries)[-1]
    ids = entities(hass, integration)
    registry = er.async_get(hass)
    registered = registry.async_get(ids[mask_id])
    assert registered.config_subentry_id == mask_id
    assert registered.hidden_by is None
    assert registered.device_id != registry.async_get(ids["normal"]).device_id
    assert dr.async_get(hass).async_get(registered.device_id).name == "Test masks New mask"
    assert set(hass.states.get(ids[mask_id]).attributes["supported_color_modes"]) == modes
    await call(hass, ids[mask_id])
    assert integration.runtime_data.engine.intents[mask_id].on
    assert not integration.runtime_data.engine.normal.on
    assert not output.calls
    await call(hass, ids[mask_id], "turn_off")
    assert not integration.runtime_data.engine.intents[mask_id].on
    assert not integration.runtime_data.engine.resolution(0).intent.on


async def test_options_edit_preserves_activation_deadline_and_identity(hass, integration, output):
    from homeassistant.helpers import device_registry as dr

    sun, wash = integration.subentries
    subentry = integration.subentries[wash]
    hass.config_entries.async_update_subentry(
        integration, subentry, data={**subentry.data, "duration": 60}
    )
    await hass.async_block_till_done()
    ids = entities(hass, integration)
    controller = integration.runtime_data
    await controller.set_apply(True)
    await call(hass, ids["normal"])
    await call(hass, ids[sun], color_temp_kelvin=3500)
    await call(hass, ids[wash], rgb_color=[0, 255, 0], brightness=88)
    await settle(hass, controller)
    before = dict(controller.engine.intents)
    result = await options_step(hass, integration, "edit_mask")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"mask": wash})
    data = result["data_schema"](
        {
            "name": "Renamed notification",
            "priority": 5,
            "power": False,
            "appearance": True,
            "brightness": True,
            "duration": 120,
        }
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], data)
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    controller = integration.runtime_data
    await settle(hass, controller)
    assert controller.engine.intents == before
    assert controller.authorized_on
    assert entities(hass, integration) == ids
    assert output.color_temp_kelvin == 3500
    assert output.brightness == 88
    device = er.async_get(hass).async_get(ids[wash]).device_id
    assert dr.async_get(hass).async_get(device).name == "Test masks Renamed notification"
    assert hass.data["light_masks"] == {}
    assert await hass.config_entries.async_reload(integration.entry_id)
    await hass.async_block_till_done()
    assert not integration.runtime_data.engine.intents[wash].on
    assert integration.runtime_data.engine.intents[sun].on


async def test_options_remove_requires_confirmation_and_releases_only_removed_mask(
    hass, integration, output
):
    sun, wash = integration.subentries
    ids = entities(hass, integration)
    controller = integration.runtime_data
    await controller.set_apply(True)
    await call(hass, ids["normal"])
    await call(hass, ids[sun], color_temp_kelvin=3700)
    await call(hass, ids[wash], rgb_color=[0, 255, 0])
    await settle(hass, controller)
    for confirmed in (False, True):
        result = await options_step(hass, integration, "remove_mask")
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"mask": wash}
        )
        assert result["step_id"] == "confirm_remove"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"confirm": confirmed}
        )
        await hass.async_block_till_done()
        if not confirmed:
            assert result["reason"] == "removal_cancelled"
            assert integration.runtime_data is controller
            assert controller.engine.intents[wash].on
    controller = integration.runtime_data
    await settle(hass, controller)
    assert wash not in integration.subentries
    assert wash not in controller.engine.intents
    assert controller.engine.intents[sun].on
    assert hass.states.get(ids[wash]) is None
    assert er.async_get(hass).async_get(ids[wash]) is None
    assert wash not in (await controller.store.async_load())["intents"]
    assert output.color_temp_kelvin == 3700
    assert output.is_on


async def test_legacy_visibility_upgrade_respects_user_hidden(hass, integration):
    sun, wash = integration.subentries
    ids = entities(hass, integration)
    registry = er.async_get(hass)
    registry.async_update_entity(ids[sun], hidden_by=er.RegistryEntryHider.INTEGRATION)
    registry.async_update_entity(ids[wash], hidden_by=er.RegistryEntryHider.USER)
    await hass.config_entries.async_reload(integration.entry_id)
    await hass.async_block_till_done()
    assert entities(hass, integration) == ids
    assert registry.async_get(ids[sun]).hidden_by is None
    assert registry.async_get(ids[wash]).hidden_by is er.RegistryEntryHider.USER


async def test_options_replace_base_preserves_intents_in_shadow(
    hass, integration, output, replacement_output
):
    controller = integration.runtime_data
    ids = entities(hass, integration)
    sun, wash = integration.subentries
    await controller.set_apply(True)
    await call(hass, ids["normal"], brightness=100)
    await call(hass, ids[sun], color_temp_kelvin=3500)
    await call(hass, ids[wash], rgb_color=[0, 255, 0])
    await settle(hass, controller)
    before = controller.engine.snapshot()
    old_calls = list(output.calls)
    result = await options_step(hass, integration, "base")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "name": "Renamed integration",
            "output": replacement_output.entity_id,
            "individual_output": True,
        },
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    controller = integration.runtime_data
    assert controller.output == replacement_output.entity_id
    assert integration.unique_id == er.async_get(hass).async_get(replacement_output.entity_id).id
    assert controller.engine.snapshot() == {**before, "apply": False}
    assert not (await controller.store.async_load())["apply"]
    assert entities(hass, integration) == ids
    assert not controller.authorized_on
    assert replacement_output.calls == []
    assert output.calls == old_calls
    await controller.set_apply(True)
    await settle(hass, controller)
    assert replacement_output.calls == []
    await controller.resume()
    await settle(hass, controller)
    assert replacement_output.is_on
    assert replacement_output.brightness == 100
    assert replacement_output.color_mode == "xy"
    assert output.calls == old_calls


async def test_options_base_rejects_unsafe_and_incompatible_targets(
    hass, integration, output, replacement_output
):
    from homeassistant.components.light import ColorMode
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    before = dict(integration.data)
    replacement_output._attr_supported_color_modes = {ColorMode.ONOFF}
    replacement_output._attr_color_mode = ColorMode.ONOFF
    replacement_output.async_write_ha_state()
    competitor = MockConfigEntry(domain="light_masks", data={"output": "light.already_owned"})
    competitor.add_to_hass(hass)
    hass.states.async_set("light.already_owned", "off", {"supported_color_modes": ["onoff"]})
    for target, error in (
        (entities(hass, integration)["normal"], "aggregate_output"),
        ("light.missing", "unavailable_output"),
        (replacement_output.entity_id, "incompatible_state"),
        ("light.already_owned", "already_configured"),
    ):
        result = await options_step(hass, integration, "base")
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {"name": "Test", "output": target, "individual_output": True},
        )
        assert result["errors"] == {"base": error}
        assert dict(integration.data) == before
        hass.config_entries.options.async_abort(result["flow_id"])


async def test_options_base_storage_failure_does_not_change_target(
    hass, integration, replacement_output, monkeypatch
):
    from homeassistant.exceptions import HomeAssistantError

    controller = integration.runtime_data
    monkeypatch.setattr(
        controller.store, "async_save", AsyncMock(side_effect=HomeAssistantError("disk full"))
    )
    # Engine holds the bound saver installed during construction.
    monkeypatch.setattr(controller.engine, "_save", controller.store.async_save)
    before = dict(integration.data)
    result = await options_step(hass, integration, "base")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"name": "Test", "output": replacement_output.entity_id, "individual_output": True},
    )
    assert result["errors"] == {"base": "storage_failed"}
    assert dict(integration.data) == before
    assert replacement_output.calls == []


async def test_options_mask_validation_and_preserved_error_input(hass, integration, output):
    result = await options_step(hass, integration, "add_mask")
    data = result["data_schema"](
        {
            "name": "My notification",
            "priority": 100,
            "power": False,
            "brightness": False,
            "appearance": True,
        }
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], data)
    assert result["errors"] == {"priority": "duplicate_priority"}
    assert result["data_schema"]({})["name"] == "My notification"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**data, "priority": 200, "appearance": False}
    )
    assert result["errors"] == {"base": "empty_mask"}
    from homeassistant.components.light import ColorMode

    output._attr_supported_color_modes = {ColorMode.ONOFF}
    output._attr_color_mode = ColorMode.ONOFF
    output.async_write_ha_state()
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**data, "priority": 200}
    )
    assert result["errors"] == {"appearance": "unsupported_appearance"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**data, "priority": 200, "appearance": False, "brightness": True}
    )
    assert result["errors"] == {"brightness": "unsupported_brightness"}


async def test_new_entry_defaults_and_empty_options_menu(hass, output):
    result = await hass.config_entries.flow.async_init("light_masks", context={"source": "user"})
    data = result["data_schema"](
        {
            "name": "New integration",
            "output": output.entity_id,
            "individual_output": True,
        }
    )
    assert data["power_port"] is False
    result = await hass.config_entries.flow.async_configure(result["flow_id"], data)
    assert result["type"] == "create_entry"
    entry = result["result"]
    await hass.async_block_till_done()
    assert set(entities(hass, entry)) == {"normal"}
    options = await hass.config_entries.options.async_init(entry.entry_id)
    assert options["menu_options"] == ["base", "add_mask"]
    assert options["description_placeholders"]["count"] == "0"


async def test_overlapping_config_reloads_preserve_state_and_stop_old_workers(hass, integration):
    from custom_components.light_masks import async_reload

    wash = list(integration.subentries)[1]
    await call(hass, entities(hass, integration)[wash], rgb_color=[0, 255, 0])
    first = integration.runtime_data
    before = first.engine.snapshot()
    entered, release = asyncio.Event(), asyncio.Event()
    original_stop = first.stop

    async def paused_stop():
        await original_stop()
        entered.set()
        await release.wait()

    first.stop = paused_stop
    reload_one = asyncio.create_task(async_reload(hass, integration))
    await entered.wait()
    reload_two = asyncio.create_task(async_reload(hass, integration))
    release.set()
    await asyncio.gather(reload_one, reload_two)
    await hass.async_block_till_done()
    assert first._worker.done()
    assert not integration.runtime_data._worker.done()
    assert integration.runtime_data.engine.snapshot() == before
    assert hass.data["light_masks"] == {}


async def test_failed_config_reload_cleans_handoff_before_manual_retry(hass, integration, output):
    from custom_components.light_masks import async_reload

    wash = list(integration.subentries)[1]
    await call(hass, entities(hass, integration)[wash], rgb_color=[0, 255, 0])
    first = integration.runtime_data
    hass.states.async_set(output.entity_id, "unavailable")
    await async_reload(hass, integration)
    assert not hasattr(integration, "runtime_data")
    assert hass.data["light_masks"] == {}
    assert first._worker.done()
    output.async_write_ha_state()
    await hass.config_entries.async_reload(integration.entry_id)
    await hass.async_block_till_done()
    assert not integration.runtime_data.engine.intents[wash].on


async def test_config_reload_preserves_safety_and_power_barriers(hass, integration, output):
    from custom_components.light_masks import async_reload

    controller = integration.runtime_data
    wash = list(integration.subentries)[1]
    await controller.set_apply(True)
    await call(hass, entities(hass, integration)[wash], rgb_color=[0, 255, 0])
    assert not controller.authorized_on
    controller._safety_block = True
    controller.error = "Existing safety barrier"
    await async_reload(hass, integration)
    await settle(hass, integration.runtime_data)
    controller = integration.runtime_data
    assert controller.engine.intents[wash].on
    assert not controller.authorized_on
    assert controller._safety_block
    assert controller.error == "Existing safety barrier"
    assert output.calls == []


async def test_base_replacement_rechecks_ownership_after_storage(
    hass, integration, replacement_output, monkeypatch
):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    controller = integration.runtime_data
    original_save = controller.engine._save

    async def save_with_new_owner(snapshot):
        await original_save(snapshot)
        MockConfigEntry(
            domain="light_masks", data={"output": replacement_output.entity_id}
        ).add_to_hass(hass)

    monkeypatch.setattr(controller.engine, "_save", save_with_new_owner)
    before = dict(integration.data)
    result = await options_step(hass, integration, "base")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"name": "Test", "output": replacement_output.entity_id, "individual_output": True},
    )
    assert result["errors"] == {"base": "already_configured"}
    assert dict(integration.data) == before
    assert not controller.engine.apply
    assert replacement_output.calls == []
