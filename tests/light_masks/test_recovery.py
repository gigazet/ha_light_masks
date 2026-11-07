"""Automatic reconciliation, availability recovery and upgrade regression tests."""

import asyncio

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from tests.light_masks.test_integration import call, entities, settle


async def test_upgrade_clears_legacy_suspension_and_removes_button(hass, integration, output):
    c = integration.runtime_data
    await call(hass, entities(hass, integration)["normal"])
    await c.engine.set_mode(apply=True)
    await c.store.async_save({**c.engine.snapshot(), "suspended": True})
    registry = er.async_get(hass)
    button = registry.async_get_or_create(
        "button", "light_masks", f"{integration.entry_id}_resume", config_entry=integration
    )
    ir.async_create_issue(
        hass,
        "light_masks",
        f"{integration.entry_id}_external_change",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="external_change",
    )
    assert await hass.config_entries.async_reload(integration.entry_id)
    await settle(hass, integration.runtime_data)
    assert registry.async_get(button.entity_id) is None
    assert not any(
        e.domain == "button"
        for e in er.async_entries_for_config_entry(registry, integration.entry_id)
    )
    assert not any(
        key == ("light_masks", f"{integration.entry_id}_external_change")
        for key in ir.async_get(hass).issues
    )
    assert "suspended" not in await integration.runtime_data.store.async_load()
    assert output.is_on
    assert integration.runtime_data.status == "in_sync"


@pytest.mark.parametrize(
    ("final_on", "expected"),
    [
        (False, ("off", {"transition": 0.0})),
        (True, ("on", {"brightness": 70, "color_temp_kelvin": 4000, "transition": 0.0})),
    ],
)
async def test_recovery_applies_latest_intent_without_manual_action(
    hass, integration, output, final_on, expected
):
    c = integration.runtime_data
    await c.set_apply(True)
    await call(hass, entities(hass, integration)["normal"], brightness=90)
    await settle(hass, c)
    output.calls.clear()
    output._attr_available = False
    output.async_write_ha_state()
    await settle(hass, c)
    await call(hass, entities(hass, integration)["normal"], "turn_off")
    if final_on:
        await call(hass, entities(hass, integration)["normal"], brightness=70)
    await settle(hass, c)
    assert not output.calls
    output._attr_is_on = not final_on
    output._attr_available = True
    output.async_write_ha_state()
    await settle(hass, c)
    assert output.is_on == final_on
    assert output.calls == [expected]
    assert c.status == "in_sync"


async def test_unavailable_then_recovered_device_retries_failed_delivery(hass, integration, output):
    c = integration.runtime_data
    output.fail = True
    await c.set_apply(True)
    await call(hass, entities(hass, integration)["normal"])
    await settle(hass, c)
    assert c.status == "failed"
    assert len(output.calls) == 3
    output._attr_available = False
    output.async_write_ha_state()
    await settle(hass, c)
    output.fail = False
    output._attr_available = True
    output.async_write_ha_state()
    await settle(hass, c)
    assert output.is_on
    assert len(output.calls) == 4
    assert c.status == "in_sync"


async def test_shadow_never_reconciles_external_changes(hass, integration, output):
    c = integration.runtime_data
    before = c.engine.snapshot()
    await call(hass, output.entity_id)
    await asyncio.sleep(0.04)
    await settle(hass, c)
    assert output.is_on
    assert c.engine.snapshot() == before
    assert len(output.calls) == 1
    assert c.status == "shadow"


@pytest.mark.parametrize("retry", ["command", "reconnect"])
async def test_repeated_external_writes_are_bounded_and_reported(hass, integration, output, retry):
    c = integration.runtime_data
    await c.set_apply(True)
    await settle(hass, c)
    for _ in range(3):
        await call(hass, output.entity_id)
        await asyncio.sleep(0.04)
        await settle(hass, c)
        assert not output.is_on
        assert c.status == "in_sync"
    await call(hass, output.entity_id)
    await asyncio.sleep(0.04)
    await settle(hass, c)
    count = len(output.calls)
    assert c.status == "failed"
    assert "competing" in c.error
    assert ("light_masks", f"{integration.entry_id}_delivery") in ir.async_get(hass).issues
    for brightness in (100, 120, 140):
        await call(hass, output.entity_id, brightness=brightness)
        await asyncio.sleep(0.04)
    await settle(hass, c)
    assert len(output.calls) == count + 3
    if retry == "command":
        await call(hass, entities(hass, integration)["normal"], "turn_off")
    else:
        output._attr_available = False
        output.async_write_ha_state()
        await settle(hass, c)
        output._attr_available = True
        output.async_write_ha_state()
    await settle(hass, c)
    assert not output.is_on
    assert c.status == "in_sync"
    assert ("light_masks", f"{integration.entry_id}_delivery") not in ir.async_get(hass).issues


async def test_reconciliation_budget_ages_out(hass, integration, output, monkeypatch):
    from custom_components.light_masks import controller

    monkeypatch.setattr(controller, "MAX_RECONCILIATIONS", 1)
    monkeypatch.setattr(controller, "RECONCILIATION_WINDOW", 0.01)
    c = integration.runtime_data
    await c.set_apply(True)
    await settle(hass, c)
    for _ in range(3):
        await call(hass, output.entity_id)
        await asyncio.sleep(0.04)
        await settle(hass, c)
        assert not output.is_on
        assert c.status == "in_sync"
        assert len(c._reconciliations) == 1


async def test_shadow_never_writes_on_availability_recovery(hass, integration, output):
    c = integration.runtime_data
    await call(hass, entities(hass, integration)["normal"])
    output._attr_available = False
    output.async_write_ha_state()
    await settle(hass, c)
    output._attr_available = True
    output.async_write_ha_state()
    await settle(hass, c)
    assert c.engine.normal.on
    assert not output.is_on
    assert not output.calls
    assert c.status == "shadow"
