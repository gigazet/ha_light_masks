"""Endpoint topology, compatible light groups and output normalization."""

import re
from collections.abc import Mapping
from typing import Any

from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, SUPPORTED_MODES
from .engine import color_from_payload
from .model import Color, Intent


def _zigbee2mqtt_group(
    hass: HomeAssistant, registered: er.RegistryEntry | None
) -> dr.DeviceEntry | None:
    """Recognize discovery metadata, not user-editable display names."""
    if registered is None or registered.platform != "mqtt" or registered.device_id is None:
        return None
    devices = dr.async_get(hass)
    device = devices.async_get(registered.device_id)
    if (
        not isinstance(device, dr.DeviceEntry)
        or device.manufacturer != "Zigbee2MQTT"
        or device.model != "Group"
    ):
        return None
    bridge = devices.async_get(device.via_device_id) if device.via_device_id else None
    if (
        not isinstance(bridge, dr.DeviceEntry)
        or bridge.manufacturer != "Zigbee2MQTT"
        or bridge.model != "Bridge"
        or registered.config_entry_id not in bridge.config_entries
        or not any(
            domain == "mqtt" and re.fullmatch(r"zigbee2mqtt_.+_[0-9]+", identifier)
            for domain, identifier in device.identifiers
        )
    ):
        raise ValueError("aggregate_output")
    return device


def endpoint_tree(
    hass: HomeAssistant,
    entity_id: str,
    unavailable_attributes: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[State, ...]:
    """Inspect supported groups without changing their native delivery target."""
    registry = er.async_get(hass)
    pending, seen, states = [entity_id], set(), []
    while pending:
        current = pending.pop()
        if current in seen:
            raise ValueError("aggregate_output")
        seen.add(current)
        state = endpoint_state(hass, current, unavailable_attributes)
        if not current.startswith("light.") or state is None:
            raise ValueError("unavailable_output")
        registered = registry.async_get(current)
        platform = registered.platform if registered else None
        if platform in (DOMAIN, "lightener", "lightener_studio"):
            raise ValueError("aggregate_output")
        native_group = _zigbee2mqtt_group(hass, registered)
        if platform == "group" or native_group is not None:
            key = "group_entities" if native_group is not None else "entity_id"
            members = state.attributes.get(key)
            if (
                not isinstance(members, (list, tuple))
                or not members
                or not all(isinstance(member, str) for member in members)
                or (native_group is not None and "entity_id" in state.attributes)
            ):
                raise ValueError("aggregate_output")
            if native_group is not None:
                assert registered is not None
                devices = dr.async_get(hass)
                for member in members:
                    member_state = endpoint_state(hass, member, unavailable_attributes)
                    if member_state is None:
                        raise ValueError("unavailable_output")
                    member_entry = registry.async_get(member)
                    member_device = (
                        devices.async_get(member_entry.device_id)
                        if member_entry and member_entry.device_id
                        else None
                    )
                    if (
                        member_entry is None
                        or member_entry.platform != "mqtt"
                        or member_entry.config_entry_id != registered.config_entry_id
                        or not isinstance(member_device, dr.DeviceEntry)
                        or member_device.via_device_id != native_group.via_device_id
                        or member_device.model == "Group"
                        or not any(
                            domain == "mqtt"
                            and re.fullmatch(r"zigbee2mqtt_0x[0-9a-fA-F]{16}", identifier)
                            for domain, identifier in member_device.identifiers
                        )
                        or any(
                            key in member_state.attributes
                            for key in ("entity_id", "group_entities")
                        )
                    ):
                        raise ValueError("aggregate_output")
            pending.extend(members)
        elif any(key in state.attributes for key in ("entity_id", "group_entities")):
            raise ValueError("aggregate_output")
        states.append(state)
    return tuple(states)


def endpoint_state(
    hass: HomeAssistant,
    entity_id: str,
    unavailable_attributes: Mapping[str, Mapping[str, Any]] | None,
) -> State | None:
    state = hass.states.get(entity_id)
    if unavailable_attributes is not None and (state is None or state.state not in ("on", "off")):
        if attributes := unavailable_attributes.get(entity_id):
            # Core omits extra attributes when an entity is unavailable. Frozen
            # metadata can prove isolation, never availability or permission to send.
            return State(
                entity_id,
                state.state if state else "unavailable",
                {**attributes, **(state.attributes if state else {})},
            )
    return state


def endpoint_members(
    hass: HomeAssistant,
    entity_id: str,
    unavailable_attributes: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[State, ...]:
    return tuple(
        state
        for state in endpoint_tree(hass, entity_id, unavailable_attributes)
        if not any(key in state.attributes for key in ("entity_id", "group_entities"))
    )


def member_identities(
    hass: HomeAssistant,
    entity_id: str,
    unavailable_attributes: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[str]:
    registry = er.async_get(hass)
    return sorted(
        registered.id if (registered := registry.async_get(state.entity_id)) else state.entity_id
        for state in endpoint_members(hass, entity_id, unavailable_attributes)
    )


def validate_endpoint(
    hass: HomeAssistant,
    entity_id: str,
    entry_id: str | None = None,
    *,
    require_available: bool = True,
    unavailable_attributes: Mapping[str, Mapping[str, Any]] | None = None,
) -> State:
    """Validate every member, compatible capabilities and exclusive ownership."""
    state = endpoint_state(hass, entity_id, unavailable_attributes)
    if (
        not entity_id.startswith("light.")
        or state is None
        or (require_available and state.state not in ("on", "off"))
    ):
        raise ValueError("unavailable_output")
    registry = er.async_get(hass)
    registered = registry.async_get(entity_id)
    modes = set(state.attributes.get("supported_color_modes", []))
    for member in endpoint_tree(hass, entity_id, unavailable_attributes):
        if require_available and member.state not in ("on", "off"):
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
    identities = member_identities(hass, entity_id, unavailable_attributes)
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.entry_id == entry_id:
            if entry.data.get("mode") != "multi_zone" and (
                entry.data.get("output_members", identities) != identities
            ):
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


def capability_key(attributes: Mapping[str, Any]) -> dict[str, Any]:
    modes = sorted(attributes["supported_color_modes"])
    return {
        "modes": modes,
        "minimum": attributes.get("min_color_temp_kelvin") if "color_temp" in modes else None,
        "maximum": attributes.get("max_color_temp_kelvin") if "color_temp" in modes else None,
        "transition": bool(attributes.get("supported_features", 0) & 32),
    }


def native_record(
    hass: HomeAssistant,
    output: str,
    entry_id: str | None = None,
    *,
    require_available: bool = True,
    frozen: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    unavailable_attributes = None
    if not require_available and frozen is not None:
        members = record_members(hass, frozen)
        attributes = record_attributes(frozen)
        unavailable_attributes = {member: attributes for member in members} | {
            output: {**attributes, "group_entities": members}
        }
    state = validate_endpoint(
        hass,
        output,
        entry_id,
        require_available=require_available,
        unavailable_attributes=unavailable_attributes,
    )
    registered = er.async_get(hass).async_get(output)
    device = _zigbee2mqtt_group(hass, registered)
    if device is None or registered is None:
        raise ValueError("native_zone_required")
    return {
        "output": output,
        "output_registry_id": registered.id,
        "output_members": member_identities(hass, output, unavailable_attributes),
        "bridge_id": device.via_device_id,
        "config_entry_id": registered.config_entry_id,
        "capabilities": capability_key(state.attributes),
    }


def record_output(hass: HomeAssistant, record: Mapping[str, Any]) -> str:
    registered = er.async_get(hass).async_get(record["output_registry_id"])
    if registered is None:
        raise ValueError("group_changed")
    return registered.entity_id


def record_members(hass: HomeAssistant, record: Mapping[str, Any]) -> tuple[str, ...]:
    registry = er.async_get(hass)
    members = []
    for identity in record["output_members"]:
        registered = registry.async_get(identity)
        if registered is None:
            raise ValueError("group_changed")
        members.append(registered.entity_id)
    return tuple(members)


def record_attributes(record: Mapping[str, Any]) -> dict[str, Any]:
    capabilities = record["capabilities"]
    return {
        "supported_color_modes": capabilities["modes"],
        "min_color_temp_kelvin": capabilities["minimum"],
        "max_color_temp_kelvin": capabilities["maximum"],
        "supported_features": 32 if capabilities["transition"] else 0,
    }


def validate_zones(
    hass: HomeAssistant,
    zones: list[dict[str, Any]],
    aggregate: Mapping[str, Any] | None,
    entry_id: str | None = None,
    *,
    enrolling: bool = False,
    require_available: bool = True,
) -> dict[str, str]:
    """Validate disjoint native owners and their optional exact-cover delivery alias."""
    if len(zones) < 2 or len({zone["id"] for zone in zones}) != len(zones):
        raise ValueError("invalid_zones")
    outputs: dict[str, str] = {}
    union: set[str] = set()
    reference = zones[0]
    for record in [*zones, *([aggregate] if aggregate else [])]:
        output = record_output(hass, record)
        current = native_record(
            hass,
            output,
            entry_id,
            require_available=enrolling or require_available,
            frozen=record,
        )
        for key in (
            "output_registry_id",
            "output_members",
            "bridge_id",
            "config_entry_id",
            "capabilities",
        ):
            if current[key] != record[key]:
                raise ValueError("group_changed")
        if any(
            current[key] != reference[key]
            for key in ("bridge_id", "config_entry_id", "capabilities")
        ):
            raise ValueError("incompatible_group")
        members = set(current["output_members"])
        if record is aggregate:
            if members != union:
                raise ValueError("aggregate_cover")
        else:
            if union.intersection(members):
                raise ValueError("overlapping_zones")
            union.update(members)
            outputs[record["id"]] = output
            if enrolling:
                states = {member.state for member in endpoint_members(hass, output)}
                root = hass.states.get(output)
                if len(states) != 1 or root is None or root.state not in states:
                    raise ValueError("mixed_zone")
    return outputs


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
