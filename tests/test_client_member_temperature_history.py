"""Raw temperature history shares bounded daily reads without becoming telemetry."""

from __future__ import annotations

import asyncio
import copy
import json
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pymelans.exceptions import MelansConnectionError, MelansProtocolError
from pymelans.topology import parse_membership

from pymelans import MelansClient, MelansDevice, cached_snapshot, const

DAY = "2024-02-29"
RAW = ",".join(["23.3"] * 48)
MEMBERS = (
    {"Group": "1", "Address": "1", "Model": "IC", "SubModel": ""},
    {"Group": "1", "Address": "2", "Model": "IC", "SubModel": "HH"},
    {"Group": "2", "Address": "3", "Model": "LC"},
    {"Group": "2", "Address": "4", "Model": "IC"},
    {"Group": "2", "Address": "4", "Model": "LC"},
    {"Group": "2", "Address": "5", "Model": "FUTURE"},
)


def directory(*records):
    root = ET.Element("Packet")
    ET.SubElement(root, "Command").text = "getResponse"
    node = ET.SubElement(ET.SubElement(ET.SubElement(root, "DatabaseManager"), "ControlGroup"), "MnetGroupList")
    for attributes in records:
        ET.SubElement(node, "MnetGroupRecord", attributes)
    return ET.tostring(root, encoding="unicode")


def reply(*, address="1", day=DAY, data_name="ROOM_TEMP", raw=RAW, records=1, **extra):
    selected = date.fromisoformat(day)
    root = ET.Element("Packet")
    ET.SubElement(root, "Command").text = "getResponse"
    energy = ET.SubElement(ET.SubElement(root, "DatabaseManager"), "EnergyManagement")
    node = ET.SubElement(energy, "IcTrendDList", {
        "Address": address, "Year": str(selected.year), "Month": str(selected.month),
        "Day": str(selected.day), "DataName": data_name,
    })
    field = {"ROOM_TEMP": "RoomTempData", "FAN_TIME": "FanTimeData", "THERMO_TIME": "ThermoTimeData"}[data_name]
    for _ in range(records):
        ET.SubElement(node, "IcTrendDRecord", {**({field: raw} if raw is not None else {}), **extra})
    return ET.tostring(root, encoding="unicode")


def echo_request(payload):
    node = ET.fromstring(payload).find("./DatabaseManager/EnergyManagement/IcTrendDList")
    day = date(int(node.get("Year")), int(node.get("Month")), int(node.get("Day"))).isoformat()
    return reply(address=node.get("Address"), day=day, data_name=node.get("DataName"))


@pytest.fixture
def client(monkeypatch):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr("pymelans.client.time", SimpleNamespace(monotonic=lambda: clock.now, perf_counter=lambda: clock.now))
    c = MelansClient("fixture.invalid", stale_after=20)
    c._names = {"1": "private-room", "2": None}
    c.topology = parse_membership(directory(*MEMBERS), expected_groups=c._names)
    c._request = AsyncMock(return_value=reply())
    return c, clock


def snapshot(c, address="1", day=DAY):
    return c.member_temperature_history_snapshot(address, day)


def cache_size(c):
    return sum(len(days) for days in c.member_temperature_history_snapshot().values())


async def read(c, kind, address="1", day=DAY):
    if kind == "room":
        return await c.async_read_member_temperature_history(address, day)
    return await c.async_read_member_runtime(address, day, "FAN_TIME")


def selected_snapshot(c, kind):
    return snapshot(c) if kind == "room" else c.member_runtime_snapshot("1", DAY, "FAN_TIME")


@pytest.mark.asyncio
async def test_one_exact_room_selector_exports_only_raw_fields_with_no_typed_values_or_unit_claim(client):
    c, _ = client
    c._request.return_value = reply(Password="private-secret", FanTimeData="1,2", RoomName="private-room")
    result = await c.async_read_member_temperature_history("001", DAY)
    c._request.assert_awaited_once()
    root = ET.fromstring(c._request.call_args.args[0])
    manager = root.find("./DatabaseManager")
    assert root.findtext("./Command") == "getRequest"
    assert len(manager) == len(manager[0]) == 1 and manager[0].tag == "EnergyManagement"
    assert manager[0][0].tag == "IcTrendDList" and len(manager[0][0]) == 0
    assert manager[0][0].attrib == {"Address": "1", "Year": "2024", "Month": "2", "Day": "29", "DataName": "ROOM_TEMP"}
    assert set(result) == {
        "address", "date_iso", "data_name", "read_status", "quality", "last_attempt",
        "last_successful_read", "error", "record_count", "raw_attributes", "raw_tokens",
        "unit", "interval_minutes", "timezone", "aggregation", "slots", "issues",
    }
    assert result["address"] == "1" and result["date_iso"] == DAY and result["data_name"] == "ROOM_TEMP"
    assert result["read_status"] == "updated" and result["quality"] == "fresh" and result["error"] is None
    assert result["last_attempt"] <= result["last_successful_read"]
    assert result["record_count"] == 1 and result["raw_attributes"] == {"RoomTempData": RAW}
    assert result["raw_tokens"] == ["23.3"] * 48 and len(result["slots"]) == 48
    assert result["slots"][0] == {"index": 0, "start_time": "00:00", "end_time": "00:30", "raw_token": "23.3"}
    assert result["slots"][-1] == {"index": 47, "start_time": "23:30", "end_time": "24:00", "raw_token": "23.3"}
    assert result["unit"] is result["timezone"] is result["aggregation"] is None
    assert result["interval_minutes"] == 30 and result["issues"] == []
    assert "private" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("address,day", [
    (0, DAY), (251, DAY), (True, DAY), (1.0, DAY), ("*", DAY), ("1,2", DAY), ("١", DAY),
    (3, DAY), (4, DAY), (5, DAY), (6, DAY), (1, "2023-02-29"), (1, "1969-12-31"),
    (1, "2038-01-01"), (1, "2024-2-29"), (1, None), (1, date(2024, 2, 29)),
])
async def test_invalid_or_unverified_member_and_date_are_zero_io_without_cache_allocation(client, address, day):
    c, _ = client
    with pytest.raises(ValueError):
        await c.async_read_member_temperature_history(address, day)
    c._request.assert_not_awaited()
    assert c.member_temperature_history_snapshot() == {} and c._last_member_runtime_request is None


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["topology", "configured-groups"])
async def test_membership_must_come_from_a_configured_topology_not_a_guessed_address(client, missing):
    c, _ = client
    if missing == "topology":
        c.topology = None
    else:
        c._names = {}
    with pytest.raises(ValueError, match="allowlist"):
        await c.async_read_member_temperature_history("1", DAY)
    c._request.assert_not_awaited()
    assert c.member_temperature_history_snapshot() == {}


@pytest.mark.parametrize("values", [(None, DAY), ("1", None), ("*", DAY), ("1", "2024-2-29")])
def test_partial_or_invalid_cached_selector_does_not_allocate_or_read(client, values):
    c, _ = client
    with pytest.raises(ValueError):
        c.member_temperature_history_snapshot(*values)
    assert c.member_temperature_history_snapshot() == {}
    c._request.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("first_kind,second_kind", [("room", "runtime"), ("runtime", "room")])
async def test_both_daily_actions_share_completion_cooldown_without_allocating_the_other_cache(client, first_kind, second_kind):
    c, clock = client

    async def delayed(payload):
        clock.now += 40
        return echo_request(payload)

    c._request.side_effect = delayed
    first = await read(c, first_kind)
    completed = clock.now
    before_room, before_runtime = copy.deepcopy(c.member_temperature_history_snapshot()), copy.deepcopy(c.member_runtime_snapshot())
    clock.now = completed + 14.99
    for address, day in (("1", DAY), ("2", DAY), ("1", "2024-03-01")):
        result = await read(c, second_kind, address, day)
        assert result["read_status"] == "cooldown" and result["last_attempt"] is None
        assert result["quality"] == "unknown"
    assert c.member_temperature_history_snapshot() == before_room and c.member_runtime_snapshot() == before_runtime
    assert selected_snapshot(c, first_kind)["last_successful_read"] == first["last_successful_read"]
    c._request.assert_awaited_once()
    clock.now = completed + 15
    c._request.side_effect = echo_request
    assert (await read(c, second_kind, "2"))["read_status"] == "updated"
    assert c._request.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("first_kind,second_kind", [("room", "runtime"), ("runtime", "room")])
async def test_concurrent_cross_action_calls_serialize_and_only_one_get_is_sent(client, first_kind, second_kind):
    c, clock = client
    started, release = asyncio.Event(), asyncio.Event()

    async def blocked(payload):
        started.set()
        await release.wait()
        clock.now += 40
        return echo_request(payload)

    c._request.side_effect = blocked
    first = asyncio.create_task(read(c, first_kind))
    await started.wait()
    second = asyncio.create_task(read(c, second_kind, "2"))
    await asyncio.sleep(0)
    c._request.assert_awaited_once()
    release.set()
    a, b = await asyncio.gather(first, second)
    assert a["read_status"] == "updated" and b["read_status"] == "cooldown"
    assert b["last_attempt"] is None and b["record_count"] is None
    assert cache_size(c) == (1 if first_kind == "room" else 0)


@pytest.mark.asyncio
async def test_allowlist_is_rechecked_after_waiting_for_shared_history_lock(client):
    c, _ = client
    async with c._member_runtime_lock:
        action = asyncio.create_task(c.async_read_member_temperature_history("2", DAY))
        await asyncio.sleep(0)
        c.topology = parse_membership(directory(MEMBERS[0]), expected_groups=c._names)
    with pytest.raises(ValueError, match="allowlist"):
        await action
    c._request.assert_not_awaited()
    assert c.member_temperature_history_snapshot() == {} and c._last_member_runtime_request is None


@pytest.mark.asyncio
async def test_cancellation_while_waiting_for_lock_is_not_an_attempt_or_cooldown(client):
    c, _ = client
    async with c._member_runtime_lock:
        action = asyncio.create_task(c.async_read_member_temperature_history("1", DAY))
        await asyncio.sleep(0)
        action.cancel()
        with pytest.raises(asyncio.CancelledError):
            await action
    assert c.member_temperature_history_snapshot() == {} and c._last_member_runtime_request is None
    c._request.assert_not_awaited()
    assert (await c.async_read_member_temperature_history("1", DAY))["read_status"] == "updated"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["room", "runtime"])
@pytest.mark.parametrize("warm", [False, True])
async def test_cancelled_attempt_is_explicit_error_retains_old_sample_and_starts_shared_cooldown(client, kind, warm):
    c, clock = client
    c._request.side_effect = echo_request
    if warm:
        await read(c, kind)
        clock.now += 15
    before = selected_snapshot(c, kind)
    seen = (c._member_temperature_histories if kind == "room" else c._member_runtimes).get(
        ("1", DAY) if kind == "room" else ("1", DAY, "FAN_TIME"), {},
    ).get("seen")
    started = asyncio.Event()

    async def blocked(_):
        started.set()
        await asyncio.Event().wait()

    c._request.side_effect = blocked
    core_before = copy.deepcopy(cached_snapshot(c))
    for key in ("member_runtimes", "member_temperature_histories"):
        core_before.pop(key, None)
    action = asyncio.create_task(read(c, kind))
    await started.wait()
    action.cancel()
    with pytest.raises(asyncio.CancelledError):
        await action
    after = selected_snapshot(c, kind)
    record = (c._member_temperature_histories if kind == "room" else c._member_runtimes)[
        ("1", DAY) if kind == "room" else ("1", DAY, "FAN_TIME")
    ]
    assert after["read_status"] == "error" and after["error"] == {"type": "CancelledError", "code": None}
    assert after["quality"] == ("stale" if warm else "unknown")
    assert after["last_attempt"] is not None and after["last_successful_read"] == before["last_successful_read"]
    assert record.get("seen") == seen and after["raw_attributes"] == before["raw_attributes"]
    assert after["raw_tokens"] == before["raw_tokens"]
    if kind == "room":
        assert after["slots"] == before["slots"]
    assert not c._member_runtime_lock.locked()
    core_after = cached_snapshot(c)
    for key in ("member_runtimes", "member_temperature_histories"):
        core_after.pop(key, None)
    assert core_after == core_before and c._pending_writes == {} and not c._readback_requested
    assert (await read(c, "runtime" if kind == "room" else "room", "2"))["read_status"] == "cooldown"
    clock.now += 15
    c._request.side_effect = echo_request
    assert (await read(c, kind))["read_status"] == "updated"


@pytest.mark.asyncio
async def test_age_and_failed_refresh_preserve_raw_slots_without_unit_or_success_claim(client):
    c, clock = client
    first = await c.async_read_member_temperature_history("1", DAY)
    clock.now += 21
    aged = snapshot(c)
    assert aged["quality"] == "stale" and aged["slots"] == first["slots"]
    assert aged["raw_attributes"] == first["raw_attributes"]
    c._request.side_effect = MelansProtocolError("private-message", code="0101", point="private-point")
    failed = await c.async_read_member_temperature_history("1", DAY)
    assert failed["read_status"] == "error" and failed["quality"] == "stale"
    assert failed["last_successful_read"] == first["last_successful_read"]
    assert failed["raw_tokens"] == first["raw_tokens"] and failed["slots"] == first["slots"]
    assert failed["error"] == {"type": "MelansProtocolError", "code": "0101"}
    assert failed["unit"] is failed["aggregation"] is failed["timezone"] is None
    assert "private" not in json.dumps(failed)
    clock.now += 15
    c._request.side_effect = None
    c._request.return_value = reply(raw=",".join(["24.1"] * 48))
    recovered = await c.async_read_member_temperature_history("1", DAY)
    assert recovered["quality"] == "fresh" and recovered["error"] is None
    assert recovered["slots"][0]["raw_token"] == "24.1"


@pytest.mark.asyncio
async def test_first_failed_get_is_sanitised_unknown_and_also_blocks_runtime_get(client):
    c, _ = client
    c._request.side_effect = MelansProtocolError("http://private.invalid/secret", code="private.invalid/path")
    result = await c.async_read_member_temperature_history("1", DAY)
    assert result["read_status"] == "error" and result["quality"] == "unknown"
    assert result["last_successful_read"] is None and result["record_count"] is None
    assert result["raw_attributes"] == {} and result["raw_tokens"] == result["slots"] == []
    assert result["error"] == {"type": "MelansProtocolError", "code": None}
    assert "private.invalid" not in json.dumps(result)
    assert (await c.async_read_member_runtime("2", DAY, "THERMO_TIME"))["read_status"] == "cooldown"
    c._request.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["address", "date", "runtime-name", "padded-key", "duplicate-record", "aggregate", "partial-error", "mixed-text", "notification"])
async def test_bad_identity_or_envelope_cannot_replace_a_previous_good_raw_sample(client, defect):
    c, clock = client
    first = await c.async_read_member_temperature_history("1", DAY)
    root = ET.fromstring(reply())
    node = root.find("./DatabaseManager/EnergyManagement/IcTrendDList")
    if defect == "address":
        node.set("Address", "2")
    elif defect == "date":
        node.set("Day", "28")
    elif defect == "runtime-name":
        node.set("DataName", "FAN_TIME")
    elif defect == "padded-key":
        node.set("Month", "02")
    elif defect == "duplicate-record":
        ET.SubElement(node, "IcTrendDRecord", {"RoomTempData": RAW})
    elif defect == "aggregate":
        node.set("MultiGroup", "")
    elif defect == "partial-error":
        ET.SubElement(root.find("./DatabaseManager"), "ERROR", {"Code": "0101", "Message": "private"})
    elif defect == "mixed-text":
        node.text = "junk"
    else:
        root[0].text = "notifyRequest"
    clock.now += 15
    c._request.return_value = ET.tostring(root, encoding="unicode")
    failed = await c.async_read_member_temperature_history("1", DAY)
    assert failed["read_status"] == "error" and failed["quality"] == "stale"
    assert failed["raw_attributes"] == first["raw_attributes"] and failed["slots"] == first["slots"]
    assert failed["last_successful_read"] == first["last_successful_read"]


@pytest.mark.asyncio
@pytest.mark.parametrize("raw,records,kind", [(None, 0, "missing_record"), (None, 1, "missing_data"), ("0,0", 1, "unexpected_token_count")])
async def test_incomplete_series_stays_raw_evidence_without_padding_or_filling(client, raw, records, kind):
    c, _ = client
    c._request.return_value = reply(raw=raw, records=records)
    result = await c.async_read_member_temperature_history("1", DAY)
    assert result["quality"] == "fresh" and result["read_status"] == "updated"
    assert result["record_count"] == records and result["slots"] == []
    assert result["raw_tokens"] == (raw.split(",") if raw is not None else [])
    assert result["issues"] == [{"key": "record" if records == 0 else "RoomTempData", "kind": kind}]


@pytest.mark.asyncio
async def test_raw_tokens_are_not_coerced_trimmed_or_treated_as_valid_measurements(client):
    c, _ = client
    tokens = ["0", "-1", "NaN", "23junk", "*", "", " 23.0 "] + ["23.3"] * 41
    c._request.return_value = reply(raw=",".join(tokens))
    result = await c.async_read_member_temperature_history("1", DAY)
    assert result["raw_tokens"] == tokens and [slot["raw_token"] for slot in result["slots"]] == tokens
    assert result["issues"] == [] and result["unit"] is None
    assert all(set(slot) == {"index", "start_time", "end_time", "raw_token"} for slot in result["slots"])


@pytest.mark.asyncio
async def test_cached_exports_are_detached_zero_io_and_never_allocate_or_refresh_recency(client):
    c, clock = client
    assert snapshot(c)["quality"] == "unknown" and c.member_temperature_history_snapshot() == {}
    c._request.assert_not_awaited()
    c._request.return_value = reply(raw="0,0", Password="private-secret")
    result = await c.async_read_member_temperature_history("1", DAY)
    expected = copy.deepcopy(c.member_temperature_history_snapshot())
    result["raw_attributes"].clear()
    result["raw_tokens"][0] = "caller"
    result["issues"][0]["kind"] = "caller"
    assert c.member_temperature_history_snapshot() == expected
    clock.now += 15
    c._request.return_value = reply()
    result = await c.async_read_member_temperature_history("1", DAY)
    expected = copy.deepcopy(c.member_temperature_history_snapshot())
    result["slots"][0]["raw_token"] = "caller"
    exported = c.member_temperature_history_snapshot()
    exported["1"][DAY]["slots"].clear()
    assert c.member_temperature_history_snapshot() == expected and "private" not in json.dumps(expected)
    assert snapshot(c, day="2024-03-01")["quality"] == "unknown"
    assert c.member_temperature_history_snapshot() == expected
    clock.now += 15
    c._request.side_effect = MelansConnectionError("private")
    failed = await c.async_read_member_temperature_history("1", DAY)
    failed["error"]["type"] = "caller"
    assert snapshot(c)["error"]["type"] == "MelansConnectionError"
    assert c._request.await_count == 3


@pytest.mark.asyncio
async def test_separate_32_entry_lru_counts_failed_attempts_but_not_snapshot_or_cooldown(client):
    c, clock = client
    c._request.side_effect = echo_request
    days = [(date(2024, 1, 1) + timedelta(days=index)).isoformat() for index in range(34)]
    for day in days[:32]:
        await c.async_read_member_temperature_history("1", day)
        clock.now += 15
    assert cache_size(c) == 32 and c.member_runtime_snapshot() == {}
    snapshot(c, day=days[0])
    c.member_temperature_history_snapshot()
    before = copy.deepcopy(c.member_temperature_history_snapshot())
    clock.now -= 1
    assert (await c.async_read_member_temperature_history("2", days[33]))["read_status"] == "cooldown"
    assert c.member_temperature_history_snapshot() == before
    clock.now += 1
    await c.async_read_member_temperature_history("1", days[32])
    assert cache_size(c) == 32 and days[0] not in c.member_temperature_history_snapshot()["1"]
    clock.now += 15
    c._request.side_effect = MelansConnectionError("private")
    await c.async_read_member_temperature_history("1", days[1])
    clock.now += 15
    c._request.side_effect = echo_request
    await c.async_read_member_temperature_history("1", days[33])
    assert cache_size(c) == 32 and days[1] in c.member_temperature_history_snapshot()["1"]
    assert days[2] not in c.member_temperature_history_snapshot()["1"] and c._request.await_count == 35


@pytest.mark.asyncio
async def test_room_and_runtime_errors_are_isolated_from_each_other_and_all_core_health_and_commands(client):
    c, clock = client
    c._request.side_effect = echo_request
    await c.async_read_member_runtime("1", DAY, "FAN_TIME")
    clock.now += 15
    at = datetime(2025, 1, 1, tzinfo=UTC)
    c.devices["1"] = MelansDevice("1", None, {"Drive": "OFF", "Mode": "COOL", "SetTemp": "25"})
    c.last_attempt = c.last_success = at
    c.group_last_success = {"1": at}
    c._group_seen = {"1": clock.now}
    c.failed_groups = {"2"}
    c.member_states = {"1": {"Group": "1", "FanStatus": "OFF"}}
    c.member_last_attempt = c.member_last_success = {"1": at}
    c._member_seen = {"1": clock.now}
    c.member_read_status = {"1": "updated"}
    c.last_command = {"group_id": "1", "attributes": {"Drive": "ON"}, "status": "accepted_unconfirmed"}
    c._pending_writes = {"1": {"Drive": "ON"}}
    c._readback_requested = True
    before = copy.deepcopy(cached_snapshot(c))
    await c.async_read_member_temperature_history("1", DAY)
    after = cached_snapshot(c)
    before.pop("member_temperature_histories", None)
    after.pop("member_temperature_histories", None)
    assert before == after
    clock.now += 15
    runtime = copy.deepcopy(c.member_runtime_snapshot())
    c._request.side_effect = MelansConnectionError("private")
    await c.async_read_member_temperature_history("1", DAY)
    assert c.member_runtime_snapshot() == runtime
    room = copy.deepcopy(c.member_temperature_history_snapshot())
    clock.now += 15
    await c.async_read_member_runtime("1", DAY, "FAN_TIME")
    assert c.member_temperature_history_snapshot() == room
    assert c._pending_writes == {"1": {"Drive": "ON"}} and c._readback_requested
    assert c.group_last_success == {"1": at} and c._group_seen == {"1": 115}
    assert c.member_last_success == {"1": at} and c._member_seen == {"1": 115}
    assert c.devices["1"].raw_attributes() == {"Drive": "OFF", "Mode": "COOL", "SetTemp": "25"}


@pytest.mark.asyncio
async def test_regular_poll_and_all_cache_exports_do_not_fetch_temperature_history(client):
    c, _ = client
    c._request.return_value = '<Packet><Command>getResponse</Command><DatabaseManager><Mnet Group="1" Drive="OFF" /><Mnet Group="2" Drive="OFF" /></DatabaseManager></Packet>'
    await c.async_update()
    c._request.assert_awaited_once()
    assert ET.fromstring(c._request.call_args.args[0]).find(".//EnergyManagement") is None
    assert c.member_temperature_history_snapshot() == {} and snapshot(c)["last_attempt"] is None
    cached_snapshot(c)
    c._request.assert_awaited_once()


@pytest.mark.asyncio
async def test_temperature_history_uses_the_shared_transport_io_lock_and_request_spacing(client, monkeypatch):
    c, clock = client
    del c._request
    monkeypatch.setattr(const, "MIN_REQUEST_SPACING", 1.5)
    real_sleep = asyncio.sleep
    sent_at, active = [], 0

    async def advance(delay):
        clock.now += delay
        await real_sleep(0)

    async def response(payload):
        nonlocal active
        active += 1
        assert active == 1
        sent_at.append(clock.now)
        await real_sleep(0)
        active -= 1
        return echo_request(payload) if "IcTrendDList" in payload else "other read"

    monkeypatch.setattr("pymelans.client.asyncio", SimpleNamespace(sleep=advance))
    c._ws.request = AsyncMock(side_effect=response)
    c._http.request = AsyncMock()
    async with c._io_lock:
        action = asyncio.create_task(c.async_read_member_temperature_history("1", DAY))
        await real_sleep(0)
        c._ws.request.assert_not_awaited()
    other = asyncio.create_task(c._request('<Packet><Command>getRequest</Command><DatabaseManager><SystemData Version="*" /></DatabaseManager></Packet>'))
    result, _ = await asyncio.gather(action, other)
    assert result["read_status"] == "updated" and len(sent_at) == 2
    assert sent_at[1] - sent_at[0] >= 1.5
    c._http.request.assert_not_awaited()
