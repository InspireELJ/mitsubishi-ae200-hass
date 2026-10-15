"""Offline contracts for explicit single-group wall-remote restriction planning."""

from __future__ import annotations

import itertools
import xml.etree.ElementTree as ET
from dataclasses import FrozenInstanceError

import pytest
from pymelans.exceptions import MelansProtocolError
from pymelans.local_restrictions import (
    READ_ATTRIBUTES,
    LocalRestrictions,
    LocalRestrictionsPlan,
    build_local_restrictions_request,
    build_local_restrictions_set_request,
    parse_local_restriction_stops,
    parse_local_restrictions,
    plan_local_restrictions_patch,
    validate_local_restriction_patch,
)

from pymelans import protocol

CAPABILITIES = {"Model": "IC", "SubModel": "", "IcKind": "NORMAL", "GroupType": "OLD"}
FIELDS = ("DriveItem", "ModeItem", "SetTempItem", "FilterItem")
FUNCTIONS = ("power", "mode", "temperature", "filter_reset")
RAW = {"RemoCon": "PERMIT", **dict.fromkeys(FIELDS, "CHK_OFF")}


def packet(body, command="getResponse"):
    return f"<Packet><Command>{command}</Command><DatabaseManager>{body}</DatabaseManager></Packet>"


def restrictions_packet(attributes=None, group="7", **extra):
    node = ET.Element("Mnet", {"Group": group, **(RAW if attributes is None else attributes), **extra})
    return packet(ET.tostring(node, encoding="unicode"))


def parse(payload):
    return parse_local_restrictions(payload, expected_group="7")


def plan(baseline, patch, capabilities=None):
    return plan_local_restrictions_patch(
        baseline, patch, capability_attributes=CAPABILITIES if capabilities is None else capabilities,
    )


def _private_fixture_restore_request(original):
    """Only an offline fixture helper, never a raw restoration library API."""
    return protocol.build_set_request(original.group_id, original.raw_attributes())


@pytest.mark.parametrize("group", [7, "7", "007"])
def test_pre_read_has_exactly_five_restriction_attributes_and_no_drive_command(group):
    root = ET.fromstring(build_local_restrictions_request(group))
    assert root.findtext("./Command") == "getRequest"
    manager = root.find("./DatabaseManager")
    assert len(manager) == 1 and manager[0].tag == "Mnet" and len(manager[0]) == 0
    assert manager[0].attrib == {"Group": "7", **dict.fromkeys(READ_ATTRIBUTES, "*")}
    assert set(READ_ATTRIBUTES) == {"RemoCon", *FIELDS}
    assert "Drive" not in manager[0].attrib


@pytest.mark.parametrize("group", [0, 51, True, "*", "", "7,8", "7.0", "٧", " 7", "0007"])
def test_request_and_expected_group_are_bounded_ascii(group):
    with pytest.raises(ValueError):
        build_local_restrictions_request(group)
    with pytest.raises(ValueError):
        parse_local_restrictions(restrictions_packet(), expected_group=group)


@pytest.mark.parametrize("master", ["PERMIT", "PROHIBIT"])
@pytest.mark.parametrize("bits", list(itertools.product([False, True], repeat=4)))
@pytest.mark.parametrize("function,value", list(itertools.product(FUNCTIONS, [False, True])))
def test_each_single_function_patch_preserves_every_unrequested_effective_restriction(master, bits, function, value):
    raw = {"RemoCon": master, **{field: "CHK_ON" if bit else "CHK_OFF" for field, bit in zip(FIELDS, bits)}}
    baseline = parse(restrictions_packet(raw))
    expected_before = dict(zip(FUNCTIONS, bits if master == "PROHIBIT" else (False,) * 4))
    expected_after = {**expected_before, function: value}
    result = plan(baseline, {function: value})
    assert baseline.effective() == expected_before and result.before == expected_before
    assert result.after == expected_after
    written = result.write_attributes()
    assert written["RemoCon"] == ("PROHIBIT" if any(expected_after.values()) else "PERMIT")
    assert written == {
        "RemoCon": "PROHIBIT" if any(expected_after.values()) else "PERMIT",
        **{field: "CHK_ON" if expected_after[key] else "CHK_OFF" for key, field in zip(FUNCTIONS, FIELDS)},
    }
    assert parse(restrictions_packet(written)).effective() == expected_after
    assert baseline.raw_attributes() == raw


def test_permit_with_hidden_chk_on_does_not_accidentally_lock_other_wall_remote_functions():
    original_raw = {"RemoCon": "PERMIT", **dict.fromkeys(FIELDS, "CHK_ON")}
    baseline = parse(restrictions_packet(original_raw))
    result = plan(baseline, {"filter_reset": True})
    assert baseline.effective() == dict.fromkeys(FUNCTIONS, False)
    assert result.write_attributes() == {
        "RemoCon": "PROHIBIT", "DriveItem": "CHK_OFF", "ModeItem": "CHK_OFF",
        "SetTempItem": "CHK_OFF", "FilterItem": "CHK_ON",
    }
    assert baseline.raw_attributes() == original_raw
    restored = ET.fromstring(_private_fixture_restore_request(baseline)).find("./DatabaseManager/Mnet")
    assert restored.attrib == {"Group": "7", **original_raw}
    assert restored.get("Drive") is None


def test_multi_item_patch_merges_once_and_set_payload_contains_exactly_the_five_fields():
    baseline = parse(restrictions_packet({**RAW, "RemoCon": "PROHIBIT", "DriveItem": "CHK_ON"}))
    result = plan(baseline, {"power": False, "mode": True, "temperature": True, "filter_reset": False})
    root = ET.fromstring(build_local_restrictions_set_request(result))
    assert root.findtext("./Command") == "setRequest"
    manager = root.find("./DatabaseManager")
    assert len(manager) == 1 and manager[0].tag == "Mnet" and len(manager[0]) == 0
    assert manager[0].attrib == {
        "Group": "7", "RemoCon": "PROHIBIT", "DriveItem": "CHK_OFF", "ModeItem": "CHK_ON",
        "SetTempItem": "CHK_ON", "FilterItem": "CHK_OFF",
    }
    assert not ({"Drive", "Mode", "SetTemp", "FilterSign", "ErrorSign"} & manager[0].attrib.keys())


def test_clearing_final_effective_restriction_normalises_all_four_and_permits_master():
    baseline = parse(restrictions_packet({**RAW, "RemoCon": "PROHIBIT", "FilterItem": "CHK_ON"}))
    assert plan(baseline, {"filter_reset": False}).write_attributes() == RAW


@pytest.mark.parametrize("patch", [
    {}, {"power": None}, {"power": 0}, {"power": 1}, {"power": "true"}, {"power": []},
    {"power": True, "mode": "bad"}, {"Drive": True}, {"power": True, "unknown": False},
    {"Power": True}, {"power": True, "mode": False, "temperature": False, "filter_reset": True, "extra": False},
    [("power", True)], None,
])
def test_empty_nonboolean_unknown_and_partly_invalid_patches_are_rejected_as_a_whole(patch):
    baseline = parse(restrictions_packet())
    with pytest.raises(ValueError):
        validate_local_restriction_patch(patch)
    with pytest.raises(ValueError):
        plan(baseline, patch)
    assert baseline.raw_attributes() == RAW


def test_patch_validation_exports_an_independent_copy_before_io():
    source = {"filter_reset": True, "power": False}
    validated = validate_local_restriction_patch(source)
    assert validated == source
    source["filter_reset"] = False
    validated["power"] = True
    assert validated["filter_reset"] is True and source["power"] is False


@pytest.mark.parametrize("key,bad", [
    ("Model", None), ("Model", ""), ("Model", "*"), ("Model", "LC"), ("Model", "ic"),
    ("IcKind", None), ("IcKind", ""), ("IcKind", "*"), ("IcKind", "HH"),
    ("GroupType", None), ("GroupType", "*"), ("GroupType", "NEW"), ("GroupType", "old"),
    ("SubModel", None), ("SubModel", "HH"), ("SubModel", "EOA"), ("SubModel", "FUTURE"), ("SubModel", "*"),
])
def test_only_exact_known_old_normal_ic_capability_fields_allow_planning(key, bad):
    capabilities = {**CAPABILITIES}
    if bad is None:
        capabilities.pop(key)
    else:
        capabilities[key] = bad
    with pytest.raises(ValueError):
        plan(parse(restrictions_packet()), {"filter_reset": True}, capabilities)


def test_unrelated_chiller_prohibit_switch_capabilities_do_not_disable_known_old_ic_rules():
    capabilities = {**CAPABILITIES, "ProhibitSW": "DISABLE", "RemoConProhibitSW": "DISABLE"}
    assert plan(parse(restrictions_packet()), {"filter_reset": True}, capabilities).after["filter_reset"] is True


@pytest.mark.parametrize("key", READ_ATTRIBUTES)
@pytest.mark.parametrize("bad", [None, "", "*", "FUTURE", "ON", "off", " CHK_OFF"])
def test_every_baseline_raw_field_must_be_known_even_when_master_is_permit(key, bad):
    attributes = {**RAW}
    if bad is None:
        attributes.pop(key)
    else:
        attributes[key] = bad
    with pytest.raises(MelansProtocolError):
        parse(restrictions_packet(attributes))


def test_samples_and_plans_are_frozen_and_all_exports_are_independent_copies():
    baseline = parse(restrictions_packet(Name="private-room", Password="secret", Drive="ON"))
    result = plan(baseline, {"temperature": True})
    with pytest.raises(FrozenInstanceError):
        baseline.group_id = "8"
    with pytest.raises(FrozenInstanceError):
        result.group_id = "8"
    raw, effective, before, after, written = (
        baseline.raw_attributes(), baseline.effective(), result.before, result.after, result.write_attributes(),
    )
    raw["RemoCon"] = "PROHIBIT"
    effective["power"] = before["power"] = after["power"] = True
    written["Drive"] = "ON"
    assert baseline.raw_attributes() == RAW and baseline.effective() == dict.fromkeys(FUNCTIONS, False)
    assert result.before["power"] is result.after["power"] is False
    assert "Drive" not in result.write_attributes()
    assert "private-" not in repr(baseline) and "secret" not in repr(baseline)


def test_dataclasses_reject_mutable_or_incomplete_internal_state():
    with pytest.raises(ValueError):
        LocalRestrictions("7", list(RAW.items()))
    with pytest.raises(ValueError):
        LocalRestrictions("7", tuple(list(RAW.items())[:-1]))
    with pytest.raises(ValueError):
        LocalRestrictions("007", tuple(RAW.items()))
    effective = tuple((key, False) for key in FUNCTIONS)
    with pytest.raises(ValueError):
        LocalRestrictionsPlan("7", list(effective), effective)
    with pytest.raises(ValueError):
        LocalRestrictionsPlan("7", effective, tuple((key, 0) for key in FUNCTIONS))


@pytest.mark.parametrize("source", [{}, {"RemoCon": "PERMIT"}, None])
def test_planner_and_set_builder_do_not_accept_unvalidated_raw_dicts(source):
    with pytest.raises(ValueError):
        plan(source, {"power": True})
    with pytest.raises(ValueError):
        build_local_restrictions_set_request(source)


@pytest.mark.parametrize("group", [None, "", "*", "8", "007", "0", "51", "٧"])
def test_response_group_identity_must_exactly_match_canonical_requested_group(group):
    attributes = {**RAW}
    if group is not None:
        attributes["Group"] = group
    with pytest.raises(MelansProtocolError):
        parse(packet(ET.tostring(ET.Element("Mnet", attributes), encoding="unicode")))


@pytest.mark.parametrize("attribute", ["Address", "MultiGroup"])
@pytest.mark.parametrize("value", ["", "*", "7"])
@pytest.mark.parametrize("location", ["root", "record"])
def test_address_or_aggregate_identity_anywhere_is_rejected(attribute, value, location):
    root = ET.fromstring(restrictions_packet())
    node = root if location == "root" else root.find("./DatabaseManager/Mnet")
    node.set(attribute, value)
    with pytest.raises(MelansProtocolError):
        parse(ET.tostring(root, encoding="unicode"))


@pytest.mark.parametrize("text", ["junk", "\u00a0", "\u2003"])
@pytest.mark.parametrize("location", ["root", "command-tail", "database", "record", "record-tail"])
def test_non_xml_whitespace_is_not_accepted_as_a_restriction_sample(text, location):
    root = ET.fromstring(restrictions_packet())
    paths = {"root": ".", "command-tail": "./Command", "database": "./DatabaseManager",
             "record": "./DatabaseManager/Mnet", "record-tail": "./DatabaseManager/Mnet"}
    node = root.find(paths[location])
    if location.endswith("tail"):
        node.tail = text
    else:
        node.text = text
    with pytest.raises(MelansProtocolError):
        parse(ET.tostring(root, encoding="unicode"))


def test_xml_whitespace_is_accepted_without_mutating_the_five_raw_values():
    root = ET.fromstring(restrictions_packet())
    for node in root.iter():
        if node.tag != "Command":
            node.text = " \t\r\n"
        node.tail = " \t\r\n"
    assert parse(ET.tostring(root, encoding="unicode")).raw_attributes() == RAW


@pytest.mark.parametrize("payload", [
    "not XML", restrictions_packet().replace("getResponse", "notifyRequest"),
    restrictions_packet().replace("getResponse", "getErrorResponse"),
    restrictions_packet().replace("getResponse", "setResponse"),
    restrictions_packet().replace("<Mnet", '<ERROR Code="0101" Message="private" /><Mnet'),
    '<!DOCTYPE Packet [<!ENTITY value "PERMIT">]>' + restrictions_packet(),
    '<Packet><Command>getResponse</Command><Command>getResponse</Command><DatabaseManager /></Packet>',
    '<Packet><Command>getResponse<Other /></Command><DatabaseManager /></Packet>',
    '<Packet><Command>getResponse</Command><DatabaseManager /><DatabaseManager /></Packet>',
    restrictions_packet().replace("</Packet>", "<Other /></Packet>"),
    packet("<Mnet /><Mnet />"), packet("<Nested><Mnet /></Nested>"), packet("<Mnet><Other /></Mnet>"),
    packet("<A>" * 20 + "</A>" * 20), packet("<A />" * 2100),
    restrictions_packet({**RAW, "RemoCon": "x" * 513}), " " * (1024 * 1024 + 1),
    restrictions_packet({**RAW, "RemoCon": "\ud800"}),
], ids=[
    "malformed", "notification", "error-command", "wrong-command", "partial-error", "dtd",
    "duplicate-command", "nested-command", "duplicate-database", "extra-root-child", "duplicate-record",
    "nested-record", "record-child", "deep-tree", "many-nodes", "long-value", "oversized", "invalid-text",
])
def test_strict_xml_and_shared_response_limits_reject_incomplete_or_ambiguous_samples(payload):
    with pytest.raises(MelansProtocolError):
        parse(payload)


def stop_packets(global_value="OFF", bacnet="OFF", energy="OFF"):
    global_attrs = {} if global_value is None else {"AllTurnOff": global_value}
    group_attrs = {"Group": "7"}
    if bacnet is not None:
        group_attrs["BACnetTurnOff"] = bacnet
    if energy is not None:
        group_attrs["TurnOff"] = energy
    return (
        packet(ET.tostring(ET.Element("SystemData", global_attrs), encoding="unicode")),
        packet(ET.tostring(ET.Element("Mnet", group_attrs), encoding="unicode")),
    )


@pytest.mark.parametrize("values", list(itertools.product(["ON", "OFF"], repeat=3)))
def test_stop_parser_requires_and_independently_reports_all_three_known_sources(values):
    global_payload, group_payload = stop_packets(*values)
    stops = parse_local_restriction_stops(global_payload, group_payload, expected_group="7")
    assert stops == dict(zip(
        ("global_emergency_stop", "bacnet_emergency_stop", "energy_saving_stop"),
        (value == "ON" for value in values),
    ))


@pytest.mark.parametrize("position", [0, 1, 2])
@pytest.mark.parametrize("unknown", [None, "", "*", "FUTURE", "off", " OFF", "0"])
def test_missing_or_unknown_stop_token_never_becomes_false(position, unknown):
    values = ["OFF", "OFF", "OFF"]
    values[position] = unknown
    with pytest.raises(MelansProtocolError):
        parse_local_restriction_stops(*stop_packets(*values), expected_group="7")


@pytest.mark.parametrize("role", [0, 1], ids=["global", "group"])
@pytest.mark.parametrize("defect", ["wrong-role", "duplicate", "nested", "error-command", "partial-error", "mixed", "nbsp", "extra-root"])
def test_each_stop_reply_has_its_own_strict_original_envelope_and_text_guard(role, defect):
    replies = list(stop_packets())
    root = ET.fromstring(replies[role])
    manager = root.find("./DatabaseManager")
    node = manager[0]
    if defect == "wrong-role":
        node.tag = "Mnet" if role == 0 else "SystemData"
    elif defect == "duplicate":
        ET.SubElement(manager, node.tag, dict(node.attrib))
    elif defect == "nested":
        ET.SubElement(node, "Other")
    elif defect == "error-command":
        root.find("./Command").text = "getErrorResponse"
    elif defect == "partial-error":
        ET.SubElement(manager, "ERROR", {"Code": "0101", "Message": "private"})
    elif defect in ("mixed", "nbsp"):
        node.text = "junk" if defect == "mixed" else "\u00a0"
    else:
        ET.SubElement(root, "Other")
    replies[role] = ET.tostring(root, encoding="unicode")
    with pytest.raises(MelansProtocolError):
        parse_local_restriction_stops(*replies, expected_group="7")


@pytest.mark.parametrize("role", [0, 1])
@pytest.mark.parametrize("key", ["Address", "MultiGroup"])
@pytest.mark.parametrize("value", ["", "*", "7"])
def test_stop_guard_rejects_aggregate_or_address_identity_in_either_reply(role, key, value):
    replies = list(stop_packets())
    root = ET.fromstring(replies[role])
    root.find("./DatabaseManager")[0].set(key, value)
    replies[role] = ET.tostring(root, encoding="unicode")
    with pytest.raises(MelansProtocolError):
        parse_local_restriction_stops(*replies, expected_group="7")


@pytest.mark.parametrize("identity", ["8", "007", "*", "", "٧"])
def test_group_stop_identity_is_canonical_and_bound_to_the_selected_group(identity):
    global_payload, group_payload = stop_packets()
    group_root = ET.fromstring(group_payload)
    group_root.find("./DatabaseManager/Mnet").set("Group", identity)
    with pytest.raises(MelansProtocolError):
        parse_local_restriction_stops(global_payload, ET.tostring(group_root, encoding="unicode"), expected_group="7")


@pytest.mark.parametrize("role", [0, 1])
def test_stop_guard_reuses_dtd_and_byte_size_limits_in_both_replies(role):
    replies = list(stop_packets())
    replies[role] = '<!DOCTYPE Packet [<!ENTITY value "OFF">]>' + replies[role]
    with pytest.raises(MelansProtocolError):
        parse_local_restriction_stops(*replies, expected_group="7")
    replies[role] = " " * (1024 * 1024 + 1)
    with pytest.raises(MelansProtocolError):
        parse_local_restriction_stops(*replies, expected_group="7")
