"""Bounded raw-only room-temperature history for one configured IC member/day.

The original daily graph suggests 48 half-hour labels. Labels describe graph
positions, not proven sampling instants or averaging periods. No numeric unit,
measurement validity, collection state, timezone or aggregation is inferred.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .daily_trend import (
    BUCKET_COUNT,
    BUCKET_MINUTES,
    _build_request,
    _directory,
    _time_label,
    member_day_selector,
)

DATA_NAME = "ROOM_TEMP"
DATA_FIELD = "RoomTempData"


@dataclass(frozen=True)
class TemperatureHistoryIssue:
    key: str
    kind: str


@dataclass(frozen=True)
class TemperatureHistorySlot:
    index: int
    start_time: str
    end_time: str
    raw_token: str = field(repr=False)


@dataclass(frozen=True)
class MemberTemperatureHistory:
    address: str
    date: str
    record_count: int
    raw_data: str | None = field(repr=False)
    raw_tokens: tuple[str, ...] = field(repr=False)
    slots: tuple[TemperatureHistorySlot, ...]
    issues: tuple[TemperatureHistoryIssue, ...] = ()

    def raw_attributes(self) -> dict[str, str]:
        """Return only RoomTempData in a new dictionary, without unit claims."""
        return {} if self.raw_data is None else {DATA_FIELD: self.raw_data}


def _selector(address: str | int, date_iso: str) -> dict[str, str]:
    return {**member_day_selector(address, date_iso), "DataName": DATA_NAME}


def build_member_temperature_history_request(address: str | int, date_iso: str) -> str:
    """Read only ROOM_TEMP for one explicit member/date; never discover members."""
    return _build_request(_selector(address, date_iso))


def parse_member_temperature_history(
    payload: str, *, expected_address: str | int, expected_date: str,
) -> MemberTemperatureHistory:
    """Keep selected raw evidence and label only an exact 48-token response.

    Tokens remain strings even if numeric-looking, blank or unknown. No padding,
    truncation, runtime alias, unit conversion or aggregate is performed.
    """
    selector = _selector(expected_address, expected_date)
    directory = _directory(payload, selector, context="member-temperature-history")
    records = list(directory)
    raw_data = records[0].get(DATA_FIELD) if records else None
    raw_tokens = tuple(raw_data.split(",")) if raw_data is not None else ()
    issues: list[TemperatureHistoryIssue] = []
    slots: list[TemperatureHistorySlot] = []
    if not records:
        issues.append(TemperatureHistoryIssue("record", "missing_record"))
    elif raw_data is None:
        issues.append(TemperatureHistoryIssue(DATA_FIELD, "missing_data"))
    elif len(raw_tokens) != BUCKET_COUNT:
        issues.append(TemperatureHistoryIssue(DATA_FIELD, "unexpected_token_count"))
    else:
        for index, token in enumerate(raw_tokens):
            start = index * BUCKET_MINUTES
            slots.append(TemperatureHistorySlot(
                index, _time_label(start), _time_label(start + BUCKET_MINUTES), token,
            ))
    return MemberTemperatureHistory(
        selector["Address"], expected_date, len(records),
        raw_data, raw_tokens, tuple(slots), tuple(issues),
    )
