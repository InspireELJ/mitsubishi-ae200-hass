"""Offline tests for pymelans parsing and the device model.

Fixtures are real responses captured from a live AE-200 (192.168.1.100,
firmware c.2021) so CI exercises the actual wire format without hardware.
"""

from __future__ import annotations

import pytest

from pymelans import const as mc
from pymelans.device import MelansDevice
from pymelans.exceptions import MelansProtocolError
from pymelans.protocol import (
    build_set_request,
    build_state_request,
    parse_group_list,
    parse_state,
)

GROUP_LIST_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Packet><Command>getResponse</Command><DatabaseManager><ControlGroup><MnetList>
<MnetRecord Group="1" GroupNameWeb="BEDROOM" />
<MnetRecord Group="2" GroupNameWeb="FAMILY ROOM" />
<MnetRecord Group="3" GroupNameWeb="KITCHEN" />
<MnetRecord Group="4" GroupNameWeb="GYM" />
</MnetList></ControlGroup></DatabaseManager></Packet>"""

STATE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Packet><Command>getResponse</Command><DatabaseManager>
<Mnet Group="2" Drive="ON" Mode="COOL" SetTemp="21" InletTemp="26.4" FanSpeed="AUTO" AirDirection="HORIZONTAL" RemoCon="PERMIT" FilterSign="ON" ErrorSign="OFF" InletTempHWHP="0.0" OutletTempHWHP="0.0" />
<Mnet Group="3" Drive="OFF" Mode="COOL" SetTemp="25" InletTemp="-40.7" FanSpeed="AUTO" />
</DatabaseManager></Packet>"""

EMPTY_GROUP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Packet><Command>getResponse</Command><DatabaseManager>
<Mnet Group="9" Drive="*" Mode="*" SetTemp="*" InletTemp="*" />
</DatabaseManager></Packet>"""

ERROR_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Packet><Command>getErrorResponse</Command><DatabaseManager>
<Mnet Group="*" /><ERROR Point="Group[*]" Code="0201" Message="Invalid Value" />
</DatabaseManager></Packet>"""


def test_parse_group_list():
    groups = parse_group_list(GROUP_LIST_XML)
    assert {g["id"]: g["name"] for g in groups} == {
        "1": "BEDROOM", "2": "FAMILY ROOM", "3": "KITCHEN", "4": "GYM",
    }


def test_parse_state_skips_unconfigured():
    assert parse_state(EMPTY_GROUP_XML) == {}


def test_parse_state_values():
    state = parse_state(STATE_XML)
    assert set(state) == {"2", "3"}
    assert state["2"]["Drive"] == "ON"
    assert state["2"]["SetTemp"] == "21"


def test_parse_error_raises():
    with pytest.raises(MelansProtocolError) as exc:
        parse_state(ERROR_XML)
    assert exc.value.code == "0201"


def test_device_ac_classification():
    state = parse_state(STATE_XML)
    dev = MelansDevice("2", "FAMILY ROOM", state["2"])
    assert dev.kind == mc.KIND_AC  # HWHP=0.0 must NOT be read as ATW
    assert dev.is_on is True
    assert dev.target_temperature == 21.0
    assert dev.current_temperature == 26.4
    assert dev.fan_speed == "AUTO"
    assert dev.filter_sign is True
    assert dev.error_sign is False
    assert dev.local_remote_prohibited is False


def test_device_faulted_sensor_is_none():
    state = parse_state(STATE_XML)
    dev = MelansDevice("3", "KITCHEN", state["3"])
    # InletTemp -40.7 means the thermistor is faulted -> no reading
    assert dev.current_temperature is None
    assert dev.is_on is False


def test_build_requests_roundtrip():
    req = build_state_request([2])
    assert 'Group="2"' in req and "getRequest" in req
    setreq = build_set_request(2, {"Drive": "ON", "SetTemp": "21"})
    assert 'Drive="ON"' in setreq and 'SetTemp="21"' in setreq and "setRequest" in setreq
