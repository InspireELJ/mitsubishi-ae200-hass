"""Capability and compound-write contracts based on the vendor web protocol."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pymelans.capabilities import air_directions, fan_speeds, modes
from pymelans.exceptions import MelansProtocolError
from pymelans.protocol import build_capability_request

from pymelans import MelansClient, MelansDevice

CAPS = {
    "Model": "IC",
    "IcKind": "NORMAL",
    "AutoModeSW": "DISABLE",
    "DryModeSW": "ENABLE",
    "FanModeSW": "ENABLE",
    "FanSpeedSW": "4STAGES",
    "FanAutoSW": "ENABLE",
    "AirDirectionSW": "ENABLE",
    "AirStageSW": "5STAGES",
    "AirAutoSW": "ENABLE",
    "SwingSW": "ENABLE",
    "TempDetailC": "ENABLE",
    "TempRange1Low1": "19",
    "TempRange1High1": "30",
    "TempRange1Low2": "19",
    "TempRange1High2": "28",
    "TempRange1Low3": "17",
    "TempRange1High3": "28",
}
ACK = "<Packet><Command>setResponse</Command><DatabaseManager /></Packet>"


@pytest.mark.parametrize(
    "stages,extra,expected",
    [
        ("4STAGES", {}, ["LOW", "MID2", "MID1", "HIGH"]),
        ("3STAGES", {}, ["MID2", "MID1", "HIGH"]),
        ("2STAGES", {}, ["MID2", "HIGH"]),
        ("2STAGES", {"FanExLowSW": "ENABLE"}, ["LOW", "MID2", "HIGH"]),
        (
            "2STAGES",
            {"FanExLowSW": "ENABLE", "FanExHighSW": "ENABLE"},
            ["LOW", "MID2", "MID1", "HIGH"],
        ),
    ],
)
def test_fan_stage_order(stages, extra, expected):
    assert fan_speeds({"FanSpeedSW": stages, **extra}) == expected
    assert fan_speeds({"FanSpeedSW": stages, **extra, "FanAutoSW": "ENABLE"}) == [*expected, "AUTO"]


def test_unknown_and_disabled_capabilities_remain_distinct():
    assert fan_speeds({}) is None
    assert fan_speeds({"FanSpeedSW": "NONE", "FanAutoSW": "ENABLE"}) == []
    assert air_directions({}) is None
    assert air_directions({"AirDirectionSW": "DISABLE"}) == []
    assert modes({}) is None
    assert modes(CAPS) == ["COOL", "HEAT", "DRY", "FAN"]


def test_directions_use_vendor_positions_and_optional_features():
    assert air_directions(CAPS) == [
        "HORIZONTAL",
        "MID0",
        "MID1",
        "MID2",
        "VERTICAL",
        "AUTO",
        "SWING",
    ]
    assert air_directions({**CAPS, "AirStageSW": "4STAGES", "SwingSW": "DISABLE"}) == [
        "HORIZONTAL",
        "MID1",
        "MID2",
        "VERTICAL",
        "AUTO",
    ]


def test_mode_specific_ranges_and_step_do_not_use_remote_lockout_flags():
    dev = MelansDevice("1", None, {"Mode": "COOL", "ModeItem": "CHK_ON"}, CAPS)
    assert dev.temperature_range() == (19, 30)
    assert dev.temperature_range("DRY") == (19, 30)
    assert dev.temperature_range("HEAT") == (17, 28)
    assert dev.temperature_range("FAN") is None
    assert dev.temperature_step == 0.5
    assert dev.supported_modes == ["COOL", "HEAT", "DRY", "FAN"]


@pytest.mark.asyncio
async def test_compound_command_is_one_request_without_optimistic_cache():
    client = MelansClient("fixture.invalid")
    client.devices["1"] = MelansDevice("1", None, {"Mode": "COOL", "Drive": "OFF"}, CAPS)
    client._request = AsyncMock(return_value=ACK)
    await client.set_climate("01", power=True, mode="heat", temperature=20.5)
    client._request.assert_awaited_once()
    node = ET.fromstring(client._request.call_args.args[0]).find("./DatabaseManager/Mnet")
    assert node.attrib == {"Group": "1", "Drive": "ON", "Mode": "HEAT", "SetTemp": "20.5"}
    assert client.devices["1"].is_on is False
    assert client.last_command["status"] == "accepted_unconfirmed"
    client._confirm_readback("1", {"Drive": "ON", "Mode": "HEAT", "SetTemp": "20.50"})
    assert client.last_command["status"] == "confirmed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "settings",
    [
        {},
        {"power": 1},
        {"mode": "AUTO", "power": True},
        {"temperature": float("nan")},
        {"temperature": 18},
        {"temperature": 20.1},
        {"mode": "bad", "power": True},
    ],
)
async def test_invalid_compound_setting_sends_nothing(settings):
    client = MelansClient("fixture.invalid")
    client.devices["1"] = MelansDevice("1", None, {"Mode": "COOL"}, CAPS)
    client._request = AsyncMock(return_value=ACK)
    with pytest.raises(ValueError):
        await client.set_climate("1", **settings)
    client._request.assert_not_awaited()


@pytest.mark.asyncio
async def test_capability_query_is_bounded_optional_and_does_not_mark_state_fresh():
    client = MelansClient("fixture.invalid")
    client._names = {"1": None}
    values = ET.Element("Mnet", {"Group": "1", **CAPS, "Password": "not-a-capability"})
    client._request = AsyncMock(
        return_value=(
            "<Packet><Command>getResponse</Command><DatabaseManager>"
            + ET.tostring(values, encoding="unicode")
            + "</DatabaseManager></Packet>"
        )
    )
    await client.async_load_capabilities()
    assert client.group_capabilities["1"] == CAPS
    assert client.last_success is None and client.devices == {}
    client._request = AsyncMock(side_effect=MelansProtocolError("unsupported"))
    await client.async_load_capabilities()
    assert client.capability_error == "MelansProtocolError"
    client._request.assert_awaited_once()
    with pytest.raises(ValueError):
        build_capability_request([])
    with pytest.raises(ValueError):
        build_capability_request(range(1, 52))


@pytest.mark.asyncio
async def test_new_group_temperature_is_not_sent_with_legacy_field():
    client = MelansClient("fixture.invalid")
    client.devices["1"] = MelansDevice("1", None, {"Mode": "COOL"},
                                     {**CAPS, "GroupType": "NEW", "AutoModeSWEx": "TWO_SETPOINT"})
    client._request = AsyncMock(return_value=ACK)
    with pytest.raises(ValueError, match="NEW group"):
        await client.set_climate("1", power=True, temperature=20)
    client._request.assert_not_awaited()
    assert client.devices["1"].supported_modes is None


@pytest.mark.asyncio
async def test_latest_command_confirmation_is_not_held_by_older_unconfirmed_fields():
    client = MelansClient("fixture.invalid")
    client._request = AsyncMock(return_value=ACK)
    await client.set_fan_speed("1", "HIGH")
    client._last_write = 0
    await client.set_power("1", False)
    client._confirm_readback("1", {"Drive": "OFF", "FanSpeed": "LOW"})
    assert client.last_command["status"] == "confirmed"
    assert client._pending_writes["1"] == {"FanSpeed": "HIGH"}


@pytest.mark.asyncio
async def test_compound_readback_requires_one_matching_sample_after_partial_matches():
    client = MelansClient("fixture.invalid")
    client._request = AsyncMock(return_value=ACK)
    await client.set_climate("1", mode="HEAT", power=True)

    client._confirm_readback("1", {"Mode": "HEAT", "Drive": "OFF"})
    assert client.last_command["status"] == "not_yet_confirmed"
    client._confirm_readback("1", {"Mode": "COOL", "Drive": "ON"})
    assert client.last_command["status"] == "not_yet_confirmed"
    assert "1" not in client._pending_writes

    client._confirm_readback("1", {"Mode": "HEAT", "Drive": "ON"})
    assert client.last_command["status"] == "confirmed"
    client._request.assert_awaited_once()


@pytest.mark.asyncio
async def test_poll_sample_before_write_cannot_confirm_at_later_poll_commit(monkeypatch):
    clock = SimpleNamespace(now=10.0)
    monkeypatch.setattr(
        "pymelans.client.time",
        SimpleNamespace(monotonic=lambda: clock.now, perf_counter=lambda: clock.now),
    )
    client = MelansClient("fixture.invalid")
    client._names = {"1": None, "2": None}
    poll_requests = 0

    def state_response(*groups):
        return (
            "<Packet><Command>getResponse</Command><DatabaseManager>"
            + "".join(f'<Mnet Group="{gid}" Drive="OFF" />' for gid in groups)
            + "</DatabaseManager></Packet>"
        )

    async def request(payload):
        nonlocal poll_requests
        if ET.fromstring(payload).findtext("./Command") == "setRequest":
            clock.now = 21.0
            return ACK
        poll_requests += 1
        if poll_requests == 1:
            # Firmware returns only the first group's sample from the batch.
            return state_response("1")
        if poll_requests == 2:
            # A write completes after group 1 was sampled but before this
            # poll commits either group's state at the end of its loop.
            clock.now = 20.0
            await client.set_power("1", False)
            clock.now = 30.0
            return state_response("2")
        clock.now = 40.0
        return state_response("1", "2")

    client._request = AsyncMock(side_effect=request)
    await client.async_update()
    assert client._group_seen["1"] == 10.0
    assert client._group_write_ack_at["1"] == 21.0
    assert client.last_command["status"] == "accepted_unconfirmed"
    assert client._pending_writes["1"] == {"Drive": "OFF"}
    assert client._readback_requested
    assert set(client.last_command) == {"group_id", "attributes", "at", "status"}

    await client.async_update()
    assert client.last_command["status"] == "confirmed"
    assert "1" not in client._pending_writes
