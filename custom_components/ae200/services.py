"""Explicit, bounded actions for a selected AE-200 controller."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from ._pymelans import MelansError, cached_snapshot, member_runtime
from .const import DOMAIN

SERVICE_READ_MEMBER = "read_member"
SERVICE_READ_CONTROL_CONTEXT = "read_control_context"
SERVICE_READ_TODAY_SCHEDULE = "read_today_schedule"
SERVICE_READ_CONTROLLER_CLOCK = "read_controller_clock"
SERVICE_READ_WEEKLY_PATTERN = "read_weekly_pattern"
SERVICE_SET_LOCAL_RESTRICTIONS = "set_local_restrictions"
SERVICE_READ_MEMBER_RUNTIME = "read_member_runtime"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_ADDRESS = "address"
ATTR_GROUP_ID = "group_id"
ATTR_SEASON = "season"
ATTR_PATTERN = "pattern"
ATTR_DATE_ISO = "date_iso"
ATTR_DATA_NAME = "data_name"
_LOCAL_RESTRICTION_FIELDS = {
    "power_prohibited": "power",
    "mode_prohibited": "mode",
    "temperature_prohibited": "temperature",
    "filter_reset_prohibited": "filter_reset",
}


def _member_address(value: Any) -> int:
    """Accept one integer address, without truncating fractional input."""
    if isinstance(value, bool) or not (
        isinstance(value, int) or isinstance(value, str) and value.isdecimal()
    ):
        raise vol.Invalid("address must be an integer from 1 to 250")
    address = int(value)
    if not 1 <= address <= 250:
        raise vol.Invalid("address must be an integer from 1 to 250")
    return address


READ_MEMBER_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): vol.All(str, vol.Length(min=1)),
        vol.Required(ATTR_ADDRESS): _member_address,
    }
)


def _group_id(value: Any) -> int:
    """Accept exactly one integer group without coercing booleans or floats."""
    if isinstance(value, bool) or not (
        isinstance(value, int) or isinstance(value, str) and value.isdecimal()
    ):
        raise vol.Invalid("group_id must be an integer from 1 to 50")
    group = int(value)
    if not 1 <= group <= 50:
        raise vol.Invalid("group_id must be an integer from 1 to 50")
    return group


READ_CONTROL_CONTEXT_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): vol.All(str, vol.Length(min=1)),
        vol.Required(ATTR_GROUP_ID): _group_id,
    }
)
READ_TODAY_SCHEDULE_SCHEMA = READ_CONTROL_CONTEXT_SCHEMA
READ_CONTROLLER_CLOCK_SCHEMA = vol.Schema(
    {vol.Required(ATTR_CONFIG_ENTRY_ID): vol.All(str, vol.Length(min=1))}
)


def _weekly_index(value: Any, field: str, maximum: int) -> int:
    """Validate one concrete weekly selector without truncation or guessing."""
    if isinstance(value, bool) or not (
        isinstance(value, int) or isinstance(value, str) and value.isdecimal()
    ):
        raise vol.Invalid(f"{field} must be an integer from 1 to {maximum}")
    number = int(value)
    if not 1 <= number <= maximum:
        raise vol.Invalid(f"{field} must be an integer from 1 to {maximum}")
    return number


def _season(value: Any) -> int:
    return _weekly_index(value, ATTR_SEASON, 5)


def _pattern(value: Any) -> int:
    return _weekly_index(value, ATTR_PATTERN, 7)


READ_WEEKLY_PATTERN_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): vol.All(str, vol.Length(min=1)),
        vol.Required(ATTR_GROUP_ID): _group_id,
        vol.Required(ATTR_SEASON): _season,
        vol.Required(ATTR_PATTERN): _pattern,
    }
)


def _strict_bool(value: Any) -> bool:
    if type(value) is not bool:
        raise vol.Invalid("local restrictions must be true or false booleans")
    return value


def _require_local_patch(data: dict[str, Any]) -> dict[str, Any]:
    if not any(field in data for field in _LOCAL_RESTRICTION_FIELDS):
        raise vol.Invalid("at least one local restriction must be supplied")
    return data


SET_LOCAL_RESTRICTIONS_SCHEMA = vol.All(
    vol.Schema({
        vol.Required(ATTR_CONFIG_ENTRY_ID): vol.All(str, vol.Length(min=1)),
        vol.Required(ATTR_GROUP_ID): _group_id,
        **{vol.Optional(field): _strict_bool for field in _LOCAL_RESTRICTION_FIELDS},
    }),
    _require_local_patch,
)


def _runtime_selector(data: dict[str, Any]) -> dict[str, Any]:
    """Share strict address/date/channel validation with the protocol layer."""
    try:
        selector = member_runtime._selector(
            data[ATTR_ADDRESS], data[ATTR_DATE_ISO], data[ATTR_DATA_NAME]
        )
    except ValueError as err:
        raise vol.Invalid(str(err)) from err
    return {**data, ATTR_ADDRESS: int(selector["Address"])}


READ_MEMBER_RUNTIME_SCHEMA = vol.All(
    vol.Schema({
        vol.Required(ATTR_CONFIG_ENTRY_ID): vol.All(str, vol.Length(min=1)),
        vol.Required(ATTR_ADDRESS): object,
        vol.Required(ATTR_DATE_ISO): object,
        vol.Required(ATTR_DATA_NAME): object,
    }),
    _runtime_selector,
)


def _entry_coordinator(hass: HomeAssistant, entry_id: str) -> Any:
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError("Select an existing AE-200 configuration entry")
    coordinator = hass.data.get(DOMAIN, {}).get(entry_id)
    if coordinator is None:
        raise HomeAssistantError("The selected AE-200 configuration entry is not loaded")
    return coordinator


def async_setup_services(hass: HomeAssistant) -> None:
    """Register once for the integration, independently of entry lifecycle."""
    async def async_read_member(call: ServiceCall) -> ServiceResponse:
        coordinator = _entry_coordinator(hass, call.data[ATTR_CONFIG_ENTRY_ID])
        address = call.data[ATTR_ADDRESS]
        try:
            result = await coordinator.client.async_read_member(address)
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        except MelansError as err:
            raise HomeAssistantError("The AE-200 member read failed") from err
        if result is None:
            raise HomeAssistantError(
                "The AE-200 member read failed or is cooling down. "
                "Wait at least 15 seconds before another member read"
            )

        # Never return the raw transport result. Shared diagnostics applies the
        # same allowlist and identity redaction as the diagnostic download.
        member = cached_snapshot(coordinator.client).get("members", {}).get(str(address))
        if member is None:
            raise HomeAssistantError("The AE-200 member read did not produce a diagnostic sample")
        return {"address": address, **member}

    async def async_read_control_context(call: ServiceCall) -> ServiceResponse:
        coordinator = _entry_coordinator(hass, call.data[ATTR_CONFIG_ENTRY_ID])
        try:
            context = await coordinator.client.async_read_control_context(call.data[ATTR_GROUP_ID])
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        except MelansError as err:
            raise HomeAssistantError("The AE-200 control context read failed") from err
        if isinstance(context, dict) and context.get("read_status") == "cooldown":
            raise HomeAssistantError(
                "The AE-200 control context read is cooling down. "
                "Wait at least 15 seconds before another control context read"
            )
        if not isinstance(context, dict) or context.get("read_status") not in (
            "updated", "partial", "error"
        ):
            raise HomeAssistantError("The AE-200 read did not produce a control context")
        # The client builds an allowlisted diagnostic snapshot. A partial read
        # keeps independent global/group quality so callers can use either part.
        return context

    async def async_read_today_schedule(call: ServiceCall) -> ServiceResponse:
        coordinator = _entry_coordinator(hass, call.data[ATTR_CONFIG_ENTRY_ID])
        try:
            schedule = await coordinator.client.async_read_today_schedule(call.data[ATTR_GROUP_ID])
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        except MelansError as err:
            raise HomeAssistantError("The AE-200 schedule read failed") from err
        if isinstance(schedule, dict) and schedule.get("read_status") == "cooldown":
            raise HomeAssistantError(
                "Today's AE-200 schedule read is cooling down. "
                "Wait at least 15 seconds before another schedule read"
            )
        if not isinstance(schedule, dict) or schedule.get("read_status") not in ("updated", "error"):
            raise HomeAssistantError("The AE-200 read did not produce today's schedule")
        # This is a bounded controller snapshot, including a valid empty list.
        # It does not update entities or infer the next scheduled execution.
        return schedule

    async def async_read_controller_clock(call: ServiceCall) -> ServiceResponse:
        coordinator = _entry_coordinator(hass, call.data[ATTR_CONFIG_ENTRY_ID])
        try:
            clock = await coordinator.client.async_read_controller_clock()
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        except MelansError as err:
            raise HomeAssistantError("The AE-200 controller clock read failed") from err
        if isinstance(clock, dict) and clock.get("read_status") == "cooldown":
            raise HomeAssistantError(
                "The AE-200 controller clock read is cooling down. "
                "Wait at least 15 seconds before another controller clock read"
            )
        if not isinstance(clock, dict) or clock.get("read_status") not in ("updated", "error"):
            raise HomeAssistantError("The AE-200 read did not produce a controller clock sample")
        # The client exports an allowlisted local wall-clock snapshot. Its
        # independent quality is preserved without inferring a time zone.
        return clock

    async def async_read_weekly_pattern(call: ServiceCall) -> ServiceResponse:
        coordinator = _entry_coordinator(hass, call.data[ATTR_CONFIG_ENTRY_ID])
        try:
            pattern = await coordinator.client.async_read_weekly_pattern(
                call.data[ATTR_GROUP_ID], call.data[ATTR_SEASON], call.data[ATTR_PATTERN]
            )
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        except MelansError as err:
            raise HomeAssistantError("The AE-200 weekly pattern read failed") from err
        if isinstance(pattern, dict) and pattern.get("read_status") == "cooldown":
            raise HomeAssistantError(
                "The AE-200 weekly pattern read is cooling down. "
                "Wait at least 15 seconds before another weekly pattern read"
            )
        if not isinstance(pattern, dict) or pattern.get("read_status") not in ("updated", "error"):
            raise HomeAssistantError("The AE-200 read did not produce a weekly pattern")
        # A pattern is an independently timed plan, not an enabled schedule,
        # a weekday assignment or a prediction of the next execution.
        return pattern

    async def async_set_local_restrictions(call: ServiceCall) -> ServiceResponse:
        coordinator = _entry_coordinator(hass, call.data[ATTR_CONFIG_ENTRY_ID])
        patch = {key: call.data[field] for field, key in _LOCAL_RESTRICTION_FIELDS.items()
                 if field in call.data}
        try:
            report = await coordinator.client.async_set_local_restrictions(
                call.data[ATTR_GROUP_ID], patch
            )
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        except MelansError as err:
            raise HomeAssistantError("The AE-200 local restriction change failed") from err
        if not isinstance(report, dict) or report.get("status") not in (
            "no_change", "confirmed", "accepted_unconfirmed", "not_yet_confirmed",
            "outcome_unknown", "failed",
        ):
            raise HomeAssistantError("The AE-200 change did not produce a local restriction report")
        # This report belongs to this transaction. Failed or uncertain outcomes
        # remain visible and never trigger another write, read or refresh here.
        return report

    async def async_read_member_runtime(call: ServiceCall) -> ServiceResponse:
        coordinator = _entry_coordinator(hass, call.data[ATTR_CONFIG_ENTRY_ID])
        try:
            runtime = await coordinator.client.async_read_member_runtime(
                call.data[ATTR_ADDRESS], call.data[ATTR_DATE_ISO], call.data[ATTR_DATA_NAME]
            )
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        except MelansError as err:
            raise HomeAssistantError("The AE-200 member runtime read failed") from err
        if isinstance(runtime, dict) and runtime.get("read_status") == "cooldown":
            raise HomeAssistantError(
                "The AE-200 member runtime read is cooling down. "
                "Wait at least 15 seconds before another member runtime read"
            )
        if not isinstance(runtime, dict) or runtime.get("read_status") not in ("updated", "error"):
            raise HomeAssistantError("The AE-200 read did not produce a member runtime sample")
        # Retain the client's independently timed evidence, including unknown
        # measurement validity. Never sum buckets or infer current operation.
        return runtime

    if not hass.services.has_service(DOMAIN, SERVICE_READ_MEMBER):
        hass.services.async_register(
            DOMAIN,
            SERVICE_READ_MEMBER,
            async_read_member,
            schema=READ_MEMBER_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_READ_CONTROL_CONTEXT):
        hass.services.async_register(
            DOMAIN,
            SERVICE_READ_CONTROL_CONTEXT,
            async_read_control_context,
            schema=READ_CONTROL_CONTEXT_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_READ_TODAY_SCHEDULE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_READ_TODAY_SCHEDULE,
            async_read_today_schedule,
            schema=READ_TODAY_SCHEDULE_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_READ_CONTROLLER_CLOCK):
        hass.services.async_register(
            DOMAIN,
            SERVICE_READ_CONTROLLER_CLOCK,
            async_read_controller_clock,
            schema=READ_CONTROLLER_CLOCK_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_READ_WEEKLY_PATTERN):
        hass.services.async_register(
            DOMAIN,
            SERVICE_READ_WEEKLY_PATTERN,
            async_read_weekly_pattern,
            schema=READ_WEEKLY_PATTERN_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_SET_LOCAL_RESTRICTIONS):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_LOCAL_RESTRICTIONS,
            async_set_local_restrictions,
            schema=SET_LOCAL_RESTRICTIONS_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_READ_MEMBER_RUNTIME):
        hass.services.async_register(
            DOMAIN,
            SERVICE_READ_MEMBER_RUNTIME,
            async_read_member_runtime,
            schema=READ_MEMBER_RUNTIME_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )
