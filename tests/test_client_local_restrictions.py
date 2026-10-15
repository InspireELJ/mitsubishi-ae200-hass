"""Fresh guarded wall-remote transactions never repeat or invent an ACK."""

from __future__ import annotations

import asyncio
import copy
import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pymelans.exceptions import (
    MelansCommandUncertainError,
    MelansConnectionError,
    MelansProtocolError,
)

from pymelans import MelansClient, MelansDevice, cached_snapshot, const

CAPS = {"Model": "IC", "IcKind": "NORMAL", "GroupType": "OLD"}
FIELDS = ("DriveItem", "ModeItem", "SetTempItem", "FilterItem")
RAW = {"RemoCon": "PERMIT", **dict.fromkeys(FIELDS, "CHK_ON")}
EXPECTED = {"RemoCon": "PROHIBIT", "DriveItem": "CHK_OFF", "ModeItem": "CHK_OFF", "SetTempItem": "CHK_OFF", "FilterItem": "CHK_ON"}
ACK = "<Packet><Command>setResponse</Command><DatabaseManager /></Packet>"
REJECT = ('<Packet><Command>setErrorResponse</Command><DatabaseManager>'
          '<ERROR Code="0101" Message="private-message" /></DatabaseManager></Packet>')


def reply(tag, attributes, command="getResponse"):
    root = ET.Element("Packet")
    ET.SubElement(root, "Command").text = command
    ET.SubElement(ET.SubElement(root, "DatabaseManager"), tag, attributes)
    return ET.tostring(root, encoding="unicode")


class Wire:
    """In-memory request recorder; never creates a network connection."""

    def __init__(self):
        self.calls = []
        self.raw = {"1": dict(RAW), "2": dict(RAW)}
        self.global_stop = "OFF"
        self.group_stops = {"TurnOff": "OFF", "BACnetTurnOff": "OFF"}
        self.after_send = False
        self.ack = ACK
        self.write_error = None
        self.apply_write = True
        self.post_error = None
        self.post_raw = None
        self.baseline_error = None
        self.global_response = None
        self.hook = None

    async def request(self, payload):
        root = ET.fromstring(payload)
        command = root.findtext("./Command")
        node = root.find("./DatabaseManager")[0]
        attrs = dict(node.attrib)
        group = attrs.get("Group")
        if command == "setRequest":
            kind = "set" if "RemoCon" in attrs else "legacy"
        elif node.tag == "SystemData":
            kind = "global"
            self.after_send = False
        elif "TurnOff" in attrs:
            kind = "stops"
        elif "RemoCon" in attrs:
            kind = "post" if self.after_send else "baseline"
        else:
            raise AssertionError("unexpected request in fixture")
        self.calls.append((kind, group, attrs))
        if self.hook:
            await self.hook(kind, group, attrs)
        if kind == "global":
            return self.global_response or reply("SystemData", {"AllTurnOff": self.global_stop})
        if kind == "stops":
            return reply("Mnet", {"Group": group, **self.group_stops})
        if kind == "baseline":
            if self.baseline_error:
                raise self.baseline_error
            return reply("Mnet", {"Group": group, **self.raw[group]})
        if kind == "set":
            self.after_send = True
            if self.apply_write:
                self.raw[group] = {key: value for key, value in attrs.items() if key != "Group"}
            if self.write_error:
                raise self.write_error
            return self.ack
        if kind == "post":
            if self.post_error:
                raise self.post_error
            return reply("Mnet", {"Group": group, **(self.raw[group] if self.post_raw is None else self.post_raw)})
        return ACK


@pytest.fixture
def client(monkeypatch):
    clock = SimpleNamespace(now=100.0, perf=0)

    def perf_counter():
        clock.perf += 1
        return clock.now + clock.perf / 1000

    monkeypatch.setattr("pymelans.client.time", SimpleNamespace(monotonic=lambda: clock.now, perf_counter=perf_counter))
    monkeypatch.setattr(const, "MIN_WRITE_SPACING", 0)
    c = MelansClient("fixture.invalid", stale_after=90, write_spacing=0)
    c._names = {"1": "private-room", "2": None}
    at = datetime(2025, 1, 1, tzinfo=UTC)
    c.last_attempt = c.last_success = at
    for group in c._names:
        c.devices[group] = MelansDevice(group, None, {"Drive": "OFF", "Mode": "COOL", **RAW}, dict(CAPS))
        c.group_capabilities[group] = dict(CAPS)
        c.group_last_success[group] = at
        c._group_seen[group] = clock.now
    wire = Wire()
    c._request = AsyncMock(side_effect=wire.request)
    return c, clock, wire


def kinds(wire):
    return [kind for kind, _, _ in wire.calls]


@pytest.mark.asyncio
async def test_fresh_transaction_reads_three_guards_writes_five_fields_once_then_confirms(client):
    c, _, wire = client
    result = await c.async_set_local_restrictions("001", {"filter_reset": True})
    assert kinds(wire) == ["global", "stops", "baseline", "set", "post"]
    assert wire.calls[0][2] == {"AllTurnOff": "*"}
    assert wire.calls[1][2] == {"Group": "1", "TurnOff": "*", "BACnetTurnOff": "*"}
    minimal = {"Group": "1", **dict.fromkeys(RAW, "*")}
    assert wire.calls[2][2] == wire.calls[4][2] == minimal
    assert wire.calls[3][2] == {"Group": "1", **EXPECTED}
    assert result["group_id"] == "1" and result["status"] == "confirmed"
    assert result["before_raw"] == RAW and result["expected_raw"] == result["after_raw"] == EXPECTED
    assert result["before"] == dict.fromkeys(("power", "mode", "temperature", "filter_reset"), False)
    assert result["after"] == {"power": False, "mode": False, "temperature": False, "filter_reset": True}
    assert result["readback_matches"] is True and result["error"] is None
    assert result["write_error"] is result["readback_error"] is None
    assert datetime.fromisoformat(result["at"]).utcoffset().total_seconds() == 0
    assert result["readback_at"] >= result["at"]
    assert c.last_command["status"] == "confirmed" and c.last_command["attributes"] == EXPECTED
    assert not c._pending_writes


@pytest.mark.asyncio
@pytest.mark.parametrize("patch", [{}, {"power": None}, {"power": 0}, {"power": "true"}, {"Drive": True}, {"filter_reset": True, "mode": "bad"}])
async def test_invalid_patch_is_rejected_before_any_io(client, patch):
    c, _, wire = client
    with pytest.raises(ValueError):
        await c.async_set_local_restrictions("1", patch)
    assert not wire.calls and c.last_command is None


@pytest.mark.asyncio
@pytest.mark.parametrize("group", [0, 51, "3", "*", "1,2", "١"])
async def test_invalid_or_unconfigured_group_never_probes(client, group):
    c, _, wire = client
    with pytest.raises(ValueError):
        await c.async_set_local_restrictions(group, {"filter_reset": True})
    assert not wire.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["stale", "failed", "missing-device", "missing-capability", "new", "other-model", "other-kind"])
async def test_freshness_and_exact_equipment_gates_precede_all_io(client, gate):
    c, clock, wire = client
    if gate == "stale":
        clock.now += c.stale_after + 1
    elif gate == "failed":
        c.failed_groups.add("1")
    elif gate == "missing-device":
        c.devices.pop("1")
    else:
        cap = c.devices["1"].capability_attributes
        if gate == "missing-capability":
            cap.pop("IcKind")
        else:
            cap[{"new": "GroupType", "other-model": "Model", "other-kind": "IcKind"}[gate]] = {
                "new": "NEW", "other-model": "LC", "other-kind": "HH",
            }[gate]
    with pytest.raises(ValueError):
        await c.async_set_local_restrictions("1", {"filter_reset": True})
    assert not wire.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["global", "bacnet", "energy"])
async def test_known_active_stop_source_blocks_all_writes(client, source):
    c, _, wire = client
    if source == "global":
        wire.global_stop = "ON"
    else:
        wire.group_stops["BACnetTurnOff" if source == "bacnet" else "TurnOff"] = "ON"
    with pytest.raises(ValueError):
        await c.async_set_local_restrictions("1", {"filter_reset": True})
    assert kinds(wire) == ["global", "stops"] and c.last_command is None


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["unknown-global", "unknown-group", "mixed-global", "missing-baseline", "unknown-item"])
async def test_unknown_or_malformed_prewrite_evidence_raises_without_setting_anything(client, defect):
    c, _, wire = client
    if defect == "unknown-global":
        wire.global_stop = "*"
    elif defect == "unknown-group":
        wire.group_stops["TurnOff"] = "FUTURE"
    elif defect == "mixed-global":
        wire.global_response = reply("SystemData", {"AllTurnOff": "OFF"}).replace("</SystemData>", "junk</SystemData>")
        wire.global_response = wire.global_response.replace(" />", ">junk</SystemData>")
    elif defect == "missing-baseline":
        wire.raw["1"].pop("ModeItem")
    else:
        wire.raw["1"]["ModeItem"] = "*"
    with pytest.raises(MelansProtocolError):
        await c.async_set_local_restrictions("1", {"filter_reset": True})
    assert "set" not in kinds(wire) and "post" not in kinds(wire) and c.last_command is None


@pytest.mark.asyncio
async def test_no_change_keeps_hidden_raw_bits_and_does_not_touch_an_old_command(client):
    c, _, wire = client
    old = {"group_id": "2", "attributes": {"Drive": "ON"}, "status": "accepted_unconfirmed"}
    c.last_command = copy.deepcopy(old)
    result = await c.async_set_local_restrictions("1", {"filter_reset": False})
    assert kinds(wire) == ["global", "stops", "baseline"]
    assert result["status"] == "no_change" and result["before_raw"] == result["expected_raw"] == RAW
    assert result["after_raw"] == {} and result["after"] is None
    assert result["readback_matches"] is result["readback_at"] is None
    assert c.last_command == old and wire.raw["1"] == RAW


@pytest.mark.asyncio
async def test_post_read_mismatch_is_not_confirmed_or_retried(client):
    c, _, wire = client
    wire.post_raw = dict(RAW)
    result = await c.async_set_local_restrictions("1", {"filter_reset": True})
    assert result["status"] == "not_yet_confirmed" and result["readback_matches"] is False
    assert result["after_raw"] == RAW and result["after"]["filter_reset"] is False
    assert c.last_command["status"] == "not_yet_confirmed"
    assert kinds(wire).count("set") == kinds(wire).count("post") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("post_error", [MelansConnectionError("private"), MelansProtocolError("private", code="0101")])
async def test_post_read_failure_leaves_acknowledged_write_unconfirmed(client, post_error):
    c, _, wire = client
    wire.post_error = post_error
    result = await c.async_set_local_restrictions("1", {"filter_reset": True})
    assert result["status"] == "accepted_unconfirmed"
    assert result["write_error"] is None and result["readback_error"] == result["error"]
    assert result["after_raw"] == {} and result["after"] is None
    assert result["readback_at"] is result["readback_matches"] is None
    assert c.last_command["status"] == "accepted_unconfirmed"
    assert kinds(wire) == ["global", "stops", "baseline", "set", "post"]


@pytest.mark.asyncio
@pytest.mark.parametrize("ack", [REJECT, REJECT.replace("setErrorResponse", "setResponse")])
async def test_explicit_controller_rejection_is_failed_without_a_postread(client, ack):
    c, _, wire = client
    wire.ack = ack
    wire.apply_write = False
    result = await c.async_set_local_restrictions("1", {"filter_reset": True})
    assert result["status"] == "failed" and result["write_error"]["type"] == "MelansProtocolError"
    assert result["readback_error"] is None and result["after_raw"] == {}
    assert c.last_command["status"] == "failed"
    assert kinds(wire) == ["global", "stops", "baseline", "set"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    "bad-xml", "wrong-command", "duplicate-command", "nested-rejection-command", "frame-protocol", "uncertain",
])
async def test_lost_or_invalid_ack_keeps_unknown_even_if_one_postread_matches(client, failure):
    c, _, wire = client
    if failure == "bad-xml":
        wire.ack = "not XML"
    elif failure == "wrong-command":
        wire.ack = ACK.replace("setResponse", "getResponse")
    elif failure == "duplicate-command":
        wire.ack = ACK.replace("</Command>", "</Command><Command>setResponse</Command>")
    elif failure == "nested-rejection-command":
        wire.ack = REJECT.replace("</Command>", "<Nested /></Command>")
    else:
        wire.write_error = MelansProtocolError("bad frame") if failure == "frame-protocol" else MelansCommandUncertainError("private")
    result = await c.async_set_local_restrictions("1", {"filter_reset": True})
    assert result["status"] == "outcome_unknown" and result["readback_matches"] is True
    assert result["after_raw"] == EXPECTED and result["after"]["filter_reset"] is True
    assert result["write_error"]["type"] == "MelansCommandUncertainError"
    assert c.last_command["status"] == "outcome_unknown" and not c._pending_writes
    assert kinds(wire) == ["global", "stops", "baseline", "set", "post"]


@pytest.mark.asyncio
async def test_uncertain_write_error_has_priority_over_postread_error_and_no_replay(client):
    c, _, wire = client
    wire.write_error = MelansCommandUncertainError("private-write")
    wire.post_error = MelansProtocolError("private-read", code="0101")
    result = await c.async_set_local_restrictions("1", {"filter_reset": True})
    assert result["status"] == "outcome_unknown" and result["readback_matches"] is None
    assert result["error"] == result["write_error"] == {"type": "MelansCommandUncertainError", "code": None}
    assert result["readback_error"] == {"type": "MelansProtocolError", "code": "0101"}
    assert kinds(wire).count("set") == kinds(wire).count("post") == 1
    assert "private" not in json.dumps(result)


@pytest.mark.asyncio
async def test_explicit_rejection_codes_are_sanitised_in_both_report_and_generic_diagnostics(client):
    c, _, wire = client
    wire.ack = REJECT.replace('Code="0101"', 'Code="http://private.invalid/secret"')
    result = await c.async_set_local_restrictions("1", {"filter_reset": True})
    assert result["status"] == "failed" and result["write_error"]["code"] is None
    assert c.last_command["error_code"] is None
    assert "private.invalid" not in json.dumps(cached_snapshot(c)) + json.dumps(result)


@pytest.mark.asyncio
async def test_global_completion_cooldown_applies_across_groups_and_failures(client):
    c, clock, wire = client

    async def delay_post(kind, group, attrs):
        if kind == "post":
            clock.now += 40

    wire.hook = delay_post
    await c.async_set_local_restrictions("1", {"filter_reset": True})
    completed = clock.now
    clock.now = completed + 14.99
    with pytest.raises(ValueError):
        await c.async_set_local_restrictions("2", {"filter_reset": True})
    assert len(wire.calls) == 5
    clock.now = completed + 15
    wire.hook = None
    wire.ack = REJECT
    assert (await c.async_set_local_restrictions("2", {"filter_reset": True}))["status"] == "failed"
    call_count = len(wire.calls)
    with pytest.raises(ValueError):
        await c.async_set_local_restrictions("1", {"filter_reset": False})
    assert len(wire.calls) == call_count


@pytest.mark.asyncio
async def test_freshness_and_capability_are_rechecked_after_waiting_for_write_lock(client):
    c, clock, wire = client
    async with c._write_lock:
        action = asyncio.create_task(c.async_set_local_restrictions("1", {"filter_reset": True}))
        await asyncio.sleep(0)
        assert not wire.calls
        clock.now += c.stale_after + 1
    with pytest.raises(ValueError):
        await action
    assert not wire.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["stale", "capability"])
async def test_group_gate_is_rechecked_after_pre_reads_before_any_set(client, changed):
    c, clock, wire = client

    async def alter_after_pre_read(kind, group, attrs):
        if kind == "baseline":
            if changed == "stale":
                clock.now += c.stale_after + 1
            else:
                c.devices["1"].capability_attributes["GroupType"] = "NEW"

    wire.hook = alter_after_pre_read
    with pytest.raises(ValueError):
        await c.async_set_local_restrictions("1", {"filter_reset": True})
    assert kinds(wire) == ["global", "stops", "baseline"]


@pytest.mark.asyncio
async def test_legacy_write_waits_for_whole_local_read_merge_write_postread_transaction(client):
    c, _, wire = client
    started, release = asyncio.Event(), asyncio.Event()

    async def hold_baseline(kind, group, attrs):
        if kind == "baseline":
            started.set()
            await release.wait()

    wire.hook = hold_baseline
    action = asyncio.create_task(c.async_set_local_restrictions("1", {"filter_reset": True}))
    await started.wait()
    legacy = asyncio.create_task(c.set_power("2", True))
    await asyncio.sleep(0)
    assert "legacy" not in kinds(wire)
    release.set()
    result, _ = await asyncio.gather(action, legacy)
    assert result["status"] == "confirmed"
    assert kinds(wire) == ["global", "stops", "baseline", "set", "post", "legacy"]


@pytest.mark.asyncio
async def test_concurrent_local_patches_cannot_bypass_completion_cooldown(client):
    c, _, wire = client
    started, release = asyncio.Event(), asyncio.Event()

    async def hold_baseline(kind, group, attrs):
        if kind == "baseline":
            started.set()
            await release.wait()

    wire.hook = hold_baseline
    first = asyncio.create_task(c.async_set_local_restrictions("1", {"filter_reset": True}))
    await started.wait()
    second = asyncio.create_task(c.async_set_local_restrictions("2", {"mode": True}))
    await asyncio.sleep(0)
    release.set()
    assert (await first)["status"] == "confirmed"
    with pytest.raises(ValueError):
        await second
    assert kinds(wire).count("set") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stage,command_status", [("baseline", None), ("set", "outcome_unknown"), ("post", "accepted_unconfirmed")])
async def test_cancellation_propagates_without_replay_and_distinguishes_prewrite_send_and_postread(client, stage, command_status):
    c, clock, wire = client
    started = asyncio.Event()

    async def block_stage(kind, group, attrs):
        if kind == stage:
            started.set()
            await asyncio.Event().wait()

    wire.hook = block_stage
    action = asyncio.create_task(c.async_set_local_restrictions("1", {"filter_reset": True}))
    await started.wait()
    clock.now += 1
    action.cancel()
    with pytest.raises(asyncio.CancelledError):
        await action
    if command_status is None:
        assert c.last_command is None and "set" not in kinds(wire)
    else:
        assert c.last_command["status"] == command_status and kinds(wire).count("set") == 1
        if stage == "set":
            assert c.last_command["error_type"] == "CancelledError" and not c._pending_writes
            assert "post" not in kinds(wire)
        assert c._readback_requested
    call_count = len(wire.calls)
    with pytest.raises(ValueError):
        await c.async_set_local_restrictions("2", {"filter_reset": True})
    assert len(wire.calls) == call_count


@pytest.mark.asyncio
async def test_cancellation_during_pacing_before_request_does_not_replace_old_last_command(client, monkeypatch):
    c, clock, wire = client
    old = {"group_id": "2", "attributes": {"Drive": "ON"}, "status": "accepted_unconfirmed"}
    c.last_command = copy.deepcopy(old)
    c._write_spacing = 2
    c._last_write = clock.now
    started = asyncio.Event()

    async def block_pacing(delay):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("pymelans.client.asyncio", SimpleNamespace(sleep=block_pacing, CancelledError=asyncio.CancelledError))
    action = asyncio.create_task(c.async_set_local_restrictions("1", {"filter_reset": True}))
    await started.wait()
    action.cancel()
    with pytest.raises(asyncio.CancelledError):
        await action
    assert kinds(wire) == ["global", "stops", "baseline"]
    assert c.last_command == old and c._last_write == clock.now


@pytest.mark.asyncio
async def test_pre_ack_poll_sample_cannot_confirm_new_local_command(client):
    c, _, wire = client
    started, release = asyncio.Event(), asyncio.Event()

    async def hold_post(kind, group, attrs):
        if kind == "post":
            started.set()
            await release.wait()

    wire.hook = hold_post
    action = asyncio.create_task(c.async_set_local_restrictions("1", {"filter_reset": True}))
    await started.wait()
    assert c.last_command["status"] == "accepted_unconfirmed"
    c._confirm_readback("1", EXPECTED, sampled_at=0)
    assert c.last_command["status"] == "accepted_unconfirmed" and c._pending_writes["1"] == EXPECTED
    release.set()
    assert (await action)["status"] == "confirmed" and c.last_command["status"] == "confirmed"


@pytest.mark.asyncio
async def test_transaction_does_not_refresh_climate_or_other_optional_caches_and_exports_are_independent(client):
    c, _, wire = client
    previous = (c.last_attempt, c.last_success, dict(c.group_last_success), dict(c._group_seen), c.health,
                copy.deepcopy(c.today_schedule_snapshot()), copy.deepcopy(c.controller_clock_snapshot()),
                copy.deepcopy(c.control_context_snapshot()), copy.deepcopy(c.weekly_pattern_snapshot()))
    c._pending_writes["1"] = {"Drive": "ON"}
    result = await c.async_set_local_restrictions("1", {"filter_reset": True})
    assert (c.last_attempt, c.last_success, c.group_last_success, c._group_seen, c.health) == previous[:5]
    assert c.devices["1"].raw("Drive") == "OFF" and c.devices["1"].raw("FilterItem") == "CHK_ON"
    assert c._pending_writes["1"] == {"Drive": "ON"}
    assert (c.today_schedule_snapshot(), c.controller_clock_snapshot(), c.control_context_snapshot(), c.weekly_pattern_snapshot()) == previous[5:]
    result["expected_raw"]["DriveItem"] = "CHK_ON"
    result["requested"]["filter_reset"] = False
    assert c.last_command["attributes"] == EXPECTED and wire.raw["1"] == EXPECTED
    assert "before_raw" not in json.dumps(cached_snapshot(c))


@pytest.mark.asyncio
async def test_all_transaction_requests_share_common_io_lock_spacing_and_uncertain_set_never_falls_back(client, monkeypatch):
    c, clock, wire = client
    del c._request
    monkeypatch.setattr(const, "MIN_REQUEST_SPACING", 1.5)
    real_sleep = asyncio.sleep
    sent_at = []

    async def advance(delay):
        clock.now += delay
        await real_sleep(0)

    async def request(payload):
        sent_at.append(clock.now)
        return await wire.request(payload)

    monkeypatch.setattr("pymelans.client.asyncio", SimpleNamespace(sleep=advance, CancelledError=asyncio.CancelledError))
    wire.write_error = MelansCommandUncertainError("private")
    c._ws.request = AsyncMock(side_effect=request)
    c._http.request = AsyncMock()
    async with c._io_lock:
        action = asyncio.create_task(c.async_set_local_restrictions("1", {"filter_reset": True}))
        await real_sleep(0)
        c._ws.request.assert_not_awaited()
    result = await action
    assert result["status"] == "outcome_unknown" and result["readback_matches"] is True
    assert kinds(wire) == ["global", "stops", "baseline", "set", "post"]
    assert all(later - earlier >= 1.5 for earlier, later in zip(sent_at, sent_at[1:]))
    c._http.request.assert_not_awaited()
