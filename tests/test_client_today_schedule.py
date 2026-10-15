"""Optional TodayList reads cannot become polls, commands or fresh old plans."""

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


def schedule_reply(*records, group="1", multi_group=None, command="getResponse"):
    root = ET.Element("Packet")
    ET.SubElement(root, "Command").text = command
    control = ET.SubElement(ET.SubElement(root, "DatabaseManager"), "ScheduleControl")
    attributes = {"Group": group, "Name": "private-room"}
    if multi_group is not None:
        attributes["MultiGroup"] = multi_group
    directory = ET.SubElement(control, "TodayList", attributes)
    for attributes in records:
        ET.SubElement(directory, "TodayRecord", attributes)
    return ET.tostring(root, encoding="unicode")


PLAN = {"Index": "1", "Hour": "9", "Minute": "30", "Drive": "ON", "Mode": "COOL"}


@pytest.fixture
def client(monkeypatch):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr("pymelans.client.time", SimpleNamespace(
        monotonic=lambda: clock.now, perf_counter=lambda: clock.now,
    ))
    c = MelansClient("fixture.invalid", stale_after=20)
    c._names = {"1": "private-room", "2": None}
    c._request = AsyncMock(return_value=schedule_reply(PLAN))
    return c, clock


@pytest.mark.asyncio
async def test_action_reads_one_minimal_configured_group_and_exports_raw_plan(client):
    c, _ = client
    result = await c.async_read_today_schedule("001")
    c._request.assert_awaited_once()
    root = ET.fromstring(c._request.call_args.args[0])
    assert root.findtext("./Command") == "getRequest"
    manager = root.find("./DatabaseManager")
    assert len(manager) == 1 and manager[0].tag == "ScheduleControl"
    assert len(manager[0]) == 1 and manager[0][0].tag == "TodayList"
    assert manager[0][0].attrib == {"Group": "1"} and len(manager[0][0]) == 0
    assert result["group_id"] == "1" and result["read_status"] == "updated"
    assert result["quality"] == "fresh" and result["error"] is None
    assert result["last_attempt"] <= result["last_successful_read"]
    assert result["records"] == [{
        "index": "1", "hour": 9, "minute": 30, "raw_attributes": PLAN, "issues": [],
    }]
    assert result["issues"] == []
    assert not ({"enabled", "next_run", "date", "timezone", "is_on"} & result.keys())
    assert "private-" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("group", [0, 51, True, "*", "", "3", "1,2", "1.0", "١", " 1"])
async def test_unknown_and_invalid_groups_do_not_probe_or_mutate_cache(client, group):
    c, _ = client
    before = copy.deepcopy(c.today_schedule_snapshot())
    with pytest.raises(ValueError):
        await c.async_read_today_schedule(group)
    c._request.assert_not_called()
    assert c.today_schedule_snapshot() == before


@pytest.mark.asyncio
async def test_empty_today_list_is_a_successful_empty_snapshot_without_enablement_claim(client):
    c, _ = client
    c._request.return_value = schedule_reply()
    result = await c.async_read_today_schedule("1")
    assert result["quality"] == "fresh" and result["read_status"] == "updated"
    assert result["last_successful_read"] is not None and result["error"] is None
    assert result["records"] == result["issues"] == []
    assert "enabled" not in result and "supported" not in result
    c._request.assert_awaited_once()


@pytest.mark.asyncio
async def test_row_issues_and_unknown_actions_survive_export_without_current_state_aliases(client):
    c, _ = client
    rows = [
        {**PLAN, "Index": "2", "Drive": "", "Mode": "*"},
        {**PLAN, "Drive": "FUTURE", "Hour": "*"},
        {**PLAN, "Index": "01", "Drive": "OFF", "Hour": "*"},
        {**PLAN, "Index": "0", "Minute": "FUTURE", "Password": "secret"},
    ]
    c._request.return_value = schedule_reply(*rows)
    result = await c.async_read_today_schedule("1")
    assert result["read_status"] == "updated" and result["quality"] == "fresh"
    assert [record["index"] for record in result["records"]] == ["2", None, None, None]
    assert result["records"][0]["raw_attributes"]["Drive"] == ""
    assert result["records"][0]["raw_attributes"]["Mode"] == "*"
    assert result["records"][1]["raw_attributes"]["Drive"] == "FUTURE"
    assert result["records"][1]["hour"] is result["records"][2]["hour"] is None
    assert "conflicting_index" in result["records"][1]["issues"]
    assert "conflicting_index" in result["records"][2]["issues"]
    assert "invalid_index" in result["records"][3]["issues"]
    assert result["records"][3]["minute"] is None
    assert any(issue == {"kind": "invalid_index", "record_index": 3, "key": "Index"}
               for issue in result["issues"])
    assert "secret" not in json.dumps(result)
    assert all("is_on" not in record and "hvac_mode" not in record for record in result["records"])


@pytest.mark.asyncio
async def test_cooldown_is_controller_wide_and_starts_at_request_completion(client):
    c, clock = client

    async def delayed_reply(_):
        clock.now += 40
        return schedule_reply(PLAN)

    c._request.side_effect = delayed_reply
    first = await c.async_read_today_schedule("1")
    completed_at = clock.now
    clock.now = completed_at + 14.99
    for group in ("1", "2"):
        assert (await c.async_read_today_schedule(group))["read_status"] == "cooldown"
    c._request.assert_awaited_once()
    assert c.today_schedule_snapshot("1")["last_successful_read"] == first["last_successful_read"]
    assert c.today_schedule_snapshot("2")["last_attempt"] is None
    clock.now = completed_at + 15
    c._request.side_effect = None
    c._request.return_value = schedule_reply(group="2")
    assert (await c.async_read_today_schedule("2"))["read_status"] == "updated"
    assert c._request.await_count == 2


@pytest.mark.asyncio
async def test_concurrent_group_actions_share_one_cooldown_and_do_not_read_a_directory(client):
    c, clock = client
    started, release = asyncio.Event(), asyncio.Event()

    async def reply(_):
        started.set()
        await release.wait()
        clock.now += 40
        return schedule_reply(PLAN)

    c._request.side_effect = reply
    first = asyncio.create_task(c.async_read_today_schedule("1"))
    await started.wait()
    second = asyncio.create_task(c.async_read_today_schedule("2"))
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
    action = asyncio.create_task(c.async_read_today_schedule("1"))
    await started.wait()
    action.cancel()
    with pytest.raises(asyncio.CancelledError):
        await action
    c._request.assert_awaited_once()
    assert (await c.async_read_today_schedule("2"))["read_status"] == "cooldown"
    c._request.assert_awaited_once()
    clock.now += 15
    c._request.side_effect = None
    c._request.return_value = schedule_reply(group="2")
    assert (await c.async_read_today_schedule("2"))["read_status"] == "updated"
    assert c._request.await_count == 2


@pytest.mark.asyncio
async def test_failed_refresh_keeps_old_raw_but_marks_sample_stale_and_time_unknown(client):
    c, clock = client
    first = await c.async_read_today_schedule("1")
    clock.now += 15
    c._request.side_effect = MelansProtocolError("private-controller-message", code="0101", point="private-point")
    result = await c.async_read_today_schedule("1")
    assert result["quality"] == "stale" and result["read_status"] == "error"
    assert result["last_successful_read"] == first["last_successful_read"]
    assert result["records"][0]["raw_attributes"] == PLAN
    assert result["records"][0]["hour"] is result["records"][0]["minute"] is None
    assert result["error"] == {"type": "MelansProtocolError", "code": "0101"}
    assert (await c.async_read_today_schedule("2"))["read_status"] == "cooldown"
    assert c._request.await_count == 2
    assert "private-" not in json.dumps(result)


@pytest.mark.asyncio
async def test_first_failure_is_unknown_and_unsafe_error_code_is_not_exported(client):
    c, _ = client
    c._request.side_effect = MelansProtocolError("http://private.invalid/secret", code="private.invalid/path")
    result = await c.async_read_today_schedule("1")
    assert result["quality"] == "unknown" and result["read_status"] == "error"
    assert result["last_attempt"] is not None and result["last_successful_read"] is None
    assert result["records"] == result["issues"] == []
    assert result["error"] == {"type": "MelansProtocolError", "code": None}
    assert "private" not in json.dumps(cached_snapshot(c))


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    schedule_reply(PLAN, group="2"),
    schedule_reply(PLAN, multi_group="*"),
    schedule_reply(*([PLAN] * 25)),
    schedule_reply(PLAN, command="notifyRequest"),
    '<Packet><Command>getErrorResponse</Command><DatabaseManager>'
    '<ERROR Code="0101" Message="private-message" />'
    '<ScheduleControl><TodayList Group="1"><TodayRecord Index="1" Hour="1" Minute="0" />'
    '</TodayList></ScheduleControl></DatabaseManager></Packet>',
], ids=["wrong-group", "unverified-multigroup", "too-many-records", "notification", "partial-error"])
async def test_invalid_responses_never_replace_old_rows_or_trigger_more_queries(client, response):
    c, clock = client
    first = await c.async_read_today_schedule("1")
    clock.now += 15
    c._request.return_value = response
    result = await c.async_read_today_schedule("1")
    assert result["read_status"] == "error" and result["quality"] == "stale"
    assert result["last_successful_read"] == first["last_successful_read"]
    assert result["records"][0]["raw_attributes"] == PLAN
    assert result["records"][0]["hour"] is result["records"][0]["minute"] is None
    assert c._request.await_count == 2


@pytest.mark.asyncio
async def test_sample_expiry_withholds_typed_time_but_keeps_evidence_without_io(client):
    c, clock = client
    first = await c.async_read_today_schedule("1")
    clock.now += c.stale_after + 0.01
    result = c.today_schedule_snapshot("1")
    assert result["quality"] == "stale" and result["last_successful_read"] == first["last_successful_read"]
    assert result["records"][0]["raw_attributes"] == PLAN
    assert result["records"][0]["hour"] is result["records"][0]["minute"] is None
    c._request.assert_awaited_once()


@pytest.mark.asyncio
async def test_refreshing_another_group_does_not_renew_previous_group_plan(client):
    c, clock = client
    first = await c.async_read_today_schedule("1")
    clock.now += 15
    c._request.return_value = schedule_reply(group="2")
    await c.async_read_today_schedule("2")
    clock.now += 6
    assert c.today_schedule_snapshot("1")["quality"] == "stale"
    assert c.today_schedule_snapshot("1")["last_successful_read"] == first["last_successful_read"]
    assert c.today_schedule_snapshot("2")["quality"] == "fresh"
    assert c._request.await_count == 2


@pytest.mark.asyncio
async def test_all_cache_exports_are_deep_copies_and_never_perform_io(client):
    c, clock = client
    initial = c.today_schedule_snapshot("1")
    assert initial["quality"] == "unknown" and initial["last_attempt"] is None
    c._request.assert_not_called()
    c._request.return_value = schedule_reply({**PLAN, "Index": "*"})
    action = await c.async_read_today_schedule("1")
    expected = copy.deepcopy(c.today_schedule_snapshot())
    action["records"][0]["raw_attributes"]["Hour"] = "0"
    action["records"][0]["issues"].append("caller-mutation")
    action["issues"][0]["kind"] = "caller-mutation"
    exported = cached_snapshot(c)["today_schedules"]
    assert exported == expected
    exported["1"]["records"][0]["raw_attributes"]["Drive"] = "OFF"
    exported["1"]["issues"].clear()
    assert c.today_schedule_snapshot() == expected
    clock.now += 15
    c._request.side_effect = MelansConnectionError("private-message")
    action = await c.async_read_today_schedule("1")
    action["error"]["type"] = "caller-mutation"
    export = cached_snapshot(c)["today_schedules"]
    export["1"]["error"]["code"] = "caller-mutation"
    assert c.today_schedule_snapshot("1")["error"] == {"type": "MelansConnectionError", "code": None}
    assert c._request.await_count == 2


@pytest.mark.asyncio
async def test_plan_read_cannot_refresh_climate_clocks_or_confirm_pending_commands(client):
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
    await c.async_read_today_schedule("1")
    assert c.last_attempt == c.last_success == at
    assert c.group_last_success == {"1": at} and c._group_seen == {"1": 100.0}
    assert c.failed_groups == {"2"} and c.health == previous_health
    assert c.last_command == previous_command and c._pending_writes == {"1": {"Drive": "ON"}}
    assert c._readback_requested and c.devices["1"].raw_attributes() == {"Drive": "OFF", "Mode": "COOL"}


@pytest.mark.asyncio
async def test_regular_climate_poll_does_not_fetch_schedule_or_refresh_its_cache(client):
    c, _ = client
    c._request.return_value = (
        '<Packet><Command>getResponse</Command><DatabaseManager>'
        '<Mnet Group="1" Drive="OFF" /><Mnet Group="2" Drive="OFF" />'
        '</DatabaseManager></Packet>'
    )
    await c.async_update()
    c._request.assert_awaited_once()
    assert ET.fromstring(c._request.call_args.args[0]).find(".//ScheduleControl") is None
    assert c.today_schedule_snapshot() == {}


@pytest.mark.asyncio
async def test_schedule_read_uses_common_controller_io_lock_and_spacing(client, monkeypatch):
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
        return schedule_reply(PLAN)

    c._ws.request = AsyncMock(side_effect=reply)
    c._http.request = AsyncMock()
    async with c._io_lock:
        action = asyncio.create_task(c.async_read_today_schedule("1"))
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
