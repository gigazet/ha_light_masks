"""Explicit shadow/apply control."""

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LightMasksEntry
from .entity import MaskEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: LightMasksEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([ApplySwitch(entry.runtime_data, "apply")])


class ApplySwitch(MaskEntity, SwitchEntity):
    _attr_translation_key = "apply"
    _attr_entity_category = EntityCategory.CONFIG

    @property
    def is_on(self) -> bool:
        return self.controller.engine.apply

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.controller.set_apply(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.controller.set_apply(False)
