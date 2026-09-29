import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from custom_components.light_masks.engine import Engine
from custom_components.light_masks.model import Color, Intent, Mask, matches, resolve

BLUE = Color("xy", (0.15, 0.1))
GREEN = Color("xy", (0.3, 0.6))


def test_channels_gate_and_latest_hidden_intent():
    masks = (Mask("sun", 10), Mask("wash", 100), Mask("dim", 50, True, False))
    intents = {
        "sun": Intent(True, color=Color("color_temp", 3000)),
        "wash": Intent(True, 64, GREEN),
        "dim": Intent(True, 100),
    }
    normal = Intent(False, 200, BLUE)
    result = resolve(normal, masks, intents, 0)
    assert result.payload() == {}
    assert result.intent == Intent(False, 100, GREEN)
    intents["sun"] = Intent(True, color=Color("color_temp", 5000))
    intents["wash"] = Intent(False, 64, GREEN)
    result = resolve(Intent(True, 200, BLUE), masks, intents, 0)
    assert result.payload() == {"brightness": 100, "color_temp_kelvin": 5000}
    assert result.appearance_owner == "sun"
    assert result.brightness_owner == "dim"


def test_power_overrides_are_released_by_off():
    masks = (Mask("alarm", 20, power="force_on"), Mask("sleep", 100, power="force_off"))
    intents = {"alarm": Intent(True), "sleep": Intent(True)}
    assert not resolve(Intent(), masks, intents, 0).intent.on
    intents["sleep"] = Intent(False)
    assert resolve(Intent(), masks, intents, 0).intent.on
    intents["alarm"] = Intent(False)
    assert not resolve(Intent(), masks, intents, 0).intent.on


def test_invalid_masks_and_expiry():
    with pytest.raises(ValueError, match="Duplicate"):
        resolve(Intent(), (Mask("a", 10), Mask("b", 10)), {}, 0)
    with pytest.raises(ValueError):
        Mask("empty", 10, appearance=False)
    result = resolve(
        Intent(True, color=BLUE),
        (Mask("expired", 10),),
        {"expired": Intent(True, color=GREEN, expires_at=10)},
        10,
    )
    assert result.intent.color == BLUE


async def test_toggle_atomic_and_power_port_payload_is_ignored():
    save = AsyncMock()
    engine = Engine(Intent(False, 128, BLUE), (Mask("wash", 10),), save)
    await asyncio.gather(*[engine.update("wash", "toggle", {}, 0) for _ in range(100)])
    assert not engine.intents["wash"].on
    assert engine.revision == 100
    await engine.update(
        "normal", "toggle", {"brightness": 0, "xy_color": (0.2, 0.5)}, 0, power_only=True
    )
    assert engine.normal == Intent(True, 128, BLUE)


async def test_failed_storage_does_not_accept_intent():
    engine = Engine(Intent(), (Mask("wash", 10),), AsyncMock(side_effect=OSError("disk full")))
    with pytest.raises(OSError):
        await engine.update("wash", "on", {"brightness": 100}, 0)
    assert not engine.intents["wash"].on
    assert engine.revision == 0


async def test_restore_and_absolute_lease():
    masks = (
        Mask("wash", 100, duration=60),
        Mask("sun", 10, restore=True),
        Mask("expired", 20, restore=True, duration=30),
    )
    engine = Engine(Intent(), masks, AsyncMock())
    for mask in masks:
        await engine.update(mask.id, "on", {"xy_color": (0.3, 0.6)}, 100)
    restored = Engine(Intent(), masks, AsyncMock())
    restored.restore(engine.snapshot(), 140)
    assert not restored.intents["wash"].on
    assert restored.intents["sun"].on
    assert not restored.intents["expired"].on
    configured = Engine(Intent(), masks, AsyncMock())
    configured.restore(engine.snapshot(), 140, preserve_activation=True)
    assert configured.intents["wash"].on
    assert configured.intents["wash"].expires_at == 160
    assert not configured.intents["expired"].on
    await engine.update("wash", "on", {}, 120)
    assert engine.intents["wash"].expires_at == 180
    assert await engine.expire(181)
    assert not engine.intents["wash"].on


async def test_zero_releases_and_clear_fields():
    engine = Engine(Intent(True, 128, BLUE), (Mask("wash", 100),), AsyncMock())
    await engine.update("wash", "on", {"xy_color": (0.3, 0.6)}, 0)
    await engine.update("wash", "clear", {"fields": ["appearance"]}, 0)
    assert engine.intents["wash"].on
    assert engine.resolution(0).intent.color == BLUE
    await engine.update("wash", "on", {"brightness": 0}, 0)
    assert not engine.intents["wash"].on
    assert engine.normal.on


def test_resolution_32_masks_p95():
    masks = tuple(Mask(str(i), i + 1) for i in range(32))
    intents = {m.id: Intent(True, color=GREEN) for m in masks}
    samples = []
    for _ in range(1000):
        start = time.perf_counter()
        resolve(Intent(True, 128, BLUE), masks, intents, 0)
        samples.append(time.perf_counter() - start)
    assert sorted(samples)[949] < 0.005


async def test_external_observation_cannot_overwrite_concurrent_intent():
    engine = Engine(Intent(True, 128, BLUE), (Mask("wash", 100),), AsyncMock())
    revision = engine.revision
    await engine.update("wash", "on", {"xy_color": (0.3, 0.6)}, 0)
    assert await engine.reconcile(Intent(False), 0, revision) == "superseded"
    assert engine.normal == Intent(True, 128, BLUE)
    assert await engine.reconcile(Intent(False), 0, engine.revision) == "suspended"
    assert engine.suspended
    assert engine.normal.on


def test_hs_wrap_and_achromatic_feedback():
    assert matches(
        Intent(True, color=Color("hs", (360, 100))),
        Intent(True, color=Color("hs", (0, 100))),
    )
    assert matches(
        Intent(True, color=Color("hs", (200, 0))),
        Intent(True, color=Color("hs", (10, 0))),
    )
    assert not matches(
        Intent(True, color=Color("hs", (200, 50))),
        Intent(True, color=Color("hs", (10, 50))),
    )
