"""Room-temperature history stays explicit, raw-only and independent of entities."""

from __future__ import annotations

import asyncio
import copy
import importlib
import json
import sys
import time
import types
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import voluptuous as vol
from pymelans.member_temperature_history import parse_member_temperature_history
from test_ha_controller_clock import clock_service as clock_service

from pymelans import MelansClient, cached_snapshot

DATA = {"config_entry_id": "selected", "address": 4, "date_iso": "2024-02-29"}
ROOT = Path(__file__).resolve().parents[1]
SERVICE = "read_member_temperature_history"


@pytest.fixture
def history_service(clock_service):
    context = clock_service
    context.client.async_read_member_temperature_history = AsyncMock()
    context.client.async_read_member_runtime = AsyncMock()
    context.services.async_setup_services(context.hass)
    return context


async def call(context, data):
    registered = context.hass.services.registered["ae200", SERVICE]
    validated = registered["schema"](data)
    return await registered["handler"](types.SimpleNamespace(data=validated))


@pytest.mark.asyncio
@pytest.mark.parametrize("field", DATA)
async def test_all_three_selectors_are_required(history_service, field):
    context = history_service
    with pytest.raises(vol.Invalid):
        await call(context, {key: value for key, value in DATA.items() if key != field})
    context.client.async_read_member_temperature_history.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("entry_kind", ["missing", "wrong_domain", "unloaded"])
async def test_invalid_entry_never_selects_another_controller(history_service, entry_kind):
    context = history_service
    if entry_kind == "wrong_domain":
        context.entries["invalid"] = types.SimpleNamespace(domain="another_integration")
        context.hass.data["ae200"]["invalid"] = context.coordinator
    elif entry_kind == "unloaded":
        context.entries["invalid"] = types.SimpleNamespace(domain="ae200")
    error = context.HomeAssistantError if entry_kind == "unloaded" else context.ServiceValidationError
    with pytest.raises(error):
        await call(context, {**DATA, "config_entry_id": "invalid"})
    context.client.async_read_member_temperature_history.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["", None, True, 4, 4.0, ["selected"]])
async def test_entry_selector_cannot_be_inferred_or_coerced(history_service, value):
    context = history_service
    with pytest.raises(vol.Invalid):
        await call(context, {**DATA, "config_entry_id": value})
    context.client.async_read_member_temperature_history.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [
    "1969-12-31", "2038-01-01", "2023-02-29", "2024-02-30", "2024-04-31",
    "2024-2-01", "2024-02-1", "2024-02-29 ", " 2024-02-29", "２０２４-02-29",
    "2024-02-29T00:00:00", "2024-02-29Z", date(2024, 2, 29), None, True, 20240229,
    "*", ["2024-02-29"],
])
async def test_date_is_one_strict_explicit_gregorian_day(history_service, value):
    context = history_service
    with pytest.raises(vol.Invalid):
        await call(context, {**DATA, "date_iso": value})
    context.client.async_read_member_temperature_history.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [
    0, 251, True, False, 4.0, "4.0", "٤", "0004", "*", "4,5", [4], None,
])
async def test_address_is_one_bounded_ascii_integer(history_service, value):
    context = history_service
    with pytest.raises(vol.Invalid):
        await call(context, {**DATA, "address": value})
    context.client.async_read_member_temperature_history.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [
    {"data_name": "ROOM_TEMP"}, {"data_name": "FAN_TIME"}, {"data_name": "*"},
    {"group_id": 4}, {"Group": "4"}, {"Address": "*"}, {"addresses": [4]},
    {"date": "2024-02-29"}, {"year": 2024}, {"all": True}, {"unit": "C"},
    {"aggregation": "mean"}, {"start_collection": True}, {"timezone": "UTC"},
])
async def test_channel_scope_units_and_control_cannot_be_added(history_service, extra):
    context = history_service
    with pytest.raises(vol.Invalid):
        await call(context, {**DATA, **extra})
    context.client.async_read_member_temperature_history.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("address,day", [
    (1, "1970-01-01"), (250, "2037-12-31"), ("004", "2024-02-29"),
])
async def test_one_canonical_member_day_reaches_only_the_selected_client(history_service, address, day):
    context = history_service
    snapshot = {"read_status": "updated", "quality": "fresh", "raw_attributes": {}}
    context.client.async_read_member_temperature_history.return_value = snapshot
    other = types.SimpleNamespace(async_read_member_temperature_history=AsyncMock())
    context.entries["other"] = types.SimpleNamespace(domain="ae200")
    context.hass.data["ae200"]["other"] = types.SimpleNamespace(client=other)
    assert await call(context, {**DATA, "address": address, "date_iso": day}) is snapshot
    context.client.async_read_member_temperature_history.assert_awaited_once_with(int(address), day)
    other.async_read_member_temperature_history.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot", [
    {"read_status": "updated", "quality": "fresh", "raw_attributes": {"RoomTempData": "18,*,@m"},
     "raw_tokens": ["18", "*", "@m"], "slots": [], "issues": [{"kind": "unexpected_token_count"}],
     "unit": None, "aggregation": None, "timezone": None},
    {"read_status": "updated", "quality": "fresh", "raw_attributes": {}, "slots": []},
    {"read_status": "error", "quality": "unknown", "raw_attributes": {}, "slots": []},
    {"read_status": "error", "quality": "stale", "raw_attributes": {"RoomTempData": "*"}, "slots": []},
])
async def test_raw_quality_and_unknowns_pass_through_without_another_request(history_service, snapshot):
    context = history_service
    before = copy.deepcopy(snapshot)
    context.client.async_read_member_temperature_history.return_value = snapshot
    response = await call(context, DATA)
    assert response is snapshot and json.loads(json.dumps(response)) == before
    assert not {"reported_celsius", "temperature", "average", "total", "hvac_action"} & response.keys()
    context.client.async_read_member_temperature_history.assert_awaited_once_with(4, "2024-02-29")
    context.coordinator.async_request_refresh.assert_not_awaited()
    for name in ("async_read_member", "async_read_member_runtime", "async_read_control_context",
                 "async_read_today_schedule", "async_read_controller_clock", "_request", "_write"):
        getattr(context.client, name).assert_not_awaited()


@pytest.mark.asyncio
async def test_cooldown_is_visible_without_a_retry_or_runtime_fallback(history_service):
    context = history_service
    context.client.async_read_member_temperature_history.return_value = {
        "read_status": "cooldown", "quality": "fresh", "raw_tokens": ["18"] * 48,
    }
    with pytest.raises(context.HomeAssistantError, match="Wait at least 15 seconds"):
        await call(context, DATA)
    assert context.client.async_read_member_temperature_history.await_count == 1
    context.client.async_read_member_runtime.assert_not_awaited()


@pytest.mark.asyncio
async def test_membership_validation_is_not_replaced_with_a_probe(history_service):
    context = history_service
    context.client.async_read_member_temperature_history.side_effect = ValueError("member topology is not verified")
    with pytest.raises(context.ServiceValidationError, match="not verified"):
        await call(context, DATA)
    context.client._request.assert_not_awaited()


@pytest.mark.asyncio
async def test_protocol_failure_is_safe_and_cancellation_propagates(history_service):
    context = history_service
    context.client.async_read_member_temperature_history.side_effect = context.services.MelansError("private.invalid")
    with pytest.raises(context.HomeAssistantError) as error:
        await call(context, DATA)
    assert "private.invalid" not in str(error.value)
    context.client.async_read_member_temperature_history.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await call(context, DATA)
    assert context.client.async_read_member_temperature_history.await_count == 2
    context.client.async_read_member_runtime.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [None, {}, [], {"read_status": "partial"}, {"read_status": "unknown"}])
async def test_missing_or_invalid_snapshot_is_not_reported_as_success(history_service, invalid):
    context = history_service
    context.client.async_read_member_temperature_history.return_value = invalid
    with pytest.raises(context.HomeAssistantError, match="did not produce a member temperature history sample"):
        await call(context, DATA)
    assert context.client.async_read_member_temperature_history.await_count == 1


def test_action_metadata_matches_the_fixed_raw_history_contract(history_service):
    context = history_service
    registration = context.hass.services.registered["ae200", SERVICE]
    assert registration["supports_response"] == context.SupportsResponse.ONLY
    component = ROOT / "custom_components/ae200"
    strings = json.loads((component / "strings.json").read_text())
    english = json.loads((component / "translations/en.json").read_text())
    action = strings["services"][SERVICE]
    assert action == english["services"][SERVICE]
    assert set(action["fields"]) == set(DATA)
    assert all(field["name"] and field["description"] for field in action["fields"].values())
    assert "1970 to 2037" in action["fields"]["date_iso"]["description"]
    yaml = (component / "services.yaml").read_text().split(SERVICE + ":\n", 1)[1].split("\nread_member_runtime:", 1)[0]
    assert "config_entry:" in yaml and "integration: ae200" in yaml and "date:" in yaml
    assert yaml.count("required: true") == 3 and "data_name:" not in yaml


@pytest.mark.asyncio
@pytest.mark.parametrize("with_sample", [False, True])
async def test_ha_cached_diagnostics_exports_history_without_io_or_cache_allocation(
    history_service, monkeypatch, with_sample,
):
    context = history_service
    client = MelansClient("fixture.invalid")
    client._request = AsyncMock(side_effect=AssertionError("Cached diagnostics must not read"))
    client.async_read_member_temperature_history = AsyncMock()
    context.coordinator.client = client
    context.coordinator.update_interval = timedelta(seconds=30)
    context.coordinator.last_update_success = True
    context.coordinator.host = "fixture.invalid"
    if with_sample:
        raw = ",".join(["18.0"] * 48)
        payload = (
            '<Packet><Command>getResponse</Command><DatabaseManager><EnergyManagement>'
            '<IcTrendDList Address="4" Year="2024" Month="2" Day="29" DataName="ROOM_TEMP">'
            f'<IcTrendDRecord RoomTempData="{raw}" />'
            '</IcTrendDList></EnergyManagement></DatabaseManager></Packet>'
        )
        client._member_temperature_histories["4", "2024-02-29"] = {
            "sample": parse_member_temperature_history(payload, expected_address="4", expected_date="2024-02-29"),
            "last_attempt": datetime.now(UTC), "last_success": datetime.now(UTC),
            "seen": time.monotonic(), "error": None, "read_status": "updated",
        }
    before = copy.deepcopy(client._member_temperature_histories)
    direct = cached_snapshot(client)
    expected = client.member_temperature_history_snapshot()
    assert direct["member_temperature_histories"] == expected
    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = object
    monkeypatch.setitem(sys.modules, "homeassistant.config_entries", config_entries)
    module = importlib.import_module(context.services.__package__ + ".diagnostics")
    entry = types.SimpleNamespace(entry_id="selected")
    result = await module.async_get_config_entry_diagnostics(context.hass, entry)
    assert result["controller"]["member_temperature_histories"] == expected
    assert client._member_temperature_histories == before
    client._request.assert_not_awaited()
    client.async_read_member_temperature_history.assert_not_awaited()
    assert "fixture.invalid" not in json.dumps(result)
    if with_sample:
        exported = result["controller"]["member_temperature_histories"]["4"]["2024-02-29"]
        assert exported["unit"] is exported["aggregation"] is exported["timezone"] is None
        assert len(exported["slots"]) == 48
        assert all(set(slot) == {"index", "start_time", "end_time", "raw_token"} for slot in exported["slots"])
        exported["slots"][0]["raw_token"] = "changed"
        exported["raw_tokens"].clear()
        assert client._member_temperature_histories == before
