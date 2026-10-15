"""Explicit controller-clock evidence stays separate from climate and HA time."""

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

RAW = {"Year": "2024", "Month": "02", "Day": "29", "Hour": "09", "Minute": "05", "Second": "00"}
COMPONENTS = {"year": 2024, "month": 2, "day": 29, "hour": 9, "minute": 5, "second": 0}


def clock_reply(attributes=None, command="getResponse"):
    root = ET.Element("Packet")
    ET.SubElement(root, "Command").text = command
    ET.SubElement(ET.SubElement(root, "DatabaseManager"), "Clock", {
        **(RAW if attributes is None else attributes), "Name": "private-controller", "Timezone": "private-zone",
    })
    return ET.tostring(root, encoding="unicode")


@pytest.fixture
def client(monkeypatch):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr("pymelans.client.time", SimpleNamespace(
        monotonic=lambda: clock.now, perf_counter=lambda: clock.now,
    ))
    c = MelansClient("fixture.invalid", stale_after=20)
    c._names = {"1": "private-room", "2": None}
    c._request = AsyncMock(return_value=clock_reply())
    return c, clock


def assert_typed_unknown(snapshot):
    assert snapshot["components"] == dict.fromkeys(COMPONENTS)
    assert snapshot["local_datetime"] is None and snapshot["timezone"] is None


@pytest.mark.asyncio
async def test_clock_action_reads_only_six_fields_and_keeps_local_time_without_zone(client):
    c, _ = client
    result = await c.async_read_controller_clock()
    c._request.assert_awaited_once()
    root = ET.fromstring(c._request.call_args.args[0])
    assert root.findtext("./Command") == "getRequest"
    manager = root.find("./DatabaseManager")
    assert len(manager) == 1 and manager[0].tag == "Clock" and len(manager[0]) == 0
    assert manager[0].attrib == dict.fromkeys(RAW, "*")
    assert result["read_status"] == "updated" and result["quality"] == "fresh"
    assert result["last_attempt"] <= result["last_successful_read"]
    assert result["error"] is None and result["issues"] == []
    assert result["raw_attributes"] == RAW and result["components"] == COMPONENTS
    assert result["local_datetime"] == "2024-02-29T09:05:00" and result["timezone"] is None
    assert not ({"utc_datetime", "drift", "synced", "accurate", "schedule_date"} & result.keys())
    assert "private-" not in json.dumps(result)


@pytest.mark.asyncio
async def test_clock_read_does_not_discover_or_require_any_group_directory(client):
    c, _ = client
    c._names = {}
    result = await c.async_read_controller_clock()
    assert result["quality"] == "fresh" and not c.devices and not c._names
    c._request.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("attributes,key", [
    ({**RAW, "Year": "FUTURE"}, "year"),
    ({**RAW, "Month": "*"}, "month"),
    ({**RAW, "Day": "30"}, "day"),
    ({**RAW, "Hour": "24"}, "hour"),
    ({**RAW, "Minute": ""}, "minute"),
    ({key: value for key, value in RAW.items() if key != "Second"}, "second"),
])
async def test_valid_xml_with_unknown_components_is_updated_without_inventing_complete_time(client, attributes, key):
    c, _ = client
    c._request.return_value = clock_reply(attributes)
    result = await c.async_read_controller_clock()
    assert result["read_status"] == "updated" and result["quality"] == "fresh"
    assert result["error"] is None and result["last_successful_read"] is not None
    assert result["components"][key] is None and result["local_datetime"] is None
    assert result["raw_attributes"] == attributes and result["timezone"] is None
    assert any(issue["key"] == key.title() for issue in result["issues"])


@pytest.mark.asyncio
async def test_empty_clock_fields_remain_unknown_after_a_structurally_valid_read(client):
    c, _ = client
    c._request.return_value = clock_reply({})
    result = await c.async_read_controller_clock()
    assert result["read_status"] == "updated" and result["quality"] == "fresh"
    assert_typed_unknown(result)
    assert result["raw_attributes"] == {} and len(result["issues"]) == 6


@pytest.mark.asyncio
async def test_clock_cooldown_starts_after_slow_request_completion_and_does_not_renew_sample(client):
    c, clock = client

    async def delayed_reply(_):
        clock.now += 40
        return clock_reply()

    c._request.side_effect = delayed_reply
    first = await c.async_read_controller_clock()
    completed_at = clock.now
    clock.now = completed_at + 14.99
    second = await c.async_read_controller_clock()
    assert second["read_status"] == "cooldown"
    assert second["last_attempt"] == first["last_attempt"]
    assert second["last_successful_read"] == first["last_successful_read"]
    c._request.assert_awaited_once()
    clock.now = completed_at + 15
    c._request.side_effect = None
    assert (await c.async_read_controller_clock())["read_status"] == "updated"
    assert c._request.await_count == 2


@pytest.mark.asyncio
async def test_concurrent_clock_actions_cannot_bypass_controller_cooldown(client):
    c, clock = client
    started, release = asyncio.Event(), asyncio.Event()

    async def reply(_):
        started.set()
        await release.wait()
        clock.now += 40
        return clock_reply()

    c._request.side_effect = reply
    first = asyncio.create_task(c.async_read_controller_clock())
    await started.wait()
    second = asyncio.create_task(c.async_read_controller_clock())
    await asyncio.sleep(0)
    c._request.assert_awaited_once()
    release.set()
    first_result, second_result = await asyncio.gather(first, second)
    assert first_result["read_status"] == "updated" and second_result["read_status"] == "cooldown"
    assert second_result["last_successful_read"] == first_result["last_successful_read"]
    c._request.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancellation_propagates_releases_lock_and_applies_completion_cooldown(client):
    c, clock = client
    started = asyncio.Event()

    async def blocked(_):
        started.set()
        await asyncio.Event().wait()

    c._request.side_effect = blocked
    action = asyncio.create_task(c.async_read_controller_clock())
    await started.wait()
    action.cancel()
    with pytest.raises(asyncio.CancelledError):
        await action
    assert (await c.async_read_controller_clock())["read_status"] == "cooldown"
    c._request.assert_awaited_once()
    clock.now += 15
    c._request.side_effect = None
    assert (await c.async_read_controller_clock())["read_status"] == "updated"
    assert c._request.await_count == 2


@pytest.mark.asyncio
async def test_failed_clock_refresh_keeps_raw_and_success_time_but_withholds_typed_components(client):
    c, clock = client
    first = await c.async_read_controller_clock()
    clock.now += 15
    c._request.side_effect = MelansProtocolError("private-message", code="0101", point="private-point")
    result = await c.async_read_controller_clock()
    assert result["quality"] == "stale" and result["read_status"] == "error"
    assert result["last_successful_read"] == first["last_successful_read"]
    assert result["raw_attributes"] == RAW and result["issues"] == first["issues"]
    assert_typed_unknown(result)
    assert result["error"] == {"type": "MelansProtocolError", "code": "0101"}
    assert "private-" not in json.dumps(result)
    assert (await c.async_read_controller_clock())["read_status"] == "cooldown"
    assert c._request.await_count == 2


@pytest.mark.asyncio
async def test_first_read_failure_is_unknown_and_error_messages_or_unsafe_codes_are_discarded(client):
    c, _ = client
    c._request.side_effect = MelansProtocolError("http://private.invalid/secret", code="private.invalid/path")
    result = await c.async_read_controller_clock()
    assert result["read_status"] == "error" and result["quality"] == "unknown"
    assert result["last_attempt"] is not None and result["last_successful_read"] is None
    assert_typed_unknown(result)
    assert result["raw_attributes"] == {} and result["issues"] == []
    assert result["error"] == {"type": "MelansProtocolError", "code": None}
    assert "private" not in json.dumps(cached_snapshot(c))


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    clock_reply(command="notifyRequest"),
    '<Packet><Command>getResponse</Command><DatabaseManager><Clock /><Clock /></DatabaseManager></Packet>',
    '<Packet><Command>getResponse</Command><DatabaseManager><Clock>junk</Clock></DatabaseManager></Packet>',
    '<Packet><Command>getErrorResponse</Command><DatabaseManager>'
    '<ERROR Code="0101" Message="private-message" /><Clock Year="2024" /></DatabaseManager></Packet>',
], ids=["notification", "duplicate-clock", "mixed-content", "partial-error"])
async def test_invalid_clock_replies_do_not_replace_old_samples_or_trigger_more_reads(client, response):
    c, clock = client
    first = await c.async_read_controller_clock()
    clock.now += 15
    c._request.return_value = response
    result = await c.async_read_controller_clock()
    assert result["read_status"] == "error" and result["quality"] == "stale"
    assert result["last_successful_read"] == first["last_successful_read"]
    assert result["raw_attributes"] == RAW
    assert_typed_unknown(result)
    assert c._request.await_count == 2


@pytest.mark.asyncio
async def test_clock_cache_does_not_advance_time_or_renew_expired_samples(client):
    c, clock = client
    first = await c.async_read_controller_clock()
    clock.now += 5
    assert c.controller_clock_snapshot()["local_datetime"] == first["local_datetime"]
    clock.now += c.stale_after
    snapshot = c.controller_clock_snapshot()
    assert snapshot["quality"] == "stale" and snapshot["raw_attributes"] == RAW
    assert snapshot["last_successful_read"] == first["last_successful_read"]
    assert_typed_unknown(snapshot)
    c._request.assert_awaited_once()


@pytest.mark.asyncio
async def test_cache_exports_are_deep_copies_and_never_perform_clock_reads(client):
    c, clock = client
    initial = c.controller_clock_snapshot()
    assert initial["quality"] == "unknown" and initial["last_attempt"] is None
    assert_typed_unknown(initial)
    c._request.assert_not_called()
    c._request.return_value = clock_reply({**RAW, "Hour": "*"})
    result = await c.async_read_controller_clock()
    expected = copy.deepcopy(c.controller_clock_snapshot())
    result["raw_attributes"]["Hour"] = "0"
    result["components"]["year"] = 2000
    result["issues"][0]["kind"] = "caller-mutation"
    exported = cached_snapshot(c)["controller_clock"]
    assert exported == expected
    exported["raw_attributes"].clear()
    exported["issues"].clear()
    assert c.controller_clock_snapshot() == expected
    clock.now += 15
    c._request.side_effect = MelansConnectionError("private-message")
    result = await c.async_read_controller_clock()
    result["error"]["type"] = "caller-mutation"
    exported = cached_snapshot(c)["controller_clock"]
    exported["error"]["code"] = "caller-mutation"
    assert c.controller_clock_snapshot()["error"] == {"type": "MelansConnectionError", "code": None}
    assert c._request.await_count == 2


@pytest.mark.asyncio
async def test_clock_read_does_not_change_climate_health_group_clocks_commands_or_schedule_date(client):
    c, _ = client
    at = datetime(2025, 1, 1, tzinfo=UTC)
    c.devices["1"] = MelansDevice("1", None, {"Drive": "OFF", "Mode": "COOL"})
    c.last_attempt = c.last_success = at
    c.group_last_success = {"1": at}
    c._group_seen = {"1": 100.0}
    c.failed_groups = {"2"}
    c.last_command = {"group_id": "1", "attributes": {"Drive": "ON"}, "status": "accepted_unconfirmed"}
    c._pending_writes = {"1": {"Drive": "ON"}}
    c._readback_requested = True
    previous_command, previous_health = copy.deepcopy(c.last_command), c.health
    previous_schedules = copy.deepcopy(c.today_schedule_snapshot())
    await c.async_read_controller_clock()
    assert c.last_attempt == c.last_success == at
    assert c.group_last_success == {"1": at} and c._group_seen == {"1": 100.0}
    assert c.failed_groups == {"2"} and c.health == previous_health
    assert c.last_command == previous_command and c._pending_writes == {"1": {"Drive": "ON"}}
    assert c._readback_requested and c.devices["1"].raw_attributes() == {"Drive": "OFF", "Mode": "COOL"}
    assert c.today_schedule_snapshot() == previous_schedules


@pytest.mark.asyncio
async def test_regular_poll_does_not_fetch_optional_clock(client):
    c, _ = client
    c._request.return_value = (
        '<Packet><Command>getResponse</Command><DatabaseManager>'
        '<Mnet Group="1" Drive="OFF" /><Mnet Group="2" Drive="OFF" />'
        '</DatabaseManager></Packet>'
    )
    await c.async_update()
    c._request.assert_awaited_once()
    assert ET.fromstring(c._request.call_args.args[0]).find(".//Clock") is None
    assert c.controller_clock_snapshot()["last_attempt"] is None


@pytest.mark.asyncio
async def test_clock_reuses_common_controller_io_lock_and_request_spacing(client, monkeypatch):
    c, clock = client
    del c._request
    monkeypatch.setattr(const, "MIN_REQUEST_SPACING", 1.5)
    real_sleep = asyncio.sleep
    sent_at, active = [], 0

    async def advance(delay):
        clock.now += delay
        await real_sleep(0)

    monkeypatch.setattr("pymelans.client.asyncio", SimpleNamespace(sleep=advance))

    async def reply(_):
        nonlocal active
        active += 1
        assert active == 1
        sent_at.append(clock.now)
        await real_sleep(0)
        active -= 1
        return clock_reply()

    c._ws.request = AsyncMock(side_effect=reply)
    c._http.request = AsyncMock()
    async with c._io_lock:
        action = asyncio.create_task(c.async_read_controller_clock())
        await real_sleep(0)
        c._ws.request.assert_not_awaited()
    ordinary = asyncio.create_task(c._request(
        '<Packet><Command>getRequest</Command><DatabaseManager><SystemData Version="*" />'
        '</DatabaseManager></Packet>'
    ))
    result, _ = await asyncio.gather(action, ordinary)
    assert result["read_status"] == "updated" and c._ws.request.await_count == 2
    assert sent_at[1] - sent_at[0] >= 1.5
    c._http.request.assert_not_awaited()
