"""Config-flow coverage and recovery through Core's public flow managers."""

import json
from pathlib import Path

import pytest
from homeassistant.components.light import ColorMode

from tests.light_masks.test_integration import options_step


async def test_empty_output_error_can_be_corrected(hass, output):
    result = await hass.config_entries.flow.async_init("light_masks", context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["errors"] == {"base": "unavailable_output"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"output": output.entity_id}
    )
    assert result["type"] == "create_entry"
    from homeassistant.helpers import entity_registry as er

    assert result["result"].unique_id == er.async_get(hass).async_get(output.entity_id).id
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(result["result"].entry_id)


async def test_unregistered_output_has_stable_entry_identity(hass):
    entity_id = "light.unregistered_output"
    hass.states.async_set(entity_id, "off", {"supported_color_modes": ["onoff"]})
    result = await hass.config_entries.flow.async_init("light_masks", context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"output": entity_id}
    )
    assert result["type"] == "create_entry"
    assert result["result"].unique_id == entity_id
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(result["result"].entry_id)


async def test_duplicate_entry_aborts(hass, output):
    result = await hass.config_entries.flow.async_init("light_masks", context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"output": output.entity_id}
    )
    entry = result["result"]
    await hass.async_block_till_done()
    duplicate = await hass.config_entries.flow.async_init("light_masks", context={"source": "user"})
    duplicate = await hass.config_entries.flow.async_configure(
        duplicate["flow_id"], {"output": output.entity_id}
    )
    assert duplicate["type"] == "abort"
    assert duplicate["reason"] == "already_configured"
    from homeassistant.helpers import entity_registry as er

    assert entry.unique_id == er.async_get(hass).async_get(output.entity_id).id
    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("flow_kind", ["add_mask", "mask", "user", "reconfigure"])
@pytest.mark.parametrize(
    ("failure", "errors"),
    [
        ("duplicate", {"priority": "duplicate_priority"}),
        ("empty", {"base": "empty_mask"}),
        ("brightness", {"brightness": "unsupported_brightness"}),
        ("appearance", {"appearance": "unsupported_appearance"}),
        ("missing", {"base": "unavailable_output"}),
    ],
)
async def test_mask_errors_can_be_corrected(hass, integration, output, flow_kind, failure, errors):
    target = list(integration.subentries)[1]
    if flow_kind in ("user", "reconfigure"):
        manager = hass.config_entries.subentries
        context = {"source": flow_kind}
        if flow_kind == "reconfigure":
            context["subentry_id"] = target
        result = await manager.async_init((integration.entry_id, "mask"), context=context)
    else:
        manager = hass.config_entries.options
        result = await options_step(
            hass, integration, "edit_mask" if flow_kind == "mask" else flow_kind
        )
        if flow_kind == "mask":
            result = await manager.async_configure(result["flow_id"], {"mask": target})
    valid = {
        "name": "Recovered mask",
        "priority": 200,
        "power": False,
        "brightness": False,
        "appearance": True,
        "restore": False,
        "duration": 0,
    }
    data = dict(valid)
    if failure == "duplicate":
        data["priority"] = 10
    elif failure == "empty":
        data["appearance"] = False
    elif failure in ("brightness", "appearance"):
        output._attr_supported_color_modes = {ColorMode.ONOFF}
        output._attr_color_mode = ColorMode.ONOFF
        output.async_write_ha_state()
        data.update(brightness=failure == "brightness", appearance=failure == "appearance")
    else:
        hass.states.async_remove(output.entity_id)
    result = await manager.async_configure(result["flow_id"], data)
    assert result["errors"] == errors
    output._attr_supported_color_modes = {ColorMode.COLOR_TEMP, ColorMode.XY}
    output._attr_color_mode = ColorMode.COLOR_TEMP
    output.async_write_ha_state()
    result = await manager.async_configure(result["flow_id"], valid)
    assert result["type"] == ("abort" if flow_kind == "reconfigure" else "create_entry")
    assert any(sub.title == "Recovered mask" for sub in integration.subentries.values())
    await hass.async_block_till_done()


@pytest.mark.parametrize("step", ["mask", "confirm_remove", "edit_mask"])
async def test_options_handle_masks_removed_during_flow(hass, integration, step):
    target = list(integration.subentries)[1]
    first_step = "remove_mask" if step == "confirm_remove" else "edit_mask"
    result = await hass.config_entries.options.async_init(integration.entry_id)
    if step != "edit_mask":
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": first_step}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"mask": target}
        )
    for subentry in list(integration.subentries):
        hass.config_entries.async_remove_subentry(integration, subentry)
    await hass.async_block_till_done()
    data = (
        {"next_step_id": "edit_mask"}
        if step == "edit_mask"
        else {"confirm": True}
        if step == "confirm_remove"
        else {
            "name": "Removed",
            "priority": 200,
            "power": False,
            "brightness": False,
            "appearance": True,
            "restore": False,
            "duration": 0,
        }
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], data)
    assert result["type"] == "abort"
    assert result["reason"] == "mask_not_found"


def test_every_flow_field_has_a_description():
    root = Path(__file__).parents[2] / "custom_components" / "light_masks"
    for path in (root / "strings.json", *sorted((root / "translations").glob("*.json"))):
        translations = json.loads(path.read_text(encoding="utf-8"))
        for flow in (
            translations["config"],
            translations["options"],
            *translations["config_subentries"].values(),
        ):
            for step in flow["step"].values():
                assert set(step.get("data", {})) == set(step.get("data_description", {})), path
