"""Offline topology contracts; synthetic records contain no site inventory."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from itertools import count

import pytest
from pymelans.exceptions import MelansProtocolError

from pymelans import topology


def packet(body: str, command: str = "getResponse") -> str:
    return f"<Packet><Command>{command}</Command><DatabaseManager>{body}</DatabaseManager></Packet>"


def directory(*rows: str) -> str:
    return packet("<ControlGroup><MnetGroupList>" + "".join(rows)
                  + "</MnetGroupList></ControlGroup>")


def test_builders_are_read_only_and_address_request_requires_allowlist():
    root = ET.fromstring(topology.build_membership_request())
    assert root.findtext("./Command") == "getRequest"
    assert "DefrostStatus" not in topology.ADDRESS_READ_ATTRIBUTES
    assert root.find("./DatabaseManager/ControlGroup/MnetGroupList") is not None
    root = ET.fromstring(topology.build_address_request(["002", 2, 3], allowed_addresses=[2, 3]))
    nodes = root.findall("./DatabaseManager/Mnet")
    assert [node.get("Address") for node in nodes] == ["2", "3"]
    assert all(set(node.attrib) == {"Address", *topology.ADDRESS_READ_ATTRIBUTES} for node in nodes)
    assert all(value == "*" for node in nodes for key, value in node.attrib.items()
               if key != "Address")
    assert root.findtext("./Command") == "getRequest"
    with pytest.raises(ValueError, match="allowlist"):
        topology.build_address_request([4], allowed_addresses=[2, 3])


@pytest.mark.parametrize("values", [[], [0], [251], [-1], [True], ["*"], ["1,2"], ["１"]])
def test_address_builder_rejects_unbounded_or_nonconcrete_targets(values):
    with pytest.raises(ValueError):
        topology.build_address_request(values, allowed_addresses=[1, 2])


def test_public_normaliser_excludes_zero_and_does_not_accept_injection_or_unicode_digits():
    assert topology.normalise_address("011") == "11"
    assert topology.normalise_address(250) == "250"
    for value in (0, 251, True, "*", "１１", '1" Group="*'):
        with pytest.raises(ValueError):
            topology.normalise_address(value)


def test_request_consumption_is_bounded_even_for_infinite_iterators():
    with pytest.raises(ValueError):
        topology.build_address_request(count(1), allowed_addresses=[1])
    with pytest.raises(ValueError):
        topology.build_address_request([1], allowed_addresses=count(1))
    with pytest.raises(ValueError):
        topology.parse_membership(directory(), expected_groups=count(1))
    with pytest.raises(TypeError):
        topology.build_address_request("11", allowed_addresses=[11])


def test_membership_preserves_multiple_members_and_all_edges():
    snapshot = topology.parse_membership(directory(
        '<MnetGroupRecord Group="1" Address="11" Model="IC" SubModel="" />',
        '<MnetGroupRecord Group="1" Address="12" Model="IC" Contact="A" />',
        '<MnetGroupRecord Group="2" Address="12" Model="IC" Contact="B" />',
    ), expected_groups=[1, 2])
    assert len(snapshot.records) == 3
    assert len(snapshot.members_for_group(1)) == 2
    assert snapshot.addresses == ("11", "12")
    assert snapshot.records[0].sub_model is None
    assert snapshot.records[0].attributes["SubModel"] == ""
    assert snapshot.issues == ()


def test_directory_records_invalid_or_unexpected_fields_without_expanding_allowlist():
    snapshot = topology.parse_membership(directory(
        '<MnetGroupRecord Group="1" Address="11" Model="IC" />',
        '<MnetGroupRecord Group="2" Address="12" Model="IC" />',
        '<MnetGroupRecord Group="1" Address="0" Model="IC" />',
        '<MnetGroupRecord Group="1" Address="251" />',
        '<MnetGroupRecord Group="1" Address="*" />',
        '<MnetGroupRecord Address="14" />',
        '<MnetGroupRecord Group="51" Address="15" />',
    ), expected_groups=[1])
    assert len(snapshot.records) == 7
    assert snapshot.addresses == ("11",)
    assert snapshot.records[1].issues == ("unexpected_group",)
    assert snapshot.records[2].address == "0"
    assert snapshot.records[2].issues == ("zero_address",)
    assert snapshot.records[3].attributes["Address"] == "251"
    assert snapshot.records[4].address is None
    assert snapshot.records[5].issues == ("missing_group",)
    assert snapshot.records[6].issues == ("invalid_group",)


def test_identical_membership_duplicates_are_retained_and_requests_deduplicated():
    snapshot = topology.parse_membership(directory(
        '<MnetGroupRecord Group="1" Address="011" Model="IC" />',
        '<MnetGroupRecord Group="01" Address="11" Model="IC" />',
    ), expected_groups=[1])
    assert len(snapshot.records) == 2
    assert snapshot.addresses == ("11",)
    assert all(row.issues == ("duplicate_membership",) for row in snapshot.records)


def test_conflicting_membership_and_address_family_are_isolated():
    snapshot = topology.parse_membership(directory(
        '<MnetGroupRecord Group="1" Address="11" Model="IC" />',
        '<MnetGroupRecord Group="1" Address="11" Model="LC" />',
        '<MnetGroupRecord Group="2" Address="12" Model="IC" />',
        '<MnetGroupRecord Group="1" Address="12" Model="LC" />',
    ), expected_groups=[1, 2])
    assert snapshot.addresses == ()
    assert all("conflicting_address_model" in row.issues for row in snapshot.records)
    assert "conflicting_membership" in snapshot.records[0].issues


def test_clean_edge_does_not_readmit_address_with_another_conflicting_or_unexpected_edge():
    snapshot = topology.parse_membership(directory(
        '<MnetGroupRecord Group="1" Address="11" Model="IC" Data="A" />',
        '<MnetGroupRecord Group="1" Address="11" Model="IC" Data="B" />',
        '<MnetGroupRecord Group="2" Address="11" Model="IC" />',
        '<MnetGroupRecord Group="1" Address="12" Model="IC" />',
        '<MnetGroupRecord Group="3" Address="12" Model="IC" />',
    ), expected_groups=[1, 2])
    assert len(snapshot.records) == 5
    assert snapshot.addresses == ()


def test_address_parser_keys_members_by_address_not_group_and_keeps_unknown_tokens():
    snapshot = topology.parse_address_state(packet(
        '<Mnet Address="011" Group="1" ThermoStatus="FUTURE" FanStatus="" AlarmCode="0007" />'
        '<Mnet Address="12" Group="1" ThermoStatus="*" DefrostStatus="0" />'
    ), expected_addresses=[11, 12])
    assert set(snapshot.states) == {"11", "12"}
    assert [row.group_id for row in snapshot.records] == ["1", "1"]
    assert snapshot.records[0].value("ThermoStatus") == "FUTURE"
    assert snapshot.records[0].value("FanStatus") is None
    assert snapshot.records[1].value("ThermoStatus") is None
    assert snapshot.records[1].value("FanStatus") is None
    assert snapshot.records[1].value("DefrostStatus") == "0"
    assert snapshot.states["11"]["AlarmCode"] == "0007"
    assert snapshot.issues == ()


def test_address_parser_preserves_invalid_unexpected_records_and_reports_missing():
    snapshot = topology.parse_address_state(packet(
        '<Mnet Address="11" Group="*" FanStatus="UNKNOWN" />'
        '<Mnet Address="13" Group="1" />'
        '<Mnet Address="0" />'
        '<Mnet Address="１２" />'
        '<Mnet Group="1" />'
        '<Mnet Address="14" Group="500" />'
    ), expected_addresses=[11, 12, 14])
    assert set(snapshot.states) == {"11"}
    assert len(snapshot.records) == 6
    assert snapshot.records[0].group_id is None
    assert snapshot.records[1].issues == ("unexpected_address",)
    assert snapshot.records[2].issues == ("invalid_address",)
    assert snapshot.records[3].issues == ("invalid_address",)
    assert snapshot.records[4].issues == ("missing_address",)
    assert snapshot.records[5].issues == ("invalid_group",)
    assert snapshot.missing_addresses == ("12",)
    assert topology.ParseIssue("missing_address", key="12") in snapshot.issues


def test_identical_address_duplicates_are_retained_but_conflicts_have_no_selected_state():
    snapshot = topology.parse_address_state(packet(
        '<Mnet Address="011" Group="1" ThermoStatus="A" />'
        '<Mnet Address="11" Group="01" ThermoStatus="A" />'
        '<Mnet Address="12" Group="1" ThermoStatus="A" />'
        '<Mnet Address="12" Group="1" ThermoStatus="B" />'
    ), expected_addresses=[11, 12])
    assert len(snapshot.records) == 4
    assert set(snapshot.states) == {"11"}
    assert snapshot.records[0].issues == ("duplicate_address",)
    assert snapshot.records[2].issues == ("conflicting_address",)
    assert snapshot.records[3].issues == ("conflicting_address",)
    assert snapshot.missing_addresses == ()


@pytest.mark.parametrize("body", ["", "<ControlGroup />", "<MnetGroupList />"])
def test_membership_requires_the_verified_directory_path(body):
    with pytest.raises(MelansProtocolError, match="directory"):
        topology.parse_membership(packet(body), expected_groups=[1])


@pytest.mark.parametrize("payload", [
    "", "<html />", packet("", "setResponse"), packet("", "notification"),
    '<!DOCTYPE Packet [<!ENTITY test "x">]>' + packet(""),
    '<Packet><Command>getResponse</Command><Command>getResponse</Command>'
    '<DatabaseManager /></Packet>',
    '<Packet><Command>getResponse</Command><DatabaseManager /><DatabaseManager /></Packet>',
])
def test_parsers_reject_malformed_wrong_command_or_ambiguous_envelopes(payload):
    with pytest.raises(MelansProtocolError):
        topology.parse_address_state(payload, expected_addresses=[11])


def test_error_code_is_preserved_without_accepting_error_reply():
    with pytest.raises(MelansProtocolError) as error:
        topology.parse_address_state(packet('<ERROR Code="0201" Point="Address" />',
                                            "getErrorResponse"), expected_addresses=[11])
    assert error.value.code == "0201"
    assert error.value.point == "Address"


def test_partial_address_data_in_error_response_is_never_accepted():
    payload = packet(
        '<Mnet Address="11" Group="1" Model="IC" ErrorSign="OFF" '
        'AlarmCode="8000" ThermoStatus="OFF" FanStatus="OFF" DefrostStatus="*" />'
        '<ERROR Point="DefrostStatus" Code="0101" Message="Unknown Attribute" />',
        "getErrorResponse",
    )
    with pytest.raises(MelansProtocolError) as error:
        topology.parse_address_state(payload, expected_addresses=[11])
    assert error.value.code == "0101"
    assert error.value.point == "DefrostStatus"


def test_supported_status_schema_keeps_alarm_code_uninterpreted():
    snapshot = topology.parse_address_state(packet(
        '<Mnet Address="11" Group="1" Model="IC" SubModel="" ErrorSign="OFF" '
        'AlarmCode="8000" ThermoStatus="OFF" FanStatus="OFF" />'
    ), expected_addresses=[11])
    assert snapshot.states["11"]["AlarmCode"] == "8000"
    assert snapshot.records[0].value("ThermoStatus") == "OFF"


def test_empty_valid_results_do_not_invent_members_or_states():
    members = topology.parse_membership(directory(), expected_groups=[1])
    assert members.records == () and members.addresses == ()
    state = topology.parse_address_state(packet(""), expected_addresses=[11])
    assert state.records == () and state.states == {}
    assert state.missing_addresses == ("11",)


def test_xml_and_record_bounds_are_enforced(monkeypatch):
    monkeypatch.setattr(topology, "MAX_XML_BYTES", 100)
    with pytest.raises(MelansProtocolError, match="size|oversized"):
        topology.parse_address_state(packet(" " * 101), expected_addresses=[11])
    monkeypatch.setattr(topology, "MAX_XML_BYTES", 1024 * 1024)
    monkeypatch.setattr(topology, "MAX_RECORDS", 1)
    with pytest.raises(MelansProtocolError, match="record limit"):
        topology.parse_address_state(packet('<Mnet Address="11" /><Mnet Address="12" />'),
                                     expected_addresses=[11, 12])
    with pytest.raises(MelansProtocolError, match="record limit"):
        topology.parse_membership(directory('<MnetGroupRecord />', '<MnetGroupRecord />'),
                                  expected_groups=[1])


@pytest.mark.parametrize("body", [
    '<Mnet Address="11" Data="' + "x" * 513 + '" />',
    "<x>" * 17 + "</x>" * 17,
    "<x />" * 2048,
])
def test_attribute_depth_and_node_count_are_bounded(body):
    with pytest.raises(MelansProtocolError, match="limits"):
        topology.parse_address_state(packet(body), expected_addresses=[11])
