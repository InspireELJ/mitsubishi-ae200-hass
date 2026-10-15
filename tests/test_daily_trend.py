"""Keep the runtime public protocol contract stable through core extraction."""

from __future__ import annotations

import pytest
from pymelans.daily_trend import member_day_selector
from pymelans.exceptions import MelansProtocolError

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
