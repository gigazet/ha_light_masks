"""Integration setup, central options and native mask subentries."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any
from uuid import uuid4

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentry,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import DOMAIN
from .endpoint import (
    member_identities,
    native_record,
    validate_endpoint,
    validate_zones,
)


def output_entity(hass: HomeAssistant, entry: ConfigEntry) -> str:
    registry = er.async_get(hass)
    if registry_id := entry.data.get("output_registry_id"):
        if registered := registry.async_get(registry_id):
            return registered.entity_id
    return str(entry.data["output"])


def mask_schema(
    entry: ConfigEntry, current: ConfigSubentry | None, values: Mapping[str, Any] | None
) -> vol.Schema:
    defaults = dict(current.data) if current else {}
    used = {
        sub.data["priority"] for sub in entry.subentries.values() if sub.subentry_type == "mask"
    }
    priority = next((value for value in (100, *range(1, 1001)) if value not in used), 100)
    defaults.update(
        name=current.title if current else "",
        power=defaults.get("power", "transparent") != "transparent",
        force_off=defaults.get("power") == "force_off",
    )
    defaults.update(values or {})
    fields: dict[Any, Any] = {
        vol.Required("name", default=defaults["name"]): vol.All(str, vol.Strip, vol.Length(min=1)),
        vol.Required("priority", default=defaults.get("priority", priority)): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=1000)
        ),
        vol.Required("power", default=defaults["power"]): bool,
        vol.Required("brightness", default=defaults.get("brightness", False)): bool,
        vol.Required("appearance", default=defaults.get("appearance", True)): bool,
        vol.Required("restore", default=defaults.get("restore", False)): bool,
        vol.Required("duration", default=defaults.get("duration", 0)): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=604800)
        ),
    }
    if current and current.data["power"] == "force_off":
        fields[vol.Required("force_off", default=defaults["force_off"])] = bool
    return vol.Schema(fields)


def mask_data(user_input: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(user_input)
    force_off = data.pop("force_off", False)
    data["power"] = (
        "force_off"
        if data["power"] and force_off
        else "force_on"
        if data["power"]
        else "transparent"
    )
    return data


def mask_errors(
    hass: HomeAssistant, entry: ConfigEntry, current: ConfigSubentry | None, data: Mapping[str, Any]
) -> dict[str, str]:
    if any(
        sub.subentry_type == "mask"
        and (current is None or sub.subentry_id != current.subentry_id)
        and sub.data["priority"] == data["priority"]
        for sub in entry.subentries.values()
    ):
        return {"priority": "duplicate_priority"}
    if not data["brightness"] and not data["appearance"] and data["power"] == "transparent":
        return {"base": "empty_mask"}
    state = hass.states.get(output_entity(hass, entry))
    if state is None:
        return {"base": "unavailable_output"}
    modes = set(state.attributes.get("supported_color_modes", []))
    if data["brightness"] and not modes - {"onoff"}:
        return {"brightness": "unsupported_brightness"}
    if data["appearance"] and not modes - {"onoff", "brightness"}:
        return {"appearance": "unsupported_appearance"}
    return {}


class LightMasksConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._base: dict[str, Any] = {}
        self._zones: list[dict[str, Any]] = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            if user_input.get("multi_zone"):
                self._base = dict(user_input)
                return await self.async_step_zone()
            try:
                if not user_input.get("output"):
                    raise ValueError("unavailable_output")
                state = validate_endpoint(self.hass, user_input["output"])
                registered = er.async_get(self.hass).async_get(user_input["output"])
                await self.async_set_unique_id(
                    registered.id if registered else user_input["output"]
                )
                self._abort_if_unique_id_configured()
                state = validate_endpoint(self.hass, user_input["output"])
            except ValueError as err:
                errors["base"] = str(err)
            else:
                return self.async_create_entry(
                    title=user_input.get("name", "").strip() or state.name,
                    data={
                        **user_input,
                        "output_registry_id": registered.id if registered else None,
                        "output_members": member_identities(self.hass, user_input["output"]),
                    },
                )
        return self.async_show_form(
            step_id="user",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Optional("name", default=""): vol.All(str, vol.Strip),
                    vol.Optional("multi_zone", default=False): bool,
                    vol.Optional("output"): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="light")
                    ),
                    vol.Required("power_port", default=False): bool,
                    vol.Required("fallback_brightness", default=128): vol.All(
                        vol.Coerce(int), vol.Range(min=1, max=255)
                    ),
                    vol.Required("fallback_kelvin", default=4000): vol.All(
                        vol.Coerce(int), vol.Range(min=1000, max=40000)
                    ),
                }
            ),
        )

    async def async_step_zone(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            try:
                record = native_record(self.hass, user_input["output"])
                zone = {**record, "id": f"zone_{uuid4().hex}", "name": user_input["name"]}
                candidates = [*self._zones, zone]
                if len(candidates) > 1:
                    validate_zones(self.hass, candidates, None, enrolling=True)
                elif not user_input["add_another"]:
                    raise ValueError("invalid_zones")
            except ValueError as err:
                errors["base"] = str(err)
            else:
                self._zones = candidates
                if not user_input["add_another"]:
                    return await self.async_step_aggregate()
        return self.async_show_form(
            step_id="zone",
            errors=errors,
            description_placeholders={"count": str(len(self._zones))},
            data_schema=vol.Schema(
                {
                    vol.Required("name"): vol.All(str, vol.Strip, vol.Length(min=1)),
                    vol.Required("output"): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="light")
                    ),
                    vol.Required("add_another", default=True): bool,
                }
            ),
        )

    async def async_step_aggregate(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            try:
                aggregate = (
                    native_record(self.hass, user_input["aggregate"])
                    if user_input.get("aggregate")
                    else None
                )
                validate_zones(self.hass, self._zones, aggregate, enrolling=True)
                if not user_input["confirm"]:
                    raise ValueError("confirm_routing")
            except ValueError as err:
                errors["base"] = str(err)
            else:
                return self.async_create_entry(
                    title=self._base.get("name", "").strip() or self._zones[0]["name"],
                    data={
                        "mode": "multi_zone",
                        "zones": self._zones,
                        "aggregate": aggregate,
                        "output": self._zones[0]["output"],
                        "output_registry_id": self._zones[0]["output_registry_id"],
                        "output_members": sorted(
                            member for zone in self._zones for member in zone["output_members"]
                        ),
                        "power_port": False,
                        "fallback_brightness": self._base["fallback_brightness"],
                        "fallback_kelvin": self._base["fallback_kelvin"],
                    },
                )
        return self.async_show_form(
            step_id="aggregate",
            errors=errors,
            description_placeholders={
                "zones": ", ".join(f"{z['name']}: {z['output']}" for z in self._zones)
            },
            data_schema=vol.Schema(
                {
                    vol.Optional("aggregate"): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="light")
                    ),
                    vol.Required("confirm", default=False): bool,
                }
            ),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return LightMasksOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {"mask": MaskSubentryFlow}


class LightMasksOptionsFlow(OptionsFlow):
    _selected: str | None = None

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        masks = [
            sub for sub in self.config_entry.subentries.values() if sub.subentry_type == "mask"
        ]
        menu = ["base", "add_mask"]
        if self.config_entry.data.get("mode") == "multi_zone":
            menu.append("rename_zone")
        if masks:
            menu.extend(("edit_mask", "remove_mask"))
        return self.async_show_menu(
            step_id="init",
            menu_options=menu,
            description_placeholders={"count": str(len(masks))},
        )

    async def async_step_rename_zone(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        zones = self.config_entry.data["zones"]
        if user_input is not None:
            self._selected = user_input["zone"]
            return await self.async_step_zone_name()
        return self.async_show_form(
            step_id="rename_zone",
            data_schema=vol.Schema(
                {
                    vol.Required("zone"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(value=z["id"], label=z["name"])
                                for z in zones
                            ]
                        )
                    ),
                }
            ),
        )

    async def async_step_zone_name(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self.config_entry
        zone = next(z for z in entry.data["zones"] if z["id"] == self._selected)
        if user_input is not None:
            self.hass.config_entries.async_update_entry(
                entry,
                data={
                    **entry.data,
                    "zones": [
                        {**z, "name": user_input["name"]} if z["id"] == self._selected else dict(z)
                        for z in entry.data["zones"]
                    ],
                },
            )
            return self.async_create_entry(title="", data=dict(entry.options))
        return self.async_show_form(
            step_id="zone_name",
            data_schema=vol.Schema(
                {
                    vol.Required("name", default=zone["name"]): vol.All(
                        str, vol.Strip, vol.Length(min=1)
                    )
                }
            ),
        )

    async def async_step_base(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self.config_entry
        old_output = output_entity(self.hass, entry)
        errors = {}
        if user_input is not None:
            try:
                if user_input.get("output", old_output) != old_output:
                    raise ValueError("base_fixed")
            except ValueError as err:
                errors["base"] = str(err)
            else:
                self.hass.config_entries.async_update_entry(
                    entry,
                    title=user_input["name"],
                )
                return self.async_create_entry(title="", data=dict(entry.options))
        values = {"name": entry.title, "output": old_output, **(user_input or {})}
        return self.async_show_form(
            step_id="base",
            errors=errors,
            description_placeholders={
                "output": ", ".join(
                    registered.entity_id
                    if (registered := er.async_get(self.hass).async_get(zone["output_registry_id"]))
                    else zone["output"]
                    for zone in entry.data["zones"]
                )
                if entry.data.get("mode") == "multi_zone"
                else old_output
            },
            data_schema=vol.Schema(
                {
                    vol.Required("name", default=values["name"]): vol.All(
                        str, vol.Strip, vol.Length(min=1)
                    ),
                }
            ),
        )

    async def async_step_add_mask(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self._mask_form(user_input, None, "add_mask")

    async def async_step_edit_mask(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._selected = user_input["mask"]
            return await self.async_step_mask()
        return self._selection("edit_mask")

    async def async_step_mask(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        current = self.config_entry.subentries.get(self._selected or "")
        if current is None:
            return self.async_abort(reason="mask_not_found")
        return self._mask_form(user_input, current, "mask")

    async def async_step_remove_mask(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._selected = user_input["mask"]
            return await self.async_step_confirm_remove()
        return self._selection("remove_mask")

    async def async_step_confirm_remove(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        current = self.config_entry.subentries.get(self._selected or "")
        if current is None:
            return self.async_abort(reason="mask_not_found")
        if user_input is not None:
            if not user_input["confirm"]:
                return self.async_abort(reason="removal_cancelled")
            self.hass.config_entries.async_remove_subentry(self.config_entry, current.subentry_id)
            return self.async_create_entry(title="", data=dict(self.config_entry.options))
        return self.async_show_form(
            step_id="confirm_remove",
            description_placeholders={"name": current.title},
            data_schema=vol.Schema({vol.Required("confirm", default=False): bool}),
        )

    def _selection(self, step: str) -> ConfigFlowResult:
        options = [
            selector.SelectOptionDict(
                value=sub.subentry_id, label=f"{sub.title} (priority {sub.data['priority']})"
            )
            for sub in self.config_entry.subentries.values()
            if sub.subentry_type == "mask"
        ]
        if not options:
            return self.async_abort(reason="mask_not_found")
        return self.async_show_form(
            step_id=step,
            data_schema=vol.Schema(
                {
                    vol.Required("mask"): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=options)
                    ),
                }
            ),
        )

    def _mask_form(
        self, user_input: dict[str, Any] | None, current: ConfigSubentry | None, step: str
    ) -> ConfigFlowResult:
        entry = self.config_entry
        errors = {}
        if user_input is not None:
            data = mask_data(user_input)
            errors = mask_errors(self.hass, entry, current, data)
            if not errors:
                if current:
                    self.hass.config_entries.async_update_subentry(
                        entry, current, title=data["name"], data=data
                    )
                else:
                    self.hass.config_entries.async_add_subentry(
                        entry,
                        ConfigSubentry(
                            title=data["name"],
                            subentry_type="mask",
                            unique_id=None,
                            data=MappingProxyType(data),
                        ),
                    )
                return self.async_create_entry(title="", data=dict(entry.options))
        return self.async_show_form(
            step_id=step, errors=errors, data_schema=mask_schema(entry, current, user_input)
        )


class MaskSubentryFlow(ConfigSubentryFlow):
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._form(user_input, "user")

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._form(user_input, "reconfigure")

    async def _form(self, user_input: dict[str, Any] | None, step: str) -> SubentryFlowResult:
        entry = self._get_entry()
        current = self._get_reconfigure_subentry() if step == "reconfigure" else None
        errors = {}
        if user_input is not None:
            data = mask_data(user_input)
            errors = mask_errors(self.hass, entry, current, data)
            if not errors:
                if current:
                    return self.async_update_and_abort(
                        entry, current, title=data["name"], data=data
                    )
                return self.async_create_entry(title=data["name"], data=data)
        return self.async_show_form(
            step_id=step, errors=errors, data_schema=mask_schema(entry, current, user_input)
        )
