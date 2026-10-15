"""Offline contracts for bounded raw controller TodayList snapshots."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest
from pymelans.exceptions import MelansProtocolError
from pymelans.today_schedule import (
    MAX_TODAY_RECORDS,
    READ_ATTRIBUTES,
    TodaySchedule,
    build_today_schedule_request,
    parse_today_schedule,
)


def packet(body, command="getResponse"):
    return f"<Packet><Command>{command}</Command><DatabaseManager>{body}</DatabaseManager></Packet>"


def today_packet(*records, group="7", multi_group=None):
    control = ET.Element("ScheduleControl")
    attributes = {} if group is None else {"Group": group}
    if multi_group is not None:
        attributes["MultiGroup"] = multi_group
    directory = ET.SubElement(control, "TodayList", attributes)
    for attributes in records:
        ET.SubElement(directory, "TodayRecord", attributes)
    return packet(ET.tostring(control, encoding="unicode"))


def parse(payload):
    return parse_today_schedule(payload, expected_group="7")


VALID = {"Index": "1", "Hour": "9", "Minute": "30", "Drive": "ON", "Mode": "COOL"}


@pytest.mark.parametrize("group", [7, "7", "007"])
def test_request_is_one_concrete_group_without_calendar_scan_or_write_attributes(group):
    root = ET.fromstring(build_today_schedule_request(group))
    assert root.tag == "Packet" and root.findtext("./Command") == "getRequest"
    manager = root.find("./DatabaseManager")
    assert len(manager) == 1 and manager[0].tag == "ScheduleControl"
    assert manager[0].attrib == {} and len(manager[0]) == 1
    node = manager[0][0]
    assert node.tag == "TodayList" and node.attrib == {"Group": "7"} and len(node) == 0


@pytest.mark.parametrize("group", [0, 51, True, -1, "", "*", "7,8", "7.0", "٧", "７", " 7", "0007"])
def test_group_inputs_require_a_bounded_ascii_identifier(group):
    with pytest.raises(ValueError):
        build_today_schedule_request(group)
    with pytest.raises(ValueError):
        parse_today_schedule(today_packet(), expected_group=group)


@pytest.mark.parametrize("multi_group", [None, ""])
def test_empty_directory_is_a_valid_snapshot_without_invented_enablement(multi_group):
    schedule = parse(today_packet(group="007", multi_group=multi_group))
    assert schedule == TodaySchedule("7", (), ())
    assert not hasattr(schedule, "enabled") and not hasattr(schedule, "next_run")
    assert not hasattr(schedule, "date") and not hasattr(schedule, "timezone")


@pytest.mark.parametrize("whitespace", ["", " ", "\t\r\n "])
def test_container_and_record_text_allow_only_xml_formatting_whitespace(whitespace):
    root = ET.Element("Packet")
    ET.SubElement(root, "Command").text = "getResponse"
    manager = ET.SubElement(root, "DatabaseManager")
    control = ET.SubElement(manager, "ScheduleControl")
    directory = ET.SubElement(control, "TodayList", {"Group": "7"})
    record = ET.SubElement(directory, "TodayRecord", VALID)
    for node in (manager, control, directory, record):
        node.text = node.tail = whitespace

    schedule = parse(ET.tostring(root, encoding="unicode"))

    assert len(schedule.records) == 1
    assert schedule.records[0].raw_attributes() == VALID
    assert schedule.issues == ()


@pytest.mark.parametrize("node_index", range(4), ids=["database", "control", "list", "row"])
@pytest.mark.parametrize("location", ["text", "tail"])
@pytest.mark.parametrize("value", ["unsupported", " \n error\t", "\u00a0"])
def test_non_whitespace_container_and_record_content_is_rejected(node_index, location, value):
    root = ET.Element("Packet")
    ET.SubElement(root, "Command").text = "getResponse"
    manager = ET.SubElement(root, "DatabaseManager")
    control = ET.SubElement(manager, "ScheduleControl")
    directory = ET.SubElement(control, "TodayList", {"Group": "7"})
    record = ET.SubElement(directory, "TodayRecord", VALID)
    setattr((manager, control, directory, record)[node_index], location, value)

    with pytest.raises(MelansProtocolError, match="unexpected text"):
        parse(ET.tostring(root, encoding="unicode"))


def test_textual_error_in_otherwise_empty_list_is_not_a_successful_empty_schedule():
    with pytest.raises(MelansProtocolError, match="unexpected text"):
        parse(packet('<ScheduleControl><TodayList Group="7">unsupported</TodayList></ScheduleControl>'))


def test_records_keep_source_order_and_independent_immutable_raw_snapshots():
    schedule = parse(today_packet(
        {**VALID, "Index": "2", "Hour": "18", "Minute": "0", "Drive": "OFF"},
        VALID,
    ))
    assert [row.index for row in schedule.records] == ["2", "1"]
    assert [row.hour for row in schedule.records] == [18, 9]
    assert [row.minute for row in schedule.records] == [0, 30]
    assert schedule.issues == ()
    raw = schedule.records[0].raw_attributes()
    raw["Hour"] = "0"
    raw["Password"] = "secret"
    assert schedule.records[0].hour == 18
    assert schedule.records[0].raw_attributes()["Hour"] == "18"
    assert "Password" not in schedule.records[0].raw_attributes()


def test_only_eighteen_raw_record_fields_are_kept_and_unknown_actions_are_not_aliased():
    expected = {
        "Index", "Hour", "Minute", "Drive", "Mode", "VentMode", "SetTemp",
        "SetTemp1", "SetTemp2", "SetTemp3", "SetTemp4", "SetTemp5", "AirDirection",
        "FanSpeed", "Humid", "DriveItem", "ModeItem", "SetTempItem",
    }
    assert set(READ_ATTRIBUTES) == expected and len(READ_ATTRIBUTES) == 18
    attributes = {key: "FUTURE_TOKEN" for key in expected}
    attributes.update(Index="01", Hour="09", Minute="00", Drive="", Mode="*", SetTemp="19.5")
    row = parse(today_packet({
        **attributes, "Name": "private-room", "IPAddress": "private-host", "Password": "secret",
        "AlarmCode": "private-fault", "SerialNumber": "private-device",
    })).records[0]
    assert row.raw_attributes() == attributes
    assert row.index == "1" and row.hour == 9 and row.minute == 0
    assert not hasattr(row, "is_on") and not hasattr(row, "hvac_mode")
    assert "private-" not in repr(row.raw_attributes()) and "secret" not in repr(row.raw_attributes())


@pytest.mark.parametrize("attribute,maximum", [("Hour", 23), ("Minute", 59)])
@pytest.mark.parametrize("value", [None, "", "*", "FUTURE", "-1", "+1", "1.0", " 1", "1 ", "١", "001", "99"])
def test_missing_or_invalid_clock_fields_remain_nullable_with_raw_and_issue(attribute, maximum, value):
    attributes = {**VALID}
    if value is None:
        attributes.pop(attribute)
    else:
        attributes[attribute] = value
    schedule = parse(today_packet(attributes))
    row = schedule.records[0]
    assert getattr(row, attribute.lower()) is None
    assert row.raw_attributes() == attributes
    assert f"unknown_or_invalid_{attribute.lower()}" in row.issues
    assert any(issue.key == attribute and issue.record_index == 0 for issue in schedule.issues)
    assert row.index == "1"


@pytest.mark.parametrize("hour,minute,expected", [
    ("0", "0", (0, 0)), ("00", "00", (0, 0)), ("23", "59", (23, 59)),
    ("24", "0", (None, 0)), ("23", "60", (23, None)),
])
def test_clock_boundaries_do_not_wrap_or_infer_another_day(hour, minute, expected):
    row = parse(today_packet({**VALID, "Hour": hour, "Minute": minute})).records[0]
    assert (row.hour, row.minute) == expected


@pytest.mark.parametrize("index", [None, "", "*", "0", "25", "-1", "1.0", "+1", "1,2", "١", "1" * 100])
def test_invalid_indices_are_isolated_without_losing_their_raw_rows(index):
    attributes = {**VALID}
    if index is None:
        attributes.pop("Index")
    else:
        attributes["Index"] = index
    schedule = parse(today_packet(attributes, {**VALID, "Index": "2"}))
    assert len(schedule.records) == 2
    row = schedule.records[0]
    assert row.index is None and row.raw_attributes() == attributes
    assert row.hour == 9 and row.minute == 30 and row.issues == ("invalid_index",)
    assert schedule.records[1].index == "2" and schedule.records[1].issues == ()
    assert [(issue.kind, issue.record_index, issue.key) for issue in schedule.issues] == [
        ("invalid_index", 0, "Index"),
    ]


def test_identical_duplicates_retain_rows_and_report_the_alias_without_overwriting():
    schedule = parse(today_packet(VALID, {**VALID, "Index": "001"}))
    assert len(schedule.records) == 2
    assert [row.index for row in schedule.records] == ["1", "1"]
    assert all(row.issues == ("duplicate_index",) for row in schedule.records)
    assert [issue.record_index for issue in schedule.issues] == [0, 1]
    assert schedule.records[1].raw_attributes()["Index"] == "001"


@pytest.mark.parametrize("reverse", [False, True])
def test_every_row_of_a_conflicting_index_is_isolated_independently_of_source_order(reverse):
    rows = [VALID, {**VALID, "Index": "01", "Drive": "OFF"}, {**VALID, "Index": "2"}]
    if reverse:
        rows.reverse()
    schedule = parse(today_packet(*rows))
    assert [row.raw_attributes() for row in schedule.records] == rows
    conflict_rows = [row for row in schedule.records if row.raw_attributes()["Index"] in ("1", "01")]
    assert all(row.index is None and "conflicting_index" in row.issues for row in conflict_rows)
    assert next(row for row in schedule.records if row.raw_attributes()["Index"] == "2").index == "2"


def test_sensitive_unknown_attributes_do_not_create_false_duplicate_conflicts():
    schedule = parse(today_packet({**VALID, "Name": "private-a"}, {**VALID, "Name": "private-b"}))
    assert [row.index for row in schedule.records] == ["1", "1"]
    assert all(row.issues == ("duplicate_index",) for row in schedule.records)
    assert all("private-" not in repr(row.raw_attributes()) for row in schedule.records)


def test_twenty_four_rows_are_bounded_and_excess_is_rejected_not_truncated():
    rows = [{**VALID, "Index": str(index)} for index in range(1, MAX_TODAY_RECORDS + 1)]
    assert len(parse(today_packet(*rows)).records) == 24
    with pytest.raises(MelansProtocolError):
        parse(today_packet(*rows, VALID))


@pytest.mark.parametrize("group", [None, "", "*", "0", "51", "8", "٧", "7.0"])
def test_response_requires_the_exact_valid_requested_group(group):
    with pytest.raises(MelansProtocolError):
        parse(today_packet(group=group))


@pytest.mark.parametrize("multi_group", ["*", "7", "7,8", "0", " "])
def test_any_nonempty_multigroup_is_rejected_without_expanding_the_query_scope(multi_group):
    with pytest.raises(MelansProtocolError):
        parse(today_packet(multi_group=multi_group))


@pytest.mark.parametrize("body", [
    "", "<TodayList Group=\"7\" />", "<ScheduleControl />",
    '<Nested><ScheduleControl><TodayList Group="7" /></ScheduleControl></Nested>',
    '<ScheduleControl><Nested><TodayList Group="7" /></Nested></ScheduleControl>',
    '<ScheduleControl><TodayList Group="7" /><TodayList Group="7" /></ScheduleControl>',
    '<ScheduleControl><TodayList Group="7" /><Other /></ScheduleControl>',
    '<ScheduleControl><TodayList Group="7" /></ScheduleControl><ScheduleControl />',
    '<ScheduleControl><TodayList Group="7" /></ScheduleControl><TodayList Group="7" />',
    '<ScheduleControl><TodayList Group="7"><Other /></TodayList></ScheduleControl>',
    '<ScheduleControl><TodayList Group="7"><TodayList Group="7" /></TodayList></ScheduleControl>',
    '<ScheduleControl><TodayList Group="7"><TodayRecord><Other /></TodayRecord></TodayList></ScheduleControl>',
    '<ScheduleControl><TodayList Group="7" Address="7" /></ScheduleControl>',
    '<ScheduleControl><TodayList Group="7"><TodayRecord Address="7" /></TodayList></ScheduleControl>',
    '<ScheduleControl><TodayList Group="7"><TodayRecord Group="8" /></TodayList></ScheduleControl>',
    '<ScheduleControl><TodayList Group="7"><TodayRecord Group="*" /></TodayList></ScheduleControl>',
])
def test_structure_identity_and_record_child_types_are_strict(body):
    with pytest.raises(MelansProtocolError):
        parse(packet(body))


@pytest.mark.parametrize("payload", [
    "not XML",
    packet('<ERROR Code="0101" Message="private-message" /><ScheduleControl><TodayList Group="7" /></ScheduleControl>'),
    packet('<ScheduleControl><TodayList Group="7" /></ScheduleControl>', command="getErrorResponse"),
    packet('<ScheduleControl><TodayList Group="7" /></ScheduleControl>', command="notifyRequest"),
    packet('<ScheduleControl><TodayList Group="7" /></ScheduleControl>', command="setResponse"),
    '<!DOCTYPE Packet [<!ENTITY group "7">]>' + packet('<ScheduleControl><TodayList Group="&group;" /></ScheduleControl>'),
    '<Packet><Command>getResponse</Command><Command>getResponse</Command><DatabaseManager /></Packet>',
    '<Packet><Command>getResponse</Command><DatabaseManager /><DatabaseManager /></Packet>',
    packet("<A>" * 20 + "</A>" * 20),
    packet("<A />" * 2100),
    today_packet({**VALID, "Mode": "x" * 513}),
    " " * (1024 * 1024 + 1),
    packet('<ScheduleControl><TodayList Group="7" Invalid="\ud800" /></ScheduleControl>'),
], ids=[
    "malformed", "error-with-partial-list", "error-command", "notification", "wrong-command",
    "dtd", "duplicate-command", "duplicate-database", "deep-tree", "many-nodes",
    "long-attribute", "oversized", "invalid-text",
])
def test_parser_reuses_shared_xml_envelope_and_safety_limits(payload):
    with pytest.raises(MelansProtocolError):
        parse(payload)
