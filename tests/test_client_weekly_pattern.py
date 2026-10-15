"""Selected weekly-plan evidence has independent caches and shared throttling."""

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

PLAN = {"Index": "1", "Hour": "9", "Minute": "30", "Drive": "ON", "Mode": "COOL"}


def weekly_reply(*records, group="1", season="2", pattern="3", command="getResponse", **extra):
    root = ET.Element("Packet")
    ET.SubElement(root, "Command").text = command
    control = ET.SubElement(ET.SubElement(root, "DatabaseManager"), "ScheduleControl")
    directory = ET.SubElement(control, "WPatternList", {
        "Group": group, "Season": season, "Pattern": pattern, "Name": "private-room", **extra,
    })
    for attributes in records:
        ET.SubElement(directory, "WPatternRecord", attributes)
    return ET.tostring(root, encoding="unicode")


@pytest.fixture
def client(monkeypatch):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr("pymelans.client.time", SimpleNamespace(
        monotonic=lambda: clock.now, perf_counter=lambda: clock.now,
    ))
    c = MelansClient("fixture.invalid", stale_after=20)
    c._names = {"1": "private-room", "2": None}
    c._request = AsyncMock(return_value=weekly_reply(PLAN))
    return c, clock


def snapshot(c, group="1", season="2", pattern="3"):
    return c.weekly_pattern_snapshot(group, season, pattern)


@pytest.mark.asyncio
async def test_action_uses_one_explicit_group_season_pattern_get_without_scanning(client):
    c, _ = client
    result = await c.async_read_weekly_pattern("001", "02", "003")
    c._request.assert_awaited_once()
    root = ET.fromstring(c._request.call_args.args[0])
    assert root.findtext("./Command") == "getRequest"
    manager = root.find("./DatabaseManager")
    assert len(manager) == 1 and manager[0].tag == "ScheduleControl"
    assert len(manager[0]) == 1 and manager[0][0].tag == "WPatternList"
    assert manager[0][0].attrib == {"Group": "1", "Season": "2", "Pattern": "3"}
    assert len(manager[0][0]) == 0
    assert {key: result[key] for key in ("group_id", "season", "pattern")} == {
        "group_id": "1", "season": "2", "pattern": "3",
    }
    assert result["read_status"] == "updated" and result["quality"] == "fresh"
    assert result["last_attempt"] <= result["last_successful_read"] and result["error"] is None
    assert result["records"] == [{"index": "1", "hour": 9, "minute": 30, "raw_attributes": PLAN, "issues": []}]
    assert result["issues"] == [] and "private-" not in json.dumps(result)
    assert not ({"enabled", "weekday", "date", "timezone", "next_run", "is_on"} & result.keys())


@pytest.mark.asyncio
@pytest.mark.parametrize("values", [
    (0, 2, 3), (51, 2, 3), (3, 2, 3), (True, 2, 3), ("*", 2, 3), ("1,2", 2, 3),
    ("١", 2, 3), (1, 0, 3), (1, 6, 3), (1, "*", 3), (1, 2, 0), (1, 2, 8), (1, 2, "3.0"),
])
async def test_unknown_groups_or_invalid_selectors_do_not_probe_or_mutate_cache(client, values):
    c, _ = client
    before = copy.deepcopy(c.weekly_pattern_snapshot())
    with pytest.raises(ValueError):
        await c.async_read_weekly_pattern(*values)
    c._request.assert_not_called()
    assert c.weekly_pattern_snapshot() == before


@pytest.mark.parametrize("values", [("1", None, None), (None, "2", "3"), ("1", "2", None)])
def test_partial_snapshot_selector_is_rejected_without_io(client, values):
    c, _ = client
    with pytest.raises(ValueError):
        c.weekly_pattern_snapshot(*values)
    c._request.assert_not_called()


@pytest.mark.asyncio
async def test_empty_list_is_valid_evidence_without_claiming_activation_or_support(client):
    c, _ = client
    c._request.return_value = weekly_reply()
    result = await c.async_read_weekly_pattern("1", "2", "3")
    assert result["read_status"] == "updated" and result["quality"] == "fresh"
    assert result["last_successful_read"] is not None and result["error"] is None
    assert result["records"] == result["issues"] == []
    assert "enabled" not in result and "supported" not in result
    c._request.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_and_conflicting_rows_export_raw_source_order_and_safe_issues(client):
    c, _ = client
    c._request.return_value = weekly_reply(
        {**PLAN, "Index": "2", "Drive": "", "Mode": "*", "Hour": "18"},
        {**PLAN, "Drive": "FUTURE", "Hour": "*"},
        {**PLAN, "Index": "01", "Drive": "OFF", "Hour": "*"},
        {**PLAN, "Index": "0", "Minute": "FUTURE", "Password": "secret"},
    )
    result = await c.async_read_weekly_pattern("1", "2", "3")
    assert result["read_status"] == "updated" and result["quality"] == "fresh"
    assert [row["index"] for row in result["records"]] == ["2", None, None, None]
    assert result["records"][0]["raw_attributes"]["Drive"] == ""
    assert result["records"][0]["raw_attributes"]["Mode"] == "*"
    assert result["records"][1]["raw_attributes"]["Drive"] == "FUTURE"
    assert result["records"][1]["hour"] is result["records"][2]["hour"] is None
    assert "conflicting_index" in result["records"][1]["issues"]
    assert "invalid_index" in result["records"][3]["issues"]
    assert any(issue == {"kind": "invalid_index", "record_index": 3, "key": "Index"} for issue in result["issues"])
    assert "secret" not in json.dumps(result)


@pytest.mark.asyncio
async def test_changing_pattern_season_or_group_cannot_bypass_completion_based_cooldown(client):
    c, clock = client

    async def delayed_reply(_):
        clock.now += 40
        return weekly_reply(PLAN)

    c._request.side_effect = delayed_reply
    first = await c.async_read_weekly_pattern("1", "2", "3")
    completed_at = clock.now
    clock.now = completed_at + 14.99
    for values in (("1", "2", "3"), ("1", "2", "4"), ("1", "3", "3"), ("2", "2", "3")):
        result = await c.async_read_weekly_pattern(*values)
        assert result["read_status"] == "cooldown"
        if values != ("1", "2", "3"):
            assert result["last_attempt"] is None
    assert snapshot(c)["last_successful_read"] == first["last_successful_read"]
    c._request.assert_awaited_once()
    clock.now = completed_at + 15
    c._request.side_effect = None
    c._request.return_value = weekly_reply(pattern="4")
    assert (await c.async_read_weekly_pattern("1", "2", "4"))["read_status"] == "updated"
    assert c._request.await_count == 2


@pytest.mark.asyncio
async def test_concurrent_patterns_share_the_controller_action_lock_and_cooldown(client):
    c, clock = client
    started, release = asyncio.Event(), asyncio.Event()

    async def reply(_):
        started.set()
        await release.wait()
        clock.now += 40
        return weekly_reply(PLAN)

    c._request.side_effect = reply
    first = asyncio.create_task(c.async_read_weekly_pattern("1", "2", "3"))
    await started.wait()
    second = asyncio.create_task(c.async_read_weekly_pattern("1", "2", "4"))
    await asyncio.sleep(0)
    c._request.assert_awaited_once()
    release.set()
    first_result, second_result = await asyncio.gather(first, second)
    assert first_result["read_status"] == "updated"
    assert second_result["read_status"] == "cooldown" and second_result["last_attempt"] is None
    c._request.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancellation_propagates_releases_lock_and_starts_completion_cooldown(client):
    c, clock = client
    started = asyncio.Event()

    async def blocked(_):
        started.set()
        await asyncio.Event().wait()

    c._request.side_effect = blocked
    action = asyncio.create_task(c.async_read_weekly_pattern("1", "2", "3"))
    await started.wait()
    action.cancel()
    with pytest.raises(asyncio.CancelledError):
        await action
    assert (await c.async_read_weekly_pattern("1", "2", "4"))["read_status"] == "cooldown"
    c._request.assert_awaited_once()
    clock.now += 15
    c._request.side_effect = None
    c._request.return_value = weekly_reply(pattern="4")
    assert (await c.async_read_weekly_pattern("1", "2", "4"))["read_status"] == "updated"
    assert c._request.await_count == 2


@pytest.mark.asyncio
async def test_selectors_have_independent_freshness_errors_and_raw_samples(client):
    c, clock = client
    first = await c.async_read_weekly_pattern("1", "2", "3")
    clock.now += 15
    second_plan = {**PLAN, "Hour": "18", "Drive": "OFF"}
    c._request.return_value = weekly_reply(second_plan, pattern="4")
    await c.async_read_weekly_pattern("1", "2", "4")
    clock.now += 6
    assert snapshot(c)["quality"] == "stale" and snapshot(c)["records"][0]["hour"] is None
    assert snapshot(c)["last_successful_read"] == first["last_successful_read"]
    assert snapshot(c, pattern="4")["quality"] == "fresh"
    assert snapshot(c, pattern="4")["records"][0]["hour"] == 18
    clock.now += 9
    c._request.side_effect = MelansProtocolError("private-message", code="0101", point="private-point")
    result = await c.async_read_weekly_pattern("1", "2", "3")
    assert result["read_status"] == "error" and result["quality"] == "stale"
    assert result["error"] == {"type": "MelansProtocolError", "code": "0101"}
    assert result["records"][0]["raw_attributes"] == PLAN
    assert result["records"][0]["hour"] is result["records"][0]["minute"] is None
    assert result["last_successful_read"] == first["last_successful_read"]
    assert snapshot(c, pattern="4")["quality"] == "fresh"
    assert snapshot(c, pattern="4")["error"] is None
    assert snapshot(c, pattern="4")["records"][0]["raw_attributes"] == second_plan
    assert "private-" not in json.dumps(result)


@pytest.mark.asyncio
async def test_first_failure_remains_unknown_and_unsafe_errors_are_sanitised(client):
    c, _ = client
    c._request.side_effect = MelansProtocolError("http://private.invalid/secret", code="private.invalid/path")
    result = await c.async_read_weekly_pattern("1", "2", "3")
    assert result["read_status"] == "error" and result["quality"] == "unknown"
    assert result["last_attempt"] is not None and result["last_successful_read"] is None
    assert result["records"] == result["issues"] == []
    assert result["error"] == {"type": "MelansProtocolError", "code": None}
    assert "private" not in json.dumps(cached_snapshot(c))


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    weekly_reply(PLAN, group="2"), weekly_reply(PLAN, season="1"), weekly_reply(PLAN, pattern="4"),
    weekly_reply(PLAN, season="02"), weekly_reply(PLAN, MultiGroup=""),
    weekly_reply(PLAN, Address=""), weekly_reply(PLAN, command="notifyRequest"),
    weekly_reply(PLAN).replace("<ScheduleControl>", '<ERROR Code="0101" Message="private" /><ScheduleControl>'),
    weekly_reply(PLAN).replace("</WPatternList>", "junk</WPatternList>"),
], ids=["group", "season", "pattern", "padded-key", "aggregate", "address", "notification", "partial-error", "mixed-text"])
async def test_rejected_original_xml_cannot_replace_cache_via_adapted_row_parser(client, response):
    c, clock = client
    first = await c.async_read_weekly_pattern("1", "2", "3")
    clock.now += 15
    c._request.return_value = response
    result = await c.async_read_weekly_pattern("1", "2", "3")
    assert result["quality"] == "stale" and result["read_status"] == "error"
    assert result["last_successful_read"] == first["last_successful_read"]
    assert result["records"][0]["raw_attributes"] == PLAN
    assert result["records"][0]["hour"] is result["records"][0]["minute"] is None
    assert c._request.await_count == 2


@pytest.mark.asyncio
async def test_nested_exports_are_deep_copies_and_cache_access_performs_no_io(client):
    c, clock = client
    assert c.weekly_pattern_snapshot() == {}
    assert snapshot(c)["quality"] == "unknown"
    c._request.assert_not_called()
    c._request.return_value = weekly_reply({**PLAN, "Index": "*"})
    action = await c.async_read_weekly_pattern("1", "2", "3")
    expected = copy.deepcopy(c.weekly_pattern_snapshot())
    assert expected["1"]["2"]["3"] == snapshot(c)
    action["records"][0]["raw_attributes"]["Hour"] = "0"
    action["records"][0]["issues"].append("caller-mutation")
    action["issues"][0]["kind"] = "caller-mutation"
    exported = cached_snapshot(c)["weekly_patterns"]
    assert exported == expected
    exported["1"]["2"]["3"]["records"][0]["raw_attributes"].clear()
    exported["1"]["2"]["3"]["issues"].clear()
    assert c.weekly_pattern_snapshot() == expected
    clock.now += 15
    c._request.side_effect = MelansConnectionError("private-message")
    action = await c.async_read_weekly_pattern("1", "2", "3")
    action["error"]["type"] = "caller-mutation"
    exported = cached_snapshot(c)["weekly_patterns"]
    exported["1"]["2"]["3"]["error"]["code"] = "caller-mutation"
    assert snapshot(c)["error"] == {"type": "MelansConnectionError", "code": None}
    assert c._request.await_count == 2


@pytest.mark.asyncio
async def test_weekly_read_does_not_change_climate_clocks_commands_or_other_optional_caches(client):
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
    previous = (copy.deepcopy(c.last_command), c.health, copy.deepcopy(c.today_schedule_snapshot()),
                copy.deepcopy(c.controller_clock_snapshot()), copy.deepcopy(c.control_context_snapshot()))
    await c.async_read_weekly_pattern("1", "2", "3")
    assert c.last_attempt == c.last_success == at
    assert c.group_last_success == {"1": at} and c._group_seen == {"1": 100.0}
    assert c.failed_groups == {"2"} and c.health == previous[1]
    assert c.last_command == previous[0] and c._pending_writes == {"1": {"Drive": "ON"}}
    assert c._readback_requested and c.devices["1"].raw_attributes() == {"Drive": "OFF", "Mode": "COOL"}
    assert c.today_schedule_snapshot() == previous[2]
    assert c.controller_clock_snapshot() == previous[3]
    assert c.control_context_snapshot() == previous[4]


@pytest.mark.asyncio
async def test_regular_climate_poll_does_not_read_weekly_patterns(client):
    c, _ = client
    c._request.return_value = (
        '<Packet><Command>getResponse</Command><DatabaseManager>'
        '<Mnet Group="1" Drive="OFF" /><Mnet Group="2" Drive="OFF" />'
        '</DatabaseManager></Packet>'
    )
    await c.async_update()
    c._request.assert_awaited_once()
    assert ET.fromstring(c._request.call_args.args[0]).find(".//ScheduleControl") is None
    assert c.weekly_pattern_snapshot() == {}


@pytest.mark.asyncio
async def test_weekly_read_uses_common_controller_io_lock_and_request_spacing(client, monkeypatch):
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
        return weekly_reply(PLAN)

    c._ws.request = AsyncMock(side_effect=reply)
    c._http.request = AsyncMock()
    async with c._io_lock:
        action = asyncio.create_task(c.async_read_weekly_pattern("1", "2", "3"))
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
