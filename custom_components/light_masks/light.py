"""Independent virtual lights; their state is producer intent, not physical feedback."""

from typing import Any

from homeassistant.components.light import (
    LightEntity,
    filter_turn_on_params,
    process_turn_on_params,
)
from homeassistant.components.light.const import ColorMode, LightEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LightMasksEntry
from .controller import Controller
from .entity import MaskEntity, mask_device_info, zone_device_info
from .model import Intent

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: LightMasksEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    controller = entry.runtime_data
    registry = er.async_get(hass)
    for registered in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            registered.config_subentry_id is not None
            and registered.hidden_by is er.RegistryEntryHider.INTEGRATION
        ):
            registry.async_update_entity(registered.entity_id, hidden_by=None)
    entities = [IntentLight(controller, "normal", "Main control")]
    if controller.engine.multi_zone:
        entities.extend(
            IntentLight(controller, zone, "Main control") for zone in controller.outputs
        )
    if entry.data["power_port"]:
        entities.append(IntentLight(controller, "normal", "Power", power_only=True))
    async_add_entities(entities)
    for subentry in entry.subentries.values():
        if subentry.subentry_type == "mask":
            async_add_entities(
                [IntentLight(controller, subentry.subentry_id, subentry.title)],
                config_subentry_id=subentry.subentry_id,
            )


class IntentLight(MaskEntity, LightEntity):
    def __init__(
        self, controller: Controller, producer: str, name: str, *, power_only: bool = False
    ) -> None:
        super().__init__(controller, "power" if power_only else producer)
        self.producer, self.power_only = producer, power_only
        if controller.engine.is_normal(producer) and not power_only:
            self._attr_translation_key = "main"
        else:
            self._attr_name = name
        if producer != "normal" and producer in controller.engine.normals:
            self._attr_device_info = zone_device_info(controller, producer)
        elif producer != "normal":
            self._attr_name = None
            self._attr_device_info = mask_device_info(controller, producer)
        mask = next((mask for mask in controller.engine.masks if mask.id == producer), None)
        self._attr_supported_color_modes = (
            {ColorMode.ONOFF}
            if power_only or (mask is not None and not mask.appearance and not mask.brightness)
            else {ColorMode.BRIGHTNESS}
            if mask is not None and not mask.appearance
            else {ColorMode(mode) for mode in controller.attributes["supported_color_modes"]}
        )
        self._attr_supported_features = (
            LightEntityFeature.TRANSITION
            if controller.attributes.get("supported_features", 0) & LightEntityFeature.TRANSITION
            and self._attr_supported_color_modes != {ColorMode.ONOFF}
            else LightEntityFeature(0)
        )
        self._attr_min_color_temp_kelvin = controller.attributes.get("min_color_temp_kelvin", 2000)
        self._attr_max_color_temp_kelvin = controller.attributes.get("max_color_temp_kelvin", 6500)

    @property
    def intent(self) -> Intent:
        engine = self.controller.engine
        return (
            engine.normal
            if self.producer == "normal"
            else engine.normals[self.producer]
            if self.producer in engine.normals
            else engine.intents[self.producer]
        )

    @property
    def is_on(self) -> bool:
        return self.intent.on

    @property
    def brightness(self) -> int | None:
        return (
            None
            if self._attr_supported_color_modes == {ColorMode.ONOFF}
            else self.intent.brightness
        )

    @property
    def color_mode(self) -> ColorMode | None:
        assert self._attr_supported_color_modes is not None
        if (
            self.controller.engine.multi_zone
            and self.producer == "normal"
            and self.intent.color is None
            and self._attr_supported_color_modes - {ColorMode.ONOFF, ColorMode.BRIGHTNESS}
        ):
            return ColorMode.UNKNOWN
        if self.intent.color and self.intent.color.mode in self._attr_supported_color_modes:
            return ColorMode(self.intent.color.mode)
        return sorted(self._attr_supported_color_modes, key=str)[0]

    @property
    def color_temp_kelvin(self) -> int | None:
        color = self.intent.color
        return (
            color.value
            if color and color.mode == "color_temp" and isinstance(color.value, int)
            else None
        )

    @property
    def xy_color(self) -> tuple[float, float] | None:
        color = self.intent.color
        if color and color.mode == "xy" and isinstance(color.value, tuple):
            return color.value[0], color.value[1]
        return None

    @property
    def hs_color(self) -> tuple[float, float] | None:
        color = self.intent.color
        if color and color.mode == "hs" and isinstance(color.value, tuple):
            return color.value[0], color.value[1]
        return None

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        color = self.intent.color
        if color and color.mode == "rgb" and isinstance(color.value, tuple):
            return round(color.value[0]), round(color.value[1]), round(color.value[2])
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "role": "power"
            if self.power_only
            else "normal"
            if self.controller.engine.is_normal(self.producer)
            else "mask",
            "expires_at": self.intent.expires_at,
        }
        if self.controller.engine.multi_zone and self.controller.engine.is_normal(self.producer):
            if self.producer == "normal":
                normals = tuple(self.controller.engine.normals.values())
                result.update(
                    scope="whole",
                    mixed_power=len({intent.on for intent in normals}) > 1,
                    on_zone_count=sum(intent.on for intent in normals),
                    zone_count=len(normals),
                    mixed_brightness=len({intent.brightness for intent in normals}) > 1,
                    mixed_appearance=len({intent.color for intent in normals}) > 1,
                )
            else:
                result.update(scope="zone", zone_id=self.producer)
        if not self.controller.engine.is_normal(self.producer):
            mask = next(m for m in self.controller.engine.masks if m.id == self.producer)
            result.update(
                {
                    "priority": mask.priority,
                    "owns_brightness": mask.brightness,
                    "owns_appearance": mask.appearance,
                    "power_contribution": mask.power,
                }
            )
        return result

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.controller.command(
            self.producer, "on", kwargs, self._context, power_only=self.power_only
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.controller.command(
            self.producer, "off", kwargs, self._context, power_only=self.power_only
        )

    async def async_toggle(self, **kwargs: Any) -> None:
        await self.controller.command(
            self.producer,
            "toggle",
            kwargs,
            self._context,
            power_only=self.power_only,
            prepare=lambda data: filter_turn_on_params(
                self, process_turn_on_params(self.hass, self, dict(data))
            ),
        )
