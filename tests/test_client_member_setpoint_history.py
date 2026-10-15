"""Three daily history actions share pacing but keep schemas and caches separate."""

from __future__ import annotations

import asyncio
import copy
import itertools
import json
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pymelans.exceptions import MelansConnectionError, MelansProtocolError
from pymelans.topology import parse_membership
from test_client_member_temperature_history import DAY, MEMBERS, directory
from test_client_member_temperature_history import client as client

from pymelans import cached_snapshot, const

KINDS = ("runtime", "room", "cool", "heat")
NAMES = {"runtime": "FAN_TIME", "room": "ROOM_TEMP", "cool": "COOL_SET_TEMP", "heat": "HEAT_SET_TEMP"}
FIELDS = {"FAN_TIME": "FanTimeData", "ROOM_TEMP": "RoomTempData", "COOL_SET_TEMP": "CoolSetTempData", "HEAT_SET_TEMP": "HeatSetTempData"}
TOKENS = {"FAN_TIME": "2", "ROOM_TEMP": "22.7", "COOL_SET_TEMP": "27.5", "HEAT_SET_TEMP": "19.0"}


def reply(*, address="1", day=DAY, name="COOL_SET_TEMP", raw=None, records=1, missing=False, **extra):
    selected = date.fromisoformat(day)
    root = ET.Element("Packet")
    ET.SubElement(root, "Command").text = "getResponse"
    energy = ET.SubElement(ET.SubElement(root, "DatabaseManager"), "EnergyManagement")
    node = ET.SubElement(energy, "IcTrendDList", {
        "Address": address, "Year": str(selected.year), "Month": str(selected.month),
        "Day": str(selected.day), "DataName": name,
    })
    value = ",".join([TOKENS[name]] * 48) if raw is None else raw
    for _ in range(records):
        ET.SubElement(node, "IcTrendDRecord", {**({} if missing else {FIELDS[name]: value}), **extra})
    return ET.tostring(root, encoding="unicode")


def echo_request(payload):
    node = ET.fromstring(payload).find("./DatabaseManager/EnergyManagement/IcTrendDList")
    day = date(int(node.get("Year")), int(node.get("Month")), int(node.get("Day"))).isoformat()
    return reply(address=node.get("Address"), day=day, name=node.get("DataName"))


async def read(c, kind, address="1", day=DAY):
    if kind == "runtime":
        return await c.async_read_member_runtime(address, day, NAMES[kind])
    if kind == "room":
        return await c.async_read_member_temperature_history(address, day)
    return await c.async_read_member_setpoint_history(address, day, NAMES[kind])


def snapshot(c, kind, address="1", day=DAY):
    if kind == "runtime":
        return c.member_runtime_snapshot(address, day, NAMES[kind])
    if kind == "room":
        return c.member_temperature_history_snapshot(address, day)
    return c.member_setpoint_history_snapshot(address, day, NAMES[kind])


def all_caches(c):
    return (c.member_runtime_snapshot(), c.member_temperature_history_snapshot(), c.member_setpoint_history_snapshot())


def setpoint_cache_size(c):
    return sum(len(names) for days in c.member_setpoint_history_snapshot().values() for names in days.values())


@pytest.mark.asyncio
@pytest.mark.parametrize("name,field", [("COOL_SET_TEMP", "CoolSetTempData"), ("HEAT_SET_TEMP", "HeatSetTempData")])
async def test_exact_one_selected_channel_exports_only_raw_setpoint_evidence(client, name, field):
    c, _ = client
    c._request.return_value = reply(name=name, Password="private-secret", RoomTempData="other-room", FanTimeData="1,2")
    result = await c.async_read_member_setpoint_history("001", DAY, name)
    c._request.assert_awaited_once()
    root = ET.fromstring(c._request.call_args.args[0])
    manager = root.find("./DatabaseManager")
    assert root.findtext("./Command") == "getRequest" and len(manager) == len(manager[0]) == 1
    assert manager[0].tag == "EnergyManagement" and manager[0][0].tag == "IcTrendDList" and len(manager[0][0]) == 0
    assert manager[0][0].attrib == {"Address": "1", "Year": "2024", "Month": "2", "Day": "29", "DataName": name}
    assert set(result) == {
        "address", "date_iso", "data_name", "read_status", "quality", "last_attempt",
        "last_successful_read", "error", "record_count", "raw_attributes", "raw_tokens",
        "unit", "interval_minutes", "timezone", "aggregation", "slots", "issues",
    }
    assert result["address"] == "1" and result["date_iso"] == DAY and result["data_name"] == name
    assert result["read_status"] == "updated" and result["quality"] == "fresh" and result["error"] is None
    assert result["last_attempt"] <= result["last_successful_read"]
    assert result["record_count"] == 1 and result["raw_attributes"] == {field: ",".join([TOKENS[name]] * 48)}
    assert result["raw_tokens"] == [TOKENS[name]] * 48 and result["issues"] == []
    assert result["unit"] is result["aggregation"] is result["timezone"] is None
    assert result["interval_minutes"] == 30
    assert result["slots"][0] == {"index": 0, "start_time": "00:00", "end_time": "00:30", "raw_token": TOKENS[name]}
    assert result["slots"][-1]["end_time"] == "24:00" and len(result["slots"]) == 48
    assert "private" not in json.dumps(result) and c.member_temperature_history_snapshot() == {}
    assert c.member_runtime_snapshot() == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("values", [
    (True, DAY, "COOL_SET_TEMP"), (1.0, DAY, "COOL_SET_TEMP"), (0, DAY, "COOL_SET_TEMP"),
    (251, DAY, "COOL_SET_TEMP"), ("*", DAY, "COOL_SET_TEMP"), ("1,2", DAY, "COOL_SET_TEMP"),
    ("١", DAY, "COOL_SET_TEMP"), (3, DAY, "COOL_SET_TEMP"), (4, DAY, "COOL_SET_TEMP"),
    (5, DAY, "COOL_SET_TEMP"), (6, DAY, "COOL_SET_TEMP"), (1, "2023-02-29", "COOL_SET_TEMP"),
    (1, "1969-12-31", "COOL_SET_TEMP"), (1, "2038-01-01", "COOL_SET_TEMP"),
    (1, DAY, "ROOM_TEMP"), (1, DAY, "FAN_TIME"), (1, DAY, "cool_set_temp"),
    (1, DAY, "*"), (1, DAY, ["COOL_SET_TEMP"]),
])
async def test_invalid_selector_and_unknown_non_ic_or_conflicting_members_never_probe(client, values):
    c, _ = client
    with pytest.raises(ValueError):
        await c.async_read_member_setpoint_history(*values)
    c._request.assert_not_awaited()
    assert all_caches(c) == ({}, {}, {}) and c._last_member_runtime_request is None


@pytest.mark.parametrize("values", [(None, DAY, "COOL_SET_TEMP"), ("1", None, "COOL_SET_TEMP"), ("1", DAY, None), ("1", DAY, "ROOM_TEMP")])
def test_partial_or_wrong_snapshot_selector_never_allocates_or_reads(client, values):
    c, _ = client
    with pytest.raises(ValueError):
        c.member_setpoint_history_snapshot(*values)
    assert c.member_setpoint_history_snapshot() == {}
    c._request.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("first,second", list(itertools.product(KINDS, repeat=2)))
async def test_every_action_and_channel_pair_shares_one_completion_cooldown(client, first, second):
    c, clock = client

    async def delayed(payload):
        clock.now += 40
        return echo_request(payload)

    c._request.side_effect = delayed
    await read(c, first)
    completed = clock.now
    before = copy.deepcopy(all_caches(c))
    clock.now = completed + 14.99
    assert (await read(c, second))["read_status"] == "cooldown"
    assert (await read(c, second, "2", "2024-03-01"))["read_status"] == "cooldown"
    assert all_caches(c) == before and snapshot(c, first)["read_status"] == "updated"
    c._request.assert_awaited_once()
    clock.now = completed + 15
    c._request.side_effect = echo_request
    assert (await read(c, second, "2"))["read_status"] == "updated"
    assert c._request.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("first", ["runtime", "room", "cool"])
async def test_three_concurrent_actions_send_one_request_then_two_cooldowns(client, first):
    c, clock = client
    started, release = asyncio.Event(), asyncio.Event()

    async def delayed(payload):
        started.set()
        await release.wait()
        clock.now += 40
        return echo_request(payload)

    c._request.side_effect = delayed
    action = asyncio.create_task(read(c, first))
    await started.wait()
    remaining = [kind for kind in ("runtime", "room", "cool") if kind != first]
    waiting = [asyncio.create_task(read(c, kind, "2")) for kind in remaining]
    await asyncio.sleep(0)
    c._request.assert_awaited_once()
    release.set()
    results = await asyncio.gather(action, *waiting)
    assert [result["read_status"] for result in results] == ["updated", "cooldown", "cooldown"]
    assert sum(bool(cache) for cache in all_caches(c)) == 1
    assert all(result["last_attempt"] is None for result in results[1:])


@pytest.mark.asyncio
async def test_common_helper_rechecks_membership_after_lock_wait_and_cancel_waiting_is_not_an_attempt(client):
    c, _ = client
    async with c._member_runtime_lock:
        revoked = asyncio.create_task(read(c, "cool", "2"))
        cancelled = asyncio.create_task(read(c, "heat"))
        await asyncio.sleep(0)
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        c.topology = parse_membership(directory(MEMBERS[0]), expected_groups=c._names)
    with pytest.raises(ValueError, match="allowlist"):
        await revoked
    c._request.assert_not_awaited()
    assert all_caches(c) == ({}, {}, {}) and c._last_member_runtime_request is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("warm", [False, True])
async def test_shared_helper_cancellation_retains_sample_and_success_clock_but_marks_attempt_error(client, kind, warm):
    c, clock = client
    c._request.side_effect = echo_request
    if warm:
        await read(c, kind)
        clock.now += 15
    before = snapshot(c, kind)
    caches = {"runtime": c._member_runtimes, "room": c._member_temperature_histories,
              "cool": c._member_setpoint_histories, "heat": c._member_setpoint_histories}
    key = ("1", DAY) if kind == "room" else ("1", DAY, NAMES[kind])
    old_seen = caches[kind].get(key, {}).get("seen")
    started = asyncio.Event()

    async def blocked(_):
        started.set()
        await asyncio.Event().wait()

    c._request.side_effect = blocked
    action = asyncio.create_task(read(c, kind))
    await started.wait()
    clock.now += 40
    action.cancel()
    with pytest.raises(asyncio.CancelledError):
        await action
    after = snapshot(c, kind)
    assert after["read_status"] == "error" and after["error"] == {"type": "CancelledError", "code": None}
    assert after["quality"] == ("stale" if warm else "unknown")
    assert after["last_successful_read"] == before["last_successful_read"] and after["last_attempt"] is not None
    assert after["raw_attributes"] == before["raw_attributes"] and after["raw_tokens"] == before["raw_tokens"]
    assert caches[kind][key].get("seen") == old_seen and not c._member_runtime_lock.locked()
    if kind != "runtime":
        assert after["slots"] == before["slots"]
    for other in KINDS:
        assert (await read(c, other, "2"))["read_status"] == "cooldown"
    clock.now += 15
    c._request.side_effect = echo_request
    assert (await read(c, kind))["read_status"] == "updated"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_failed_actual_attempt_uses_one_shared_cooldown_with_safe_error(client, kind):
    c, _ = client
    c._request.side_effect = MelansProtocolError("http://private.invalid/secret", code="private.invalid/path")
    result = await read(c, kind)
    assert result["read_status"] == "error" and result["quality"] == "unknown"
    assert result["last_successful_read"] is None and result["error"] == {"type": "MelansProtocolError", "code": None}
    assert "private.invalid" not in json.dumps(result)
    for other in KINDS:
        assert (await read(c, other, "2"))["read_status"] == "cooldown"
    c._request.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("raw,records,missing,count,issue", [
    (None, 0, False, 0, "missing_record"), (None, 1, True, 0, "missing_data"),
    ("", 1, False, 1, "unexpected_token_count"), (",".join([""] * 48), 1, False, 48, None),
    (",".join(["0"] * 48), 1, False, 48, None),
])
async def test_blank_field_missing_field_missing_row_and_48_empty_slots_are_distinct(client, raw, records, missing, count, issue):
    c, _ = client
    c._request.return_value = reply(name="HEAT_SET_TEMP", raw=raw, records=records, missing=missing)
    result = await read(c, "heat")
    assert result["read_status"] == "updated" and result["quality"] == "fresh"
    assert result["record_count"] == records and len(result["raw_tokens"]) == count
    assert len(result["slots"]) == (48 if count == 48 else 0)
    assert result["issues"] == ([{"key": "record" if records == 0 else "HeatSetTempData", "kind": issue}] if issue else [])
    if count == 48:
        assert [slot["raw_token"] for slot in result["slots"]] == raw.split(",")
    assert result["unit"] is result["aggregation"] is result["timezone"] is None


@pytest.mark.asyncio
async def test_response_and_nested_cache_exports_are_detached_and_failed_refresh_preserves_raw_slots(client):
    c, clock = client
    c._request.side_effect = echo_request
    result = await read(c, "cool")
    expected = copy.deepcopy(c.member_setpoint_history_snapshot())
    result["raw_attributes"].clear()
    result["raw_tokens"][0] = "caller"
    result["slots"][0]["raw_token"] = "caller"
    exported = c.member_setpoint_history_snapshot()
    exported["1"][DAY]["COOL_SET_TEMP"]["slots"].clear()
    assert c.member_setpoint_history_snapshot() == expected
    c._request.assert_awaited_once()
    assert snapshot(c, "heat")["quality"] == "unknown" and c.member_setpoint_history_snapshot() == expected
    clock.now += 21
    aged = snapshot(c, "cool")
    assert aged["quality"] == "stale" and aged["slots"] == expected["1"][DAY]["COOL_SET_TEMP"]["slots"]
    c._request.assert_awaited_once()
    c._request.side_effect = MelansProtocolError("private-message", code="0101")
    failed = await read(c, "cool")
    assert failed["quality"] == "stale" and failed["read_status"] == "error"
    assert failed["raw_tokens"] == expected["1"][DAY]["COOL_SET_TEMP"]["raw_tokens"]
    assert failed["slots"] == expected["1"][DAY]["COOL_SET_TEMP"]["slots"]
    assert failed["last_successful_read"] == expected["1"][DAY]["COOL_SET_TEMP"]["last_successful_read"]
    failed["error"]["type"] = "caller"
    assert snapshot(c, "cool")["error"] == {"type": "MelansProtocolError", "code": "0101"}
    clock.now += 15
    c._request.side_effect = None
    c._request.return_value = reply(raw="0,0")
    issue = await read(c, "cool")
    issue["issues"][0]["kind"] = "caller"
    assert snapshot(c, "cool")["issues"] == [{"key": "CoolSetTempData", "kind": "unexpected_token_count"}]


@pytest.mark.asyncio
async def test_setpoint_lru_is_32_combined_channel_attempts_without_touching_other_caches(client):
    c, clock = client
    c._request.side_effect = echo_request
    for kind in ("runtime", "room"):
        await read(c, kind)
        clock.now += 15
    old_caches = copy.deepcopy((c._member_runtimes, c._member_temperature_histories))
    keys = [((date(2024, 1, 1) + timedelta(days=index // 2)).isoformat(), "cool" if index % 2 == 0 else "heat") for index in range(34)]
    for day, kind in keys[:32]:
        await read(c, kind, day=day)
        clock.now += 15
    assert setpoint_cache_size(c) == 32
    snapshot(c, keys[0][1], day=keys[0][0])
    c.member_setpoint_history_snapshot()
    before = copy.deepcopy(c.member_setpoint_history_snapshot())
    clock.now -= 1
    assert (await read(c, keys[33][1], day=keys[33][0]))["read_status"] == "cooldown"
    assert c.member_setpoint_history_snapshot() == before
    clock.now += 1
    await read(c, keys[32][1], day=keys[32][0])
    assert snapshot(c, keys[0][1], day=keys[0][0])["quality"] == "unknown"
    clock.now += 15
    c._request.side_effect = MelansConnectionError("private")
    await read(c, keys[1][1], day=keys[1][0])
    clock.now += 15
    c._request.side_effect = echo_request
    await read(c, keys[33][1], day=keys[33][0])
    assert snapshot(c, keys[1][1], day=keys[1][0])["read_status"] == "error"
    assert snapshot(c, keys[2][1], day=keys[2][0])["quality"] == "unknown"
    assert setpoint_cache_size(c) == 32 and (c._member_runtimes, c._member_temperature_histories) == old_caches
    assert c._request.await_count == 37


@pytest.mark.asyncio
async def test_channel_failures_cannot_mutate_other_history_health_or_command_state(client):
    c, clock = client
    c._request.side_effect = echo_request
    for kind in KINDS:
        await read(c, kind)
        clock.now += 15
    c.last_command = {"group_id": "1", "attributes": {"Drive": "ON"}, "status": "accepted_unconfirmed"}
    c._pending_writes = {"1": {"Drive": "ON"}}
    c._readback_requested = True
    before = copy.deepcopy(cached_snapshot(c))
    other = copy.deepcopy(tuple(snapshot(c, kind) for kind in ("runtime", "room", "heat")))
    c._request.side_effect = MelansConnectionError("private")
    await read(c, "cool")
    assert tuple(snapshot(c, kind) for kind in ("runtime", "room", "heat")) == other
    after = cached_snapshot(c)
    before.pop("member_setpoint_histories", None)
    after.pop("member_setpoint_histories", None)
    assert before == after and c._pending_writes == {"1": {"Drive": "ON"}} and c._readback_requested


@pytest.mark.asyncio
async def test_normal_poll_and_cached_exports_never_read_setpoint_history(client):
    c, _ = client
    c._request.return_value = '<Packet><Command>getResponse</Command><DatabaseManager><Mnet Group="1" Drive="OFF" /><Mnet Group="2" Drive="OFF" /></DatabaseManager></Packet>'
    await c.async_update()
    c._request.assert_awaited_once()
    assert ET.fromstring(c._request.call_args.args[0]).find(".//EnergyManagement") is None
    assert c.member_setpoint_history_snapshot() == {} and snapshot(c, "cool")["last_attempt"] is None
    cached_snapshot(c)
    c._request.assert_awaited_once()


@pytest.mark.asyncio
async def test_setpoint_history_and_a_command_share_transport_lock_and_spacing_without_readback_side_effect(client, monkeypatch):
    c, clock = client
    del c._request
    monkeypatch.setattr(const, "MIN_REQUEST_SPACING", 1.5)
    real_sleep = asyncio.sleep
    sent_at, commands, active = [], [], 0

    async def advance(delay):
        clock.now += delay
        await real_sleep(0)

    async def response(payload):
        nonlocal active
        active += 1
        assert active == 1
        sent_at.append(clock.now)
        command = ET.fromstring(payload).findtext("./Command")
        commands.append(command)
        await real_sleep(0)
        active -= 1
        return echo_request(payload) if command == "getRequest" else "<Packet><Command>setResponse</Command></Packet>"

    monkeypatch.setattr("pymelans.client.asyncio", SimpleNamespace(sleep=advance))
    c._ws.request = AsyncMock(side_effect=response)
    c._http.request = AsyncMock()
    async with c._io_lock:
        action = asyncio.create_task(read(c, "cool"))
        await real_sleep(0)
        c._ws.request.assert_not_awaited()
    control = asyncio.create_task(c.set_climate("1", mode="FAN", power=True))
    result, _ = await asyncio.gather(action, control)
    assert result["read_status"] == "updated" and commands == ["getRequest", "setRequest"]
    assert sent_at[1] - sent_at[0] >= 1.5
    assert c.last_command["status"] == "accepted_unconfirmed" and c._pending_writes == {"1": {"Mode": "FAN", "Drive": "ON"}}
    assert c._readback_requested and c.group_last_success == {}
    c._http.request.assert_not_awaited()
