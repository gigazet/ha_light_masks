"""Shared push entity lifecycle."""

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, SIGNAL
from .controller import Controller


def mask_device_info(controller: Controller, mask_id: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"{controller.entry.entry_id}_{mask_id}")},
        name=f"{controller.entry.title} {controller.entry.subentries[mask_id].title}",
        manufacturer="Light Masks",
        model="Independent mask",
    )


def zone_device_info(controller: Controller, zone_id: str) -> DeviceInfo:
    zone = next(zone for zone in controller.entry.data["zones"] if zone["id"] == zone_id)
    return DeviceInfo(
        identifiers={(DOMAIN, f"{controller.entry.entry_id}_{zone_id}")},
        name=f"{controller.entry.title} {zone['name']}",
        manufacturer="Light Masks",
        model="Zone main control",
    )


class MaskEntity(Entity):
    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, controller: Controller, key: str) -> None:
        self.controller = controller
        self._attr_unique_id = f"{controller.entry.entry_id}_{key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, controller.entry.entry_id)},
            "name": controller.entry.title,
            "manufacturer": "Light Masks",
            "model": "Virtual light compositor",
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL}_{self.controller.entry.entry_id}",
                self.async_write_ha_state,
            )
        )
