"""Bounded raw-only room-temperature history for one configured IC member/day.

The original daily graph suggests 48 half-hour labels. Labels describe graph
positions, not proven sampling instants or averaging periods. No numeric unit,
measurement validity, collection state, timezone or aggregation is inferred.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .daily_trend import BUCKET_COUNT as BUCKET_COUNT
from .daily_trend import BUCKET_MINUTES as BUCKET_MINUTES
from .daily_trend import (
    _build_request,
    _raw_series,
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
    raw = _raw_series(
        payload, selector, selected_field=DATA_FIELD, context="member-temperature-history",
    )
    # Keep the existing public ROOM classes and their defining module while
    # sharing structural/raw parsing with other explicit daily channels.
    return MemberTemperatureHistory(
        selector["Address"], expected_date, raw.record_count, raw.raw_data, raw.raw_tokens,
        tuple(TemperatureHistorySlot(
            slot.index, slot.start_time, slot.end_time, slot.raw_token,
        ) for slot in raw.slots),
        tuple(TemperatureHistoryIssue(issue.key, issue.kind) for issue in raw.issues),
    )
