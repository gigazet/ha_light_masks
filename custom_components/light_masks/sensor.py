"""Compact delivery status."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LightMasksEntry
from .entity import MaskEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: LightMasksEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([DeliverySensor(entry.runtime_data, "delivery")])


class DeliverySensor(MaskEntity, SensorEntity):
    _attr_translation_key = "delivery"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [
        "shadow",
        "awaiting_resume",
        "in_sync",
        "pending",
        "unverified",
        "unavailable",
        "failed",
        "suspended_external_change",
    ]

    @property
    def native_value(self) -> str:
        return self.controller.status
