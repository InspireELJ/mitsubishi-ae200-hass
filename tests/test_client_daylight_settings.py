"""Explicit raw daylight configuration stays isolated from climate and Clock."""

from __future__ import annotations

import asyncio
import copy
import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pymelans.exceptions import MelansConnectionError, MelansProtocolError

from pymelans import MelansClient, MelansDevice, cached_snapshot, const

RAW = {
    "CountryCode": "000", "Month1": "01", "Day1": "1", "Hour1": "0", "Minute1": "0",
    "ShiftMin1": "30", "Month2": "12", "Day2": "31", "Hour2": "1", "Minute2": "0",
    "ShiftMin2": "-30",
}
KEYS = {"read_status", "quality", "last_attempt", "last_successful_read", "error",
        "raw_attributes", "issues"}


def reply(attributes=None, *, command="getResponse"):
    root = ET.Element("Packet")
    ET.SubElement(root, "Command").text = command
    ET.SubElement(ET.SubElement(root, "DatabaseManager"), "SummerTime", {
        **(RAW if attributes is None else attributes), "PrivateName": "private-controller",
        "Timezone": "private-zone", "Enabled": "ON",
    })
    return ET.tostring(root, encoding="unicode")


@pytest.fixture
def client(monkeypatch):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr("pymelans.client.time", SimpleNamespace(
        monotonic=lambda: clock.now, perf_counter=lambda: clock.now,
    ))
    c = MelansClient("fixture.invalid", stale_after=20)
    c._request = AsyncMock(return_value=reply())
    return c, clock


@pytest.mark.asyncio
async def test_one_exact_raw_controller_read_needs_no_group_or_clock_read(client):
    c, _ = client
    result = await c.async_read_daylight_settings()
    c._request.assert_awaited_once()
    root = ET.fromstring(c._request.call_args.args[0])
    assert root.findtext("./Command") == "getRequest"
    manager = root.find("./DatabaseManager")
    assert len(manager) == 1 and manager[0].tag == "SummerTime" and not len(manager[0])
    assert manager[0].attrib == dict.fromkeys(RAW, "*")
    assert set(result) == KEYS
    assert result["read_status"] == "updated" and result["quality"] == "fresh"
    assert result["raw_attributes"] == RAW and result["issues"] == [] and result["error"] is None
    assert result["last_attempt"] <= result["last_successful_read"]
    assert not c._names and not c.devices and not c._controller_clock
    assert "private-" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [dict.fromkeys(RAW, ""), {**RAW, "Month1": "*", "ShiftMin2": "FUTURE"}])
async def test_present_blank_and_unfamiliar_fields_remain_raw_without_enable_or_validity_claim(client, raw):
    c, _ = client
    c._request.return_value = reply(raw)
    result = await c.async_read_daylight_settings()
    assert result["read_status"] == "updated" and result["quality"] == "fresh"
    assert result["raw_attributes"] == raw and result["issues"] == []
    assert set(result) == KEYS


@pytest.mark.asyncio
async def test_partial_sample_exports_missing_issues_without_fabricating_raw_defaults(client):
    c, _ = client
    c._request.return_value = reply({"CountryCode": "000"})
    result = await c.async_read_daylight_settings()
    assert result["quality"] == "fresh" and result["read_status"] == "updated"
    assert result["raw_attributes"] == {"CountryCode": "000"}
    assert result["issues"] == [
        {"kind": "missing_attribute", "key": key} for key in RAW if key != "CountryCode"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("warm", [False, True])
@pytest.mark.parametrize("failure", [
    MelansConnectionError("private.invalid/secret"),
    MelansProtocolError("private-message", code="0101", point="private-point"),
    MelansProtocolError("private-message", code="private.invalid/path"),
])
async def test_failed_attempt_keeps_old_sample_stale_or_cold_unknown_and_sanitises_errors(client, warm, failure):
    c, clock = client
    first = await c.async_read_daylight_settings() if warm else c.daylight_settings_snapshot()
    clock.now += 15
    c._request.side_effect = failure
    result = await c.async_read_daylight_settings()
    assert result["read_status"] == "error" and result["quality"] == ("stale" if warm else "unknown")
    assert result["raw_attributes"] == first["raw_attributes"]
    assert result["last_successful_read"] == first["last_successful_read"]
    assert result["last_attempt"] is not None
    assert result["error"] == {
        "type": type(failure).__name__, "code": "0101" if getattr(failure, "code", None) == "0101" else None,
    }
    assert "private" not in json.dumps(result)
    calls = c._request.await_count
    assert (await c.async_read_daylight_settings())["read_status"] == "cooldown"
    assert c._request.await_count == calls


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    reply({}), reply(command="notifyRequest"),
    '<Packet><Command>getResponse</Command><DatabaseManager><SummerTime CountryCode="000" />'
    '<SummerTime CountryCode="000" /></DatabaseManager></Packet>',
    '<Packet><Command>getErrorResponse</Command><DatabaseManager><ERROR Code="0101" '
    'Message="private-error" /><SummerTime CountryCode="000" /></DatabaseManager></Packet>',
])
async def test_invalid_response_never_replaces_successful_sample_or_retries(client, payload):
    c, clock = client
    first = await c.async_read_daylight_settings()
    clock.now += 15
    c._request.return_value = payload
    result = await c.async_read_daylight_settings()
    assert result["read_status"] == "error" and result["quality"] == "stale"
    assert result["last_successful_read"] == first["last_successful_read"]
    assert result["raw_attributes"] == RAW and "private" not in json.dumps(result)
    assert c._request.await_count == 2


@pytest.mark.asyncio
async def test_slow_attempt_uses_completion_cooldown_without_renewing_old_sample(client):
    c, clock = client

    async def slow(_):
        clock.now += 40
        return reply()

    c._request.side_effect = slow
    first = await c.async_read_daylight_settings()
    completed = clock.now
    clock.now = completed + 14.99
    second = await c.async_read_daylight_settings()
    assert second == {**first, "read_status": "cooldown"}
    c._request.assert_awaited_once()
    clock.now = completed + 15
    c._request.side_effect = None
    assert (await c.async_read_daylight_settings())["read_status"] == "updated"
    assert c._request.await_count == 2


@pytest.mark.asyncio
async def test_concurrent_actions_serialize_and_cannot_bypass_cooldown(client):
    c, clock = client
    started, release = asyncio.Event(), asyncio.Event()

    async def blocked(_):
        started.set()
        await release.wait()
        clock.now += 40
        return reply()

    c._request.side_effect = blocked
    first = asyncio.create_task(c.async_read_daylight_settings())
    await started.wait()
    second = asyncio.create_task(c.async_read_daylight_settings())
    await asyncio.sleep(0)
    c._request.assert_awaited_once()
    release.set()
    one, two = await asyncio.gather(first, second)
    assert one["read_status"] == "updated" and two == {**one, "read_status": "cooldown"}
    c._request.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("warm", [False, True])
async def test_cancelled_attempt_retains_sample_marks_error_and_propagates_with_completion_cooldown(client, warm):
    c, clock = client
    first = await c.async_read_daylight_settings() if warm else c.daylight_settings_snapshot()
    seen = c._daylight_settings.get("seen")
    clock.now += 15
    started = asyncio.Event()

    async def blocked(_):
        started.set()
        await asyncio.Event().wait()

    c._request.side_effect = blocked
    action = asyncio.create_task(c.async_read_daylight_settings())
    await started.wait()
    clock.now += 40
    action.cancel()
    with pytest.raises(asyncio.CancelledError):
        await action
    result = c.daylight_settings_snapshot()
    assert result["read_status"] == "error" and result["quality"] == ("stale" if warm else "unknown")
    assert result["error"] == {"type": "CancelledError", "code": None}
    assert result["raw_attributes"] == first["raw_attributes"]
    assert result["last_successful_read"] == first["last_successful_read"]
    assert c._daylight_settings.get("seen") == seen
    assert c._last_daylight_request == clock.now
    calls = c._request.await_count
    assert (await c.async_read_daylight_settings())["read_status"] == "cooldown"
    assert c._request.await_count == calls and not c._daylight_lock.locked()
    clock.now += 15
    c._request.side_effect = None
    recovered = await c.async_read_daylight_settings()
    assert recovered["quality"] == "fresh" and recovered["error"] is None


@pytest.mark.asyncio
async def test_cancelled_lock_wait_creates_no_attempt_or_cooldown(client):
    c, _ = client
    before = copy.deepcopy(c.daylight_settings_snapshot())
    async with c._daylight_lock:
        action = asyncio.create_task(c.async_read_daylight_settings())
        await asyncio.sleep(0)
        action.cancel()
        with pytest.raises(asyncio.CancelledError):
            await action
    assert c.daylight_settings_snapshot() == before
    assert c._last_daylight_request is None and c._daylight_settings == {}
    c._request.assert_not_awaited()


@pytest.mark.asyncio
async def test_expiry_and_cache_exports_preserve_raw_evidence_without_io_or_mutable_aliases(client):
    c, clock = client
    initial = c.daylight_settings_snapshot()
    assert set(initial) == KEYS and initial["quality"] == "unknown"
    assert initial["raw_attributes"] == {} and initial["last_attempt"] is None
    assert cached_snapshot(c)["daylight_settings"] == initial
    c._request.assert_not_awaited()
    c._request.return_value = reply({"CountryCode": "000"})
    result = await c.async_read_daylight_settings()
    expected = copy.deepcopy(result)
    result["raw_attributes"]["CountryCode"] = "caller-mutation"
    result["issues"][0]["key"] = "caller-mutation"
    exported = cached_snapshot(c)["daylight_settings"]
    assert exported == expected
    exported["raw_attributes"].clear()
    exported["issues"].clear()
    assert c.daylight_settings_snapshot() == expected
    clock.now += c.stale_after + 0.01
    assert c.daylight_settings_snapshot() == {**expected, "quality": "stale"}
    c._request.assert_awaited_once()
    c._request.side_effect = MelansConnectionError("private")
    result = await c.async_read_daylight_settings()
    result["error"]["type"] = "caller-mutation"
    exported = cached_snapshot(c)["daylight_settings"]
    exported["error"]["code"] = "caller-mutation"
    assert c.daylight_settings_snapshot()["error"] == {"type": "MelansConnectionError", "code": None}
    assert c._request.await_count == 2


@pytest.mark.asyncio
async def test_clock_and_daily_history_cooldowns_remain_independent(client):
    c, clock = client
    c._last_clock_request = c._last_member_runtime_request = clock.now
    assert (await c.async_read_daylight_settings())["read_status"] == "updated"
    assert c._last_clock_request == c._last_member_runtime_request == clock.now
    assert (await c.async_read_controller_clock())["read_status"] == "cooldown"
    c._last_clock_request = None
    c._request.return_value = (
        '<Packet><Command>getResponse</Command><DatabaseManager>'
        '<Clock Year="2024" Month="1" Day="1" Hour="0" Minute="0" Second="0" />'
        '</DatabaseManager></Packet>'
    )
    assert (await c.async_read_controller_clock())["read_status"] == "updated"
    assert c.daylight_settings_snapshot()["read_status"] == "updated"
    assert (await c.async_read_daylight_settings())["read_status"] == "cooldown"
    assert c._request.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "failure", "cancel"])
async def test_optional_attempt_does_not_poll_or_change_core_health_commands_or_other_caches(client, outcome):
    c, _ = client
    at = datetime(2025, 1, 1, tzinfo=UTC)
    c._names = {"1": None}
    c.devices["1"] = MelansDevice("1", None, {"Drive": "OFF", "Mode": "COOL"})
    c.last_attempt = c.last_success = at
    c.group_last_success = {"1": at}
    c._group_seen = {"1": 100.0}
    c.last_command = {"status": "outcome_unknown", "attributes": {"Drive": "ON"}}
    c._pending_writes = {"1": {"Drive": "ON"}}
    c._readback_requested = True
    before = copy.deepcopy(cached_snapshot(c))
    if outcome != "success":
        c._request.side_effect = (
            asyncio.CancelledError() if outcome == "cancel" else MelansConnectionError("private")
        )
    if outcome == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await c.async_read_daylight_settings()
    else:
        await c.async_read_daylight_settings()
    after = cached_snapshot(c)
    before.pop("daylight_settings")
    after.pop("daylight_settings")
    assert before == after
    assert c._pending_writes == {"1": {"Drive": "ON"}} and c._readback_requested
    assert c.devices["1"].raw_attributes() == {"Drive": "OFF", "Mode": "COOL"}
    c._request.assert_awaited_once()


@pytest.mark.asyncio
async def test_ordinary_poll_never_fetches_optional_daylight_configuration(client):
    c, _ = client
    c._names = {"1": None}
    c._request.return_value = (
        '<Packet><Command>getResponse</Command><DatabaseManager>'
        '<Mnet Group="1" Drive="OFF" /></DatabaseManager></Packet>'
    )
    await c.async_update()
    c._request.assert_awaited_once()
    assert ET.fromstring(c._request.call_args.args[0]).find(".//SummerTime") is None
    assert c.daylight_settings_snapshot()["last_attempt"] is None


@pytest.mark.asyncio
async def test_daylight_and_other_requests_share_transport_lock_and_spacing(client, monkeypatch):
    c, clock = client
    del c._request
    monkeypatch.setattr(const, "MIN_REQUEST_SPACING", 1.5)
    real_sleep = asyncio.sleep
    sent_at, active = [], 0

    async def advance(delay):
        clock.now += delay
        await real_sleep(0)

    async def respond(_):
        nonlocal active
        active += 1
        assert active == 1
        sent_at.append(clock.now)
        await real_sleep(0)
        active -= 1
        return reply()

    monkeypatch.setattr("pymelans.client.asyncio.sleep", advance)
    c._ws.request = AsyncMock(side_effect=respond)
    c._http.request = AsyncMock()
    async with c._io_lock:
        action = asyncio.create_task(c.async_read_daylight_settings())
        await real_sleep(0)
        c._ws.request.assert_not_awaited()
    ordinary = asyncio.create_task(c._request("<Packet><Command>getRequest</Command></Packet>"))
    result, _ = await asyncio.gather(action, ordinary)
    assert result["read_status"] == "updated" and c._ws.request.await_count == 2
    assert sent_at[1] - sent_at[0] >= 1.5
    c._http.request.assert_not_awaited()
