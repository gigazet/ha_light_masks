"""Endpoint topology, compatible light groups and output normalization."""

from collections.abc import Mapping
from typing import Any

from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, SUPPORTED_MODES
from .engine import color_from_payload
from .model import Color, Intent


def endpoint_tree(hass: HomeAssistant, entity_id: str) -> tuple[State, ...]:
    """Expand only registered HA light groups; reject cycles and repeated members."""
    registry = er.async_get(hass)
    pending, seen, states = [entity_id], set(), []
    while pending:
        current = pending.pop()
        if current in seen:
            raise ValueError("aggregate_output")
        seen.add(current)
        state = hass.states.get(current)
        if not current.startswith("light.") or state is None:
            raise ValueError("unavailable_output")
        registered = registry.async_get(current)
        platform = registered.platform if registered else None
        if platform in (DOMAIN, "lightener", "lightener_studio"):
            raise ValueError("aggregate_output")
        if platform == "group":
            members = state.attributes.get("entity_id")
            if (
                not isinstance(members, (list, tuple))
                or not members
                or not all(isinstance(member, str) for member in members)
            ):
                raise ValueError("aggregate_output")
            pending.extend(members)
        elif any(key in state.attributes for key in ("entity_id", "group_entities")):
            raise ValueError("aggregate_output")
        states.append(state)
    return tuple(states)


def endpoint_members(hass: HomeAssistant, entity_id: str) -> tuple[State, ...]:
    return tuple(
        state for state in endpoint_tree(hass, entity_id) if "entity_id" not in state.attributes
    )


def member_identities(hass: HomeAssistant, entity_id: str) -> list[str]:
    registry = er.async_get(hass)
    return sorted(
        registered.id if (registered := registry.async_get(state.entity_id)) else state.entity_id
        for state in endpoint_members(hass, entity_id)
    )


def validate_endpoint(hass: HomeAssistant, entity_id: str, entry_id: str | None = None) -> State:
    """Validate every member, compatible capabilities and exclusive ownership."""
    state = hass.states.get(entity_id)
    if not entity_id.startswith("light.") or state is None or state.state not in ("on", "off"):
        raise ValueError("unavailable_output")
    registry = er.async_get(hass)
    registered = registry.async_get(entity_id)
    modes = set(state.attributes.get("supported_color_modes", []))
    for member in endpoint_tree(hass, entity_id):
        if member.state not in ("on", "off"):
            raise ValueError("unavailable_output")
        member_modes = set(member.attributes.get("supported_color_modes", []))
        if not member_modes or not member_modes <= SUPPORTED_MODES:
            raise ValueError("unsupported_output")
        # Effect metadata may retain a write-only selection after static commands.
        # Effects are unmanaged; validate only the channels this compositor owns.
        if member_modes != modes:
            raise ValueError("incompatible_group")
        keys = ["min_color_temp_kelvin", "max_color_temp_kelvin"] if "color_temp" in modes else []
        if any(member.attributes.get(key) != state.attributes.get(key) for key in keys) or (
            member.attributes.get("supported_features", 0) & 32
            != state.attributes.get("supported_features", 0) & 32
        ):
            raise ValueError("incompatible_group")
    identities = member_identities(hass, entity_id)
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.entry_id == entry_id:
            if entry.data.get("output_members", identities) != identities:
                raise ValueError("group_changed")
            continue
        if entry.data["output"] == entity_id or (
            registered and entry.data.get("output_registry_id") == registered.id
        ):
            raise ValueError("already_configured")
        owned = entry.data.get("output_members")
        if owned is None:
            other = registry.async_get(entry.data["output"])
            owned = [
                entry.data.get("output_registry_id")
                or (other.id if other else entry.data["output"])
            ]
        if set(identities).intersection(owned):
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
