"""Bounded, read-only snapshots of one group's controller TodayList.

Records describe scheduled actions, never current equipment state or confirmed
future execution. Their order and Index are not interpreted as an execution
order. A snapshot carries no calendar date, timezone or enablement claim.

The vendor's ordinary schedule editor allows 24 records. This module uses that
as a protective response limit, not a claim about every controller firmware.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field, replace

from .exceptions import MelansProtocolError
from .topology import _identifier, _response

MAX_TODAY_RECORDS = 24
READ_ATTRIBUTES = (
    "Index", "Hour", "Minute", "Drive", "Mode", "VentMode", "SetTemp",
    "SetTemp1", "SetTemp2", "SetTemp3", "SetTemp4", "SetTemp5",
    "AirDirection", "FanSpeed", "Humid", "DriveItem", "ModeItem", "SetTempItem",
)
_XML_DECL = '<?xml version="1.0" encoding="UTF-8"?>'
_ISSUE_KEYS = {
    "invalid_index": "Index", "conflicting_index": "Index", "duplicate_index": "Index",
    "unknown_or_invalid_hour": "Hour", "unknown_or_invalid_minute": "Minute",
}


def _clock_value(raw: str | None, maximum: int) -> int | None:
    if raw is None or re.fullmatch(r"[0-9]{1,2}", raw) is None:
        return None
    value = int(raw)
    return value if value <= maximum else None


@dataclass(frozen=True)
class TodayScheduleIssue:
    """A diagnostic problem, with a zero-based source-record position."""

    kind: str
    record_index: int
    key: str


@dataclass(frozen=True)
class TodayScheduleRecord:
    """One original row, including rows with invalid or conflicting indices.

    ``index`` is None for every row of a conflicting index, or an invalid
    index. Raw attributes remain available for diagnosis. Hour and minute are
    independently nullable, even when the row has index issues. Action values
    remain uninterpreted: an empty action string is vendor NO_CONTROL, while
    wildcards and future tokens must not become current-state claims.
    """

    index: str | None
    _attributes: tuple[tuple[str, str], ...] = field(repr=False)
    issues: tuple[str, ...] = ()

    def raw_attributes(self) -> dict[str, str]:
        """Return only whitelisted raw strings, as a new independent dict."""
        return dict(self._attributes)

    @property
    def hour(self) -> int | None:
        """Wall-clock hour from 0 to 23, without an inferred date/timezone."""
        return _clock_value(dict(self._attributes).get("Hour"), 23)

    @property
    def minute(self) -> int | None:
        """Wall-clock minute from 0 to 59; missing/invalid stays unknown."""
        return _clock_value(dict(self._attributes).get("Minute"), 59)


@dataclass(frozen=True)
class TodaySchedule:
    group_id: str
    records: tuple[TodayScheduleRecord, ...]
    issues: tuple[TodayScheduleIssue, ...] = ()


def build_today_schedule_request(group_id: str | int) -> str:
    """Read only one concrete group; callers must enforce their allowlist."""
    group = _identifier(group_id, 50)
    root = ET.Element("Packet")
    ET.SubElement(root, "Command").text = "getRequest"
    manager = ET.SubElement(root, "DatabaseManager")
    control = ET.SubElement(manager, "ScheduleControl")
    ET.SubElement(control, "TodayList", {"Group": group})
    return _XML_DECL + ET.tostring(root, encoding="unicode")


def _directory(payload: str, expected: str) -> ET.Element:
    manager = _response(payload)
    children = list(manager)
    if len(children) != 1 or children[0].tag != "ScheduleControl":
        raise MelansProtocolError("expected one direct ScheduleControl")
    directories = list(children[0])
    if len(directories) != 1 or directories[0].tag != "TodayList":
        raise MelansProtocolError("expected one direct ScheduleControl/TodayList")
    directory = directories[0]
    try:
        group = _identifier(directory.get("Group", ""), 50)
    except ValueError as error:
        raise MelansProtocolError("invalid group identity in TodayList") from error
    if group != expected or "Address" in directory.attrib:
        raise MelansProtocolError("unexpected group or address identity in TodayList")
    if directory.get("MultiGroup") not in (None, ""):
        raise MelansProtocolError("unverified MultiGroup in TodayList")
    nodes = list(directory)
    if len(nodes) > MAX_TODAY_RECORDS:
        raise MelansProtocolError("TodayList exceeds record limit")
    if any(node.tag != "TodayRecord" or len(node) or "Address" in node.attrib for node in nodes):
        raise MelansProtocolError("unexpected or nested TodayList record")
    # This protocol stores schedule data in attributes, never mixed content.
    # Accept XML formatting whitespace without turning textual error replies
    # into apparently successful empty schedules or records.
    for node in (manager, children[0], directory, *nodes):
        if any(value is not None and value.strip(" \t\r\n") for value in (node.text, node.tail)):
            raise MelansProtocolError("unexpected text in TodayList response")
    for node in nodes:
        if "Group" in node.attrib:
            try:
                row_group = _identifier(node.attrib["Group"], 50)
            except ValueError as error:
                raise MelansProtocolError("invalid group identity in TodayRecord") from error
            if row_group != expected:
                raise MelansProtocolError("unexpected group identity in TodayRecord")
    return directory


def parse_today_schedule(payload: str, *, expected_group: str | int) -> TodaySchedule:
    """Keep original rows and isolate all ambiguous indices without guessing.

    Invalid indices and every conflicting duplicate index become None. Every
    row is retained with its issues, in source order. Identical duplicates are
    retained and marked, never silently overwritten or treated as extra events.
    Empty lists are valid snapshots, not evidence of disablement or support.
    """
    expected = _identifier(expected_group, 50)
    directory = _directory(payload, expected)
    records: list[TodayScheduleRecord] = []
    by_index: dict[str, list[int]] = defaultdict(list)
    for position, node in enumerate(directory):
        attributes = tuple((key, node.attrib[key]) for key in READ_ATTRIBUTES if key in node.attrib)
        issues: list[str] = []
        try:
            index = _identifier(node.get("Index", ""), MAX_TODAY_RECORDS)
        except ValueError:
            index = None
            issues.append("invalid_index")
        if _clock_value(node.get("Hour"), 23) is None:
            issues.append("unknown_or_invalid_hour")
        if _clock_value(node.get("Minute"), 59) is None:
            issues.append("unknown_or_invalid_minute")
        records.append(TodayScheduleRecord(index, attributes, tuple(issues)))
        if index is not None:
            by_index[index].append(position)
    for index, positions in by_index.items():
        if len(positions) < 2:
            continue
        # Alias spellings of the same valid Index do not create a conflict.
        signatures = set()
        for position in positions:
            raw = records[position].raw_attributes()
            raw["Index"] = index
            signatures.add(tuple(sorted(raw.items())))
        conflict = len(signatures) > 1
        issue = "conflicting_index" if conflict else "duplicate_index"
        for position in positions:
            record = records[position]
            records[position] = replace(
                record, index=None if conflict else index, issues=(*record.issues, issue),
            )
    issues = tuple(
        TodayScheduleIssue(issue, position, _ISSUE_KEYS[issue])
        for position, record in enumerate(records) for issue in record.issues
    )
    return TodaySchedule(expected, tuple(records), issues)
