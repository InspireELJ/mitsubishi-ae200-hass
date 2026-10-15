"""Keep the runtime public protocol contract stable through core extraction."""

from __future__ import annotations

import pytest
from pymelans.daily_trend import (
    RawDailyTrendIssue,
    RawDailyTrendSlot,
    _raw_series,
    member_day_selector,
)
from pymelans.exceptions import MelansProtocolError
from pymelans.member_temperature_history import (
    TemperatureHistoryIssue,
    TemperatureHistorySlot,
    parse_member_temperature_history,
)

from pymelans import member_runtime


def test_shared_date_selector_is_copied_before_runtime_adds_its_channel():
    date_keys = member_day_selector("004", "2024-02-29")
    assert date_keys == {"Address": "4", "Year": "2024", "Month": "2", "Day": "29"}
    selected = member_runtime._selector("004", "2024-02-29", "FAN_TIME")
    selected["Month"] = "caller"
    assert member_day_selector("004", "2024-02-29") == date_keys
    assert member_runtime.MIN_YEAR == 1970 and member_runtime.MAX_YEAR == 2037
    assert member_runtime.BUCKET_COUNT == 48 and member_runtime.BUCKET_MINUTES == 30


@pytest.mark.parametrize("name", ["FAN_TIME", "THERMO_TIME"])
def test_runtime_request_serialisation_remains_byte_for_byte_compatible(name):
    assert member_runtime.build_member_runtime_request("004", "2024-02-29", name) == (
        '<?xml version="1.0" encoding="UTF-8"?><Packet><Command>getRequest</Command>'
        '<DatabaseManager><EnergyManagement><IcTrendDList Address="4" Year="2024" '
        f'Month="2" Day="29" DataName="{name}" /></EnergyManagement></DatabaseManager></Packet>'
    )


@pytest.mark.parametrize("address,day,name,message", [
    ("*", "not-date", "ROOM_TEMP", "identifier must be a bounded ASCII decimal integer"),
    ("4", "not-date", "ROOM_TEMP", "date must be a strict Gregorian ISO date"),
    ("4", "2023-02-29", "ROOM_TEMP", "date must be a valid Gregorian day"),
    ("4", "1969-12-31", "ROOM_TEMP", "date is outside the pilot interpretation window"),
    ("4", "2024-02-29", "ROOM_TEMP", "data_name must be FAN_TIME or THERMO_TIME"),
])
def test_runtime_validation_order_and_safe_error_text_are_unchanged(address, day, name, message):
    with pytest.raises(ValueError) as caught:
        member_runtime.build_member_runtime_request(address, day, name)
    assert str(caught.value) == message


@pytest.mark.parametrize("payload,message", [
    ('<Other />', "expected one getResponse database envelope"),
    ('<Packet><Command>getResponse</Command><DatabaseManager /><Extra /></Packet>',
     "unexpected member-runtime envelope children"),
    ('<Packet><Command>getResponse</Command><DatabaseManager /></Packet>',
     "expected one direct member-runtime EnergyManagement"),
    ('<Packet><Command>getResponse</Command><DatabaseManager><EnergyManagement />'
     '</DatabaseManager></Packet>', "expected one direct EnergyManagement/IcTrendDList"),
    ('<Packet><Command>getResponse</Command><DatabaseManager><EnergyManagement>'
     '<IcTrendDList /></EnergyManagement></DatabaseManager></Packet>',
     "member-runtime response selector mismatch"),
])
def test_runtime_error_context_is_preserved_after_shared_directory_extraction(payload, message):
    with pytest.raises(MelansProtocolError) as caught:
        member_runtime.parse_member_runtime(
            payload, expected_address="4", expected_date="2024-02-29", expected_data_name="FAN_TIME",
        )
    assert str(caught.value) == message


def room_packet(raw):
    return (
        '<Packet><Command>getResponse</Command><DatabaseManager><EnergyManagement>'
        '<IcTrendDList Address="4" Year="2024" Month="2" Day="29" DataName="ROOM_TEMP">'
        f'<IcTrendDRecord RoomTempData="{raw}" /></IcTrendDList>'
        '</EnergyManagement></DatabaseManager></Packet>'
    )


def test_raw_core_is_neutral_while_room_keeps_existing_public_slot_class_and_module():
    selector = {"Address": "4", "Year": "2024", "Month": "2", "Day": "29", "DataName": "ROOM_TEMP"}
    payload = room_packet("," * 47)
    shared = _raw_series(payload, selector, selected_field="RoomTempData", context="member-temperature-history")
    room = parse_member_temperature_history(payload, expected_address="4", expected_date="2024-02-29")
    assert shared.raw_tokens == room.raw_tokens == ("",) * 48
    assert type(shared.slots[0]) is RawDailyTrendSlot
    assert type(room.slots[0]) is TemperatureHistorySlot
    assert TemperatureHistorySlot.__module__ == "pymelans.member_temperature_history"
    assert repr(room.slots[0]) == "TemperatureHistorySlot(index=0, start_time='00:00', end_time='00:30')"
    assert [(slot.index, slot.start_time, slot.end_time, slot.raw_token) for slot in shared.slots] == [
        (slot.index, slot.start_time, slot.end_time, slot.raw_token) for slot in room.slots
    ]


def test_room_issue_class_identity_context_and_shape_survive_shared_raw_core():
    selector = {"Address": "4", "Year": "2024", "Month": "2", "Day": "29", "DataName": "ROOM_TEMP"}
    payload = room_packet("")
    shared = _raw_series(payload, selector, selected_field="RoomTempData", context="member-temperature-history")
    room = parse_member_temperature_history(payload, expected_address="4", expected_date="2024-02-29")
    assert type(shared.issues[0]) is RawDailyTrendIssue
    assert type(room.issues[0]) is TemperatureHistoryIssue
    assert TemperatureHistoryIssue.__module__ == "pymelans.member_temperature_history"
    assert room.issues == (TemperatureHistoryIssue("RoomTempData", "unexpected_token_count"),)
    assert room.raw_data == "" and room.raw_tokens == ("",) and room.slots == ()
    assert [(issue.key, issue.kind) for issue in shared.issues] == [(issue.key, issue.kind) for issue in room.issues]
    with pytest.raises(MelansProtocolError) as caught:
        parse_member_temperature_history(
            payload.replace('DataName="ROOM_TEMP"', 'DataName="COOL_SET_TEMP"'),
            expected_address="4", expected_date="2024-02-29",
        )
    assert str(caught.value) == "member-temperature-history response selector mismatch"
