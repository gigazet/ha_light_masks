"""Atomic Store writes with failure propagation for accepted intent commands."""

from typing import Any

from homeassistant.core import CoreState
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.util.file import WriteError
from homeassistant.util.json import SerializationError


class IntentStore(Store[dict[str, Any]]):
    async def async_save(self, data: dict[str, Any]) -> None:
        if self.hass.state in (CoreState.stopping, CoreState.final_write) or self._read_only:
            raise HomeAssistantError("Cannot accept intent while storage is stopping or read-only")
        await super().async_save(data)

    async def _async_write_data(self, data: dict[str, Any]) -> None:
        # Core Store logs and swallows these exceptions. Intent acceptance must fail.
        try:
            await super()._async_write_data(data)
        except (WriteError, SerializationError) as err:
            raise HomeAssistantError(f"Could not write Light Masks intent: {err}") from err
