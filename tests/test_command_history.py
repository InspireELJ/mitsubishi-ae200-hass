"""Bounded write-stage evidence survives later groups without changing control."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pymelans.exceptions import (
    MelansCommandUncertainError,
    MelansConnectionError,
    MelansProtocolError,
)

from pymelans import MelansClient, cached_snapshot

ACK = "<Packet><Command>setResponse</Command><DatabaseManager /></Packet>"


@pytest.fixture
def client(monkeypatch):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr("pymelans.client.time", SimpleNamespace(
        monotonic=lambda: clock.now, perf_counter=lambda: clock.now,
    ))
    instance = MelansClient("fixture.invalid")
    instance._request = AsyncMock(return_value=ACK)
    return instance, clock


@pytest.mark.asyncio
async def test_unknown_group_attempt_survives_other_ack_and_matching_readback(client):
    c, clock = client
    c._request.side_effect = [MelansCommandUncertainError("private-message"), ACK]
    with pytest.raises(MelansCommandUncertainError):
        await c.set_power("1", False)
    clock.now += 2
    assert await c.set_power("2", True) is None
    assert set(c.last_command) == {"group_id", "attributes", "at", "status"}
    assert c.last_command["group_id"] == "2"
    assert c.last_command["status"] == "accepted_unconfirmed"
    c._confirm_readback("1", {"Drive": "OFF"})
    c._confirm_readback("2", {"Drive": "ON"}, sampled_at=clock.now + 1)
    assert c.last_command["status"] == "confirmed"
    history = cached_snapshot(c)["command_history"]
    assert [item["sequence"] for item in history] == [1, 2]
    assert [item["group_id"] for item in history] == ["1", "2"]
    assert [item["outcome"] for item in history] == [
        "outcome_unknown", "accepted_unconfirmed",
    ]
    assert history[0]["intent"] == {"Drive": "OFF"}
    assert history[0]["error"] == {"type": "MelansCommandUncertainError", "code": None}
    assert c._request.await_count == 2


@pytest.mark.asyncio
async def test_oldest_attempts_are_evicted_at_32_with_monotonic_sequence(client):
    c, clock = client
    assert c.command_history_snapshot() == []
    for index in range(34):
        clock.now += 2
        await c.set_power("1", bool(index % 2))
    history = c.command_history_snapshot()
    assert len(history) == len(c._command_history) == 32
    assert [item["sequence"] for item in history] == list(range(3, 35))
    assert c._request.await_count == 34


@pytest.mark.asyncio
async def test_history_is_detached_from_callers_last_command_and_each_export(client):
    c, clock = client
    attributes = {"Drive": "ON"}
    c._request.side_effect = MelansConnectionError("private-message")
    with pytest.raises(MelansConnectionError):
        await c._write("1", attributes)
    expected = c.command_history_snapshot()
    attributes["Drive"] = "OFF"
    c.last_command["attributes"].clear()
    exported = cached_snapshot(c)["command_history"]
    exported[0]["intent"].clear()
    exported[0]["error"]["type"] = "caller-mutation"
    exported.clear()
    assert c.command_history_snapshot() == expected
    clock.now += 2
    c._request.side_effect = None
    await c.set_power("2", False)
    assert len(expected) == 1 and expected[0]["outcome"] == "failed"
    assert len(c.command_history_snapshot()) == 2
    assert c._request.await_count == 2


@pytest.mark.asyncio
async def test_bounded_finite_legacy_temperature_is_not_rounded_in_intent(client):
    c, _ = client
    await c._write("1", {"SetTemp": "25.0000001"})
    assert c.command_history_snapshot()[0]["intent"] == {"SetTemp": "25.0000001"}
    c._request.assert_awaited_once()


@pytest.mark.asyncio
async def test_concurrent_writes_keep_separate_outcomes_and_pending_export(client, monkeypatch):
    c, clock = client
    started, release = asyncio.Event(), asyncio.Event()
    real_sleep = asyncio.sleep

    async def paced_sleep(delay):
        clock.now += delay
        await real_sleep(0)

    async def request(payload):
        if 'Group="1"' in payload:
            started.set()
            await release.wait()
            raise MelansCommandUncertainError("private-message")
        return ACK

    monkeypatch.setattr("pymelans.client.asyncio.sleep", paced_sleep)
    c._request.side_effect = request
    first = asyncio.create_task(c.set_power("1", False))
    await started.wait()
    second = asyncio.create_task(c.set_power("2", True))
    await asyncio.sleep(0)
    pending = c.command_history_snapshot()
    assert len(pending) == 1 and pending[0]["outcome"] == "pending"
    c._request.assert_awaited_once()
    release.set()
    outcomes = await asyncio.gather(first, second, return_exceptions=True)
    assert isinstance(outcomes[0], MelansCommandUncertainError) and outcomes[1] is None
    history = c.command_history_snapshot()
    assert [item["group_id"] for item in history] == ["1", "2"]
    assert [item["outcome"] for item in history] == [
        "outcome_unknown", "accepted_unconfirmed",
    ]
    assert pending[0]["outcome"] == "pending"
    assert c._request.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("code,expected_code", [("0101", "0101"), ("x" * 33, None),
                                               ("http://private.invalid/secret", None)])
async def test_history_retains_only_allowlisted_intent_and_safe_errors(client, code, expected_code):
    c, _ = client
    c._request.return_value = (
        '<Packet><Command>setErrorResponse</Command><DatabaseManager>'
        f'<ERROR Code="{code}" Message="private-message" /></DatabaseManager></Packet>'
    )
    with pytest.raises(MelansProtocolError):
        await c._write("1", {
            "Drive": "ON", "Mode": "private-message", "Password": "private-secret",
            "UnknownField": "private-secret", "SetTemp": "nan", "FilterItem": "CHK_ON",
        })
    history = c.command_history_snapshot()
    assert set(history[0]) == {"sequence", "group_id", "at", "intent", "outcome", "error"}
    assert history[0]["intent"] == {"Drive": "ON", "FilterItem": "CHK_ON"}
    assert history[0]["outcome"] == "failed"
    assert history[0]["error"] == {"type": "MelansProtocolError", "code": expected_code}
    assert "private" not in json.dumps(history)
    c._request.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["io_lock", "io_spacing"])
async def test_cancellation_inside_request_is_unknown_without_transmission_claim(client, phase):
    c, clock = client
    del c._request
    c._ws.request = AsyncMock(return_value=ACK)
    c._http.request = AsyncMock()
    if phase == "io_lock":
        await c._io_lock.acquire()
    else:
        c._last_request = clock.now
    try:
        action = asyncio.create_task(c.set_power("1", False))
        await asyncio.sleep(0)
        assert c.command_history_snapshot()[0]["outcome"] == "pending"
        action.cancel()
        with pytest.raises(asyncio.CancelledError):
            await action
    finally:
        if phase == "io_lock":
            c._io_lock.release()
    history = c.command_history_snapshot()
    assert history[0]["outcome"] == "outcome_unknown"
    assert history[0]["error"] == {"type": "CancelledError", "code": None}
    assert c.last_command["status"] == "outcome_unknown"
    assert c._last_request == (None if phase == "io_lock" else clock.now)
    c._ws.request.assert_not_awaited()
    c._http.request.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["write_lock", "write_spacing"])
async def test_cancellation_before_existing_pending_boundary_creates_no_attempt(client, phase):
    c, clock = client
    if phase == "write_lock":
        await c._write_lock.acquire()
    else:
        c._last_write = clock.now
    try:
        action = asyncio.create_task(c.set_power("1", False))
        await asyncio.sleep(0)
        action.cancel()
        with pytest.raises(asyncio.CancelledError):
            await action
    finally:
        if phase == "write_lock":
            c._write_lock.release()
    assert c.command_history_snapshot() == [] and c.last_command is None
    c._request.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_write_and_cached_exports_do_not_add_attempts_or_io(client):
    c, _ = client
    with pytest.raises(ValueError):
        await c._write("1", {})
    assert cached_snapshot(c)["command_history"] == []
    assert c.command_history_snapshot() == []
    c._request.assert_not_awaited()


@pytest.mark.asyncio
async def test_unclassified_failure_keeps_existing_pending_semantics_without_error_text(client):
    c, _ = client
    c._request.side_effect = RuntimeError("private-message")
    with pytest.raises(RuntimeError):
        await c.set_power("1", False)
    assert c.last_command["status"] == "pending"
    record = c.command_history_snapshot()[0]
    assert record["outcome"] == "pending" and record["error"] is None
    assert "private" not in json.dumps(record)
    c._request.assert_awaited_once()
