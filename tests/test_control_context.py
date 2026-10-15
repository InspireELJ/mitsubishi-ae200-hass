"""Offline contracts for optional global and single-group stop context."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest
from pymelans.control_context import (
    GlobalControlContext,
    GroupControlContext,
    build_global_control_context_request,
    build_group_control_context_request,
    parse_global_control_context,
    parse_group_control_context,
)
from pymelans.exceptions import MelansProtocolError


def packet(body: str, command: str = "getResponse") -> str:
    return f"<Packet><Command>{command}</Command><DatabaseManager>{body}</DatabaseManager></Packet>"


def group_packet(**attributes: str) -> str:
    node = ET.Element("Mnet", {"Group": "7", **attributes})
    return packet(ET.tostring(node, encoding="unicode"))


def parse_group(payload: str) -> GroupControlContext:
    return parse_group_control_context(payload, expected_group="7")


def test_global_request_is_exactly_one_minimal_read():
    root = ET.fromstring(build_global_control_context_request())
    assert root.tag == "Packet"
    assert root.findtext("./Command") == "getRequest"
    manager = root.find("./DatabaseManager")
    assert len(manager) == 1
    assert manager[0].tag == "SystemData"
    assert manager[0].attrib == {"AllTurnOff": "*"}
    assert len(manager[0]) == 0


@pytest.mark.parametrize("group", [7, "7", "007"])
def test_group_request_is_minimal_and_uses_a_concrete_normalised_group(group):
    root = ET.fromstring(build_group_control_context_request(group))
    assert root.findtext("./Command") == "getRequest"
    manager = root.find("./DatabaseManager")
    assert len(manager) == 1
    assert manager[0].tag == "Mnet"
    assert manager[0].attrib == {"Group": "7", "TurnOff": "*", "BACnetTurnOff": "*"}
    assert len(manager[0]) == 0


@pytest.mark.parametrize("group", [0, 51, -1, True, "*", "", "1,2", "7.0", "٧", "７", " 7", "0007"])
def test_group_builder_and_expected_identity_are_bounded_ascii(group):
    with pytest.raises(ValueError):
        build_group_control_context_request(group)
    with pytest.raises(ValueError):
        parse_group_control_context(group_packet(TurnOff="OFF"), expected_group=group)


@pytest.mark.parametrize("raw,expected", [
    ("ON", True), ("OFF", False), ("", None), ("*", None),
    ("on", None), ("off", None), ("FUTURE", None), (" ON ", None),
])
def test_only_exact_stop_tokens_are_typed_and_raw_values_remain_unchanged(raw, expected):
    global_node = ET.Element("SystemData", {"AllTurnOff": raw})
    global_context = parse_global_control_context(packet(ET.tostring(global_node, encoding="unicode")))
    group_context = parse_group(group_packet(TurnOff=raw, BACnetTurnOff=raw))
    assert global_context.all_turn_off_raw == raw
    assert global_context.all_turn_off is expected
    assert group_context.turn_off_raw == group_context.bacnet_turn_off_raw == raw
    assert group_context.turn_off is group_context.bacnet_turn_off is expected


def test_missing_stop_values_are_not_invented_off():
    global_context = parse_global_control_context(packet("<SystemData />"))
    group_context = parse_group(group_packet())
    assert global_context == GlobalControlContext(None)
    assert global_context.all_turn_off is None
    assert group_context == GroupControlContext("7", None, None)
    assert group_context.turn_off is group_context.bacnet_turn_off is None


def test_global_and_group_sources_are_independent_and_unrequested_data_is_discarded():
    global_context = parse_global_control_context(packet(
        '<SystemData AllTurnOff="ON" TurnOff="OFF" Name="private-name" Number="private-number" />'
    ))
    group_context = parse_group(group_packet(
        Group="007", TurnOff="OFF", BACnetTurnOff="ON", AllTurnOff="OFF",
        Name="private-name", ErrorSign="ON", AlarmCode="private-fault",
    ))
    assert global_context == GlobalControlContext("ON")
    assert group_context == GroupControlContext("7", "OFF", "ON")
    assert global_context.all_turn_off is True
    assert group_context.turn_off is False
    assert group_context.bacnet_turn_off is True
    assert "private" not in repr(global_context) + repr(group_context)
    assert not hasattr(group_context, "error_sign")


@pytest.mark.parametrize("body", [
    "", "<Other />", "<Nested><SystemData /></Nested>",
    "<SystemData /><SystemData />", "<SystemData /><Mnet Group=\"7\" />",
    "<SystemData><SystemData /></SystemData>", "<SystemData><Other /></SystemData>",
])
def test_global_parser_requires_one_direct_leaf_record(body):
    with pytest.raises(MelansProtocolError):
        parse_global_control_context(packet(body))


@pytest.mark.parametrize("body", [
    "", "<Other />", '<Nested><Mnet Group="7" /></Nested>',
    '<Mnet Group="7" /><Mnet Group="7" />', '<Mnet Group="7" /><Mnet Group="8" />',
    '<Mnet Group="7" /><SystemData />', '<Mnet Group="7"><Other /></Mnet>',
    '<Mnet Group="7"><Mnet Group="7" /></Mnet>',
])
def test_group_parser_rejects_duplicate_conflicting_or_nested_records(body):
    with pytest.raises(MelansProtocolError):
        parse_group(packet(body))


@pytest.mark.parametrize("identity", [None, "", "*", "8", "0", "51", "-1", "٧", "7.0"])
def test_group_response_requires_the_requested_valid_identity(identity):
    node = ET.Element("Mnet", {} if identity is None else {"Group": identity})
    with pytest.raises(MelansProtocolError):
        parse_group(packet(ET.tostring(node, encoding="unicode")))


@pytest.mark.parametrize("address", ["7", "", "*"])
def test_address_response_cannot_be_mistaken_for_group_context(address):
    with pytest.raises(MelansProtocolError):
        parse_group(group_packet(Address=address, TurnOff="OFF", BACnetTurnOff="OFF"))


@pytest.mark.parametrize("parser", [parse_global_control_context, parse_group])
@pytest.mark.parametrize("payload", [
    "not XML",
    packet('<ERROR Code="0101" Message="Unknown Attribute" /><Mnet Group="7" TurnOff="OFF" />'),
    packet('<SystemData AllTurnOff="OFF" />', command="getErrorResponse"),
    packet('<SystemData AllTurnOff="OFF" />', command="notifyRequest"),
    packet('<SystemData AllTurnOff="OFF" />', command="setResponse"),
    '<!DOCTYPE Packet [<!ENTITY flag "OFF">]>' + packet('<SystemData AllTurnOff="&flag;" />'),
    '<Packet><Command>getResponse</Command><Command>getResponse</Command><DatabaseManager /></Packet>',
    '<Packet><Command>getResponse</Command><DatabaseManager /><DatabaseManager /></Packet>',
    packet("<A>" * 20 + "</A>" * 20),
    packet("<A />" * 2100),
    packet('<SystemData AllTurnOff="' + "x" * 513 + '" />'),
    " " * (1024 * 1024 + 1),
    packet('<SystemData AllTurnOff="\ud800" />'),
], ids=[
    "malformed", "error-with-partial-record", "error-command", "notification", "wrong-command",
    "dtd", "duplicate-command", "duplicate-database", "deep-tree", "many-nodes",
    "long-attribute", "oversized", "invalid-text",
])
def test_context_parsers_reuse_strict_xml_envelope_and_safety_limits(parser, payload):
    with pytest.raises(MelansProtocolError):
        parser(payload)
