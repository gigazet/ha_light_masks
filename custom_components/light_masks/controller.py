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
from .endpoint import endpoint_members, endpoint_tree, normalize, observe, validate_endpoint
from .engine import Engine, Operation
from .model import Intent, Mask, matches
from .storage import IntentStore

_LOGGER = logging.getLogger(__name__)
ACK_TIMEOUT = 3.0
SETTLE_SECONDS = 0.75
MAX_ATTEMPTS = 3


class Controller:
    """Owns one physical endpoint; virtual entities only mutate Engine."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        output: str,
        initial: Intent,
        masks: tuple[Mask, ...],
        attributes: Mapping[str, Any],
    ) -> None:
        self.hass, self.entry, self.output = hass, entry, output
        self.attributes = attributes
        self.store = IntentStore(
            hass, STORE_VERSION, f"{DOMAIN}.{entry.entry_id}", atomic_writes=True
        )
        self.engine = Engine(initial, masks, self.store.async_save)
        self.status = "shadow"
        self.error: str | None = None
        self.last_sent_revision: int | None = None
        self.attempts = 0
        self.authorized_on = initial.on
        self.members = tuple(state.entity_id for state in endpoint_members(hass, output))
        self.nodes = tuple(state.entity_id for state in endpoint_tree(hass, output))
        if self.members != (output,):
            self.authorized_on = all(hass.states.is_state(member, "on") for member in self.members)
        self._wake = asyncio.Event()
        self._observed = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None
        self._settle: asyncio.TimerHandle | None = None
        self._busy = False
        self._stopped = False
        self._transition = 0.0
        self._context: Context | None = None
        self._desired: Intent | None = None
        self._generation = 0
        self._failure_latched = False
        self._safety_block = False

    async def initialize(self, *, preserve_activation: bool = False) -> None:
        raw = await self.store.async_load()
        if raw is not None:
            self.engine.restore(raw, time.time(), preserve_activation=preserve_activation)
        for intent in (self.engine.normal, *self.engine.intents.values()):
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
        self.status, self.error = "in_sync", None
        ir.async_delete_issue(self.hass, DOMAIN, f"{self.entry.entry_id}_delivery")
        self.publish()

    def desired(self) -> Intent:
        return normalize(self.engine.resolution(time.time()).intent, self.attributes)

    def observed(self) -> Intent | None:
        try:
            if any(observe(self.hass.states.get(member)) is None for member in self.members):
                return None
            return observe(self.hass.states.get(self.output))
        except (ValueError, TypeError) as err:
            if not self._safety_block:
                self._safety_block = True
                self.status = "failed"
                self.fault("endpoint", f"Invalid output state: {err}")
            return None

    def converged(self, desired: Intent) -> bool:
        if self.observed() is None:
            return False
        return all(
            (observed := observe(self.hass.states.get(member))) is not None
            and matches(desired, observed)
            for member in self.members
        )

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
    def refresh(self, *, force: bool = False) -> None:
        desired = self.desired()
        if force or desired != self._desired:
            # Off attributes are retained in intent, not sent to the device.
            changed = (
                force
                or self._desired is None
                or (desired.on != self._desired.on or (desired.on and desired != self._desired))
            )
            self._desired = desired
            if changed:
                self._generation += 1
                self._failure_latched = False
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
        previously_authorized = self.authorized_on
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
        if producer == "normal" and self.engine.normal.on:
            self.authorized_on = True
        if (
            operation in ("on", "toggle")
            and producer != "normal"
            and self.engine.intents[producer].on
            and any(m.id == producer and m.power == "force_on" for m in self.engine.masks)
        ):
            self.authorized_on = True
        if normalize(before, self.attributes) != normalize(after, self.attributes):
            self._transition = float(transition)
            self._context = context
        self.refresh(force=self.authorized_on != previously_authorized)

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
        if new is None or new.state not in ("on", "off"):
            self.status = "unavailable"
            self.publish()
            return
        try:
            self.validate()
        except ValueError as err:
            if str(err) == "unavailable_output":
                self.status = "unavailable"
                self.publish()
                return
            self._safety_block = True
            self.status = "failed"
            self.fault("endpoint", str(err))
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
        if observed is None:
            return
        if self.converged(self.desired()):
            if self.engine.apply and not self.engine.suspended and not self._safety_block:
                self._confirm()
            return
        if not self.engine.apply or self.engine.suspended or self._safety_block:
            return
        revision = self.engine.revision
        try:
            if self.members != (self.output,):
                # An aggregate/average is not a safe baseline for divergent members.
                await self.engine.set_mode(suspended=True)
                result = "suspended"
            else:
                result = await self.engine.reconcile(observed, time.time(), revision)
            if result == "suspended":
                self.status = "suspended_external_change"
                self.fault("external_change", "External output change; explicit Resume required")
            elif result == "adopted":
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
        observed = self.observed()
        if self._safety_block:
            return "failed"
        if observed is None:
            return "unavailable"
        if desired.on and not self.authorized_on:
            return "awaiting_resume"
        return None

    def validate(self) -> None:
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
                self.publish()
                continue
            try:
                self.validate()
            except ValueError as err:
                self._safety_block = True
                self.status = "failed"
                self.fault("endpoint", str(err))
                continue
            observed = self.observed()
            if observed is not None and self.converged(desired):
                self._confirm()
                continue
            if self._failure_latched:
                continue
            self._busy = True
            try:
                await self._deliver(desired, generation)
            finally:
                self._busy = False

    async def _deliver(self, desired: Intent, generation: int) -> None:
        transition = self._transition
        self.error = None
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
            self.last_sent_revision = self.engine.revision
            self.publish()
            payload: dict[str, Any] = {"entity_id": self.output}
            if desired.on:
                if desired.brightness is not None:
                    payload["brightness"] = desired.brightness
                if desired.color is not None:
                    payload[desired.color.service_key] = desired.color.value
            if self.attributes.get("supported_features", 0) & 32:
                payload["transition"] = transition
            self._observed.clear()
            before = tuple(self.hass.states.get(member) for member in self.members)
            try:
                async with asyncio.timeout(15 + transition):
                    await self.hass.services.async_call(
                        "light",
                        "turn_on" if desired.on else "turn_off",
                        payload,
                        blocking=True,
                        context=Context(parent_id=self._context.id if self._context else None),
                    )
            except (HomeAssistantError, TimeoutError) as err:
                self.error = str(err)
                _LOGGER.warning("Light Masks delivery %s attempt %d: %s", self.output, attempt, err)
            # Wait for transition completion even when intermediate reports match.
            deadline = self.hass.loop.time() + transition + ACK_TIMEOUT
            while generation == self._generation and not self._blocked(desired):
                remaining = deadline - self.hass.loop.time()
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
                observed = self.observed()
                if (
                    self.error is None
                    and remaining <= ACK_TIMEOUT
                    and observed is not None
                    and self.converged(desired)
                ):
                    self._confirm()
                    return
            if generation != self._generation or self._blocked(desired):
                return
            after = tuple(self.hass.states.get(member) for member in self.members)
            # No report is not an acknowledgement. Do not fight unknown device state.
            if all(a is b for a, b in zip(after, before, strict=True)) and self.error is None:
                self.status = "unverified"
                self._failure_latched = True
                self.publish()
                return
        self.status = "failed"
        self._failure_latched = True
        self.fault("delivery", self.error or "Output did not converge after three attempts")

    def diagnostics(self) -> dict[str, Any]:
        resolution = self.engine.resolution(time.time())
        observed = self.observed()
        return {
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
