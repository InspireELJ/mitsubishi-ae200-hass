"""Read-only raw controller daylight settings, without time-basis inference.

These eleven SummerTime fields describe a reported configuration. They do not
establish whether a daylight adjustment is enabled or currently applied, the
Clock response's time basis, a timezone, UTC offset, accuracy or clock drift.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from .exceptions import MelansProtocolError
from .topology import _response

READ_ATTRIBUTES = (
    "CountryCode", "Month1", "Day1", "Hour1", "Minute1", "ShiftMin1",
    "Month2", "Day2", "Hour2", "Minute2", "ShiftMin2",
)
_XML_DECL = '<?xml version="1.0" encoding="UTF-8"?>'
_XML_WHITESPACE = re.compile(r"[ \t\r\n]*")


@dataclass(frozen=True)
class DaylightSettingsIssue:
    kind: str
    key: str


@dataclass(frozen=True)
class ControllerDaylightSettings:
    _attributes: tuple[tuple[str, str], ...] = field(repr=False)
    issues: tuple[DaylightSettingsIssue, ...] = ()

    def raw_attributes(self) -> dict[str, str]:
        """Copy only present allowlisted raw strings; preserve explicit blanks."""
        return dict(self._attributes)


def build_controller_daylight_settings_request() -> str:
    """Read one controller configuration leaf, without changing clock or rules."""
    root = ET.Element("Packet")
    ET.SubElement(root, "Command").text = "getRequest"
    ET.SubElement(
        ET.SubElement(root, "DatabaseManager"), "SummerTime",
        dict.fromkeys(READ_ATTRIBUTES, "*"),
    )
    return _XML_DECL + ET.tostring(root, encoding="unicode")


def _record(payload: str) -> ET.Element:
    _response(payload)
    # Validate the bounded original envelope before filtering attributes. Do
    # not let duplicate/foreign records or partial error data become a sample.
    root = ET.fromstring(payload)
    if len(root) != 2 or sorted(child.tag for child in root) != ["Command", "DatabaseManager"]:
        raise MelansProtocolError("unexpected controller-daylight envelope children")
    command = root.find("./Command")
    assert command is not None
    manager = root.find("./DatabaseManager")
    assert manager is not None
    nodes = list(manager)
    if len(command) or len(nodes) != 1 or nodes[0].tag != "SummerTime" or len(nodes[0]):
        raise MelansProtocolError("expected exactly one direct leaf SummerTime record")
    record = nodes[0]
    for node in root.iter():
        if any(key in node.attrib for key in ("Group", "Address", "MultiGroup")):
            raise MelansProtocolError("unexpected controller-daylight member identity")
        if node is not record and any(key in node.attrib for key in READ_ATTRIBUTES):
            raise MelansProtocolError("misplaced controller-daylight attributes")
        if node.tag != "Command" and _XML_WHITESPACE.fullmatch(node.text or "") is None:
            raise MelansProtocolError("non-whitespace text in controller-daylight response")
        if _XML_WHITESPACE.fullmatch(node.tail or "") is None:
            raise MelansProtocolError("non-whitespace tail in controller-daylight response")
    return record


def parse_controller_daylight_settings(payload: str) -> ControllerDaylightSettings:
    """Retain raw settings without aliases, numeric defaults or enable claims.

    An absent field is omitted and carries a structural issue. A present blank,
    wildcard, negative or unfamiliar string remains exactly that raw string;
    this raw-only parser assigns it no validity or enabled-state meaning.
    A record without any recognised attribute is not an accepted sample.
    """
    record = _record(payload)
    raw = tuple((key, record.attrib[key]) for key in READ_ATTRIBUTES if key in record.attrib)
    if not raw:
        raise MelansProtocolError("SummerTime record has no recognised settings")
    issues = tuple(
        DaylightSettingsIssue("missing_attribute", key)
        for key in READ_ATTRIBUTES if key not in record.attrib
    )
    return ControllerDaylightSettings(raw, issues)
