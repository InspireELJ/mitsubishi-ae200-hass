"""Bounded, read-only IC daily runtime evidence without aggregate claims.

The official IC-member daily graph displays 48 half-hour slots in minutes. A
numeric projection describes a controller report, not validated measurement,
enabled history collection, a daily total or a current HVAC operating state.
The 1970..2037 calendar window and single-record limit are pilot protections,
not statements of server retention, capacity or supported history dates.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from decimal import Decimal

from .daily_trend import (
    BUCKET_COUNT,
    BUCKET_MINUTES,
    _build_request,
    _time_label,
    member_day_selector,
)
from .daily_trend import MAX_YEAR as MAX_YEAR
from .daily_trend import MIN_YEAR as MIN_YEAR
from .daily_trend import _directory as _daily_directory

DATA_FIELDS = {"FAN_TIME": "FanTimeData", "THERMO_TIME": "ThermoTimeData"}
_UNSIGNED_DECIMAL = re.compile(r"[0-9]+(?:\.[0-9]+)?")


@dataclass(frozen=True)
class RuntimeIssue:
    key: str
    kind: str


@dataclass(frozen=True)
class RuntimeBucket:
    index: int
    start_time: str
    end_time: str
    raw_token: str = field(repr=False)
    minutes: float | None


@dataclass(frozen=True)
class MemberRuntime:
    address: str
    date: str
    data_name: str
    record_count: int
    raw_data: str | None = field(repr=False)
    raw_tokens: tuple[str, ...] = field(repr=False)
    buckets: tuple[RuntimeBucket, ...]
    issues: tuple[RuntimeIssue, ...] = ()

    def raw_attributes(self) -> dict[str, str]:
        """Return only the selected raw attribute in a new dictionary."""
        return {} if self.raw_data is None else {DATA_FIELDS[self.data_name]: self.raw_data}


def _selector(address: str | int, date_iso: str, data_name: str) -> dict[str, str]:
    selector = member_day_selector(address, date_iso)
    if not isinstance(data_name, str) or data_name not in DATA_FIELDS:
        raise ValueError("data_name must be FAN_TIME or THERMO_TIME")
    return {**selector, "DataName": data_name}


def build_member_runtime_request(address: str | int, date_iso: str, data_name: str) -> str:
    """Read one explicit member/day/runtime name; the client proves membership."""
    return _build_request(_selector(address, date_iso, data_name))


def _directory(payload: str, selector: dict[str, str]) -> ET.Element:
    return _daily_directory(payload, selector, context="member-runtime")


def _minute_value(raw: str, key: str, issues: list[RuntimeIssue]) -> float | None:
    if raw in ("", "*", "@m"):
        # These are unknown tokens here, not assertions of wire sentinel use.
        issues.append(RuntimeIssue(key, "missing_or_unknown_value"))
        return None
    if _UNSIGNED_DECIMAL.fullmatch(raw) is None:
        issues.append(RuntimeIssue(key, "invalid_value_format"))
        return None
    exact = Decimal(raw)
    # A protective single-member duration boundary, not a vendor schema or a
    # clamp. Decimal comparison avoids rounding 30.000...001 into accepted 30.
    if exact > BUCKET_MINUTES:
        issues.append(RuntimeIssue(key, "anomalous_value"))
        return None
    value = float(exact)
    if not math.isfinite(value) or exact != 0 and value == 0:
        issues.append(RuntimeIssue(key, "unrepresentable_value"))
        return None
    return value


def parse_member_runtime(
    payload: str, *, expected_address: str | int,
    expected_date: str, expected_data_name: str,
) -> MemberRuntime:
    """Preserve raw evidence; project slots only for the exact 48-token shape.

    Slot labels are naive controller wall-clock intervals, ending at 24:00.
    No UTC conversion, chart padding, cross-record sum or daily total is made.
    All-zero data remains a controller report with unverified logging quality.
    """
    selector = _selector(expected_address, expected_date, expected_data_name)
    directory = _directory(payload, selector)
    selected_field = DATA_FIELDS[expected_data_name]
    records = list(directory)
    raw_data = records[0].get(selected_field) if records else None
    raw_tokens = tuple(raw_data.split(",")) if raw_data is not None else ()
    issues: list[RuntimeIssue] = []
    buckets: list[RuntimeBucket] = []
    if not records:
        issues.append(RuntimeIssue("record", "missing_record"))
    elif raw_data is None:
        issues.append(RuntimeIssue(selected_field, "missing_data"))
    elif len(raw_tokens) != BUCKET_COUNT:
        issues.append(RuntimeIssue(selected_field, "unexpected_token_count"))
    else:
        for index, token in enumerate(raw_tokens):
            start = index * BUCKET_MINUTES
            buckets.append(RuntimeBucket(
                index, _time_label(start), _time_label(start + BUCKET_MINUTES),
                token, _minute_value(token, f"bucket_{index}", issues),
            ))
    return MemberRuntime(
        selector["Address"], expected_date, expected_data_name, len(records),
        raw_data, raw_tokens, tuple(buckets), tuple(issues),
    )
