"""Serialized intent store, independent of Home Assistant and device I/O."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, replace
from typing import Literal, cast

from .model import Color, Intent, Mask, Resolution, resolve, validate_masks

type Snapshot = dict[str, object]
type Save = Callable[[Snapshot], Awaitable[None]]
type Operation = Literal["on", "off", "toggle", "clear"]


def common_intent(intents: Mapping[str, Intent]) -> Intent:
    """Whole ordinary state is any-On; mixed channel values are deliberately unset."""
    values = tuple(intents.values())
    first = values[0]
    return Intent(
        any(value.on for value in values),
        first.brightness if all(v.brightness == first.brightness for v in values) else None,
        first.color if all(v.color == first.color for v in values) else None,
    )


def encode(intent: Intent) -> dict[str, object]:
    return asdict(intent)


def decode(raw: object) -> Intent:
    """Fail closed on invalid persisted values."""
    if not isinstance(raw, dict):
        raise ValueError("Intent must be an object")
    color = None
    if (value := raw.get("color")) is not None:
        if not isinstance(value, dict) or value.get("mode") not in (
            "color_temp",
            "xy",
            "hs",
            "rgb",
        ):
            raise ValueError("Invalid stored color")
        components = value["value"]
        if isinstance(components, list):
            components = tuple(components)
        color = Color(value["mode"], components)
    return Intent(raw["on"], raw.get("brightness"), color, raw.get("expires_at"))


def color_from_payload(data: Mapping[str, object]) -> Color | None:
    colors = []
    for mode in ("color_temp", "xy", "hs", "rgb"):
        key = "color_temp_kelvin" if mode == "color_temp" else f"{mode}_color"
        if key not in data:
            continue
        raw = data[key]
        if mode == "color_temp":
            if type(raw) is not int:
                raise ValueError("Kelvin must be an integer")
            colors.append(Color("color_temp", raw))
        else:
            if not isinstance(raw, (list, tuple)):
                raise ValueError("Color must be a sequence")
            colors.append(Color(mode, tuple(raw)))
    if len(colors) > 1:
        raise ValueError("Only one color representation is allowed")
    return colors[0] if colors else None


class Engine:
    """All accepted mutations are persisted before becoming visible."""

    def __init__(
        self, normal: Intent | Mapping[str, Intent], masks: tuple[Mask, ...], save: Save
    ) -> None:
        validate_masks(masks)
        self.multi_zone = not isinstance(normal, Intent)
        self.normals = {"normal": normal} if isinstance(normal, Intent) else dict(normal)
        if not self.normals or (self.multi_zone and "normal" in self.normals):
            raise ValueError("Invalid ordinary zone identities")
        if set(self.normals).intersection(mask.id for mask in masks):
            raise ValueError("Zone and mask identities overlap")
        self.masks = masks
        self.intents = {mask.id: Intent() for mask in masks}
        self.apply = False
        self.suspended = False
        self.revision = 0
        self.lock = asyncio.Lock()
        self._save = save

    @property
    def normal(self) -> Intent:
        return common_intent(self.normals) if self.multi_zone else self.normals["normal"]

    @normal.setter
    def normal(self, intent: Intent) -> None:
        if self.multi_zone:
            raise ValueError("Set multi-zone ordinary intents atomically")
        self.normals["normal"] = intent

    def is_normal(self, producer: str) -> bool:
        return producer == "normal" or producer in self.normals

    def _snapshot(
        self, normals: dict[str, Intent], intents: dict[str, Intent], apply: bool, suspended: bool
    ) -> Snapshot:
        return {
            **(
                {
                    "schema": "multi_zone_v1",
                    "normals_by_zone": {key: encode(value) for key, value in normals.items()},
                }
                if self.multi_zone
                else {"normal": encode(normals["normal"])}
            ),
            "intents": {key: encode(value) for key, value in intents.items()},
            "apply": apply,
            "suspended": suspended,
        }

    def snapshot(self) -> Snapshot:
        return self._snapshot(self.normals, self.intents, self.apply, self.suspended)

    def restore(
        self, raw: Mapping[str, object], now: float, *, preserve_activation: bool = False
    ) -> None:
        if self.multi_zone:
            stored_normals = raw.get("normals_by_zone")
            if (
                raw.get("schema") != "multi_zone_v1"
                or not isinstance(stored_normals, dict)
                or set(stored_normals) != set(self.normals)
            ):
                raise ValueError("Stored zone identities do not match configuration")
            normals = {key: decode(value) for key, value in stored_normals.items()}
        else:
            normals = {"normal": decode(raw["normal"])}
        stored = raw["intents"]
        if not isinstance(stored, dict):
            raise ValueError("Stored masks must be an object")
        intents = {}
        for mask in self.masks:
            intent = decode(stored[mask.id]) if mask.id in stored else Intent()
            if (not preserve_activation and not mask.restore) or (
                intent.expires_at is not None and intent.expires_at <= now
            ):
                intent = replace(intent, on=False, expires_at=None)
            intents[mask.id] = intent
        if type(raw["apply"]) is not bool or type(raw.get("suspended", False)) is not bool:
            raise ValueError("Invalid stored operating mode")
        self.normals, self.intents = normals, intents
        self.apply = raw["apply"]
        self.suspended = cast(bool, raw.get("suspended", False))

    def resolution(self, now: float, zone: str | None = None) -> Resolution:
        return resolve(
            self.normal if zone is None else self.normals[zone], self.masks, self.intents, now
        )

    def resolutions(self, now: float) -> dict[str, Resolution]:
        return {zone: self.resolution(now, zone) for zone in self.normals}

    async def _commit(
        self, normals: dict[str, Intent], intents: dict[str, Intent], apply: bool, suspended: bool
    ) -> None:
        await self._save(self._snapshot(normals, intents, apply, suspended))
        self.normals, self.intents = normals, intents
        self.apply, self.suspended = apply, suspended
        self.revision += 1

    async def update(
        self,
        producer: str,
        operation: Operation,
        data: Mapping[str, object],
        now: float,
        *,
        power_only: bool = False,
        prepare: Callable[[Mapping[str, object]], Mapping[str, object]] | None = None,
    ) -> tuple[Intent, Intent]:
        async with self.lock:
            previous = self.resolution(now).intent
            ordinary = self.is_normal(producer)
            if not ordinary and producer not in self.intents:
                raise ValueError("Unknown producer")
            intent = (
                self.normal
                if producer == "normal"
                else self.normals[producer]
                if ordinary
                else self.intents[producer]
            )
            mask = next((m for m in self.masks if m.id == producer), None)
            if operation == "toggle":
                operation = "off" if intent.on else "on"
            if operation == "on" and prepare is not None and not power_only:
                data = prepare(data)
            if data.get("brightness") == 0 and operation == "on" and not power_only:
                operation = "off"
            if operation == "clear":
                fields = data.get("fields")
                if (
                    not isinstance(fields, list)
                    or not fields
                    or any(key not in ("brightness", "appearance") for key in fields)
                ):
                    raise ValueError("Specify brightness and/or appearance")
                if ordinary:
                    raise ValueError("Cannot clear the normal fallback")
                intent = replace(
                    intent,
                    brightness=None if "brightness" in fields else intent.brightness,
                    color=None if "appearance" in fields else intent.color,
                )
            elif operation == "off":
                intent = replace(intent, on=False, expires_at=None)
            elif operation == "on":
                brightness = intent.brightness
                color = intent.color
                if not power_only:
                    unexpected = data.keys() - {
                        "brightness",
                        "color_temp_kelvin",
                        "xy_color",
                        "hs_color",
                        "rgb_color",
                        "transition",
                    }
                    if unexpected:
                        raise ValueError(f"Unsupported light parameters: {sorted(unexpected)}")
                    if "brightness" in data:
                        value = data["brightness"]
                        if type(value) is not int:
                            raise ValueError("Brightness must be an integer")
                        brightness = value
                    color = color_from_payload(data) or color
                intent = Intent(
                    True, brightness, color, now + mask.duration if mask and mask.duration else None
                )
            else:
                raise ValueError("Unknown operation")
            intents = {**self.intents}
            normals = dict(self.normals)
            if ordinary:
                for zone in normals if producer == "normal" else (producer,):
                    old = normals[zone]
                    normals[zone] = Intent(
                        intent.on,
                        intent.brightness
                        if (operation == "on" and not power_only and "brightness" in data)
                        else old.brightness,
                        intent.color
                        if (
                            operation == "on"
                            and not power_only
                            and color_from_payload(data) is not None
                        )
                        else old.color,
                    )
            else:
                intents[producer] = intent
            await self._commit(normals, intents, self.apply, self.suspended)
            return previous, self.resolution(now).intent

    async def set_mode(self, *, apply: bool | None = None, suspended: bool | None = None) -> None:
        async with self.lock:
            await self._commit(
                self.normals,
                self.intents,
                self.apply if apply is None else apply,
                self.suspended if suspended is None else suspended,
            )

    async def reconcile(
        self, observed: Intent, now: float, revision: int
    ) -> Literal["superseded", "suspended", "adopted"]:
        async with self.lock:
            if revision != self.revision:
                return "superseded"
            if any(
                i.on and (i.expires_at is None or i.expires_at > now) for i in self.intents.values()
            ):
                await self._commit(self.normals, self.intents, self.apply, True)
                return "suspended"
            normal = replace(
                self.normal,
                on=observed.on,
                brightness=observed.brightness or self.normal.brightness,
                color=observed.color or self.normal.color,
            )
            if self.multi_zone:
                raise ValueError("Multi-zone telemetry cannot change ordinary intent")
            await self._commit({"normal": normal}, self.intents, self.apply, self.suspended)
            return "adopted"

    async def expire(self, now: float) -> bool:
        async with self.lock:
            intents = {
                key: replace(value, on=False, expires_at=None)
                if value.expires_at is not None and value.expires_at <= now
                else value
                for key, value in self.intents.items()
            }
            if intents == self.intents:
                return False
            await self._commit(self.normals, intents, self.apply, self.suspended)
            return True
