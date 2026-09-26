"""Static, individually addressed endpoint validation and normalization."""

from collections.abc import Mapping
from typing import Any

from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, SUPPORTED_MODES
from .engine import color_from_payload
from .model import Color, Intent


def validate_endpoint(hass: HomeAssistant, entity_id: str, entry_id: str | None = None) -> State:
    """Reject known aggregates, aliases, unsupported modes and double ownership."""
    state = hass.states.get(entity_id)
    if not entity_id.startswith("light.") or state is None or state.state not in ("on", "off"):
        raise ValueError("unavailable_output")
    registry = er.async_get(hass)
    registered = registry.async_get(entity_id)
    if registered and registered.platform in (DOMAIN, "group", "lightener", "lightener_studio"):
        raise ValueError("aggregate_output")
    if any(key in state.attributes for key in ("entity_id", "group_entities")):
        raise ValueError("aggregate_output")
    modes = set(state.attributes.get("supported_color_modes", []))
    if not modes or not modes <= SUPPORTED_MODES:
        raise ValueError("unsupported_output")
    if state.attributes.get("effect") not in (None, "", "none", "None"):
        raise ValueError("active_effect")
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.entry_id == entry_id:
            continue
        if entry.data["output"] == entity_id or (
            registered and entry.data.get("output_registry_id") == registered.id
        ):
            raise ValueError("already_configured")
    return state


def observe(state: State | None) -> Intent | None:
    if state is None or state.state not in ("on", "off"):
        return None
    color = None
    mode = state.attributes.get("color_mode")
    key = "color_temp_kelvin" if mode == "color_temp" else f"{mode}_color"
    if mode in ("color_temp", "xy", "hs", "rgb") and state.attributes.get(key) is not None:
        color = color_from_payload({key: state.attributes[key]})
    brightness = state.attributes.get("brightness")
    if brightness == 0:
        brightness = None
    return Intent(state.state == "on", brightness, color)


def normalize(intent: Intent, attributes: Mapping[str, Any]) -> Intent:
    """Keep output in the same advertised space as virtual lights."""
    color = intent.color
    if color is not None and color.mode == "color_temp" and isinstance(color.value, int):
        low = attributes.get("min_color_temp_kelvin", 2000)
        high = attributes.get("max_color_temp_kelvin", 6500)
        color = Color("color_temp", max(low, min(high, color.value)))
    return Intent(intent.on, intent.brightness, color)
