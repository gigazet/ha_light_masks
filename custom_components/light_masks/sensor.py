"""Delivery status and read-only mask permissions."""

from typing import Literal

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LightMasksEntry
from .controller import Controller
from .entity import MaskEntity, mask_device_info, zone_device_info

type Permission = Literal["power", "brightness", "appearance"]
PERMISSIONS: tuple[Permission, ...] = ("power", "brightness", "appearance")


async def async_setup_entry(
    hass: HomeAssistant, entry: LightMasksEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([DeliverySensor(entry.runtime_data, "delivery")])
    if entry.runtime_data.engine.multi_zone:
        async_add_entities(
            [ZoneDeliverySensor(entry.runtime_data, zone) for zone in entry.runtime_data.outputs]
        )
    for subentry in entry.subentries.values():
        if subentry.subentry_type == "mask":
            async_add_entities(
                [
                    MaskPermissionSensor(entry.runtime_data, subentry.subentry_id, permission)
                    for permission in PERMISSIONS
                ],
                config_subentry_id=subentry.subentry_id,
            )


class MaskPermissionSensor(MaskEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, controller: Controller, mask_id: str, permission: Permission) -> None:
        super().__init__(controller, f"{mask_id}_control_{permission}")
        self.mask_id, self.permission = mask_id, permission
        self._attr_translation_key = f"control_{permission}"
        self._attr_device_info = mask_device_info(controller, mask_id)
        self._attr_options = (
            ["disabled", "force_on", "force_off"]
            if permission == "power"
            else ["disabled", "enabled"]
        )

    @property
    def native_value(self) -> str:
        mask = next(mask for mask in self.controller.engine.masks if mask.id == self.mask_id)
        if self.permission == "power":
            return "disabled" if mask.power == "transparent" else mask.power
        enabled = mask.brightness if self.permission == "brightness" else mask.appearance
        return "enabled" if enabled else "disabled"


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
        "degraded",
        "failed",
        "suspended_external_change",
    ]

    @property
    def native_value(self) -> str:
        return self.controller.status


class ZoneDeliverySensor(DeliverySensor):
    def __init__(self, controller: Controller, zone: str) -> None:
        super().__init__(controller, f"{zone}_delivery")
        self.zone = zone
        self._attr_device_info = zone_device_info(controller, zone)

    @property
    def native_value(self) -> str:
        return self.controller.delivery_status(self.zone)
