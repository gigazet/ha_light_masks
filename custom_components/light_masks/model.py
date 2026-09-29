"""Pure, immutable light intent and channel resolution."""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import isfinite
from typing import Literal

type Power = Literal["transparent", "force_on", "force_off"]
type ColorMode = Literal["color_temp", "xy", "hs", "rgb"]
type ColorValue = int | tuple[float, ...]


@dataclass(frozen=True)
class Color:
    """Exactly one appearance representation."""

    mode: ColorMode
    value: ColorValue

    def __post_init__(self) -> None:
        if self.mode == "color_temp":
            if type(self.value) is not int or self.value < 1:
                raise ValueError("Color temperature must be a positive integer")
            return
        bounds = {"xy": (1.0, 1.0), "hs": (360.0, 100.0), "rgb": (255.0,) * 3}
        if self.mode not in bounds:
            raise ValueError("Unsupported color mode")
        if not isinstance(self.value, tuple) or len(self.value) != len(bounds[self.mode]):
            raise ValueError("Invalid color components")
        if any(
            isinstance(v, bool) or not isfinite(v) or not 0 <= v <= bound
            for v, bound in zip(self.value, bounds[self.mode], strict=True)
        ):
            raise ValueError("Color components are out of range")

    @property
    def service_key(self) -> str:
        return "color_temp_kelvin" if self.mode == "color_temp" else f"{self.mode}_color"


@dataclass(frozen=True)
class Intent:
    """Stored state belonging to one producer, never copied from an overlay."""

    on: bool = False
    brightness: int | None = None
    color: Color | None = None
    expires_at: float | None = None

    def __post_init__(self) -> None:
        if type(self.on) is not bool:
            raise ValueError("Invalid activation state")
        if self.brightness is not None and (
            type(self.brightness) is not int or not 1 <= self.brightness <= 255
        ):
            raise ValueError("Brightness must be between 1 and 255")
        if self.expires_at is not None and not isfinite(self.expires_at):
            raise ValueError("Invalid expiration")


@dataclass(frozen=True)
class Mask:
    """Configuration of one independent producer."""

    id: str
    priority: int
    brightness: bool = False
    appearance: bool = True
    power: Power = "transparent"
    restore: bool = False
    duration: float | None = None

    def __post_init__(self) -> None:
        if not self.id or type(self.priority) is not int or not 1 <= self.priority <= 1000:
            raise ValueError("Mask needs an identity and a priority between 1 and 1000")
        if self.power not in ("transparent", "force_on", "force_off"):
            raise ValueError("Invalid power contribution")
        if not self.brightness and not self.appearance and self.power == "transparent":
            raise ValueError("Mask must own at least one channel")
        if self.duration is not None and (not isfinite(self.duration) or self.duration <= 0):
            raise ValueError("Maximum duration must be positive")


@dataclass(frozen=True)
class Resolution:
    """Desired logical result and per-channel provenance."""

    intent: Intent
    power_owner: str = "normal"
    brightness_owner: str = "normal"
    appearance_owner: str = "normal"

    def payload(self) -> dict[str, object]:
        if not self.intent.on:
            return {}
        result: dict[str, object] = {}
        if self.intent.brightness is not None:
            result["brightness"] = self.intent.brightness
        if self.intent.color is not None:
            result[self.intent.color.service_key] = self.intent.color.value
        return result


def validate_masks(masks: tuple[Mask, ...]) -> None:
    if len({mask.id for mask in masks}) != len(masks):
        raise ValueError("Duplicate mask identity")
    if len({mask.priority for mask in masks}) != len(masks):
        raise ValueError("Duplicate mask priority")


def resolve(
    normal: Intent, masks: tuple[Mask, ...], intents: dict[str, Intent], now: float
) -> Resolution:
    """Resolve channels independently, then gate the physical payload by power."""
    validate_masks(masks)
    result = Resolution(replace(normal, expires_at=None))
    for mask in sorted(masks, key=lambda m: m.priority):
        intent = intents.get(mask.id, Intent())
        if not intent.on or (intent.expires_at is not None and intent.expires_at <= now):
            continue
        if mask.power != "transparent":
            result = replace(
                result,
                intent=replace(result.intent, on=mask.power == "force_on"),
                power_owner=mask.id,
            )
        if mask.brightness and intent.brightness is not None:
            result = replace(
                result,
                intent=replace(result.intent, brightness=intent.brightness),
                brightness_owner=mask.id,
            )
        if mask.appearance and intent.color is not None:
            result = replace(
                result, intent=replace(result.intent, color=intent.color), appearance_owner=mask.id
            )
    return result


def matches(expected: Intent, observed: Intent) -> bool:
    """Compare normalized endpoint values; Off ignores retained attributes."""
    if expected.on != observed.on:
        return False
    if not expected.on:
        return True
    if expected.brightness is not None and (
        observed.brightness is None or abs(expected.brightness - observed.brightness) > 2
    ):
        return False
    if expected.color is None:
        return True
    if observed.color is None or observed.color.mode != expected.color.mode:
        return False
    left, right = expected.color.value, observed.color.value
    if isinstance(left, int) and isinstance(right, int):
        return abs(left - right) <= 100
    if not isinstance(left, tuple) or not isinstance(right, tuple):
        return False
    if expected.color.mode == "hs":
        hue_distance = abs(left[0] - right[0])
        return abs(left[1] - right[1]) <= 1 and (
            max(left[1], right[1]) <= 1 or min(hue_distance, 360 - hue_distance) <= 1
        )
    tolerance = {"xy": 0.01, "hs": 1.0, "rgb": 2.0}[expected.color.mode]
    return all(abs(a - b) <= tolerance for a, b in zip(left, right, strict=True))
