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

    def __init__(self, normal: Intent, masks: tuple[Mask, ...], save: Save) -> None:
        validate_masks(masks)
        self.normal = normal
        self.masks = masks
        self.intents = {mask.id: Intent() for mask in masks}
        self.apply = False
        self.suspended = False
        self.revision = 0
        self.lock = asyncio.Lock()
        self._save = save

    def snapshot(self) -> Snapshot:
        return {
            "normal": encode(self.normal),
            "intents": {key: encode(value) for key, value in self.intents.items()},
            "apply": self.apply,
            "suspended": self.suspended,
        }

    def restore(
        self, raw: Mapping[str, object], now: float, *, preserve_activation: bool = False
    ) -> None:
        normal = decode(raw["normal"])
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
        self.normal, self.intents = normal, intents
        self.apply = raw["apply"]
        self.suspended = cast(bool, raw.get("suspended", False))

    def resolution(self, now: float) -> Resolution:
        return resolve(self.normal, self.masks, self.intents, now)

    async def _commit(
        self, normal: Intent, intents: dict[str, Intent], apply: bool, suspended: bool
    ) -> None:
        await self._save(
            {
                "normal": encode(normal),
                "intents": {key: encode(value) for key, value in intents.items()},
                "apply": apply,
                "suspended": suspended,
            }
        )
        self.normal, self.intents = normal, intents
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
            if producer != "normal" and producer not in self.intents:
                raise ValueError("Unknown producer")
            intent = self.normal if producer == "normal" else self.intents[producer]
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
                if producer == "normal":
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
            normal = self.normal
            if producer == "normal":
                normal = intent
            else:
                intents[producer] = intent
            await self._commit(normal, intents, self.apply, self.suspended)
            return previous, self.resolution(now).intent

    async def set_mode(self, *, apply: bool | None = None, suspended: bool | None = None) -> None:
        async with self.lock:
            await self._commit(
                self.normal,
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
                await self._commit(self.normal, self.intents, self.apply, True)
                return "suspended"
            normal = replace(
                self.normal,
                on=observed.on,
                brightness=observed.brightness or self.normal.brightness,
                color=observed.color or self.normal.color,
            )
            await self._commit(normal, self.intents, self.apply, self.suspended)
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
            await self._commit(self.normal, intents, self.apply, self.suspended)
            return True
