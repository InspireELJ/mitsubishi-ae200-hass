"""Historical member reads stay bounded, independent and explicit."""

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
ZERO = ",".join(["0"] * 48)


def directory(*records):
    root = ET.Element("Packet")
    ET.SubElement(root, "Command").text = "getResponse"
    records_node = ET.SubElement(ET.SubElement(ET.SubElement(root, "DatabaseManager"), "ControlGroup"), "MnetGroupList")
    for attributes in records:
        ET.SubElement(records_node, "MnetGroupRecord", attributes)
    return ET.tostring(root, encoding="unicode")


MEMBERS = (
    {"Group": "1", "Address": "1", "Model": "IC", "SubModel": ""},
    {"Group": "1", "Address": "2", "Model": "IC", "SubModel": ""},
    {"Group": "2", "Address": "3", "Model": "LC"},
    {"Group": "2", "Address": "4", "Model": "IC"},
    {"Group": "2", "Address": "4", "Model": "LC"},
    {"Group": "2", "Address": "5", "Model": "FUTURE"},
)


def runtime_reply(*, address="1", day=DAY, data_name="FAN_TIME", raw=ZERO, records=1, **extra):
    selected = date.fromisoformat(day)
    root = ET.Element("Packet")
    ET.SubElement(root, "Command").text = "getResponse"
    energy = ET.SubElement(ET.SubElement(root, "DatabaseManager"), "EnergyManagement")
    node = ET.SubElement(energy, "IcTrendDList", {
        "Address": address, "Year": str(selected.year), "Month": str(selected.month),
        "Day": str(selected.day), "DataName": data_name,
    })
    field = "FanTimeData" if data_name == "FAN_TIME" else "ThermoTimeData"
    for _ in range(records):
        ET.SubElement(node, "IcTrendDRecord", {**({field: raw} if raw is not None else {}), **extra})
    return ET.tostring(root, encoding="unicode")


def echo_request(payload):
    node = ET.fromstring(payload).find("./DatabaseManager/EnergyManagement/IcTrendDList")
    day = date(int(node.get("Year")), int(node.get("Month")), int(node.get("Day"))).isoformat()
    return runtime_reply(address=node.get("Address"), day=day, data_name=node.get("DataName"))


@pytest.fixture
def client(monkeypatch):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr("pymelans.client.time", SimpleNamespace(monotonic=lambda: clock.now, perf_counter=lambda: clock.now))
    c = MelansClient("fixture.invalid", stale_after=20)
    c._names = {"1": "private-room", "2": None}
    c.topology = parse_membership(directory(*MEMBERS), expected_groups=c._names)
    c._request = AsyncMock(return_value=runtime_reply())
    return c, clock


def snapshot(c, address="1", day=DAY, data_name="FAN_TIME"):
    return c.member_runtime_snapshot(address, day, data_name)


def cache_size(c):
    return sum(len(names) for days in c.member_runtime_snapshot().values() for names in days.values())


@pytest.mark.asyncio
async def test_one_explicit_known_ic_member_day_read_exports_reported_slots_without_totals(client):
    c, _ = client
    result = await c.async_read_member_runtime("001", DAY, "FAN_TIME")
    c._request.assert_awaited_once()
    root = ET.fromstring(c._request.call_args.args[0])
    assert root.findtext("./Command") == "getRequest"
    manager = root.find("./DatabaseManager")
    assert len(manager) == 1 and manager[0].tag == "EnergyManagement" and len(manager[0]) == 1
    assert manager[0][0].attrib == {"Address": "1", "Year": "2024", "Month": "2", "Day": "29", "DataName": "FAN_TIME"}
    assert result["address"] == "1" and result["date_iso"] == DAY and result["data_name"] == "FAN_TIME"
    assert result["read_status"] == "updated" and result["quality"] == "fresh" and result["error"] is None
    assert result["last_attempt"] <= result["last_successful_read"]
    assert result["record_count"] == 1 and result["raw_attributes"] == {"FanTimeData": ZERO}
    assert result["raw_tokens"] == ["0"] * 48 and len(result["buckets"]) == 48
    assert result["buckets"][0] == {"index": 0, "start_time": "00:00", "end_time": "00:30", "raw_token": "0", "reported_minutes": 0.0}
    assert result["buckets"][-1]["end_time"] == "24:00"
    assert result["unit"] == "min" and result["interval_minutes"] == 30 and result["timezone"] is None
    assert result["issues"] == []
    assert not {"total", "actual_total", "hvac_action", "next_run", "utc_date", "is_on", "logging_enabled"} & result.keys()


@pytest.mark.asyncio
@pytest.mark.parametrize("values", [
    (0, DAY, "FAN_TIME"), (251, DAY, "FAN_TIME"), (True, DAY, "FAN_TIME"), ("*", DAY, "FAN_TIME"),
    ("1,2", DAY, "FAN_TIME"), ("١", DAY, "FAN_TIME"), (3, DAY, "FAN_TIME"), (4, DAY, "FAN_TIME"),
    (5, DAY, "FAN_TIME"), (6, DAY, "FAN_TIME"), (1, "2023-02-29", "FAN_TIME"),
    (1, "1969-12-31", "FAN_TIME"), (1, "2038-01-01", "FAN_TIME"), (1, "2024-2-29", "FAN_TIME"),
    (1, DAY, "ELECTRIC_ENERGY"), (1, DAY, "fan_time"), (1, DAY, "*"),
])
async def test_invalid_selector_unknown_unsupported_or_conflicting_member_never_probes_or_allocates_cache(client, values):
    c, _ = client
    before = c.member_runtime_snapshot()
    with pytest.raises(ValueError):
        await c.async_read_member_runtime(*values)
    c._request.assert_not_awaited()
    assert c.member_runtime_snapshot() == before == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["directory", "group-allowlist"])
async def test_member_directory_and_configured_group_membership_are_required_before_io(client, missing):
    c, _ = client
    if missing == "directory":
        c.topology = None
    else:
        c._names.pop("1")
    with pytest.raises(ValueError):
        await c.async_read_member_runtime("1", DAY, "FAN_TIME")
    c._request.assert_not_awaited()
    assert c.member_runtime_snapshot() == {}


@pytest.mark.parametrize("values", [("1", None, None), (None, DAY, "FAN_TIME"), ("1", DAY, None)])
def test_partial_snapshot_selector_is_rejected_without_io_or_allocation(client, values):
    c, _ = client
    with pytest.raises(ValueError):
        c.member_runtime_snapshot(*values)
    c._request.assert_not_awaited()
    assert c.member_runtime_snapshot() == {}


@pytest.mark.asyncio
async def test_cooldown_is_completion_based_global_across_all_three_selector_fields_and_never_inserts_keys(client):
    c, clock = client

    async def delayed(_):
        clock.now += 40
        return runtime_reply()

    c._request.side_effect = delayed
    first = await c.async_read_member_runtime("1", DAY, "FAN_TIME")
    completed = clock.now
    clock.now = completed + 14.99
    before = copy.deepcopy(c.member_runtime_snapshot())
    for values in (("1", DAY, "FAN_TIME"), ("1", DAY, "THERMO_TIME"), ("1", "2024-03-01", "FAN_TIME"), ("2", DAY, "FAN_TIME")):
        result = await c.async_read_member_runtime(*values)
        assert result["read_status"] == "cooldown"
        assert c.member_runtime_snapshot() == before
        if values != ("1", DAY, "FAN_TIME"):
            assert result["last_attempt"] is None and result["quality"] == "unknown"
    assert snapshot(c)["read_status"] == "updated"
    assert snapshot(c)["last_successful_read"] == first["last_successful_read"]
    c._request.assert_awaited_once()
    clock.now = completed + 15
    c._request.side_effect = echo_request
    assert (await c.async_read_member_runtime("2", DAY, "FAN_TIME"))["read_status"] == "updated"
    assert c._request.await_count == 2


@pytest.mark.asyncio
async def test_concurrent_runtime_reads_share_lock_and_one_completion_cooldown(client):
    c, clock = client
    started, release = asyncio.Event(), asyncio.Event()

    async def delayed(_):
        started.set()
        await release.wait()
        clock.now += 40
        return runtime_reply()

    c._request.side_effect = delayed
    first = asyncio.create_task(c.async_read_member_runtime("1", DAY, "FAN_TIME"))
    await started.wait()
    second = asyncio.create_task(c.async_read_member_runtime("2", DAY, "THERMO_TIME"))
    await asyncio.sleep(0)
    c._request.assert_awaited_once()
    release.set()
    a, b = await asyncio.gather(first, second)
    assert a["read_status"] == "updated" and b["read_status"] == "cooldown"
    assert cache_size(c) == 1 and b["last_attempt"] is None


@pytest.mark.asyncio
async def test_membership_is_rechecked_after_waiting_for_action_lock(client):
    c, _ = client
    started, release = asyncio.Event(), asyncio.Event()

    async def delayed(_):
        started.set()
        await release.wait()
        return runtime_reply()

    c._request.side_effect = delayed
    first = asyncio.create_task(c.async_read_member_runtime("1", DAY, "FAN_TIME"))
    await started.wait()
    second = asyncio.create_task(c.async_read_member_runtime("2", DAY, "FAN_TIME"))
    await asyncio.sleep(0)
    c.topology = parse_membership(directory(MEMBERS[0]), expected_groups=c._names)
    release.set()
    await first
    with pytest.raises(ValueError):
        await second
    c._request.assert_awaited_once()
    assert cache_size(c) == 1


@pytest.mark.asyncio
async def test_cancellation_propagates_releases_action_lock_and_starts_completion_cooldown(client):
    c, clock = client
    started = asyncio.Event()

    async def blocked(_):
        started.set()
        await asyncio.Event().wait()

    c._request.side_effect = blocked
    action = asyncio.create_task(c.async_read_member_runtime("1", DAY, "FAN_TIME"))
    await started.wait()
    clock.now += 40
    action.cancel()
    with pytest.raises(asyncio.CancelledError):
        await action
    assert snapshot(c)["quality"] == "unknown" and snapshot(c)["last_successful_read"] is None
    assert snapshot(c)["last_attempt"] is not None
    assert (await c.async_read_member_runtime("2", DAY, "FAN_TIME"))["read_status"] == "cooldown"
    assert cache_size(c) == 1
    clock.now += 15
    c._request.side_effect = echo_request
    assert (await c.async_read_member_runtime("2", DAY, "FAN_TIME"))["read_status"] == "updated"
    assert c._request.await_count == 2


@pytest.mark.asyncio
async def test_independent_samples_expire_without_losing_raw_then_failed_refresh_stales_old_projection(client):
    c, clock = client
    first = await c.async_read_member_runtime("1", DAY, "FAN_TIME")
    clock.now += 15
    c._request.return_value = runtime_reply(data_name="THERMO_TIME", raw=",".join(["1.5"] * 48))
    await c.async_read_member_runtime("1", DAY, "THERMO_TIME")
    clock.now += 6
    assert snapshot(c)["quality"] == "stale" and snapshot(c)["buckets"][0]["reported_minutes"] is None
    assert snapshot(c)["raw_tokens"] == ["0"] * 48
    assert snapshot(c, data_name="THERMO_TIME")["quality"] == "fresh"
    assert snapshot(c, data_name="THERMO_TIME")["buckets"][0]["reported_minutes"] == 1.5
    clock.now += 9
    c._request.side_effect = MelansProtocolError("private-message", code="0101", point="private-point")
    result = await c.async_read_member_runtime("1", DAY, "FAN_TIME")
    assert result["read_status"] == "error" and result["quality"] == "stale"
    assert result["error"] == {"type": "MelansProtocolError", "code": "0101"}
    assert result["last_successful_read"] == first["last_successful_read"]
    assert result["raw_attributes"] == {"FanTimeData": ZERO} and result["raw_tokens"] == ["0"] * 48
    assert all(bucket["reported_minutes"] is None for bucket in result["buckets"])
    assert snapshot(c, data_name="THERMO_TIME")["quality"] == "fresh"
    assert snapshot(c, data_name="THERMO_TIME")["error"] is None
    assert "private-" not in json.dumps(result)


@pytest.mark.asyncio
async def test_failure_before_expiry_invalidates_old_typed_minutes_and_recovery_is_independent(client):
    c, clock = client
    await c.async_read_member_runtime("1", DAY, "FAN_TIME")
    clock.now += 15
    c._request.side_effect = MelansConnectionError("private")
    failed = await c.async_read_member_runtime("1", DAY, "FAN_TIME")
    assert failed["quality"] == "stale" and failed["buckets"][0]["reported_minutes"] is None
    clock.now += 15
    c._request.side_effect = None
    c._request.return_value = runtime_reply(raw=",".join(["2"] * 48))
    recovered = await c.async_read_member_runtime("1", DAY, "FAN_TIME")
    assert recovered["quality"] == "fresh" and recovered["error"] is None
    assert recovered["buckets"][0]["reported_minutes"] == 2


@pytest.mark.asyncio
async def test_first_failure_is_unknown_sanitised_and_still_uses_global_cooldown(client):
    c, _ = client
    c._request.side_effect = MelansProtocolError("http://private.invalid/secret", code="private.invalid/path")
    result = await c.async_read_member_runtime("1", DAY, "FAN_TIME")
    assert result["read_status"] == "error" and result["quality"] == "unknown"
    assert result["record_count"] is None and result["raw_attributes"] == {} and result["raw_tokens"] == result["buckets"] == []
    assert result["error"] == {"type": "MelansProtocolError", "code": None}
    assert "private.invalid" not in json.dumps(cached_snapshot(c))
    assert (await c.async_read_member_runtime("2", DAY, "FAN_TIME"))["read_status"] == "cooldown"
    c._request.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("raw,records,kind", [(None, 0, "missing_record"), (None, 1, "missing_data"), ("0,0", 1, "unexpected_token_count")])
async def test_empty_or_incomplete_reports_are_successful_raw_evidence_without_zero_filled_buckets(client, raw, records, kind):
    c, _ = client
    c._request.return_value = runtime_reply(raw=raw, records=records)
    result = await c.async_read_member_runtime("1", DAY, "FAN_TIME")
    assert result["quality"] == "fresh" and result["read_status"] == "updated"
    assert result["record_count"] == records and result["buckets"] == []
    assert result["issues"][0]["kind"] == kind


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["address", "date", "data-name", "padded-key", "duplicate-record", "aggregate", "partial-error", "mixed-text", "notification"])
async def test_invalid_original_xml_cannot_replace_existing_raw_cache(client, defect):
    c, clock = client
    first = await c.async_read_member_runtime("1", DAY, "FAN_TIME")
    root = ET.fromstring(runtime_reply())
    node = root.find("./DatabaseManager/EnergyManagement/IcTrendDList")
    if defect == "address":
        node.set("Address", "2")
    elif defect == "date":
        node.set("Day", "28")
    elif defect == "data-name":
        node.set("DataName", "THERMO_TIME")
    elif defect == "padded-key":
        node.set("Month", "02")
    elif defect == "duplicate-record":
        ET.SubElement(node, "IcTrendDRecord", {"FanTimeData": ZERO})
    elif defect == "aggregate":
        node.set("MultiGroup", "")
    elif defect == "partial-error":
        ET.SubElement(root.find("./DatabaseManager"), "ERROR", {"Code": "0101", "Message": "private"})
    elif defect == "mixed-text":
        node.text = "junk"
    elif defect == "notification":
        root[0].text = "notifyRequest"
    clock.now += 15
    c._request.return_value = ET.tostring(root, encoding="unicode")
    result = await c.async_read_member_runtime("1", DAY, "FAN_TIME")
    assert result["quality"] == "stale" and result["read_status"] == "error"
    assert result["raw_attributes"] == first["raw_attributes"]
    assert result["last_successful_read"] == first["last_successful_read"]
    assert all(bucket["reported_minutes"] is None for bucket in result["buckets"])


@pytest.mark.asyncio
async def test_snapshots_and_diagnostics_are_deep_copies_without_io_or_new_cache_keys(client):
    c, clock = client
    assert snapshot(c)["quality"] == "unknown" and c.member_runtime_snapshot() == {}
    c._request.assert_not_awaited()
    tokens = ["0"] * 48
    tokens[3] = "*"
    c._request.return_value = runtime_reply(raw=",".join(tokens), Password="private-secret")
    action = await c.async_read_member_runtime("1", DAY, "FAN_TIME")
    expected = copy.deepcopy(c.member_runtime_snapshot())
    action["raw_attributes"].clear()
    action["raw_tokens"][0] = "caller"
    action["buckets"][0]["raw_token"] = "caller"
    action["issues"][0]["kind"] = "caller"
    exported = cached_snapshot(c)["member_runtimes"]
    assert exported == expected
    exported["1"][DAY]["FAN_TIME"]["buckets"].clear()
    assert c.member_runtime_snapshot() == expected and "private-secret" not in json.dumps(expected)
    assert snapshot(c, day="2024-03-01")["quality"] == "unknown" and c.member_runtime_snapshot() == expected
    clock.now += 15
    c._request.side_effect = MelansConnectionError("private")
    action = await c.async_read_member_runtime("1", DAY, "FAN_TIME")
    action["error"]["type"] = "caller"
    assert snapshot(c)["error"]["type"] == "MelansConnectionError"
    assert c._request.await_count == 2


@pytest.mark.asyncio
async def test_lru_retains_only_32_actual_attempts_and_snapshot_or_cooldown_cannot_touch_recency(client):
    c, clock = client
    c._request.side_effect = echo_request
    days = [(date(2024, 1, 1) + timedelta(days=index)).isoformat() for index in range(34)]
    for day in days[:32]:
        await c.async_read_member_runtime("1", day, "FAN_TIME")
        clock.now += 15
    assert cache_size(c) == 32
    snapshot(c, day=days[0])
    cached_snapshot(c)
    before = copy.deepcopy(c.member_runtime_snapshot())
    clock.now -= 1
    assert (await c.async_read_member_runtime("1", days[33], "THERMO_TIME"))["read_status"] == "cooldown"
    assert c.member_runtime_snapshot() == before
    clock.now += 1
    await c.async_read_member_runtime("1", days[32], "FAN_TIME")
    assert cache_size(c) == 32 and snapshot(c, day=days[0])["quality"] == "unknown"
    assert days[0] not in c.member_runtime_snapshot()["1"] and days[1] in c.member_runtime_snapshot()["1"]
    clock.now += 15
    c._request.side_effect = MelansConnectionError("private")
    await c.async_read_member_runtime("1", days[1], "FAN_TIME")
    clock.now += 15
    c._request.side_effect = echo_request
    await c.async_read_member_runtime("1", days[33], "FAN_TIME")
    assert cache_size(c) == 32 and days[1] in c.member_runtime_snapshot()["1"]
    assert days[2] not in c.member_runtime_snapshot()["1"]
    assert c._request.await_count == 35


@pytest.mark.asyncio
async def test_runtime_read_cannot_change_climate_member_health_commands_or_other_optional_caches(client):
    c, clock = client
    at = datetime(2025, 1, 1, tzinfo=UTC)
    c.devices["1"] = MelansDevice("1", None, {"Drive": "OFF", "Mode": "COOL"})
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
    await c.async_read_member_runtime("1", DAY, "FAN_TIME")
    after = cached_snapshot(c)
    before.pop("member_runtimes")
    after.pop("member_runtimes")
    assert before == after
    assert c._pending_writes == {"1": {"Drive": "ON"}} and c._readback_requested
    assert c.group_last_success == {"1": at} and c._group_seen == {"1": clock.now}
    assert c.member_last_success == {"1": at} and c._member_seen == {"1": clock.now}


@pytest.mark.asyncio
async def test_regular_group_poll_does_not_fetch_history_or_allocate_optional_cache(client):
    c, _ = client
    c._request.return_value = '<Packet><Command>getResponse</Command><DatabaseManager><Mnet Group="1" Drive="OFF" /><Mnet Group="2" Drive="OFF" /></DatabaseManager></Packet>'
    await c.async_update()
    c._request.assert_awaited_once()
    assert ET.fromstring(c._request.call_args.args[0]).find(".//EnergyManagement") is None
    assert c.member_runtime_snapshot() == {}


@pytest.mark.asyncio
async def test_runtime_and_other_requests_share_actual_io_lock_and_spacing(client, monkeypatch):
    c, clock = client
    del c._request
    monkeypatch.setattr(const, "MIN_REQUEST_SPACING", 1.5)
    real_sleep = asyncio.sleep
    sent_at, active = [], 0

    async def advance(delay):
        clock.now += delay
        await real_sleep(0)

    async def reply(payload):
        nonlocal active
        active += 1
        assert active == 1
        sent_at.append(clock.now)
        await real_sleep(0)
        active -= 1
        return runtime_reply() if "IcTrendDList" in payload else "other read"

    monkeypatch.setattr("pymelans.client.asyncio", SimpleNamespace(sleep=advance))
    c._ws.request = AsyncMock(side_effect=reply)
    c._http.request = AsyncMock()
    async with c._io_lock:
        action = asyncio.create_task(c.async_read_member_runtime("1", DAY, "FAN_TIME"))
        await real_sleep(0)
        c._ws.request.assert_not_awaited()
    other = asyncio.create_task(c._request('<Packet><Command>getRequest</Command><DatabaseManager><SystemData Version="*" /></DatabaseManager></Packet>'))
    result, _ = await asyncio.gather(action, other)
    assert result["read_status"] == "updated" and len(sent_at) == 2
    assert sent_at[1] - sent_at[0] >= 1.5
    c._http.request.assert_not_awaited()
