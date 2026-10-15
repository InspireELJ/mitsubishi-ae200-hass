"""Offline contracts for one bounded weekly-pattern selector without scans."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import FrozenInstanceError

import pytest
from pymelans.exceptions import MelansProtocolError
from pymelans.today_schedule import parse_today_schedule
from pymelans.weekly_pattern import (
    MAX_WEEKLY_RECORDS,
    WeeklyPattern,
    build_weekly_pattern_request,
    parse_weekly_pattern,
)

PLAN = {"Index": "1", "Hour": "9", "Minute": "30", "Drive": "ON", "Mode": "COOL"}
SELECTOR = {"Group": "7", "Season": "2", "Pattern": "3"}


def packet(body, command="getResponse"):
    return f"<Packet><Command>{command}</Command><DatabaseManager>{body}</DatabaseManager></Packet>"


def weekly_packet(*records, selector=None):
    control = ET.Element("ScheduleControl")
    directory = ET.SubElement(control, "WPatternList", SELECTOR if selector is None else selector)
    for attributes in records:
        ET.SubElement(directory, "WPatternRecord", attributes)
    return packet(ET.tostring(control, encoding="unicode"))


def parse(payload):
    return parse_weekly_pattern(payload, expected_group="7", expected_season="2", expected_pattern="3")


@pytest.mark.parametrize("group,season,pattern", [(7, 2, 3), ("7", "2", "3"), ("007", "02", "003")])
def test_request_is_one_explicit_canonical_selector_without_multi_group_or_enumeration(group, season, pattern):
    root = ET.fromstring(build_weekly_pattern_request(group, season, pattern))
    assert root.findtext("./Command") == "getRequest"
    manager = root.find("./DatabaseManager")
    assert len(manager) == 1 and manager[0].tag == "ScheduleControl"
    assert len(manager[0]) == 1 and manager[0][0].tag == "WPatternList"
    assert manager[0][0].attrib == SELECTOR and len(manager[0][0]) == 0


@pytest.mark.parametrize("position,invalid", [
    (0, 0), (0, 51), (1, 0), (1, 6), (2, 0), (2, 8),
    (0, True), (1, "*"), (2, ""), (0, "7,8"), (1, "2.0"),
    (2, "٣"), (0, " 7"), (0, "0007"),
])
def test_builder_and_expected_selector_require_bounded_ascii_numbers(position, invalid):
    values = [7, 2, 3]
    values[position] = invalid
    with pytest.raises(ValueError):
        build_weekly_pattern_request(*values)
    with pytest.raises(ValueError):
        parse_weekly_pattern(weekly_packet(), expected_group=values[0], expected_season=values[1], expected_pattern=values[2])


def test_empty_list_is_a_frozen_snapshot_without_activation_or_calendar_claims():
    sample = parse(weekly_packet())
    assert sample == WeeklyPattern("7", "2", "3", (), ())
    assert not hasattr(sample, "enabled") and not hasattr(sample, "weekday") and not hasattr(sample, "next_run")
    with pytest.raises(FrozenInstanceError):
        sample.season = "1"


def test_rows_use_exactly_the_today_schedule_raw_nullable_and_conflict_contract():
    rows = [
        {**PLAN, "Index": "2", "Hour": "18", "Drive": "", "Mode": "*", "Name": "private-room"},
        {**PLAN, "Hour": "*", "Drive": "FUTURE"},
        {**PLAN, "Index": "01", "Hour": "*", "Drive": "OFF"},
        {**PLAN, "Index": "0", "Minute": "FUTURE", "Password": "secret"},
    ]
    weekly = parse(weekly_packet(*rows))
    control = ET.Element("ScheduleControl")
    directory = ET.SubElement(control, "TodayList", {"Group": "7"})
    for row in rows:
        ET.SubElement(directory, "TodayRecord", row)
    today = parse_today_schedule(packet(ET.tostring(control, encoding="unicode")), expected_group="7")
    assert weekly.records == today.records and weekly.issues == today.issues
    assert [row.index for row in weekly.records] == ["2", None, None, None]
    assert weekly.records[0].hour == 18
    assert weekly.records[1].hour is weekly.records[2].hour is None
    assert weekly.records[0].raw_attributes()["Drive"] == ""
    assert weekly.records[0].raw_attributes()["Mode"] == "*"
    assert weekly.records[1].raw_attributes()["Drive"] == "FUTURE"
    assert all("private-" not in repr(row.raw_attributes()) and "secret" not in repr(row.raw_attributes()) for row in weekly.records)
    raw = weekly.records[0].raw_attributes()
    raw["Hour"] = "0"
    assert weekly.records[0].hour == 18


def test_identical_duplicates_are_marked_without_becoming_an_execution_count():
    sample = parse(weekly_packet(PLAN, {**PLAN, "Index": "001"}))
    assert len(sample.records) == 2
    assert [row.index for row in sample.records] == ["1", "1"]
    assert all(row.issues == ("duplicate_index",) for row in sample.records)


def test_twenty_four_row_bound_is_a_protection_and_excess_is_not_truncated():
    rows = [{**PLAN, "Index": str(index)} for index in range(1, MAX_WEEKLY_RECORDS + 1)]
    assert len(parse(weekly_packet(*rows)).records) == 24
    with pytest.raises(MelansProtocolError):
        parse(weekly_packet(*rows, PLAN))


@pytest.mark.parametrize("key,bad", [
    ("Group", None), ("Season", None), ("Pattern", None),
    ("Group", "8"), ("Season", "1"), ("Pattern", "4"),
    ("Group", "007"), ("Season", "02"), ("Pattern", "03"),
    ("Group", "٧"), ("Season", "*"), ("Pattern", ""),
])
def test_response_requires_exact_canonical_keys_without_aliases(key, bad):
    selector = {**SELECTOR}
    if bad is None:
        selector.pop(key)
    else:
        selector[key] = bad
    with pytest.raises(MelansProtocolError):
        parse(weekly_packet(selector=selector))


@pytest.mark.parametrize("key", ["MultiGroup", "Address"])
@pytest.mark.parametrize("value", ["", "*", "7", "7,8"])
@pytest.mark.parametrize("location", ["directory", "record", "packet"])
def test_aggregate_or_address_identities_are_rejected_before_adapting_rows(key, value, location):
    root = ET.fromstring(weekly_packet(PLAN))
    node = root if location == "packet" else root.find(".//WPatternList" if location == "directory" else ".//WPatternRecord")
    node.set(key, value)
    with pytest.raises(MelansProtocolError):
        parse(ET.tostring(root, encoding="unicode"))


@pytest.mark.parametrize("key,bad", [("Group", "8"), ("Season", "1"), ("Pattern", "4"), ("Group", "007")])
def test_row_keys_cannot_override_the_validated_directory_selector(key, bad):
    with pytest.raises(MelansProtocolError):
        parse(weekly_packet({**PLAN, key: bad}))


@pytest.mark.parametrize("body", [
    "", '<WPatternList Group="7" Season="2" Pattern="3" />', '<ScheduleControl />',
    '<Nested><ScheduleControl><WPatternList Group="7" Season="2" Pattern="3" /></ScheduleControl></Nested>',
    '<ScheduleControl><WPatternList Group="7" Season="2" Pattern="3" /><WPatternList /></ScheduleControl>',
    '<ScheduleControl><WPatternList Group="7" Season="2" Pattern="3" /><TodayList /></ScheduleControl>',
    '<ScheduleControl><WPatternList Group="7" Season="2" Pattern="3" /></ScheduleControl><Other />',
    '<ScheduleControl><WPatternList Group="7" Season="2" Pattern="3"><TodayRecord /></WPatternList></ScheduleControl>',
    '<ScheduleControl><WPatternList Group="7" Season="2" Pattern="3"><WPatternRecord><Other /></WPatternRecord></WPatternList></ScheduleControl>',
])
def test_unknown_original_structure_is_rejected_not_sanitised_away(body):
    with pytest.raises(MelansProtocolError):
        parse(packet(body))


@pytest.mark.parametrize("text", ["junk", "\u00a0", "\u2003"])
@pytest.mark.parametrize("location", ["packet", "command-tail", "database", "control", "directory", "record", "record-tail"])
def test_original_mixed_text_is_rejected_before_copying_to_today_grammar(text, location):
    root = ET.fromstring(weekly_packet(PLAN))
    paths = {"packet": ".", "command-tail": "./Command", "database": "./DatabaseManager",
             "control": ".//ScheduleControl", "directory": ".//WPatternList",
             "record": ".//WPatternRecord", "record-tail": ".//WPatternRecord"}
    node = root.find(paths[location])
    if location.endswith("tail"):
        node.tail = text
    else:
        node.text = text
    with pytest.raises(MelansProtocolError):
        parse(ET.tostring(root, encoding="unicode"))


def test_xml_whitespace_is_allowed_without_modifying_raw_attribute_values():
    root = ET.fromstring(weekly_packet(PLAN))
    for node in root.iter():
        if node.tag != "Command":
            node.text = " \t\r\n"
        node.tail = " \t\r\n"
    assert parse(ET.tostring(root, encoding="unicode")).records[0].raw_attributes() == PLAN


@pytest.mark.parametrize("payload", [
    "not XML",
    weekly_packet(PLAN).replace("getResponse", "notifyRequest"),
    weekly_packet(PLAN).replace("getResponse", "getErrorResponse"),
    weekly_packet(PLAN).replace("getResponse", "setResponse"),
    weekly_packet(PLAN).replace("<ScheduleControl>", '<ERROR Code="0101" Message="private" /><ScheduleControl>'),
    '<!DOCTYPE Packet [<!ENTITY group "7">]>' + weekly_packet(PLAN),
    '<Packet><Command>getResponse</Command><Command>getResponse</Command><DatabaseManager /></Packet>',
    '<Packet><Command>getResponse<Other /></Command><DatabaseManager /></Packet>',
    '<Packet><Command>getResponse</Command><DatabaseManager /><DatabaseManager /></Packet>',
    weekly_packet(PLAN).replace("</Packet>", "<Other /></Packet>"),
    packet("<A>" * 20 + "</A>" * 20),
    packet("<A />" * 2100),
    weekly_packet({**PLAN, "Mode": "x" * 513}),
    " " * (1024 * 1024 + 1),
    weekly_packet({**PLAN, "Mode": "\ud800"}),
], ids=[
    "malformed", "notification", "error-command", "wrong-command", "partial-error", "dtd",
    "duplicate-command", "nested-command", "duplicate-database", "extra-root-child",
    "deep-tree", "many-nodes", "long-attribute", "oversized", "invalid-text",
])
def test_shared_safety_and_original_error_nodes_are_checked_before_row_adaptation(payload):
    with pytest.raises(MelansProtocolError):
        parse(payload)
