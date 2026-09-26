"""Light Masks integration lifecycle."""

import asyncio
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import (
    ConfigEntryError,
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, PLATFORMS
from .controller import Controller
from .endpoint import observe, validate_endpoint
from .model import Color, Intent, Mask

type LightMasksEntry = ConfigEntry[Controller]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
RELOAD_LOCKS = f"{DOMAIN}_configuration_locks"


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    async def handle(call: ServiceCall) -> dict[str, Any] | None:
        entry = hass.config_entries.async_get_entry(call.data["config_entry_id"])
        if entry is None or entry.domain != DOMAIN or not hasattr(entry, "runtime_data"):
            raise ServiceValidationError("Light Masks entry is not loaded")
        controller: Controller = entry.runtime_data
        if controller._stopped:
            raise ServiceValidationError("Light Masks entry is unloaded")
        if call.service == "explain":
            return controller.diagnostics()
        if call.service == "resume":
            await controller.resume()
        elif call.service == "sync":
            await controller.sync()
        else:
            registered = er.async_get(hass).async_get(call.data["entity_id"])
            if (
                registered is None
                or registered.config_entry_id != entry.entry_id
                or registered.config_subentry_id is None
            ):
                raise ServiceValidationError("Select a mask light belonging to this entry")
            await controller.command(
                registered.config_subentry_id,
                "clear",
                {"fields": call.data["fields"]},
                call.context,
            )
        return None

    for service in ("explain", "resume", "sync", "clear_fields"):
        schema: dict[Any, Any] = {vol.Required("config_entry_id"): cv.string}
        if service == "clear_fields":
            schema.update(
                {
                    vol.Required("entity_id"): cv.entity_id,
                    vol.Required("fields"): vol.All(
                        cv.ensure_list, [vol.In(("brightness", "appearance"))], vol.Length(min=1)
                    ),
                }
            )
        hass.services.async_register(
            DOMAIN,
            service,
            handle,
            schema=vol.Schema(schema),
            supports_response=SupportsResponse.ONLY
            if service == "explain"
            else SupportsResponse.NONE,
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: LightMasksEntry) -> bool:
    output = entry.data["output"]
    registry = er.async_get(hass)
    if registry_id := entry.data.get("output_registry_id"):
        if registered := registry.async_get(registry_id):
            output = registered.entity_id
        else:
            raise ConfigEntryError(
                "Output registry entry was removed; review the replacement and recreate this entry"
            )
    try:
        state = validate_endpoint(hass, output, entry.entry_id)
    except ValueError as err:
        if str(err) == "unavailable_output":
            raise ConfigEntryNotReady("Waiting for the output light") from err
        raise ConfigEntryError(f"Unsafe or unsupported output: {err}") from err
    observed = observe(state)
    assert observed is not None
    modes = set(state.attributes["supported_color_modes"])
    color = observed.color
    if color is None:
        if "color_temp" in modes:
            color = Color("color_temp", entry.data["fallback_kelvin"])
        elif "xy" in modes:
            color = Color("xy", (0.3127, 0.3290))
        elif "hs" in modes:
            color = Color("hs", (0.0, 0.0))
        elif "rgb" in modes:
            color = Color("rgb", (255.0, 255.0, 255.0))
    initial = Intent(
        observed.on,
        None if modes == {"onoff"} else observed.brightness or entry.data["fallback_brightness"],
        color,
    )
    masks = tuple(
        Mask(
            subentry.subentry_id,
            subentry.data["priority"],
            brightness=subentry.data["brightness"],
            appearance=subentry.data["appearance"],
            power=subentry.data["power"],
            restore=subentry.data["restore"],
            duration=subentry.data["duration"] or None,
        )
        for subentry in entry.subentries.values()
        if subentry.subentry_type == "mask"
    )
    controller = Controller(hass, entry, output, initial, masks, state.attributes)
    previous: Controller | None = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    preserve_activation = previous is not None
    if previous is not None:
        if previous.output == output:
            controller.authorized_on = previous.authorized_on
            controller._safety_block = previous._safety_block
            controller.error = previous.error
        else:
            controller.authorized_on = False
    try:
        await controller.initialize(preserve_activation=preserve_activation)
    except (ValueError, KeyError, TypeError, OSError, HomeAssistantError) as err:
        raise ConfigEntryError(f"Light Masks intent storage could not be loaded: {err}") from err
    entry.runtime_data = controller
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload))
    controller.start()
    return True


async def async_reload(hass: HomeAssistant, entry: LightMasksEntry) -> None:
    locks = hass.data.setdefault(RELOAD_LOCKS, {})
    async with locks.setdefault(entry.entry_id, asyncio.Lock()):
        # Core clears runtime_data on unload. Hand off config-edit context only.
        handoffs = hass.data.setdefault(DOMAIN, {})
        previous: Controller | None = getattr(entry, "runtime_data", None)
        if previous is not None:
            handoffs[entry.entry_id] = previous
        try:
            await hass.config_entries.async_reload(entry.entry_id)
        finally:
            handoffs.pop(entry.entry_id, None)


async def async_unload_entry(hass: HomeAssistant, entry: LightMasksEntry) -> bool:
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.stop()
        return True
    return False


async def async_remove_entry(hass: HomeAssistant, entry: LightMasksEntry) -> None:
    from homeassistant.helpers.storage import Store

    from .const import STORE_VERSION

    await Store(hass, STORE_VERSION, f"{DOMAIN}.{entry.entry_id}").async_remove()
    hass.data.get(RELOAD_LOCKS, {}).pop(entry.entry_id, None)
