"""Explicit recovery control, never automatic power authorization."""

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LightMasksEntry
from .entity import MaskEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: LightMasksEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([ResumeButton(entry.runtime_data, "resume")])


class ResumeButton(MaskEntity, ButtonEntity):
    _attr_translation_key = "resume"
    _attr_entity_category = EntityCategory.CONFIG

    async def async_press(self) -> None:
        await self.controller.resume()
