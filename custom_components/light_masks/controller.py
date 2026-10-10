"""Durable intent mutations and one bounded, replace-latest physical writer."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Context, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval

from .const import DOMAIN, SIGNAL, STORE_VERSION
from .dispatch import plan_dispatch
from .endpoint import (
    endpoint_members,
    endpoint_tree,
    normalize,
    observe,
    record_members,
    record_output,
    validate_endpoint,
    validate_zones,
)
from .engine import Engine, Operation
from .model import Intent, Mask, matches
from .storage import IntentStore

_LOGGER = logging.getLogger(__name__)
ACK_TIMEOUT = 3.0
MULTI_ZONE_ACK_TIMEOUT = 5.0
SETTLE_SECONDS = 0.75
MAX_ATTEMPTS = 3


class Controller:
    """Owns one endpoint or native zone set; virtual entities only mutate Engine."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        output: str,
        initial: Intent | Mapping[str, Intent],
        masks: tuple[Mask, ...],
        attributes: Mapping[str, Any],
        *,
        outputs: Mapping[str, str] | None = None,
        aggregate: str | None = None,
    ) -> None:
        self.hass, self.entry, self.output = hass, entry, output
        self.outputs = dict(outputs or {"normal": output})
        self.aggregate = aggregate
        self.attributes = attributes
        self.store = IntentStore(
            hass, STORE_VERSION, f"{DOMAIN}.{entry.entry_id}", atomic_writes=True
        )
        self.engine = Engine(initial, masks, self.store.async_save)
        self.status = "shadow"
        self.error: str | None = None
        self.last_sent_revision: int | None = None
        self.attempts = 0
        self.zone_members = (
            {record["id"]: record_members(hass, record) for record in entry.data["zones"]}
            if self.engine.multi_zone
            else {
                zone: tuple(state.entity_id for state in endpoint_members(hass, target))
                for zone, target in self.outputs.items()
            }
        )
        self.members = tuple(member for members in self.zone_members.values() for member in members)
        self.nodes = (
            tuple(
                dict.fromkeys(
                    (*self.outputs.values(), *self.members, *((aggregate,) if aggregate else ()))
                )
            )
            if self.engine.multi_zone
            else tuple(dict.fromkeys(state.entity_id for state in endpoint_tree(hass, output)))
        )
        self.authorizations = {
            zone: (root := hass.states.get(self.outputs[zone])) is not None
            and root.state in ("on", "off")
            and all(hass.states.is_state(member, "on") for member in members)
            for zone, members in self.zone_members.items()
        }
        self.zone_status = {zone: "shadow" for zone in self.outputs}
        self.zone_errors: dict[str, str | None] = {zone: None for zone in self.outputs}
        self.zone_attempts = {zone: 0 for zone in self.outputs}
        self.transports: list[str] = []
        self.transitions = {zone: 0.0 for zone in self.outputs}
        self._wake = asyncio.Event()
        self._observed = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None
        self._settle: asyncio.TimerHandle | None = None
        self._busy = False
        self._stopped = False
        self._transition = 0.0
        self._context: Context | None = None
        self._desired: Intent | None = None
        self._desired_zones: dict[str, Intent] = {}
        self._generation = 0
        self._failure_latched = False
        self._safety_block = False
        self._availability: tuple[frozenset[str], bool] | None = None
        self._delivery_failures: dict[str, str] = {}

    @property
    def authorized_on(self) -> bool:
        return all(self.authorizations.values())

    @authorized_on.setter
    def authorized_on(self, value: bool) -> None:
        self.authorizations = dict.fromkeys(self.outputs, value)

    async def initialize(self, *, preserve_activation: bool = False) -> None:
        raw = await self.store.async_load()
        if raw is not None:
            self.engine.restore(raw, time.time(), preserve_activation=preserve_activation)
        elif self.engine.multi_zone:
            validate_zones(
                self.hass,
                self.entry.data["zones"],
                self.entry.data.get("aggregate"),
                self.entry.entry_id,
                enrolling=True,
            )
        for intent in (*self.engine.normals.values(), *self.engine.intents.values()):
            if (
                intent.color is not None
                and intent.color.mode not in self.attributes["supported_color_modes"]
            ):
                raise ValueError("Stored color is incompatible with current output capabilities")
        # Persist releases/deleted masks before any possible physical delivery.
        await self.store.async_save(self.engine.snapshot())

    @callback
    def start(self) -> None:
        self.entry.async_on_unload(
            async_track_state_change_event(self.hass, self.nodes, self._state_changed)
        )
        self.entry.async_on_unload(
            async_track_time_interval(self.hass, self._tick, timedelta(seconds=1))
        )
        self._worker = self.hass.async_create_background_task(
            self._run(), f"Light Masks writer {self.entry.entry_id}"
        )
        if self.engine.multi_zone:
            self._availability_changed()
        self.refresh()

    async def stop(self) -> None:
        self._stopped = True
        if self._settle:
            self._settle.cancel()
        if self._worker:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass

    @callback
    def publish(self) -> None:
        async_dispatcher_send(self.hass, f"{SIGNAL}_{self.entry.entry_id}")

    @callback
    def _confirm(self) -> None:
        if self._safety_block:
            return
        self.status, self.error = "in_sync", None
        self.zone_status = dict.fromkeys(self.outputs, "in_sync")
        self._update_health()
        if self._delivery_failures:
            self.error = next((error for error in self.zone_errors.values() if error), None)
        else:
            ir.async_delete_issue(self.hass, DOMAIN, f"{self.entry.entry_id}_delivery")
        self.publish()

    def desired(self) -> Intent:
        return normalize(self.engine.resolution(time.time()).intent, self.attributes)

    def desired_zones(self) -> dict[str, Intent]:
        return {
            zone: normalize(resolution.intent, self.attributes)
            for zone, resolution in self.engine.resolutions(time.time()).items()
        }

    def observed(self, output: str | None = None) -> Intent | None:
        try:
            target = output or self.output
            members = self.members
            if self.engine.multi_zone:
                members = next(
                    (
                        (target, *self.zone_members[zone])
                        for zone, root in self.outputs.items()
                        if root == target
                    ),
                    (target,),
                )
            if any(observe(self.hass.states.get(member)) is None for member in members):
                return None
            return observe(self.hass.states.get(target))
        except (ValueError, TypeError) as err:
            if not self._safety_block:
                self._safety_block = True
                self.status = "failed"
                self.fault("endpoint", f"Invalid output state: {err}")
            return None

    def converged(self, desired: Intent) -> bool:
        if self.engine.multi_zone:
            return all(
                self.zone_blocked(zone) or self.zone_converged(zone, intent)
                for zone, intent in self.desired_zones().items()
            )
        if self.observed() is None:
            return False
        return all(
            (observed := observe(self.hass.states.get(member))) is not None
            and matches(desired, observed)
            for member in self.members
        )

    def zone_converged(self, zone: str, desired: Intent) -> bool:
        return all(
            (observed := observe(self.hass.states.get(member))) is not None
            and matches(desired, observed)
            for member in self.zone_members[zone]
        )

    def zone_blocked(self, zone: str) -> str | None:
        if not self.engine.multi_zone:
            return None
        if self.observed(self.outputs[zone]) is None:
            return "unavailable"
        if self.desired_zones()[zone].on and not self.authorizations[zone]:
            return "awaiting_resume"
        return self._delivery_failures.get(zone)

    def aggregate_available(self) -> bool:
        return self.aggregate is None or self.observed(self.aggregate) is not None

    @callback
    def _update_health(self) -> None:
        if not self.engine.multi_zone or self._safety_block:
            return
        for zone in self.outputs:
            if blocked := self.zone_blocked(zone):
                self.zone_status[zone] = blocked
        if not self.engine.apply or self.engine.suspended:
            return
        if "unavailable" in self.zone_status.values() or not self.aggregate_available():
            self.status = "degraded"
        elif "failed" in self.zone_status.values():
            self.status = "failed"
        elif "unverified" in self.zone_status.values():
            self.status = "unverified"
        elif "awaiting_resume" in self.zone_status.values():
            self.status = "awaiting_resume"

    @callback
    def _availability_changed(self) -> bool:
        availability = (
            frozenset(zone for zone in self.outputs if self.observed(self.outputs[zone]) is None),
            self.aggregate_available(),
        )
        changed = self._availability is not None and availability != self._availability
        self._availability = availability
        self._update_health()
        return changed

    def active(self) -> bool:
        now = time.time()
        return any(
            i.on and (i.expires_at is None or i.expires_at > now)
            for i in self.engine.intents.values()
        )

    @callback
    def fault(self, reason: str, error: str) -> None:
        self.error = error
        _LOGGER.error("Light Masks %s: %s", self.output, error)
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"{self.entry.entry_id}_{reason}",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=reason,
            translation_placeholders={"output": self.output},
        )
        self.publish()

    @callback
    def refresh(self, *, force: bool = False, availability_only: bool = False) -> None:
        desired_zones = self.desired_zones()
        desired = self.desired()
        if force or desired_zones != self._desired_zones:
            # Off attributes are retained in intent, not sent to the device.
            changed = (
                force
                or not self._desired_zones
                or any(
                    value.on != self._desired_zones[zone].on
                    or (value.on and value != self._desired_zones[zone])
                    for zone, value in desired_zones.items()
                )
            )
            self._desired = desired
            self._desired_zones = desired_zones
            if changed:
                self._generation += 1
                if not availability_only:
                    self._failure_latched = False
                    self._delivery_failures.clear()
                self._wake.set()
        self.publish()

    async def command(
        self,
        producer: str,
        operation: Operation,
        data: Mapping[str, Any],
        context: Context | None,
        *,
        power_only: bool = False,
        prepare: Callable[[Mapping[str, object]], Mapping[str, object]] | None = None,
    ) -> None:
        transition = data.get("transition", 0)
        if not isinstance(transition, (int, float)) or not 0 <= transition <= 300:
            raise ServiceValidationError("Transition must be between 0 and 300 seconds")
        if not power_only and data.get("effect") is not None:
            raise ServiceValidationError("Native effects are not supported by Light Masks")
        previously_authorized = dict(self.authorizations)
        before_zones = self.desired_zones()
        try:
            before, after = await self.engine.update(
                producer, operation, data, time.time(), power_only=power_only, prepare=prepare
            )
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        except (OSError, HomeAssistantError) as err:
            self._safety_block = True
            self.status = "failed"
            self.fault("storage", str(err))
            raise HomeAssistantError("Light Masks could not persist the command") from err
        if self.engine.is_normal(producer):
            for zone in self.outputs if producer == "normal" else (producer,):
                if self.engine.normals[zone].on:
                    self.authorizations[zone] = True
        if (
            operation in ("on", "toggle")
            and not self.engine.is_normal(producer)
            and self.engine.intents[producer].on
            and any(m.id == producer and m.power == "force_on" for m in self.engine.masks)
        ):
            self.authorized_on = True
        if normalize(before, self.attributes) != normalize(after, self.attributes):
            self._transition = float(transition)
            self._context = context
        for zone, value in self.desired_zones().items():
            if value != before_zones[zone]:
                self.transitions[zone] = float(transition)
                self._context = context
        self.refresh(force=self.authorizations != previously_authorized)

    async def set_apply(self, enabled: bool) -> None:
        try:
            await self.engine.set_mode(apply=enabled)
        except (OSError, HomeAssistantError) as err:
            self._safety_block = True
            self.fault("storage", str(err))
            raise HomeAssistantError("Could not persist Apply mode") from err
        self.refresh(force=True)

    async def resume(self) -> None:
        try:
            self.validate()
            await self.engine.set_mode(suspended=False)
        except ValueError as err:
            raise ServiceValidationError(f"Cannot resume this output: {err}") from err
        except (OSError, HomeAssistantError) as err:
            self._safety_block = True
            self.fault("storage", str(err))
            raise HomeAssistantError("Could not persist Resume") from err
        self._safety_block = False
        self.authorized_on = True
        self.error = None
        for reason in ("external_change", "delivery", "storage", "endpoint"):
            ir.async_delete_issue(self.hass, DOMAIN, f"{self.entry.entry_id}_{reason}")
        self.refresh(force=True)

    async def sync(self) -> None:
        """Retry without granting startup power authorization or clearing suspension."""
        self.refresh(force=True)

    async def _tick(self, now: datetime) -> None:
        try:
            if await self.engine.expire(now.timestamp()):
                self._transition, self._context = 0.0, None
                self.transitions = dict.fromkeys(self.outputs, 0.0)
                self.refresh()
        except (OSError, HomeAssistantError) as err:
            if not self._safety_block:
                self._safety_block = True
                self.status = "failed"
                self.fault("storage", str(err))

    @callback
    def _state_changed(self, event: Event[EventStateChangedData]) -> None:
        self._observed.set()
        old, new = event.data.get("old_state"), event.data.get("new_state")
        if not self.engine.multi_zone and (new is None or new.state not in ("on", "off")):
            self.status = "unavailable"
            self.publish()
            return
        try:
            self.validate()
        except ValueError as err:
            if not self.engine.multi_zone and str(err) == "unavailable_output":
                self.status = "unavailable"
                self.publish()
                return
            self._safety_block = True
            self.status = "failed"
            self.fault("endpoint", str(err))
            return
        if self.engine.multi_zone and self._availability_changed():
            self.refresh(force=True, availability_only=True)
            return
        if self.engine.multi_zone and (new is None or new.state not in ("on", "off")):
            self.publish()
            return
        if old is None or old.state not in ("on", "off"):
            self.refresh(force=True)
            return
        if self._busy:
            return
        if self._settle:
            self._settle.cancel()
        self._settle = self.hass.loop.call_later(SETTLE_SECONDS, self._schedule_settled)

    @callback
    def _schedule_settled(self) -> None:
        self._settle = None
        if not self._stopped:
            self.entry.async_create_task(self.hass, self._settled(), "Light Masks observation")

    async def _settled(self) -> None:
        if self._stopped or self._busy or self._wake.is_set():
            return
        observed = self.observed()
        if observed is None and not self.engine.multi_zone:
            return
        if self.converged(self.desired()):
            if self.engine.apply and not self.engine.suspended and not self._safety_block:
                self._confirm()
            return
        if not self.engine.apply or self.engine.suspended or self._safety_block:
            return
        revision = self.engine.revision
        try:
            if self.engine.multi_zone or self.members != (self.output,):
                # An aggregate/average is not a safe baseline for divergent members.
                await self.engine.set_mode(suspended=True)
                result = "suspended"
            else:
                assert observed is not None
                result = await self.engine.reconcile(observed, time.time(), revision)
            if result == "suspended":
                self.status = "suspended_external_change"
                self.fault("external_change", "External output change; explicit Resume required")
            elif result == "adopted":
                assert observed is not None
                if observed.on:
                    self.authorized_on = True
                self.refresh()
        except (OSError, HomeAssistantError) as err:
            self._safety_block = True
            self.status = "failed"
            self.fault("storage", str(err))

    def _blocked(self, desired: Intent) -> str | None:
        if not self.engine.apply:
            return "shadow"
        if self.engine.suspended:
            return "suspended_external_change"
        observed = self.observed() if not self.engine.multi_zone else None
        if self._safety_block:
            return "failed"
        if self.engine.multi_zone:
            return None
        if observed is None:
            return "unavailable"
        if any(
            intent.on and not self.authorizations[zone]
            for zone, intent in self.desired_zones().items()
        ):
            return "awaiting_resume"
        return None

    def validate(self) -> None:
        if self.engine.multi_zone:
            outputs = validate_zones(
                self.hass,
                self.entry.data["zones"],
                self.entry.data.get("aggregate"),
                self.entry.entry_id,
                require_available=False,
            )
            if outputs != self.outputs:
                raise ValueError("Output identity changed; reload and review the integration")
            if (
                self.aggregate
                and record_output(self.hass, self.entry.data["aggregate"]) != self.aggregate
            ):
                raise ValueError("Output identity changed; reload and review the integration")
            nodes = {
                *outputs.values(),
                *(
                    member
                    for record in self.entry.data["zones"]
                    for member in record_members(self.hass, record)
                ),
                *((self.aggregate,) if self.aggregate else ()),
            }
            if nodes != set(self.nodes):
                raise ValueError("Group membership changed; reload and review the integration")
            return
        state = validate_endpoint(self.hass, self.output, self.entry.entry_id)
        if {member.entity_id for member in endpoint_members(self.hass, self.output)} != set(
            self.members
        ) or {node.entity_id for node in endpoint_tree(self.hass, self.output)} != set(self.nodes):
            raise ValueError("Group membership changed; recreate the Light Masks entry")
        if set(state.attributes["supported_color_modes"]) != set(
            self.attributes["supported_color_modes"]
        ):
            raise ValueError("Output capabilities changed; reload and review the integration")
        for key in ("min_color_temp_kelvin", "max_color_temp_kelvin", "supported_features"):
            if state.attributes.get(key) != self.attributes.get(key):
                raise ValueError("Output capabilities changed; reload and review the integration")

    async def _run(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            desired, generation = self.desired(), self._generation
            if blocked := self._blocked(desired):
                self.status = blocked
                self.zone_status = dict.fromkeys(self.outputs, blocked)
                self.publish()
                continue
            try:
                self.validate()
            except ValueError as err:
                self._safety_block = True
                self.status = "failed"
                self.fault("endpoint", str(err))
                continue
            if self.engine.multi_zone:
                self._availability_changed()
            observed = self.observed()
            if (self.engine.multi_zone or observed is not None) and self.converged(desired):
                self._confirm()
                continue
            if self._failure_latched and not self.engine.multi_zone:
                continue
            self._busy = True
            try:
                await self._deliver(desired, generation)
            finally:
                self._busy = False
                if self.engine.multi_zone and not self._stopped:
                    if self._settle:
                        self._settle.cancel()
                    self._settle = self.hass.loop.call_later(SETTLE_SECONDS, self._schedule_settled)

    async def _deliver(self, desired: Intent, generation: int) -> None:
        ack_timeout = MULTI_ZONE_ACK_TIMEOUT if self.engine.multi_zone else ACK_TIMEOUT
        desired_zones = self.desired_zones()
        transitions = (
            dict(self.transitions)
            if self.engine.multi_zone
            else dict.fromkeys(self.outputs, self._transition)
        )
        self.error = None
        unresolved = {
            zone
            for zone, intent in desired_zones.items()
            if not self.zone_blocked(zone) and not self.zone_converged(zone, intent)
        }
        self.zone_status = {
            zone: "pending" if zone in unresolved else "in_sync" for zone in self.outputs
        }
        self.zone_errors = {
            zone: self.zone_errors[zone] if zone in self._delivery_failures else None
            for zone in self.outputs
        }
        self.zone_attempts = dict.fromkeys(self.outputs, 0)
        self.transports = []
        self._update_health()
        if not unresolved:
            self._confirm()
            return
        unverified: set[str] = set()
        deadlines: dict[str, float] = {}
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if generation != self._generation or self._blocked(desired):
                return
            try:
                self.validate()
            except ValueError as err:
                self._safety_block = True
                self.status = "failed"
                self.fault("endpoint", str(err))
                return
            self.attempts = attempt
            self.error = None
            self.status = "pending"
            self._update_health()
            self.last_sent_revision = self.engine.revision
            self.publish()
            plan = plan_dispatch(
                self.outputs,
                desired_zones,
                unresolved,
                transitions,
                bool(self.attributes.get("supported_features", 0) & 32),
                self.aggregate if self.aggregate_available() else None,
            )
            self._observed.clear()
            before = {
                zone: tuple(self.hass.states.get(member) for member in self.zone_members[zone])
                for zone in unresolved
            }
            for command in plan:
                if generation != self._generation or self._blocked(desired):
                    return
                try:
                    self.validate()
                except ValueError as err:
                    self._safety_block = True
                    self.status = "failed"
                    self.fault("endpoint", str(err))
                    return
                if self.engine.multi_zone and (
                    any(self.zone_blocked(zone) for zone in command.zones)
                    or (command.output == self.aggregate and not self.aggregate_available())
                ):
                    self.refresh(force=True, availability_only=True)
                    return
                self.transports.append(command.output)
                for zone in command.zones:
                    self.zone_errors[zone] = None
                    self.zone_attempts[zone] = attempt
                try:
                    async with asyncio.timeout(15 + command.transition):
                        await self.hass.services.async_call(
                            "light",
                            "turn_on" if command.on else "turn_off",
                            {"entity_id": command.output, **command.payload},
                            blocking=True,
                            context=Context(parent_id=self._context.id if self._context else None),
                        )
                except (HomeAssistantError, TimeoutError) as err:
                    for zone in command.zones:
                        self.zone_errors[zone] = str(err)
                    self.error = str(err)
                    _LOGGER.warning(
                        "Light Masks delivery %s attempt %d: %s", command.output, attempt, err
                    )
                for zone in command.zones:
                    deadlines[zone] = self.hass.loop.time() + command.transition + ack_timeout
            # Wait for transition completion even when intermediate reports match.
            while generation == self._generation and not self._blocked(desired):
                # A no-report zone can still confirm while another zone is retried.
                for zone in unresolved | unverified:
                    if (
                        self.zone_errors[zone] is None
                        and self.hass.loop.time() >= deadlines[zone] - ack_timeout
                        and self.zone_converged(zone, desired_zones[zone])
                    ):
                        unresolved.discard(zone)
                        unverified.discard(zone)
                        self.zone_status[zone] = "in_sync"
                if not unresolved and not unverified:
                    break
                remaining = max(deadlines.values()) - self.hass.loop.time()
                if remaining <= 0:
                    break
                try:
                    async with asyncio.timeout(min(remaining, 0.1)):
                        await self._observed.wait()
                except TimeoutError:
                    pass
                self._observed.clear()
                if generation != self._generation or self._blocked(desired):
                    return
            if generation != self._generation or self._blocked(desired):
                return
            # No report is not an acknowledgement. Do not fight unknown device state.
            for zone in tuple(unresolved):
                after = tuple(self.hass.states.get(member) for member in self.zone_members[zone])
                if (
                    all(a is b for a, b in zip(after, before[zone], strict=True))
                    and self.zone_errors[zone] is None
                ):
                    unverified.add(zone)
                    unresolved.remove(zone)
                    self.zone_status[zone] = "unverified"
            if not unresolved:
                break
        for zone in unresolved:
            self.zone_status[zone] = "failed"
        if self.engine.multi_zone:
            self._delivery_failures.update(dict.fromkeys(unresolved, "failed"))
            self._delivery_failures.update(dict.fromkeys(unverified, "unverified"))
        if unresolved or unverified:
            self.status = "failed" if unresolved else "unverified"
            self._failure_latched = True
            self._update_health()
            if unresolved:
                self.fault("delivery", self.error or "Output did not converge after three attempts")
            else:
                self.publish()
        elif self.converged(desired):
            self._confirm()

    def diagnostics(self) -> dict[str, Any]:
        self._update_health()
        resolution = self.engine.resolution(time.time())
        observed = self.observed()
        result = {
            "status": self.status,
            "error": self.error,
            "revision": self.engine.revision,
            "last_sent_revision": self.last_sent_revision,
            "attempts": self.attempts,
            "authorized_on": self.authorized_on,
            "state": self.engine.snapshot(),
            "masks": [asdict(mask) for mask in self.engine.masks],
            "resolution": asdict(resolution),
            "desired": asdict(self.desired()),
            "observed": asdict(observed) if observed is not None else None,
            "output_members": list(self.members),
        }
        if self.engine.multi_zone:
            result.update(
                generation=self._generation,
                transports=self.transports,
                aggregate=self.aggregate,
                aggregate_available=self.aggregate_available(),
                zones={
                    zone: {
                        "output": output,
                        "normal": asdict(self.engine.normals[zone]),
                        "resolution": asdict(self.engine.resolution(time.time(), zone)),
                        "desired": asdict(self.desired_zones()[zone]),
                        "observed": asdict(observed)
                        if (observed := self.observed(output))
                        else None,
                        "members": list(self.zone_members[zone]),
                        "status": self.delivery_status(zone),
                        "error": self.zone_errors[zone],
                        "attempts": self.zone_attempts[zone],
                        "authorized_on": self.authorizations[zone],
                    }
                    for zone, output in self.outputs.items()
                },
            )
        return result

    def delivery_status(self, zone: str) -> str:
        if self._safety_block or self.status not in (
            "pending",
            "in_sync",
            "failed",
            "unverified",
            "degraded",
            "awaiting_resume",
        ):
            return self.status
        return self.zone_status[zone]
