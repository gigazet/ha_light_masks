"""Integration-scoped diagnostics without unrelated household data."""

from typing import Any

from homeassistant.core import HomeAssistant

from . import LightMasksEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: LightMasksEntry
) -> dict[str, Any]:
    return entry.runtime_data.diagnostics()
