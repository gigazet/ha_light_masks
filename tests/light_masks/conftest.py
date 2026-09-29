"""Real Core fixtures with an in-memory physical light, never a live HA instance."""

from types import MappingProxyType

import pytest
from homeassistant import loader
from homeassistant.components.light import ColorMode, LightEntity, LightEntityFeature
from homeassistant.components.light.const import DATA_COMPONENT
from homeassistant.config_entries import ConfigSubentry
from homeassistant.helpers import frame
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_test_home_assistant


@pytest.fixture
async def hass(tmp_path):
    # Core's plugin imports the Unix-only process runner. Use its public test
    # instance context directly: actual services, entities and Store still run.
    async with async_test_home_assistant(config_dir=str(tmp_path)) as instance:
        frame.async_setup(instance)
        instance.data.pop(loader.DATA_CUSTOM_COMPONENTS)
        yield instance
        await instance.async_stop(force=True)


class PhysicalLight(LightEntity):
    _attr_name = "Test output"
    _attr_unique_id = "test_output"
    _attr_supported_color_modes = {ColorMode.COLOR_TEMP, ColorMode.XY}
    _attr_color_mode = ColorMode.COLOR_TEMP
    _attr_color_temp_kelvin = 4000
    _attr_min_color_temp_kelvin = 2700
    _attr_max_color_temp_kelvin = 6500
    _attr_brightness = 128
    _attr_is_on = False
    _attr_supported_features = LightEntityFeature.TRANSITION | LightEntityFeature.EFFECT
    _attr_effect_list = ["none", "rainbow"]
    _attr_should_poll = False

    def __init__(self):
        self.calls = []
        self.gate = None
        self.report = True
        self.fail = False
        self.fail_after_report = False

    async def async_turn_on(self, **kwargs):
        self.calls.append(("on", kwargs))
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            from homeassistant.exceptions import HomeAssistantError

            raise HomeAssistantError("Simulated device failure")
        if not self.report:
            return
        self._attr_is_on = True
        self._attr_brightness = kwargs.get("brightness", self.brightness)
        if "xy_color" in kwargs:
            self._attr_xy_color = kwargs["xy_color"]
            self._attr_color_mode = ColorMode.XY
        if "color_temp_kelvin" in kwargs:
            self._attr_color_temp_kelvin = kwargs["color_temp_kelvin"]
            self._attr_color_mode = ColorMode.COLOR_TEMP
        self.async_write_ha_state()
        if self.fail_after_report:
            from homeassistant.exceptions import HomeAssistantError

            raise HomeAssistantError("Device reported state but rejected the command")

    async def async_turn_off(self, **kwargs):
        self.calls.append(("off", kwargs))
        self._attr_is_on = False
        self.async_write_ha_state()


@pytest.fixture
async def output(hass):
    assert await async_setup_component(hass, "light", {})
    light = PhysicalLight()
    await hass.data[DATA_COMPONENT].async_add_entities([light])
    await hass.async_block_till_done()
    return light


@pytest.fixture
async def replacement_output(hass, output):
    light = PhysicalLight()
    light._attr_name = "Replacement output"
    light._attr_unique_id = "replacement_output"
    await hass.data[DATA_COMPONENT].async_add_entities([light])
    await hass.async_block_till_done()
    return light


@pytest.fixture
async def group_output(hass, output, replacement_output):
    from homeassistant.helpers import entity_registry as er

    entry = MockConfigEntry(
        domain="group",
        title="Test group",
        options={
            "group_type": "light",
            "entities": [output.entity_id, replacement_output.entity_id],
            "hide_members": False,
            "all": False,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entity_id = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)[0].entity_id
    return entry, entity_id


@pytest.fixture
async def group_integration(hass, group_output, monkeypatch):
    from custom_components.light_masks import controller

    monkeypatch.setattr(controller, "ACK_TIMEOUT", 0.04)
    monkeypatch.setattr(controller, "SETTLE_SECONDS", 0.01)
    result = await hass.config_entries.flow.async_init("light_masks", context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"output": group_output[1]}
    )
    assert result["type"] == "create_entry", result
    entry = result["result"]
    await hass.async_block_till_done()
    yield entry
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


@pytest.fixture
async def integration(hass, output, monkeypatch):
    from custom_components.light_masks import controller

    monkeypatch.setattr(controller, "ACK_TIMEOUT", 0.04)
    monkeypatch.setattr(controller, "SETTLE_SECONDS", 0.01)
    entry = MockConfigEntry(
        domain="light_masks",
        title="Test masks",
        data={
            "output": output.entity_id,
            "power_port": True,
            "fallback_brightness": 128,
            "fallback_kelvin": 4000,
            "individual_output": True,
        },
    )
    entry.add_to_hass(hass)
    for name, priority in (("Circadian", 10), ("Washer", 100)):
        hass.config_entries.async_add_subentry(
            entry,
            ConfigSubentry(
                title=name,
                subentry_type="mask",
                unique_id=None,
                data=MappingProxyType(
                    {
                        "priority": priority,
                        "appearance": True,
                        "brightness": False,
                        "power": "transparent",
                        "restore": name == "Circadian",
                        "duration": 0,
                    }
                ),
            ),
        )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield entry
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
