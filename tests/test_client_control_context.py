"""Explicit stop-context reads remain separate from group polling and commands."""

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


def packet(tag, attributes):
    root = ET.Element("Packet")
    ET.SubElement(root, "Command").text = "getResponse"
    manager = ET.SubElement(root, "DatabaseManager")
    ET.SubElement(manager, tag, {key: value for key, value in attributes.items() if value is not None})
    return ET.tostring(root, encoding="unicode")


def global_reply(value="OFF"):
    return packet("SystemData", {"AllTurnOff": value, "Name": "private-controller"})


def group_reply(group="1", turn_off="OFF", bacnet="OFF"):
    return packet("Mnet", {
        "Group": group, "TurnOff": turn_off, "BACnetTurnOff": bacnet,
        "Name": "private-room", "AlarmCode": "private-fault",
    })


@pytest.fixture
def client(monkeypatch):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr("pymelans.client.time", SimpleNamespace(
        monotonic=lambda: clock.now, perf_counter=lambda: clock.now,
    ))
    c = MelansClient("fixture.invalid", stale_after=20)
    c._names = {"1": "private-room", "2": None}
    c._request = AsyncMock(side_effect=[global_reply(), group_reply()])
    return c, clock


def assert_typed_unknown(context):
    assert context["global"]["global_emergency_stop"] is None
    assert context["group"]["bacnet_emergency_stop"] is None
    assert context["group"]["energy_saving_stop"] is None
    assert context["control_inhibited"] is None


@pytest.mark.asyncio
async def test_explicit_action_sends_only_two_minimal_reads_for_a_known_group(client):
    c, _ = client
    result = await c.async_read_control_context("001")
    assert result["group_id"] == "1" and result["read_status"] == "updated"
    assert c._request.await_count == 2
    requests = [ET.fromstring(call.args[0]) for call in c._request.call_args_list]
    assert [root.findtext("./Command") for root in requests] == ["getRequest", "getRequest"]
    assert requests[0].find("./DatabaseManager/SystemData").attrib == {"AllTurnOff": "*"}
    assert requests[1].find("./DatabaseManager/Mnet").attrib == {
        "Group": "1", "TurnOff": "*", "BACnetTurnOff": "*",
    }
    assert all(len(root.find("./DatabaseManager")) == 1 for root in requests)
    assert result["global"]["raw_attributes"] == {"AllTurnOff": "OFF"}
    assert result["group"]["raw_attributes"] == {
        "Group": "1", "TurnOff": "OFF", "BACnetTurnOff": "OFF",
    }
    assert result["global"]["quality"] == result["group"]["quality"] == "fresh"
    assert result["global"]["error"] is result["group"]["error"] is None
    assert result["global"]["last_attempt"] <= result["global"]["last_successful_read"]
    assert result["group"]["last_attempt"] <= result["group"]["last_successful_read"]
    assert result["control_inhibited"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("group", [0, 51, True, "*", "", "3", "1,2", "1.0", "١", " 1"])
async def test_unknown_or_invalid_groups_do_not_probe_or_change_context_cache(client, group):
    c, _ = client
    before = copy.deepcopy(c.control_context_snapshot())
    with pytest.raises(ValueError):
        await c.async_read_control_context(group)
    c._request.assert_not_called()
    assert c.control_context_snapshot() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("global_stop,energy_stop,bacnet_stop,expected", [
    ("OFF", "OFF", "OFF", False),
    ("ON", "OFF", "OFF", True),
    ("OFF", "ON", "OFF", True),
    ("OFF", "OFF", "ON", True),
    ("ON", "FUTURE", "*", True),
    ("FUTURE", "OFF", "OFF", None),
    ("OFF", "FUTURE", "OFF", None),
    ("OFF", "OFF", "FUTURE", None),
    (None, "", "*", None),
])
async def test_stop_sources_have_nullable_or_semantics_without_inventing_off(
    client, global_stop, energy_stop, bacnet_stop, expected,
):
    c, _ = client
    c._request.side_effect = [global_reply(global_stop), group_reply(turn_off=energy_stop, bacnet=bacnet_stop)]
    result = await c.async_read_control_context("1")
    assert result["read_status"] == "updated"
    assert result["global"]["quality"] == result["group"]["quality"] == "fresh"
    assert result["control_inhibited"] is expected
    for raw, record, field in (
        (global_stop, result["global"], "global_emergency_stop"),
        (energy_stop, result["group"], "energy_saving_stop"),
        (bacnet_stop, result["group"], "bacnet_emergency_stop"),
    ):
        assert record[field] is (True if raw == "ON" else False if raw == "OFF" else None)


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", ["global", "group", "both"])
async def test_failures_are_independent_sanitised_and_do_not_claim_fresh_samples(client, failed):
    c, _ = client
    error = MelansProtocolError("private-controller-message", code="0101", point="private-point")
    c._request.side_effect = [
        error if failed in ("global", "both") else global_reply("ON"),
        error if failed in ("group", "both") else group_reply(turn_off="ON"),
    ]
    result = await c.async_read_control_context("1")
    assert c._request.await_count == 2
    assert result["read_status"] == ("error" if failed == "both" else "partial")
    for domain in ("global", "group"):
        record = result[domain]
        if failed in (domain, "both"):
            assert record["quality"] == "unknown"
            assert record["last_attempt"] is not None and record["last_successful_read"] is None
            assert record["error"] == {"type": "MelansProtocolError", "code": "0101"}
        else:
            assert record["quality"] == "fresh" and record["error"] is None
    assert result["control_inhibited"] is (None if failed == "both" else True)
    assert "private-" not in json.dumps(result)


@pytest.mark.asyncio
async def test_unsafe_error_codes_and_messages_are_not_exposed(client):
    c, _ = client
    c._request.side_effect = [
        MelansProtocolError("http://private.invalid/secret", code="http://private.invalid/0101"),
        MelansConnectionError("private-controller-message"),
    ]
    result = await c.async_read_control_context("1")
    assert result["global"]["error"] == {"type": "MelansProtocolError", "code": None}
    assert result["group"]["error"] == {"type": "MelansConnectionError", "code": None}
    assert "private" not in json.dumps(result) + json.dumps(cached_snapshot(c))


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    group_reply(group="2"),
    packet("Mnet", {"Group": "1", "Address": "1", "TurnOff": "OFF"}),
    '<Packet><Command>getResponse</Command><DatabaseManager>'
    '<Mnet Group="1" TurnOff="OFF" /><Mnet Group="1" TurnOff="ON" />'
    '</DatabaseManager></Packet>',
    '<Packet><Command>getErrorResponse</Command><DatabaseManager>'
    '<ERROR Code="0101" Message="private-controller-message" />'
    '<Mnet Group="1" TurnOff="OFF" BACnetTurnOff="OFF" /></DatabaseManager></Packet>',
    '<Packet><Command>notifyRequest</Command><DatabaseManager>'
    '<Mnet Group="1" TurnOff="OFF" BACnetTurnOff="OFF" /></DatabaseManager></Packet>',
], ids=["wrong-group", "address-record", "conflicting-records", "partial-error", "notification"])
async def test_invalid_or_partial_group_replies_cannot_publish_a_fresh_group_sample(client, response):
    c, _ = client
    c._request.side_effect = [global_reply(), response]
    result = await c.async_read_control_context("1")
    assert result["read_status"] == "partial" and c._request.await_count == 2
    assert result["global"]["quality"] == "fresh"
    assert result["group"]["quality"] == "unknown"
    assert result["group"]["last_successful_read"] is None
    assert result["group"]["raw_attributes"] == {}
    assert result["group"]["energy_saving_stop"] is result["group"]["bacnet_emergency_stop"] is None
    assert result["control_inhibited"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", ["global", "group", "both"])
async def test_read_failure_keeps_old_raw_and_success_time_but_withholds_old_booleans(client, failed):
    c, clock = client
    c._request.side_effect = [global_reply("ON"), group_reply(turn_off="ON", bacnet="ON")]
    previous = await c.async_read_control_context("1")
    clock.now += 15
    error = MelansConnectionError("private-controller-message")
    c._request.side_effect = [
        error if failed in ("global", "both") else global_reply(),
        error if failed in ("group", "both") else group_reply(),
    ]
    result = await c.async_read_control_context("1")
    for domain in ("global", "group"):
        if failed in (domain, "both"):
            assert result[domain]["quality"] == "stale"
            assert result[domain]["last_successful_read"] == previous[domain]["last_successful_read"]
            assert result[domain]["raw_attributes"] == previous[domain]["raw_attributes"]
        else:
            assert result[domain]["quality"] == "fresh"
    if failed in ("global", "both"):
        assert result["global"]["global_emergency_stop"] is None
    if failed in ("group", "both"):
        assert result["group"]["energy_saving_stop"] is result["group"]["bacnet_emergency_stop"] is None
    assert result["control_inhibited"] is None


@pytest.mark.asyncio
async def test_expired_context_does_not_claim_current_restrictions(client):
    c, clock = client
    c._request.side_effect = [global_reply("ON"), group_reply(turn_off="ON", bacnet="ON")]
    previous = await c.async_read_control_context("1")
    clock.now += c.stale_after + 0.01
    current = c.control_context_snapshot("1")
    assert_typed_unknown(current)
    for domain in ("global", "group"):
        assert current[domain]["quality"] == "stale"
        assert current[domain]["raw_attributes"] == previous[domain]["raw_attributes"]
        assert current[domain]["last_successful_read"] == previous[domain]["last_successful_read"]
    assert c._request.await_count == 2


@pytest.mark.asyncio
async def test_global_context_can_be_refreshed_without_renewing_another_group(client):
    c, clock = client
    c._request.side_effect = [global_reply(), group_reply(turn_off="ON")]
    first = await c.async_read_control_context("1")
    clock.now += 15
    c._request.side_effect = [global_reply(), group_reply(group="2")]
    await c.async_read_control_context("2")
    clock.now += 6
    old_group = c.control_context_snapshot("1")
    assert old_group["global"]["quality"] == "fresh"
    assert old_group["global"]["global_emergency_stop"] is False
    assert old_group["group"]["quality"] == "stale"
    assert old_group["group"]["last_successful_read"] == first["group"]["last_successful_read"]
    assert old_group["group"]["energy_saving_stop"] is None
    assert old_group["control_inhibited"] is None


@pytest.mark.asyncio
async def test_cooldown_starts_after_both_reads_and_is_shared_across_groups(client):
    c, clock = client

    async def delayed_reply(payload):
        node = ET.fromstring(payload).find("./DatabaseManager")[0]
        clock.now += 20
        return global_reply() if node.tag == "SystemData" else group_reply()

    c._request.side_effect = delayed_reply
    first = await c.async_read_control_context("1")
    completed_at = clock.now
    clock.now = completed_at + 14.99
    for group in ("1", "2"):
        result = await c.async_read_control_context(group)
        assert result["read_status"] == "cooldown"
    assert c._request.await_count == 2
    current = c.control_context_snapshot("1")
    assert current["global"]["last_successful_read"] == first["global"]["last_successful_read"]
    assert current["group"]["last_successful_read"] == first["group"]["last_successful_read"]
    assert c.control_context_snapshot("2")["group"]["last_attempt"] is None
    clock.now = completed_at + 15
    c._request.side_effect = [global_reply(), group_reply(group="2")]
    assert (await c.async_read_control_context("2"))["read_status"] == "updated"
    assert c._request.await_count == 4


@pytest.mark.asyncio
async def test_concurrent_actions_cannot_bypass_the_global_cooldown(client):
    c, clock = client
    started, release = asyncio.Event(), asyncio.Event()

    async def reply(payload):
        node = ET.fromstring(payload).find("./DatabaseManager")[0]
        if node.tag == "SystemData":
            started.set()
            await release.wait()
            clock.now += 40
            return global_reply()
        return group_reply()

    c._request.side_effect = reply
    first = asyncio.create_task(c.async_read_control_context("1"))
    await started.wait()
    second = asyncio.create_task(c.async_read_control_context("2"))
    await asyncio.sleep(0)
    c._request.assert_awaited_once()
    release.set()
    first_result, second_result = await asyncio.gather(first, second)
    assert first_result["read_status"] == "updated"
    assert second_result["read_status"] == "cooldown"
    assert c._request.await_count == 2
    assert second_result["group"]["last_attempt"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", [1, 2], ids=["global-read", "group-read"])
async def test_cancellation_propagates_releases_action_lock_and_applies_completion_cooldown(client, stage):
    c, clock = client
    started = asyncio.Event()
    calls = 0

    async def wait_for_cancellation(_):
        nonlocal calls
        calls += 1
        if calls == stage:
            started.set()
            await asyncio.Event().wait()
        return global_reply()

    c._request.side_effect = wait_for_cancellation
    action = asyncio.create_task(c.async_read_control_context("1"))
    await started.wait()
    action.cancel()
    with pytest.raises(asyncio.CancelledError):
        await action
    assert c._request.await_count == stage
    assert (await c.async_read_control_context("2"))["read_status"] == "cooldown"
    assert c._request.await_count == stage
    clock.now += 15
    c._request.side_effect = [global_reply(), group_reply(group="2")]
    assert (await c.async_read_control_context("2"))["read_status"] == "updated"
    assert c._request.await_count == stage + 2


@pytest.mark.asyncio
async def test_context_exports_are_deep_copies_and_never_read_on_cache_access(client):
    c, _ = client
    initial = c.control_context_snapshot("1")
    assert_typed_unknown(initial)
    c._request.assert_not_called()
    action = await c.async_read_control_context("1")
    expected = copy.deepcopy(c.control_context_snapshot())
    action["global"]["raw_attributes"]["AllTurnOff"] = "ON"
    action["group"]["raw_attributes"]["TurnOff"] = "ON"
    exported = cached_snapshot(c)["control_context"]
    assert exported == expected
    exported["global"]["raw_attributes"]["AllTurnOff"] = "ON"
    exported["groups"]["1"]["raw_attributes"]["BACnetTurnOff"] = "ON"
    assert c.control_context_snapshot() == expected
    assert c.control_context_snapshot("1")["control_inhibited"] is False
    assert "private" not in json.dumps(cached_snapshot(c))
    assert c._request.await_count == 2


@pytest.mark.asyncio
async def test_context_error_exports_are_copies_of_sanitised_error_cache(client):
    c, _ = client
    c._request.side_effect = [MelansProtocolError("private", code="0101"), group_reply()]
    action = await c.async_read_control_context("1")
    action["global"]["error"]["code"] = "changed"
    export = c.control_context_snapshot()
    assert export["global"]["error"]["code"] == "0101"
    export["global"]["error"]["type"] = "changed"
    assert c.control_context_snapshot("1")["global"]["error"]["type"] == "MelansProtocolError"


@pytest.mark.asyncio
async def test_context_does_not_refresh_group_health_or_confirm_a_command(client):
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
    previous_command = copy.deepcopy(c.last_command)
    previous_health = c.health
    await c.async_read_control_context("1")
    assert c.last_attempt == c.last_success == at
    assert c.group_last_success == {"1": at} and c._group_seen == {"1": 100.0}
    assert c.failed_groups == {"2"} and c.health == previous_health
    assert c.last_command == previous_command and c._pending_writes == {"1": {"Drive": "ON"}}
    assert c._readback_requested
    assert c.devices["1"].raw_attributes() == {"Drive": "OFF", "Mode": "COOL"}


@pytest.mark.asyncio
async def test_regular_poll_does_not_automatically_read_optional_context(client):
    c, _ = client
    c._request.side_effect = None
    c._request.return_value = (
        '<Packet><Command>getResponse</Command><DatabaseManager>'
        '<Mnet Group="1" Drive="OFF" /><Mnet Group="2" Drive="OFF" />'
        '</DatabaseManager></Packet>'
    )
    await c.async_update()
    c._request.assert_awaited_once()
    nodes = ET.fromstring(c._request.call_args.args[0]).findall("./DatabaseManager/Mnet")
    assert len(nodes) == 2
    assert all("TurnOff" not in node.attrib and "BACnetTurnOff" not in node.attrib for node in nodes)
    assert c.control_context_snapshot("1")["global"]["last_attempt"] is None
    assert c.control_context_snapshot("1")["group"]["last_attempt"] is None


@pytest.mark.asyncio
async def test_optional_context_reuses_common_io_lock_and_request_spacing(client, monkeypatch):
    c, clock = client
    del c._request
    monkeypatch.setattr(const, "MIN_REQUEST_SPACING", 1.5)
    real_sleep = asyncio.sleep
    sent_at, active = [], 0

    async def advance(delay):
        clock.now += delay
        await real_sleep(0)

    monkeypatch.setattr("pymelans.client.asyncio", SimpleNamespace(sleep=advance))

    async def wire_reply(payload):
        nonlocal active
        active += 1
        assert active == 1
        sent_at.append(clock.now)
        await real_sleep(0)
        active -= 1
        node = ET.fromstring(payload).find("./DatabaseManager")[0]
        return global_reply() if node.tag == "SystemData" else group_reply()

    c._ws.request = AsyncMock(side_effect=wire_reply)
    c._http.request = AsyncMock()
    async with c._io_lock:
        action = asyncio.create_task(c.async_read_control_context("1"))
        await real_sleep(0)
        c._ws.request.assert_not_awaited()
    ordinary = asyncio.create_task(c._request(
        '<Packet><Command>getRequest</Command><DatabaseManager><SystemData Version="*" />'
        '</DatabaseManager></Packet>'
    ))
    result, _ = await asyncio.gather(action, ordinary)
    assert result["read_status"] == "updated"
    assert c._ws.request.await_count == 3
    assert all(later - earlier >= 1.5 for earlier, later in zip(sent_at, sent_at[1:]))
    c._http.request.assert_not_awaited()
