"""Bounded, read-only evidence for one group/season/weekly-pattern selector.

The official client uses Group, Season and Pattern to read one WPatternList.
No selector enumeration, schedule execution, activation, calendar date or day
of week is inferred. All action rows reuse the TodayList diagnostic contract.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from .exceptions import MelansProtocolError
from .today_schedule import (
    MAX_TODAY_RECORDS,
    READ_ATTRIBUTES,
    TodayScheduleIssue,
    TodayScheduleRecord,
    parse_today_schedule,
)
from .topology import _identifier, _response

MAX_SEASON = 5
MAX_PATTERN = 7
MAX_WEEKLY_RECORDS = MAX_TODAY_RECORDS
_XML_DECL = '<?xml version="1.0" encoding="UTF-8"?>'
_XML_WHITESPACE = re.compile(r"[ \t\r\n]*")


@dataclass(frozen=True)
class WeeklyPattern:
    group_id: str
    season: str
    pattern: str
    records: tuple[TodayScheduleRecord, ...]
    issues: tuple[TodayScheduleIssue, ...] = ()


def _selector(group_id: str | int, season: str | int, pattern: str | int) -> dict[str, str]:
    return {
        "Group": _identifier(group_id, 50),
        "Season": _identifier(season, MAX_SEASON),
        "Pattern": _identifier(pattern, MAX_PATTERN),
    }


def build_weekly_pattern_request(
    group_id: str | int, season: str | int, pattern: str | int
) -> str:
    """Read exactly one explicit selector; callers enforce the group allowlist."""
    selector = _selector(group_id, season, pattern)
    root = ET.Element("Packet")
    ET.SubElement(root, "Command").text = "getRequest"
    control = ET.SubElement(ET.SubElement(root, "DatabaseManager"), "ScheduleControl")
    ET.SubElement(control, "WPatternList", selector)
    return _XML_DECL + ET.tostring(root, encoding="unicode")


def _directory(payload: str, selector: dict[str, str]) -> ET.Element:
    manager = _response(payload)
    # Shared safety checks run before this bounded second envelope inspection.
    # Validate the original tree completely before adapting its row grammar.
    root = ET.fromstring(payload)
    if len(root) != 2 or sorted(child.tag for child in root) != ["Command", "DatabaseManager"]:
        raise MelansProtocolError("unexpected weekly-pattern envelope children")
    command = root.find("./Command")
    assert command is not None
    children = list(manager)
    if len(command) or len(children) != 1 or children[0].tag != "ScheduleControl":
        raise MelansProtocolError("expected one direct weekly ScheduleControl")
    directories = list(children[0])
    if len(directories) != 1 or directories[0].tag != "WPatternList":
        raise MelansProtocolError("expected one direct ScheduleControl/WPatternList")
    directory = directories[0]
    # The verified key spelling is canonical ASCII. Do not accept a padded or
    # alternate response identity just because it normalises to the same int.
    if any(directory.get(key) != value for key, value in selector.items()):
        raise MelansProtocolError("weekly-pattern response selector mismatch")
    nodes = list(directory)
    if len(nodes) > MAX_WEEKLY_RECORDS:
        raise MelansProtocolError("WPatternList exceeds protective record limit")
    if any(node.tag != "WPatternRecord" or len(node) for node in nodes):
        raise MelansProtocolError("unexpected or nested weekly-pattern record")
    for node in root.iter():
        if "MultiGroup" in node.attrib or "Address" in node.attrib:
            raise MelansProtocolError("unverified aggregate or address identity in weekly pattern")
        if node.tag != "Command" and _XML_WHITESPACE.fullmatch(node.text or "") is None:
            raise MelansProtocolError("non-whitespace text in weekly-pattern response")
        if _XML_WHITESPACE.fullmatch(node.tail or "") is None:
            raise MelansProtocolError("non-whitespace tail in weekly-pattern response")
    for node in nodes:
        if any(key in node.attrib and node.get(key) != value for key, value in selector.items()):
            raise MelansProtocolError("weekly-pattern row selector mismatch")
    return directory


def parse_weekly_pattern(
    payload: str, *, expected_group: str | int,
    expected_season: str | int, expected_pattern: str | int,
) -> WeeklyPattern:
    """Validate the full original selector/tree, then reuse bounded row parsing.

    Original order, raw action values, nullable hours/minutes and every row's
    index/duplicate issues have exactly the TodayList diagnostic meaning.
    The 24-row limit is a protective bound from the vendor editor, not a claim
    about every firmware's maximum. No record is treated as an execution.
    """
    selector = _selector(expected_group, expected_season, expected_pattern)
    directory = _directory(payload, selector)
    root = ET.Element("Packet")
    ET.SubElement(root, "Command").text = "getResponse"
    control = ET.SubElement(ET.SubElement(root, "DatabaseManager"), "ScheduleControl")
    today = ET.SubElement(control, "TodayList", {"Group": selector["Group"]})
    for node in directory:
        ET.SubElement(today, "TodayRecord", {
            key: node.attrib[key] for key in READ_ATTRIBUTES if key in node.attrib
        })
    parsed = parse_today_schedule(ET.tostring(root, encoding="unicode"), expected_group=selector["Group"])
    return WeeklyPattern(selector["Group"], selector["Season"], selector["Pattern"], parsed.records, parsed.issues)
