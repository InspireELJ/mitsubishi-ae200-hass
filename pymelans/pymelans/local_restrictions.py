"""Pure single-group planning of four local AC wall-remote restrictions.

These are controller-reported wall-remote function restrictions, not an access
control or authentication boundary. No helper performs I/O, proves freshness,
checks stop context, or executes/retries a command. A client transaction must
perform those checks and a fresh baseline read while holding its write lock.

Planning merges effective restrictions. It deliberately normalises dormant raw
CHK_ON bits hidden by RemoCon=PERMIT; the unmodified raw baseline remains useful
to a private, explicitly verified restoration workflow. No raw restore service
or builder is provided here.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass, field
from itertools import islice

from .exceptions import MelansProtocolError
from .topology import _identifier, _response

READ_ATTRIBUTES = ("RemoCon", "DriveItem", "ModeItem", "SetTempItem", "FilterItem")
_FUNCTION_FIELDS = (
    ("power", "DriveItem"), ("mode", "ModeItem"),
    ("temperature", "SetTempItem"), ("filter_reset", "FilterItem"),
)
_REQUIRED_CAPABILITIES = {"Model": "IC", "IcKind": "NORMAL", "GroupType": "OLD"}
_XML_DECL = '<?xml version="1.0" encoding="UTF-8"?>'
_XML_WHITESPACE = re.compile(r"[ \t\r\n]*")


def _canonical_group(group_id: str | int) -> str:
    return _identifier(group_id, 50)


def _known_raw(attributes: Mapping[str, str]) -> None:
    if set(attributes) != set(READ_ATTRIBUTES):
        raise ValueError("a complete five-field local restriction baseline is required")
    if attributes["RemoCon"] not in ("PERMIT", "PROHIBIT") or any(
        attributes[key] not in ("CHK_ON", "CHK_OFF") for _, key in _FUNCTION_FIELDS
    ):
        raise ValueError("unknown local restriction baseline token")


def _effective_items(attributes: Mapping[str, str]) -> tuple[tuple[str, bool], ...]:
    _known_raw(attributes)
    return tuple(
        (function, attributes["RemoCon"] == "PROHIBIT" and attributes[field] == "CHK_ON")
        for function, field in _FUNCTION_FIELDS
    )


def _immutable_effective(values: tuple[tuple[str, bool], ...]) -> None:
    if (
        not isinstance(values, tuple)
        or len(values) != len(_FUNCTION_FIELDS)
        or any(not isinstance(item, tuple) or len(item) != 2 for item in values)
        or tuple(key for key, _ in values) != tuple(key for key, _ in _FUNCTION_FIELDS)
        or any(type(value) is not bool for _, value in values)
    ):
        raise ValueError("expected four immutable, strictly boolean effective restrictions")


@dataclass(frozen=True)
class LocalRestrictions:
    group_id: str
    _attributes: tuple[tuple[str, str], ...] = field(repr=False)

    def __post_init__(self) -> None:
        if self.group_id != _canonical_group(self.group_id):
            raise ValueError("local restriction group must be canonical")
        if (
            not isinstance(self._attributes, tuple)
            or len(self._attributes) != len(READ_ATTRIBUTES)
            or any(not isinstance(item, tuple) or len(item) != 2 for item in self._attributes)
            or tuple(key for key, _ in self._attributes) != READ_ATTRIBUTES
        ):
            raise ValueError("expected an immutable five-field raw baseline")
        _known_raw(dict(self._attributes))

    def raw_attributes(self) -> dict[str, str]:
        return dict(self._attributes)

    def effective(self) -> dict[str, bool]:
        """Return the four current effective restrictions, true = prohibited."""
        return dict(_effective_items(self.raw_attributes()))


@dataclass(frozen=True)
class LocalRestrictionsPlan:
    group_id: str
    _before: tuple[tuple[str, bool], ...] = field(repr=False)
    _after: tuple[tuple[str, bool], ...] = field(repr=False)

    def __post_init__(self) -> None:
        if self.group_id != _canonical_group(self.group_id):
            raise ValueError("local restriction plan group must be canonical")
        _immutable_effective(self._before)
        _immutable_effective(self._after)

    @property
    def before(self) -> dict[str, bool]:
        return dict(self._before)

    @property
    def after(self) -> dict[str, bool]:
        return dict(self._after)

    def write_attributes(self) -> dict[str, str]:
        """Return exactly the five normalised fields, never a Drive command."""
        values = self.after
        return {
            "RemoCon": "PROHIBIT" if any(values.values()) else "PERMIT",
            **{field: "CHK_ON" if values[function] else "CHK_OFF" for function, field in _FUNCTION_FIELDS},
        }


def build_local_restrictions_request(group_id: str | int) -> str:
    """Read exactly one group's five local wall-remote restriction fields."""
    group = _canonical_group(group_id)
    root = ET.Element("Packet")
    ET.SubElement(root, "Command").text = "getRequest"
    ET.SubElement(ET.SubElement(root, "DatabaseManager"), "Mnet", {
        "Group": group, **dict.fromkeys(READ_ATTRIBUTES, "*"),
    })
    return _XML_DECL + ET.tostring(root, encoding="unicode")


def _single_record(payload: str, tag: str, group: str | None = None) -> ET.Element:
    manager = _response(payload)
    root = ET.fromstring(payload)
    if len(root) != 2 or sorted(child.tag for child in root) != ["Command", "DatabaseManager"]:
        raise MelansProtocolError("unexpected local restriction envelope children")
    command = root.find("./Command")
    assert command is not None
    nodes = list(manager)
    if len(command) or len(nodes) != 1 or nodes[0].tag != tag or len(nodes[0]):
        raise MelansProtocolError(f"expected exactly one direct leaf local restriction {tag}")
    if group is not None and nodes[0].get("Group") != group:
        raise MelansProtocolError("local restriction response group mismatch")
    if group is None and "Group" in nodes[0].attrib:
        raise MelansProtocolError("unexpected group identity in global local restriction context")
    for node in root.iter():
        if "Address" in node.attrib or "MultiGroup" in node.attrib:
            raise MelansProtocolError("unverified address or aggregate local restriction identity")
        if node.tag != "Command" and _XML_WHITESPACE.fullmatch(node.text or "") is None:
            raise MelansProtocolError("non-whitespace text in local restriction response")
        if _XML_WHITESPACE.fullmatch(node.tail or "") is None:
            raise MelansProtocolError("non-whitespace tail in local restriction response")
    return nodes[0]


def parse_local_restrictions(payload: str, *, expected_group: str | int) -> LocalRestrictions:
    """Reject incomplete/unknown baselines and every ambiguous XML identity."""
    group = _canonical_group(expected_group)
    node = _single_record(payload, "Mnet", group)
    raw = tuple((key, node.attrib[key]) for key in READ_ATTRIBUTES if key in node.attrib)
    try:
        return LocalRestrictions(group, raw)
    except ValueError as error:
        raise MelansProtocolError("incomplete or unknown local restriction fields") from error


def parse_local_restriction_stops(
    global_payload: str, group_payload: str, *, expected_group: str | int,
) -> dict[str, bool]:
    """Require all three exact stop tokens for a future write transaction.

    This stricter write-gating parser never turns absent, unknown or malformed
    context into an inactive stop source. A true result is evidence of a stop,
    not a fault diagnosis; the caller decides whether to block its operation.
    """
    group = _canonical_group(expected_group)
    global_node = _single_record(global_payload, "SystemData")
    group_node = _single_record(group_payload, "Mnet", group)
    values = (
        ("global_emergency_stop", global_node.get("AllTurnOff")),
        ("bacnet_emergency_stop", group_node.get("BACnetTurnOff")),
        ("energy_saving_stop", group_node.get("TurnOff")),
    )
    if any(raw not in ("ON", "OFF") for _, raw in values):
        raise MelansProtocolError("all three stop sources must have exact known tokens")
    return {key: raw == "ON" for key, raw in values}


def validate_local_restriction_patch(patch: Mapping[str, bool]) -> dict[str, bool]:
    """Copy one to four explicit known bools before a caller performs any I/O."""
    if not isinstance(patch, Mapping):
        raise ValueError("local restriction patch must be a mapping")
    items = tuple(islice(patch.items(), len(_FUNCTION_FIELDS) + 1))
    functions = {key for key, _ in _FUNCTION_FIELDS}
    if (
        not 1 <= len(items) <= len(_FUNCTION_FIELDS)
        or len({key for key, _ in items}) != len(items)
        or any(key not in functions or type(value) is not bool for key, value in items)
    ):
        raise ValueError("patch requires one to four known strictly boolean restrictions")
    return dict(items)


def plan_local_restrictions_patch(
    baseline: LocalRestrictions, patch: Mapping[str, bool], *,
    capability_attributes: Mapping[str, str],
) -> LocalRestrictionsPlan:
    """Merge an explicit boolean patch on an ordinary OLD/NORMAL IC baseline.

    The caller must establish matching group capabilities, fresh observations,
    known inactive stop sources and the controller write lock before I/O. These
    requirements cannot be established by this pure planner's inputs alone.
    """
    if not isinstance(baseline, LocalRestrictions):
        raise ValueError("a validated local restriction baseline is required")
    if not isinstance(capability_attributes, Mapping) or any(
        capability_attributes.get(key) != value for key, value in _REQUIRED_CAPABILITIES.items()
    ):
        raise ValueError("only a known OLD NORMAL IC group supports this planner")
    requested = validate_local_restriction_patch(patch)
    before = _effective_items(baseline.raw_attributes())
    merged = dict(before)
    merged.update(requested)
    after = tuple((key, merged[key]) for key, _ in _FUNCTION_FIELDS)
    return LocalRestrictionsPlan(baseline.group_id, before, after)


def build_local_restrictions_set_request(plan: LocalRestrictionsPlan) -> str:
    """Build a planned five-field setRequest without executing or restoring it."""
    if not isinstance(plan, LocalRestrictionsPlan):
        raise ValueError("a validated local restriction plan is required")
    root = ET.Element("Packet")
    ET.SubElement(root, "Command").text = "setRequest"
    ET.SubElement(ET.SubElement(root, "DatabaseManager"), "Mnet", {
        "Group": plan.group_id, **plan.write_attributes(),
    })
    return _XML_DECL + ET.tostring(root, encoding="unicode")
