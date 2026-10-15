"""Strict, read-only controller identity and function-status XML.

Only the fields established by the controller's web client and live responses
are retained. Licence keys, serial numbers, network settings and unverified
options are never requested or returned by these helpers. They perform no I/O.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass

from .exceptions import MelansProtocolError
from .topology import _response

MAX_FUNCTION_INDEX = 256
MAX_FUNCTION_RECORDS = 256
MAX_TOKEN_LENGTH = 64
_XML_DECL = '<?xml version="1.0" encoding="UTF-8"?>'
_TOKEN = re.compile(r"[A-Za-z0-9_.()+-]{1,64}")


@dataclass(frozen=True)
class MetadataIssue:
    """Describe a field or record problem without storing its unsafe value."""

    kind: str
    record_index: int | None = None
    key: str | None = None


@dataclass(frozen=True)
class ControllerMetadata:
    model: str | None
    version: str | None
    version_if: str | None
    issues: tuple[MetadataIssue, ...] = ()


@dataclass(frozen=True)
class FunctionRecord:
    index: str
    status: str | None
    time_limit: str | None

    @property
    def active(self) -> bool | None:
        """Known licence states only; no endpoint or hardware support is inferred."""
        if self.status in ("ENABLE", "TRIAL"):
            return True
        if self.status in ("DISABLE", "RESERVED"):
            return False
        return None


@dataclass(frozen=True)
class FunctionSnapshot:
    records: tuple[FunctionRecord, ...]
    issues: tuple[MetadataIssue, ...] = ()


def _request() -> tuple[ET.Element, ET.Element]:
    root = ET.Element("Packet")
    ET.SubElement(root, "Command").text = "getRequest"
    return root, ET.SubElement(root, "DatabaseManager")


def build_system_data_request() -> str:
    """Read the three display identity fields without requesting device numbers."""
    root, manager = _request()
    ET.SubElement(manager, "SystemData", {"Model": "*", "Version": "*", "VersionIF": "*"})
    return _XML_DECL + ET.tostring(root, encoding="unicode")


def build_function_list_request() -> str:
    """Read the function-status directory; no activation or register operation."""
    root, manager = _request()
    ET.SubElement(ET.SubElement(manager, "FunctionControl"), "FunctionList")
    return _XML_DECL + ET.tostring(root, encoding="unicode")


def _safe_token(
    raw: str | None,
    key: str,
    issues: list[MetadataIssue],
    record_index: int | None = None,
) -> str | None:
    if raw in (None, "", "*"):
        issues.append(MetadataIssue("missing_or_unknown_field", record_index, key))
        return None
    if len(raw) > MAX_TOKEN_LENGTH or _TOKEN.fullmatch(raw) is None:
        issues.append(MetadataIssue("invalid_field_token", record_index, key))
        return None
    return raw


def parse_system_data(payload: str) -> ControllerMetadata:
    """Parse exactly one SystemData response, keeping version strings intact."""
    manager = _response(payload)
    nodes = manager.findall("./SystemData")
    if len(nodes) != 1 or len(manager.findall(".//SystemData")) != 1:
        raise MelansProtocolError("expected exactly one direct SystemData record")
    issues: list[MetadataIssue] = []
    values = [_safe_token(nodes[0].get(key), key, issues) for key in ("Model", "Version", "VersionIF")]
    return ControllerMetadata(*values, tuple(issues))


def parse_function_list(payload: str) -> FunctionSnapshot:
    """Keep safe function fields and isolate contradictory duplicate indices.

    Index aliases such as 001 and 1 denote the same bounded numeric index.
    Identical duplicates collapse to one record. Every conflicting index is
    excluded as a whole, so XML order cannot choose its displayed status.
    TimeLimit is an uninterpreted string, including the observed 9999 token.
    """
    manager = _response(payload)
    directories = manager.findall("./FunctionControl/FunctionList")
    if (
        len(manager.findall("./FunctionControl")) != 1
        or len(manager.findall(".//FunctionControl")) != 1
        or len(directories) != 1
        or len(manager.findall(".//FunctionList")) != 1
    ):
        raise MelansProtocolError("expected one direct FunctionControl/FunctionList")
    nodes = directories[0].findall("./FunctionRecord")
    if len(nodes) > MAX_FUNCTION_RECORDS:
        raise MelansProtocolError("function response exceeds record limit")
    if len(manager.findall(".//FunctionRecord")) != len(nodes):
        raise MelansProtocolError("function record outside the expected directory")
    issues: list[MetadataIssue] = []
    by_index: dict[str, list[tuple[int, FunctionRecord]]] = defaultdict(list)
    for row, node in enumerate(nodes):
        raw_index = node.get("Index")
        if (
            raw_index is None
            or re.fullmatch(r"[0-9]{1,3}", raw_index) is None
            or not 1 <= int(raw_index) <= MAX_FUNCTION_INDEX
        ):
            issues.append(MetadataIssue("invalid_function_index", row, "Index"))
            continue
        index = str(int(raw_index))
        status = _safe_token(node.get("Status"), "Status", issues, row)
        time_limit = _safe_token(node.get("TimeLimit"), "TimeLimit", issues, row)
        by_index[index].append((row, FunctionRecord(index, status, time_limit)))
    records = []
    for index, rows in sorted(by_index.items(), key=lambda item: int(item[0])):
        if len(rows) > 1:
            identical = len({record for _, record in rows}) == 1
            kind = "duplicate_function" if identical else "conflicting_function"
            issues.extend(MetadataIssue(kind, row, "Index") for row, _ in rows)
            if not identical:
                continue
        records.append(rows[0][1])
    return FunctionSnapshot(tuple(records), tuple(issues))
