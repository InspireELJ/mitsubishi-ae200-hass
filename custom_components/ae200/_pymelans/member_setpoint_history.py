"""Bounded raw-only COOL_SET_TEMP or HEAT_SET_TEMP history for one IC member.

Empty strings and numeric-looking tokens remain raw evidence. Neither this
module nor graph-position labels establish units, sample timing, active
settings, feature support, collection validity or a current climate target.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .daily_trend import (
    RawDailyTrendIssue,
    RawDailyTrendSlot,
    _build_request,
    _raw_series,
    member_day_selector,
)

DATA_FIELDS = {"COOL_SET_TEMP": "CoolSetTempData", "HEAT_SET_TEMP": "HeatSetTempData"}


@dataclass(frozen=True)
class MemberSetpointHistory:
    address: str
    date: str
    data_name: str
    record_count: int
    raw_data: str | None = field(repr=False)
    raw_tokens: tuple[str, ...] = field(repr=False)
    slots: tuple[RawDailyTrendSlot, ...]
    issues: tuple[RawDailyTrendIssue, ...] = ()

    def raw_attributes(self) -> dict[str, str]:
        """Return only the exact selected raw attribute in a new dictionary."""
        return {} if self.raw_data is None else {DATA_FIELDS[self.data_name]: self.raw_data}


def _selector(address: str | int, date_iso: str, data_name: str) -> dict[str, str]:
    selector = member_day_selector(address, date_iso)
    if not isinstance(data_name, str) or data_name not in DATA_FIELDS:
        raise ValueError("data_name must be COOL_SET_TEMP or HEAT_SET_TEMP")
    return {**selector, "DataName": data_name}


def build_member_setpoint_history_request(
    address: str | int, date_iso: str, data_name: str,
) -> str:
    """Read one explicit member/date/setpoint channel, without controls or aliases."""
    return _build_request(_selector(address, date_iso, data_name))


def parse_member_setpoint_history(
    payload: str, *, expected_address: str | int, expected_date: str, expected_data_name: str,
) -> MemberSetpointHistory:
    """Preserve the selected series, labelling only the exact 48-token shape.

    Forty-eight blank tokens are forty-eight raw blank slots, not zero values,
    a support determination or evidence of an active historic setpoint.
    """
    selector = _selector(expected_address, expected_date, expected_data_name)
    raw = _raw_series(
        payload, selector, selected_field=DATA_FIELDS[expected_data_name],
        context="member-setpoint-history",
    )
    return MemberSetpointHistory(
        selector["Address"], expected_date, expected_data_name, raw.record_count,
        raw.raw_data, raw.raw_tokens, raw.slots, raw.issues,
    )
