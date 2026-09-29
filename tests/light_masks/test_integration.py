import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.auth.const import GROUP_ID_ADMIN, GROUP_ID_READ_ONLY, GROUP_ID_USER
from homeassistant.core import Context
from homeassistant.exceptions import Unauthorized, UnknownUser
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


@pytest.mark.parametrize("effect", ["rainbow", "fading"])
async def test_effect_metadata_allows_static_masks_and_resume(hass, integration, output, effect):
    controller = integration.runtime_data
    ids = entities(hass, integration)
    sun, wash = integration.subentries
    await controller.set_apply(True)
    await call(hass, ids["normal"])
    await settle(hass, controller)
    count = len(output.calls)
    output._attr_effect = effect
    output.async_write_ha_state()
    await asyncio.sleep(0.04)
    await hass.async_block_till_done()
    assert controller.status == "in_sync"
    assert len(output.calls) == count

    await call(hass, ids[sun], color_temp_kelvin=3300)
    await call(hass, ids[wash], rgb_color=[0, 255, 0])
    await settle(hass, controller)
    assert output.color_mode == "xy"
    assert controller.status == "in_sync"
    assert output.effect == effect
    count = len(output.calls)
    await call(hass, ids[sun], color_temp_kelvin=3500)
    await settle(hass, controller)
    assert len(output.calls) == count

    await call(hass, ids[wash], "turn_off")
    await settle(hass, controller)
    assert output.color_temp_kelvin == 3500
    await call(hass, ids["normal"], brightness=90)
    await settle(hass, controller)
    assert output.brightness == 90

    await call(hass, output.entity_id, "turn_off")
    await asyncio.sleep(0.04)
    await hass.async_block_till_done()
    assert controller.engine.suspended
    await controller.resume()
    await settle(hass, controller)
    assert output.is_on
    assert controller.status == "in_sync"
    assert output.effect == effect
    assert all("effect" not in payload for _, payload in output.calls)
    await call(hass, ids["normal"], "turn_off")
    await settle(hass, controller)
    assert not output.is_on


@pytest.mark.parametrize("use_group", [False, True])
async def test_effect_output_enrollment_reload_and_delivery(
    hass, output, replacement_output, group_output, monkeypatch, use_group
):
    from homeassistant.components.light import LightEntityFeature

    from custom_components.light_masks import controller as controller_module

    monkeypatch.setattr(controller_module, "ACK_TIMEOUT", 0.04)
    monkeypatch.setattr(controller_module, "SETTLE_SECONDS", 0.01)
    members = (output, replacement_output) if use_group else (output,)
    for member in members:
        member._attr_is_on = True
        member._attr_effect = "fading"
        member._attr_effect_list = ["fading", "breathing"]
        member.async_write_ha_state()
    await hass.async_block_till_done()
    target = group_output[1] if use_group else output.entity_id
    result = await hass.config_entries.flow.async_init("light_masks", context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"output": target})
    assert result["type"] == "create_entry", result
    entry = result["result"]
    await hass.async_block_till_done()
    try:
        controller = entry.runtime_data
        assert controller.status == "shadow"
        assert all(not member.calls for member in members)
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        controller = entry.runtime_data
        main = entities(hass, entry)["normal"]
        assert (
            not hass.states.get(main).attributes["supported_features"] & LightEntityFeature.EFFECT
        )
        await controller.set_apply(True)
        await call(hass, main, brightness=90, color_temp_kelvin=3500)
        await settle(hass, controller)
        assert controller.status == "in_sync"
        for member in members:
            assert member.brightness == 90
            assert member.color_temp_kelvin == 3500
            assert member.effect == "fading"
            assert all("effect" not in payload for _, payload in member.calls)
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


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


@pytest.mark.parametrize("service", ["explain", "resume", "sync", "clear_fields"])
@pytest.mark.parametrize("caller", ["admin", "user", "read_only", "unknown", "automation"])
async def test_management_services_require_admin(
    hass, integration, output, monkeypatch, service, caller
):
    # Core makes the first user an owner regardless of the requested group.
    await hass.auth.async_create_user("Test owner", group_ids=[GROUP_ID_ADMIN])
    groups = {
        "admin": GROUP_ID_ADMIN,
        "user": GROUP_ID_USER,
        "read_only": GROUP_ID_READ_ONLY,
    }
    if caller in groups:
        user = await hass.auth.async_create_user("Test caller", group_ids=[groups[caller]])
        assert user.is_admin == (caller == "admin")
        context = Context(user_id=user.id)
    else:
        context = Context(user_id="nonexistent-user" if caller == "unknown" else None)
    controller = integration.runtime_data
    handlers = {
        "explain": Mock(return_value={"status": "shadow"}),
        "resume": AsyncMock(),
        "sync": AsyncMock(),
        "clear_fields": AsyncMock(),
    }
    for name, handler in handlers.items():
        method = {"explain": "diagnostics", "clear_fields": "command"}.get(name, name)
        monkeypatch.setattr(controller, method, handler)
    data = {"config_entry_id": integration.entry_id}
    if service == "clear_fields":
        mask = next(iter(integration.subentries))
        data.update(entity_id=entities(hass, integration)[mask], fields=["appearance"])

    async def invoke():
        return await hass.services.async_call(
            "light_masks",
            service,
            data,
            blocking=True,
            return_response=service == "explain",
            context=context,
        )

    if caller in {"user", "read_only", "unknown"}:
        error = UnknownUser if caller == "unknown" else Unauthorized
        with pytest.raises(error):
            await invoke()
        for handler in handlers.values():
            handler.assert_not_called()
    else:
        result = await invoke()
        if service == "explain":
            assert result == {"status": "shadow"}
            handlers[service].assert_called_once_with()
        elif service == "clear_fields":
            handlers[service].assert_awaited_once_with(
                mask, "clear", {"fields": ["appearance"]}, context
            )
        else:
            handlers[service].assert_awaited_once_with()
    assert not output.calls


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
    from homeassistant.helpers.translation import async_get_translations
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
    for language in (source, ukrainian):
        options = language["options"]["step"]
        native = language["config_subentries"]["mask"]["step"]
        for parent, child in (
            (options["add_mask"], native["user"]),
            (options["mask"], native["reconfigure"]),
        ):
            assert parent["data_description"] == child["data_description"]
            assert set(parent["data_description"]) <= set(parent["data"])
            assert parent["data"]["brightness"] != parent["data"]["appearance"]
            for field in ("power", "brightness", "appearance", "priority", "duration"):
                assert parent["data_description"][field]
            assert "100" in parent["data_description"]["priority"]

    for locale, language in (("en", english), ("uk", ukrainian)):
        for category in ("options", "config_subentries", "entity"):
            translated = await async_get_translations(hass, locale, category, {"light_masks"})
            for path in paths(language[category]):
                value = language[category]
                for key in path.lstrip(".").split("."):
                    value = value[key]
                assert translated[f"component.light_masks.{category}{path}"] == value


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
    permissions = {
        field: registry.async_get_entity_id(
            "sensor", "light_masks", f"{integration.entry_id}_{mask_id}_control_{field}"
        )
        for field in ("power", "brightness", "appearance")
    }
    for field, expected in (
        ("power", "force_on" if power else "disabled"),
        ("brightness", "enabled" if brightness else "disabled"),
        ("appearance", "enabled" if appearance else "disabled"),
    ):
        sensor = registry.async_get(permissions[field])
        assert sensor.config_subentry_id == mask_id
        assert sensor.device_id == registered.device_id
        assert sensor.disabled_by is None
        assert sensor.hidden_by is None
        assert sensor.entity_category.value == "diagnostic"
        assert hass.states.get(sensor.entity_id).state == expected
        assert hass.states.get(sensor.entity_id).attributes["friendly_name"] == (
            f"Test masks New mask Control "
            f"{'on/off' if field == 'power' else 'color' if field == 'appearance' else field}"
        )
    await call(hass, ids[mask_id])
    assert integration.runtime_data.engine.intents[mask_id].on
    assert not integration.runtime_data.engine.normal.on
    assert not output.calls
    await call(hass, ids[mask_id], "turn_off")
    assert not integration.runtime_data.engine.intents[mask_id].on
    assert not integration.runtime_data.engine.resolution(0).intent.on
    assert hass.states.get(permissions["appearance"]).state == (
        "enabled" if appearance else "disabled"
    )


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
    permission_id = er.async_get(hass).async_get_entity_id(
        "sensor", "light_masks", f"{integration.entry_id}_{wash}_control_brightness"
    )
    assert hass.states.get(permission_id).state == "enabled"
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
    mask_sensors = [
        entity.entity_id
        for entity in er.async_entries_for_config_entry(er.async_get(hass), integration.entry_id)
        if entity.config_subentry_id == wash and entity.domain == "sensor"
    ]
    assert len(mask_sensors) == 3
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
    assert all(hass.states.get(entity_id) is None for entity_id in mask_sensors)
    assert all(er.async_get(hass).async_get(entity_id) is None for entity_id in mask_sensors)
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


async def test_options_rename_preserves_base_intents_and_identity(
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
        },
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    controller = integration.runtime_data
    assert controller.output == output.entity_id
    assert controller.engine.snapshot() == before
    assert (await controller.store.async_load())["apply"]
    assert entities(hass, integration) == ids
    assert controller.authorized_on
    assert replacement_output.calls == []
    assert output.calls == old_calls
    assert integration.title == "Renamed integration"


async def test_options_base_is_fixed_even_for_direct_flow_input(
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
    from homeassistant.data_entry_flow import InvalidData

    for target in (
        entities(hass, integration)["normal"],
        "light.missing",
        replacement_output.entity_id,
        "light.already_owned",
    ):
        result = await options_step(hass, integration, "base")
        assert set(result["data_schema"].schema) == {"name"}
        assert result["description_placeholders"]["output"] == output.entity_id
        with pytest.raises(InvalidData):
            await hass.config_entries.options.async_configure(
                result["flow_id"], {"name": "Test", "output": target}
            )
        assert dict(integration.data) == before
        hass.config_entries.options.async_abort(result["flow_id"])


async def test_options_rejects_base_change_before_storage(
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
    from homeassistant.data_entry_flow import InvalidData

    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"], {"name": "Test", "output": replacement_output.entity_id}
        )
    controller.store.async_save.assert_not_awaited()
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
            "output": output.entity_id,
        }
    )
    assert data["power_port"] is False
    result = await hass.config_entries.flow.async_configure(result["flow_id"], data)
    assert result["type"] == "create_entry"
    entry = result["result"]
    assert entry.title == output.name
    await hass.async_block_till_done()
    assert set(entities(hass, entry)) == {"normal"}
    assert hass.states.get(entities(hass, entry)["normal"]).name.endswith("Main control")
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


async def test_name_can_be_edited_while_base_unavailable(hass, integration, output):
    before = dict(integration.data)
    hass.states.async_set(output.entity_id, "unavailable")
    result = await options_step(hass, integration, "base")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "New name"}
    )
    assert result["type"] == "create_entry"
    assert integration.title == "New name"
    assert dict(integration.data) == before
    output.async_write_ha_state()
    await hass.async_block_till_done()


async def test_group_main_control_masks_and_release(
    hass, group_integration, output, replacement_output
):
    entry = group_integration
    controller = entry.runtime_data
    assert set(controller.members) == {output.entity_id, replacement_output.entity_id}
    await controller.set_apply(True)
    await call(hass, entities(hass, entry)["normal"], brightness=150, color_temp_kelvin=3500)
    await settle(hass, controller)
    assert controller.status == "in_sync"
    assert all(light.is_on and light.brightness == 150 for light in (output, replacement_output))
    result = await options_step(hass, entry, "add_mask")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "name": "Washer",
            "priority": 100,
            "power": False,
            "brightness": False,
            "appearance": True,
            "restore": False,
            "duration": 0,
        },
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    controller = entry.runtime_data
    mask = list(entry.subentries)[0]
    await call(hass, entities(hass, entry)[mask], rgb_color=[0, 255, 0], brightness=42)
    await settle(hass, controller)
    assert controller.status == "in_sync"
    assert all(
        light.color_mode == "xy" and light.brightness == 150
        for light in (output, replacement_output)
    )
    await call(hass, entities(hass, entry)[mask], "turn_off")
    await settle(hass, controller)
    assert all(light.color_temp_kelvin == 3500 for light in (output, replacement_output))
    await call(hass, entities(hass, entry)["normal"], "turn_off")
    await settle(hass, controller)
    assert not output.is_on and not replacement_output.is_on
    calls = len(output.calls) + len(replacement_output.calls)
    await call(hass, entities(hass, entry)[mask], rgb_color=[255, 0, 0])
    await settle(hass, controller)
    assert not output.is_on and not replacement_output.is_on
    assert len(output.calls) + len(replacement_output.calls) == calls


async def test_group_confirmation_requires_every_member(
    hass, group_integration, output, replacement_output
):
    controller = group_integration.runtime_data
    replacement_output.report = False
    await controller.set_apply(True)
    await call(
        hass, entities(hass, group_integration)["normal"], brightness=128, color_temp_kelvin=4000
    )
    await settle(hass, controller)
    assert output.is_on and not replacement_output.is_on
    # The aggregate reports the desired On/color/brightness despite a failed member.
    assert hass.states.is_state(controller.output, "on")
    assert controller.status in ("unverified", "failed")
    assert not controller.converged(controller.desired())
    assert 1 <= len(replacement_output.calls) <= 3


async def test_group_external_member_change_suspends_without_masks(
    hass, group_integration, output, replacement_output
):
    controller = group_integration.runtime_data
    await controller.set_apply(True)
    await call(hass, entities(hass, group_integration)["normal"], brightness=128)
    await settle(hass, controller)
    await call(hass, replacement_output.entity_id, "turn_off")
    await asyncio.sleep(0.04)
    await hass.async_block_till_done()
    assert controller.engine.suspended
    assert controller.status == "suspended_external_change"
    assert controller.engine.normal.on
    assert not replacement_output.is_on


async def test_group_mixed_startup_does_not_wake_off_members(
    hass, group_output, output, replacement_output
):
    await call(hass, output.entity_id, brightness=128)
    await hass.async_block_till_done()
    result = await hass.config_entries.flow.async_init("light_masks", context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"output": group_output[1]}
    )
    entry = result["result"]
    await hass.async_block_till_done()
    controller = entry.runtime_data
    assert controller.engine.normal.on
    assert not controller.authorized_on
    await controller.set_apply(True)
    await settle(hass, controller)
    assert controller.status == "awaiting_resume"
    assert not replacement_output.calls
    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize(
    ("attribute", "value", "error"),
    [
        ("_attr_supported_color_modes", {"onoff"}, "incompatible_group"),
        ("_attr_min_color_temp_kelvin", 3000, "incompatible_group"),
        ("_attr_supported_features", 0, "incompatible_group"),
        ("_attr_available", False, "unavailable_output"),
    ],
)
async def test_group_rejects_incompatible_or_unavailable_members(
    hass, group_output, replacement_output, attribute, value, error
):
    from homeassistant.components.light import LightEntityFeature

    from custom_components.light_masks.endpoint import validate_endpoint

    if attribute == "_attr_supported_features":
        value = LightEntityFeature(value)
    setattr(replacement_output, attribute, value)
    replacement_output.async_write_ha_state()
    await hass.async_block_till_done()
    with pytest.raises(ValueError, match=error):
        validate_endpoint(hass, group_output[1])


async def test_group_ownership_protects_members(hass, group_integration, output):
    from custom_components.light_masks.endpoint import validate_endpoint

    with pytest.raises(ValueError, match="already_configured"):
        validate_endpoint(hass, output.entity_id)


async def test_member_ownership_prevents_group_enrollment(hass, integration, group_output):
    from custom_components.light_masks.endpoint import validate_endpoint

    with pytest.raises(ValueError, match="already_configured"):
        validate_endpoint(hass, group_output[1])


async def test_group_membership_change_blocks_delivery_and_reload(
    hass, group_integration, group_output, output
):
    controller = group_integration.runtime_data
    await controller.set_apply(True)
    await call(hass, entities(hass, group_integration)["normal"])
    await settle(hass, controller)
    calls = list(output.calls)
    group_entry, _ = group_output
    hass.config_entries.async_update_entry(
        group_entry, options={**group_entry.options, "entities": [output.entity_id]}
    )
    await hass.config_entries.async_reload(group_entry.entry_id)
    await hass.async_block_till_done()
    assert controller._safety_block
    await call(hass, entities(hass, group_integration)["normal"], brightness=80)
    await settle(hass, controller)
    assert output.calls == calls
    await hass.config_entries.async_reload(group_integration.entry_id)
    assert not hasattr(group_integration, "runtime_data")


async def test_nested_groups_cycles_and_duplicate_members(hass, group_output, output):
    from custom_components.light_masks.endpoint import endpoint_members, validate_endpoint

    registry = er.async_get(hass)
    outer = registry.async_get_or_create("light", "group", "outer_group")
    attributes = dict(hass.states.get(group_output[1]).attributes)
    hass.states.async_set(outer.entity_id, "off", {**attributes, "entity_id": [group_output[1]]})
    validate_endpoint(hass, outer.entity_id)
    assert len(endpoint_members(hass, outer.entity_id)) == 2
    for members in ([outer.entity_id], [group_output[1], output.entity_id], []):
        hass.states.async_set(outer.entity_id, "off", {**attributes, "entity_id": members})
        with pytest.raises(ValueError, match="aggregate_output"):
            validate_endpoint(hass, outer.entity_id)


async def test_group_unavailable_member_defers_authorized_delivery(
    hass, group_integration, output, replacement_output
):
    controller = group_integration.runtime_data
    await controller.set_apply(True)
    replacement_output._attr_available = False
    replacement_output.async_write_ha_state()
    await hass.async_block_till_done()
    await call(hass, entities(hass, group_integration)["normal"], brightness=70)
    await settle(hass, controller)
    assert not output.calls
    assert controller.status == "unavailable"
    replacement_output._attr_available = True
    replacement_output.async_write_ha_state()
    await settle(hass, controller)
    assert not controller._safety_block
    assert controller.status == "in_sync"
    assert output.brightness == replacement_output.brightness == 70


async def test_group_malformed_feedback_blocks_without_killing_writer(
    hass, group_integration, output, replacement_output
):
    controller = group_integration.runtime_data
    await controller.set_apply(True)
    await call(hass, entities(hass, group_integration)["normal"], brightness=100)
    await settle(hass, controller)
    attributes = dict(hass.states.get(replacement_output.entity_id).attributes)
    # Invalid XY is not part of the aggregate's active CCT state.
    hass.states.async_set(
        replacement_output.entity_id,
        "on",
        {**attributes, "color_mode": "xy", "xy_color": (2.0, 2.0)},
    )
    await asyncio.sleep(0.04)
    await hass.async_block_till_done()
    assert not controller.converged(controller.desired())
    assert controller._safety_block
    assert controller.status == "failed"
    assert controller.error.startswith("Invalid output state:")
    assert not controller._worker.done()


async def test_overlapping_group_and_virtual_members_rejected(
    hass, group_integration, group_output
):
    from custom_components.light_masks.endpoint import validate_endpoint

    registry = er.async_get(hass)
    attributes = dict(hass.states.get(group_output[1]).attributes)
    outer = registry.async_get_or_create("light", "group", "overlapping_group")
    hass.states.async_set(outer.entity_id, "off", attributes)
    with pytest.raises(ValueError, match="already_configured"):
        validate_endpoint(hass, outer.entity_id)
    for platform in ("light_masks", "lightener", "lightener_studio"):
        member = registry.async_get_or_create("light", platform, f"virtual_{platform}")
        hass.states.async_set(member.entity_id, "off", {"supported_color_modes": ["xy"]})
        hass.states.async_set(
            outer.entity_id, "off", {**attributes, "entity_id": [member.entity_id]}
        )
        with pytest.raises(ValueError, match="aggregate_output"):
            validate_endpoint(hass, outer.entity_id)


async def test_main_control_upgrade_preserves_custom_name_and_entity_id(hass, integration):
    registry = er.async_get(hass)
    main = entities(hass, integration)["normal"]
    registry.async_update_entity(main, name="My everyday lighting")
    await hass.config_entries.async_reload(integration.entry_id)
    await hass.async_block_till_done()
    assert entities(hass, integration)["normal"] == main
    assert hass.states.get(main).name == "My everyday lighting"


async def test_group_member_failure_is_not_confirmed_from_matching_state(
    hass, group_integration, replacement_output
):
    controller = group_integration.runtime_data
    replacement_output.fail_after_report = True
    await controller.set_apply(True)
    await call(hass, entities(hass, group_integration)["normal"], brightness=128)
    await settle(hass, controller)
    assert controller.converged(controller.desired())
    assert controller.status == "failed"
    assert controller.error
    assert len(replacement_output.calls) == 3


async def test_group_ownership_rechecked_after_flow_await(hass, group_output, output, monkeypatch):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.light_masks.config_flow import LightMasksConfigFlow

    original = LightMasksConfigFlow.async_set_unique_id

    async def claim_member(self, unique_id, *, raise_on_progress=True):
        result = await original(self, unique_id, raise_on_progress=raise_on_progress)
        MockConfigEntry(
            domain="light_masks",
            title="Other owner",
            data={"output": output.entity_id},
            disabled_by=ConfigEntryDisabler.USER,
        ).add_to_hass(hass)
        return result

    from homeassistant.config_entries import ConfigEntryDisabler

    monkeypatch.setattr(LightMasksConfigFlow, "async_set_unique_id", claim_member)
    result = await hass.config_entries.flow.async_init("light_masks", context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"output": group_output[1]}
    )
    assert result["errors"] == {"base": "already_configured"}
    assert len(hass.config_entries.async_entries("light_masks")) == 1


async def test_nested_group_delivery(hass, group_output, output, replacement_output):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    outer = MockConfigEntry(
        domain="group",
        title="Outer group",
        options={
            "group_type": "light",
            "entities": [group_output[1]],
            "hide_members": False,
            "all": False,
        },
    )
    outer.add_to_hass(hass)
    assert await hass.config_entries.async_setup(outer.entry_id)
    await hass.async_block_till_done()
    output_id = er.async_entries_for_config_entry(er.async_get(hass), outer.entry_id)[0].entity_id
    result = await hass.config_entries.flow.async_init("light_masks", context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"output": output_id}
    )
    assert result["type"] == "create_entry"
    entry = result["result"]
    await hass.async_block_till_done()
    controller = entry.runtime_data
    await controller.set_apply(True)
    await call(hass, entities(hass, entry)["normal"], brightness=130, rgb_color=[0, 255, 0])
    await settle(hass, controller)
    assert controller.status == "in_sync"
    assert all(
        light.is_on and light.brightness == 130 and light.color_mode == "xy"
        for light in (output, replacement_output)
    )
    await call(hass, entities(hass, entry)["normal"], "turn_off")
    await settle(hass, controller)
    assert not output.is_on and not replacement_output.is_on
    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("native", [False, True])
async def test_mask_editor_starts_blank_and_requires_name(hass, integration, native):
    from homeassistant.data_entry_flow import InvalidData

    manager = hass.config_entries.subentries if native else hass.config_entries.options
    result = (
        await manager.async_init((integration.entry_id, "mask"), context={"source": "user"})
        if native
        else await options_step(hass, integration, "add_mask")
    )
    fields = {str(key): key for key in result["data_schema"].schema}
    assert fields["name"].default() == ""
    assert "force_off" not in fields
    for name in ("", "   "):
        with pytest.raises(InvalidData):
            await manager.async_configure(result["flow_id"], {"name": name})
    assert len(integration.subentries) == 2
    result = await manager.async_configure(
        result["flow_id"],
        {"name": "My mask", "priority": 200, "brightness": True, "appearance": False},
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    mask = list(integration.subentries.values())[-1]
    assert mask.title == "My mask"
    assert mask.data["brightness"] is True
    assert mask.data["appearance"] is False


async def test_permission_indicators_preserve_identity_and_show_legacy_power(hass, integration):
    mask_id = list(integration.subentries)[1]
    registry = er.async_get(hass)
    sensor_id = registry.async_get_entity_id(
        "sensor", "light_masks", f"{integration.entry_id}_{mask_id}_control_power"
    )
    assert hass.states.get(sensor_id).state == "disabled"
    for power in ("force_off", "force_on", "transparent"):
        subentry = integration.subentries[mask_id]
        hass.config_entries.async_update_subentry(
            integration, subentry, data={**subentry.data, "power": power}
        )
        await hass.async_block_till_done()
        assert (
            registry.async_get_entity_id(
                "sensor", "light_masks", f"{integration.entry_id}_{mask_id}_control_power"
            )
            == sensor_id
        )
        assert hass.states.get(sensor_id).state == ("disabled" if power == "transparent" else power)
        assert not integration.runtime_data.engine.intents[mask_id].on


@pytest.mark.parametrize("duration", [0, 120])
async def test_edit_duration_preserves_timer_until_next_on(hass, integration, duration):
    import time

    mask_id = list(integration.subentries)[1]
    subentry = integration.subentries[mask_id]
    hass.config_entries.async_update_subentry(
        integration, subentry, data={**subentry.data, "duration": 60}
    )
    await hass.async_block_till_done()
    mask_entity = entities(hass, integration)[mask_id]
    await call(hass, mask_entity, brightness=80)
    deadline = integration.runtime_data.engine.intents[mask_id].expires_at
    result = await options_step(hass, integration, "edit_mask")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"mask": mask_id})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"duration": duration}
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert integration.runtime_data.engine.intents[mask_id].expires_at == deadline
    before = time.time()
    await call(hass, mask_entity, brightness=90)
    renewed = integration.runtime_data.engine.intents[mask_id].expires_at
    if duration == 0:
        assert renewed is None
    else:
        assert before + duration <= renewed <= time.time() + duration
