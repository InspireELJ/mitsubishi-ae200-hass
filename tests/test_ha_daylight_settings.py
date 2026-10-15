"""Daylight settings are explicit raw-only responses, never automatic controls."""

from __future__ import annotations

import asyncio
import copy
import importlib
import json
import re
import sys
import time
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import voluptuous as vol
from pymelans.controller_daylight_settings import parse_controller_daylight_settings
from test_ha_controller_clock import clock_service as clock_service

from pymelans import MelansClient, cached_snapshot

ROOT = Path(__file__).resolve().parents[1]
SERVICE = "read_daylight_settings"
DATA = {"config_entry_id": "selected"}


@pytest.fixture
def daylight_service(clock_service):
    context = clock_service
    context.client.async_read_daylight_settings = AsyncMock()
    context.services.async_setup_services(context.hass)
    return context


async def call(context, data):
    registered = context.hass.services.registered["ae200", SERVICE]
    return await registered["handler"](types.SimpleNamespace(data=registered["schema"](data)))


def sample(status="updated", quality="fresh"):
    return {
        "read_status": status, "quality": quality,
        "last_attempt": "2026-01-01T00:00:00+00:00",
        "last_successful_read": "2026-01-01T00:00:00+00:00" if quality != "unknown" else None,
        "error": {"type": "MelansConnectionError", "code": None} if status == "error" else None,
        "raw_attributes": {"CountryCode": "000", "ShiftMin2": "-30"} if quality != "unknown" else {},
        "issues": [],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [
    {}, *({"config_entry_id": value} for value in ("", None, True, 1, 1.0, ["selected"])),
    *({**DATA, key: value} for key, value in (
        ("group_id", 4), ("address", 4), ("country", "000"), ("CountryCode", "000"),
        ("enable", True), ("enabled", False), ("timezone", "UTC"), ("offset", 60),
        ("current_dst", True), ("ShiftMin1", "60"), ("clock", True), ("refresh", True),
    )),
])
async def test_explicit_entry_only_schema_rejects_missing_invalid_and_extra_data_before_io(daylight_service, data):
    c = daylight_service
    with pytest.raises(vol.Invalid):
        await call(c, data)
    c.client.async_read_daylight_settings.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["unknown", "wrong_domain", "unloaded"])
async def test_invalid_entry_never_uses_another_loaded_controller(daylight_service, kind):
    c = daylight_service
    if kind == "wrong_domain":
        c.entries["invalid"] = types.SimpleNamespace(domain="other_integration")
        c.hass.data["ae200"]["invalid"] = c.coordinator
    elif kind == "unloaded":
        c.entries["invalid"] = types.SimpleNamespace(domain="ae200")
    error = c.HomeAssistantError if kind == "unloaded" else c.ServiceValidationError
    with pytest.raises(error):
        await call(c, {"config_entry_id": "invalid"})
    c.client.async_read_daylight_settings.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot", [sample(), sample("error", "unknown"), sample("error", "stale")])
async def test_returns_selected_safe_snapshot_once_without_clock_refresh_or_control(daylight_service, snapshot):
    c = daylight_service
    c.client.async_read_daylight_settings.return_value = snapshot
    other = types.SimpleNamespace(async_read_daylight_settings=AsyncMock())
    c.entries["other"] = types.SimpleNamespace(domain="ae200")
    c.hass.data["ae200"]["other"] = types.SimpleNamespace(client=other)
    result = await call(c, DATA)
    assert result == snapshot and json.loads(json.dumps(result)) == snapshot
    assert set(result) == {"read_status", "quality", "last_attempt", "last_successful_read",
                           "error", "raw_attributes", "issues"}
    c.client.async_read_daylight_settings.assert_awaited_once_with()
    other.async_read_daylight_settings.assert_not_awaited()
    c.client.async_read_controller_clock.assert_not_awaited()
    c.client.async_read_today_schedule.assert_not_awaited()
    c.client.async_read_control_context.assert_not_awaited()
    c.client.async_read_member.assert_not_awaited()
    c.coordinator.async_request_refresh.assert_not_awaited()
    c.client._write.assert_not_awaited()
    c.client._request.assert_not_awaited()
    assert c.hass.services.registered["ae200", SERVICE]["supports_response"] == c.SupportsResponse.ONLY


@pytest.mark.asyncio
async def test_cooldown_with_cached_raw_data_is_a_clear_error_without_retry(daylight_service):
    c = daylight_service
    c.client.async_read_daylight_settings.return_value = sample("cooldown")
    with pytest.raises(c.HomeAssistantError, match="Wait at least 15 seconds"):
        await call(c, DATA)
    c.client.async_read_daylight_settings.assert_awaited_once_with()
    c.coordinator.async_request_refresh.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [None, {}, [], {"read_status": "unknown"}, {"read_status": "partial"}])
async def test_invalid_client_result_is_an_error_without_fallback(daylight_service, invalid):
    c = daylight_service
    c.client.async_read_daylight_settings.return_value = invalid
    with pytest.raises(c.HomeAssistantError, match="did not produce a daylight settings sample"):
        await call(c, DATA)
    c.client.async_read_daylight_settings.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_client_validation_and_transport_errors_are_safe_and_cancellation_propagates(daylight_service):
    c = daylight_service
    c.client.async_read_daylight_settings.side_effect = ValueError("unsupported controller read")
    with pytest.raises(c.ServiceValidationError, match="unsupported"):
        await call(c, DATA)
    c.client.async_read_daylight_settings.side_effect = c.services.MelansError("http://private.invalid/secret")
    with pytest.raises(c.HomeAssistantError, match="daylight settings read failed") as error:
        await call(c, DATA)
    assert "private" not in str(error.value)
    c.client.async_read_daylight_settings.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await call(c, DATA)
    assert c.client.async_read_daylight_settings.await_count == 3
    c.client._request.assert_not_awaited()
    c.coordinator.async_request_refresh.assert_not_awaited()


def test_daylight_action_translation_and_yaml_have_only_required_entry():
    component = ROOT / "custom_components/ae200"
    strings = json.loads((component / "strings.json").read_text())
    english = json.loads((component / "translations/en.json").read_text())
    action = strings["services"][SERVICE]
    assert action == english["services"][SERVICE]
    assert set(action["fields"]) == {"config_entry_id"}
    assert action["fields"]["config_entry_id"]["name"] and action["fields"]["config_entry_id"]["description"]
    yaml = (component / "services.yaml").read_text().split(SERVICE + ":\n", 1)[1]
    yaml = re.split(r"\n[a-z][a-z_]*:\n", yaml, maxsplit=1)[0]
    assert yaml.count("required: true") == 1
    assert "config_entry:" in yaml and "integration: ae200" in yaml
    assert "group_id:" not in yaml and "address:" not in yaml and "boolean:" not in yaml


@pytest.mark.asyncio
@pytest.mark.parametrize("with_sample", [False, True])
async def test_native_diagnostics_copy_raw_daylight_cache_without_reading(daylight_service, monkeypatch, with_sample):
    c = daylight_service
    client = MelansClient("fixture.invalid")
    client._request = AsyncMock(side_effect=AssertionError("Diagnostics must use cached data"))
    client.async_read_daylight_settings = AsyncMock()
    c.coordinator.client = client
    c.coordinator.update_interval = timedelta(seconds=30)
    c.coordinator.last_update_success = True
    if with_sample:
        now = datetime.now(UTC)
        client._daylight_settings = {
            "sample": parse_controller_daylight_settings(
                '<Packet><Command>getResponse</Command><DatabaseManager>'
                '<SummerTime CountryCode="000" ShiftMin1="*" ShiftMin2="-30" />'
                '</DatabaseManager></Packet>'
            ),
            "last_attempt": now, "last_success": now, "seen": time.monotonic(), "error": None,
        }
        client._daylight_read_status = "updated"
    before = copy.deepcopy(client._daylight_settings)
    expected = client.daylight_settings_snapshot()
    assert cached_snapshot(client)["daylight_settings"] == expected
    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = object
    monkeypatch.setitem(sys.modules, "homeassistant.config_entries", config_entries)
    native = importlib.import_module(c.services.__package__ + ".diagnostics")
    result = await native.async_get_config_entry_diagnostics(c.hass, types.SimpleNamespace(entry_id="selected"))
    exported = result["controller"]["daylight_settings"]
    assert exported == expected and "fixture.invalid" not in json.dumps(result)
    if with_sample:
        exported["raw_attributes"]["CountryCode"] = "caller-mutation"
        exported["issues"][0]["kind"] = "caller-mutation"
    assert client._daylight_settings == before
    client._request.assert_not_awaited()
    client.async_read_daylight_settings.assert_not_awaited()
