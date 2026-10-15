"""Cancelled optional refreshes preserve evidence without claiming success."""

from __future__ import annotations

import asyncio
import copy
import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pymelans.exceptions import MelansProtocolError
from pymelans.topology import parse_membership
from test_client_control_context import global_reply, group_reply
from test_client_controller_clock import clock_reply
from test_client_today_schedule import PLAN, schedule_reply
from test_client_topology import DIRECTORY, STATE, address_packet
from test_client_weekly_pattern import weekly_reply

from pymelans import MelansClient, MelansDevice, cached_snapshot

FAMILIES = ("clock", "today", "weekly", "member", "context")
LOCKS = {
    "clock": "_clock_lock", "today": "_today_schedule_lock", "weekly": "_weekly_pattern_lock",
    "member": "_member_lock", "context": "_control_context_lock",
}
COMPLETIONS = {
    "clock": "_last_clock_request", "today": "_last_today_schedule_request",
    "weekly": "_last_weekly_pattern_request", "member": "_last_member_request",
    "context": "_last_control_context_request",
}
CANCELLED = {"type": "CancelledError", "code": None}


def response(payload):
    root = ET.fromstring(payload)
    if root.find(".//Clock") is not None:
        return clock_reply()
    if (node := root.find(".//TodayList")) is not None:
        return schedule_reply(PLAN, group=node.get("Group"))
    if (node := root.find(".//WPatternList")) is not None:
        return weekly_reply(PLAN, group=node.get("Group"), season=node.get("Season"), pattern=node.get("Pattern"))
    if root.find(".//SystemData") is not None:
        return global_reply()
    node = root.find(".//Mnet")
    if "Address" in node.attrib:
        return address_packet({**STATE, "Address": node.get("Address")})
    return group_reply(group=node.get("Group"))


def action(c, family, *, other=False):
    group = "2" if other else "1"
    if family == "clock":
        return c.async_read_controller_clock()
    if family == "today":
        return c.async_read_today_schedule(group)
    if family == "weekly":
        return c.async_read_weekly_pattern(group, "2", "4" if other else "3")
    if family == "member":
        return c.async_read_member(group)
    return c.async_read_control_context(group)


def selected(c, family, *, other=False):
    group = "2" if other else "1"
    if family == "clock":
        return c.controller_clock_snapshot()
    if family == "today":
        return c.today_schedule_snapshot(group)
    if family == "weekly":
        return c.weekly_pattern_snapshot(group, "2", "4" if other else "3")
    if family == "member":
        return cached_snapshot(c)["members"].get(group)
    return c.control_context_snapshot(group)


def raw_rows(snapshot):
    return [row["raw_attributes"] for row in snapshot["records"]]


@pytest.fixture
def client(monkeypatch):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr("pymelans.client.time", SimpleNamespace(
        monotonic=lambda: clock.now, perf_counter=lambda: clock.now,
    ))
    c = MelansClient("fixture.invalid", stale_after=20)
    c._names = {"1": None, "2": None}
    c.topology = parse_membership(DIRECTORY, expected_groups=c._names)
    c._request = AsyncMock(side_effect=response)
    return c, clock


@pytest.mark.asyncio
@pytest.mark.parametrize("family", FAMILIES[:-1])
@pytest.mark.parametrize("warm", [False, True])
async def test_single_read_cancel_is_visible_with_preserved_raw_and_success_time(client, family, warm):
    c, clock = client
    first = None
    if warm:
        await action(c, family)
        first = copy.deepcopy(selected(c, family))
        clock.now += 15
        assert selected(c, family)["quality"] == "fresh"
    c._request.reset_mock()
    c._request.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await action(c, family)
    result = selected(c, family)
    assert result["read_status"] == "error" and result["error"] == CANCELLED
    assert result["quality"] == ("stale" if warm else "unknown")
    assert result["last_attempt"] is not None
    assert result["last_successful_read"] == (first["last_successful_read"] if warm else None)
    if family in ("today", "weekly"):
        assert raw_rows(result) == (raw_rows(first) if warm else [])
        assert all(row["hour"] is None and row["minute"] is None for row in result["records"])
    else:
        assert result["raw_attributes"] == (first["raw_attributes"] if warm else {})
    if family == "clock":
        assert all(value is None for value in result["components"].values())
        assert result["local_datetime"] is None
    assert result["issues"] == (first["issues"] if warm else [])
    c._request.assert_awaited_once()
    # The safe error is independently copied by the cached exporter.
    result["error"]["type"] = "caller-mutation"
    assert selected(c, family)["error"] == CANCELLED


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", [1, 2], ids=["global", "group"])
@pytest.mark.parametrize("warm", [False, True])
async def test_context_cancel_marks_only_current_read_and_retains_successful_part(client, stage, warm):
    c, clock = client
    if warm:
        await action(c, "context")
        clock.now += 15
    before = copy.deepcopy(selected(c, "context"))
    c._request.reset_mock()
    c._request.side_effect = (
        [global_reply(), asyncio.CancelledError()] if stage == 2 else asyncio.CancelledError()
    )
    with pytest.raises(asyncio.CancelledError):
        await action(c, "context")
    result = selected(c, "context")
    current = "global" if stage == 1 else "group"
    assert result["read_status"] == ("error" if stage == 1 else "partial")
    assert result[current]["quality"] == ("stale" if warm else "unknown")
    assert result[current]["error"] == CANCELLED
    assert result[current]["raw_attributes"] == before[current]["raw_attributes"]
    assert result[current]["last_successful_read"] == before[current]["last_successful_read"]
    assert result[current]["last_attempt"] is not None
    if stage == 1:
        assert result["group"] == before["group"]
        assert result["global"]["global_emergency_stop"] is None
    else:
        assert result["global"]["quality"] == "fresh" and result["global"]["error"] is None
        assert result["global"]["last_successful_read"] is not None
        assert result["group"]["bacnet_emergency_stop"] is None
        assert result["group"]["energy_saving_stop"] is None
    assert result["control_inhibited"] is None
    assert c._request.await_count == stage


@pytest.mark.asyncio
async def test_context_first_failure_then_group_cancel_is_error_without_replacing_first_failure(client):
    c, _ = client
    c._request.side_effect = [MelansProtocolError("private.invalid", code="0101"), asyncio.CancelledError()]
    with pytest.raises(asyncio.CancelledError):
        await action(c, "context")
    result = selected(c, "context")
    assert result["read_status"] == "error" and result["control_inhibited"] is None
    assert result["global"]["error"] == {"type": "MelansProtocolError", "code": "0101"}
    assert result["group"]["error"] == CANCELLED
    assert all(result[key]["quality"] == "unknown" for key in ("global", "group"))
    assert "private" not in json.dumps(result) and c._request.await_count == 2


@pytest.mark.asyncio
async def test_context_preserves_fresh_known_stop_when_other_source_is_cancelled(client):
    c, _ = client
    c._request.side_effect = [global_reply("ON"), asyncio.CancelledError()]
    with pytest.raises(asyncio.CancelledError):
        await action(c, "context")
    result = selected(c, "context")
    assert result["read_status"] == "partial"
    assert result["global"]["global_emergency_stop"] is True
    assert result["group"]["bacnet_emergency_stop"] is None
    assert result["control_inhibited"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("warm", [False, True])
async def test_cancel_while_waiting_for_family_lock_has_no_attempt_or_cache_mutation(client, family, warm):
    c, clock = client
    if warm:
        await action(c, family)
        clock.now += 15
    before = copy.deepcopy(cached_snapshot(c))
    before_selected = copy.deepcopy(selected(c, family))
    completion = getattr(c, COMPLETIONS[family])
    c._request.reset_mock()
    async with getattr(c, LOCKS[family]):
        task = asyncio.create_task(action(c, family))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert cached_snapshot(c) == before and selected(c, family) == before_selected
    assert getattr(c, COMPLETIONS[family]) == completion
    c._request.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("family", FAMILIES)
async def test_cancel_cooldown_is_measured_from_completion_and_recovery_clears_error(client, family):
    c, clock = client
    started = asyncio.Event()

    async def blocked(_):
        started.set()
        await asyncio.Event().wait()

    c._request.side_effect = blocked
    task = asyncio.create_task(action(c, family))
    await started.wait()
    clock.now += 40
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    completed = clock.now
    clock.now = completed + 14.99
    cooled = await action(c, family, other=family != "clock")
    if family == "member":
        assert cooled is None and selected(c, family, other=True)["read_status"] == "cooldown"
    else:
        assert cooled["read_status"] == "cooldown"
    c._request.assert_awaited_once()
    clock.now = completed + 15
    c._request.side_effect = response
    await action(c, family)
    recovered = selected(c, family)
    assert recovered["read_status"] == "updated"
    if family == "context":
        assert recovered["global"]["error"] is None and recovered["group"]["error"] is None
    else:
        assert recovered["quality"] == "fresh" and recovered["error"] is None
    assert not getattr(c, LOCKS[family]).locked()
    assert c._request.await_count == (3 if family == "context" else 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("family", FAMILIES)
async def test_cancel_while_queued_on_common_io_lock_sends_nothing_and_marks_attempt(client, family):
    c, _ = client
    del c._request
    c._ws.request = AsyncMock(side_effect=AssertionError("Cancelled queued read must not send"))
    c._http.request = AsyncMock(side_effect=AssertionError("Cancelled queued read must not fall back"))
    async with c._io_lock:
        task = asyncio.create_task(action(c, family))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    result = selected(c, family)
    current = result["global"] if family == "context" else result
    assert current["error"] == CANCELLED and current["quality"] == "unknown"
    assert result["read_status"] == "error" and current["last_attempt"] is not None
    c._ws.request.assert_not_awaited()
    c._http.request.assert_not_awaited()
    assert not getattr(c, LOCKS[family]).locked()


@pytest.mark.asyncio
@pytest.mark.parametrize("family", FAMILIES)
async def test_cancel_keeps_other_selectors_optional_caches_and_core_state_unchanged(client, family):
    c, clock = client
    for name in FAMILIES:
        await action(c, name)
    # A different selector in the same family has a separate successful cache.
    if family != "clock":
        clock.now += 15
        await action(c, family, other=True)
    clock.now += 15
    other = copy.deepcopy(selected(c, family, other=True)) if family != "clock" else None
    at = datetime.now(UTC)
    c.devices["1"] = MelansDevice("1", None, {"Drive": "OFF", "Mode": "COOL"})
    c.last_attempt = c.last_success = at
    c.group_last_success = {"1": at}
    c._group_seen = {"1": clock.now}
    c.last_command = {"status": "outcome_unknown", "attributes": {"Drive": "ON"}}
    c._pending_writes = {"1": {"Drive": "ON"}}
    c._readback_requested = True
    before = copy.deepcopy(cached_snapshot(c))
    c._request.reset_mock()
    c._request.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await action(c, family)
    after = cached_snapshot(c)
    key = {"clock": "controller_clock", "today": "today_schedules", "weekly": "weekly_patterns",
           "member": "members", "context": "control_context"}[family]
    before.pop(key)
    after.pop(key)
    assert before == after
    if family not in ("clock", "context"):
        assert selected(c, family, other=True) == other
    elif family == "context":
        # Global cancellation correctly invalidates the shared source; the
        # other group's independent stop record was never attempted.
        assert selected(c, family, other=True)["group"] == other["group"]
    assert c._pending_writes == {"1": {"Drive": "ON"}} and c._readback_requested
    assert c.devices["1"].raw_attributes() == {"Drive": "OFF", "Mode": "COOL"}
    c._request.assert_awaited_once()
