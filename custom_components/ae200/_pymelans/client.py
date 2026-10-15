"""Rate-limited MELANS client with per-group freshness and checked writes."""

from __future__ import annotations

import asyncio
import math
import time
from datetime import UTC, datetime

import aiohttp

from . import capabilities, const, protocol
from .device import MelansDevice
from .exceptions import MelansConnectionError, MelansError
from .transport import HttpTransport, WebsocketTransport


class MelansClient:
    def __init__(
        self,
        host: str,
        *,
        port: int = const.DEFAULT_PORT,
        prefer_websocket: bool = True,
        session: aiohttp.ClientSession | None = None,
        write_spacing: float = const.MIN_WRITE_SPACING,
        stale_after: float = const.DEFAULT_STALE_AFTER,
    ):
        self.host = host
        self.port = port
        self._prefer_ws = prefer_websocket
        self._ws = WebsocketTransport(host, port=port)
        self._http = HttpTransport(host, port=port, session=session)
        self._names: dict[str, str | None] = {}
        self.devices: dict[str, MelansDevice] = {}
        self.group_capabilities: dict[str, dict[str, str]] = {}
        self.capability_error: str | None = None
        self._write_spacing = max(write_spacing, const.MIN_WRITE_SPACING)
        self.stale_after = max(stale_after, const.MIN_POLL_INTERVAL)
        self._last_write = 0.0
        self._last_request: float | None = None
        self._last_poll: float | None = None
        self._last_poll_ok = False
        self._ws_retry_after = 0.0
        self._uses_ws = prefer_websocket
        self._write_lock = asyncio.Lock()
        self._io_lock = asyncio.Lock()
        self._poll_lock = asyncio.Lock()
        self._group_seen: dict[str, float] = {}
        self.group_last_success: dict[str, datetime] = {}
        self.failed_groups: set[str] = set()
        self.group_errors: dict[str, dict[str, str | None]] = {}
        self.last_attempt: datetime | None = None
        self.last_success: datetime | None = None
        self.last_command: dict[str, object] | None = None
        self._pending_writes: dict[str, dict[str, str]] = {}
        self._group_write_ack_at: dict[str, float] = {}
        self._readback_requested = False

    async def __aenter__(self) -> MelansClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def close(self) -> None:
        await self._ws.close()
        await self._http.close()

    async def _request(self, payload: str) -> str:
        """Serialise discovery, reads and writes against one controller."""
        async with self._io_lock:
            if self._last_request is not None:
                delay = const.MIN_REQUEST_SPACING - (time.monotonic() - self._last_request)
                if delay > 0:
                    await asyncio.sleep(delay)
            self._last_request = time.monotonic()
            if self._prefer_ws and time.monotonic() >= self._ws_retry_after:
                try:
                    response = await self._ws.request(payload)
                except MelansConnectionError:
                    self._uses_ws = False
                    self._ws_retry_after = time.monotonic() + 300
                    delay = const.MIN_REQUEST_SPACING - (time.monotonic() - self._last_request)
                    if delay > 0:
                        await asyncio.sleep(delay)
                    self._last_request = time.monotonic()
                else:
                    self._uses_ws = True
                    self._ws_retry_after = 0.0
                    return response
            self._uses_ws = False
            return await self._http.request(payload)

    async def discover(self) -> dict[str, str | None]:
        """Try configured groups first, then bounded reads for old firmware."""
        if self._prefer_ws:
            try:
                payload = await self._request(protocol.build_group_list_request())
                groups = protocol.parse_group_list(payload)
                self._names = {g["id"]: g["name"] for g in groups if g["id"]}
                if self._names:
                    return dict(self._names)
            except MelansError:
                pass
        found: dict[str, str | None] = {}
        connection_failures = 0
        for gid in range(1, const.MAX_GROUPS + 1):
            try:
                payload = await self._request(protocol.build_state_request([gid]))
                state = protocol.parse_state(payload)
            except MelansConnectionError:
                connection_failures += 1
                if connection_failures >= 2:
                    raise MelansConnectionError("controller unavailable during discovery") from None
                continue
            except MelansError:
                connection_failures = 0
                continue
            connection_failures = 0
            if str(gid) in state:
                found[str(gid)] = None
        self._names = found
        return dict(found)

    async def async_load_capabilities(self) -> None:
        """Attempt one bounded capability read for already discovered groups.

        Older controllers may reject it. Such a rejection leaves capability
        data unknown and does not prevent normal monitoring or add a poll loop.
        """
        if not self._names:
            return
        try:
            response = await self._request(protocol.build_capability_request(self._names))
            groups = protocol.parse_state(response)
        except MelansError as err:
            self.capability_error = type(err).__name__
            return
        self.capability_error = None
        for gid, attrs in groups.items():
            if gid not in self._names:
                continue
            data = {
                key: value
                for key, value in attrs.items()
                if key in capabilities.READ_ATTRIBUTES and value not in ("", "*")
            }
            self.group_capabilities[gid] = data
            if gid in self.devices:
                self.devices[gid].capability_attributes = dict(data)

    async def async_update(self) -> dict[str, MelansDevice]:
        """Retain the historical cache for diagnostics, never as fresh data."""
        async with self._poll_lock:
            if self._last_poll is not None and not self._readback_requested:
                if time.monotonic() - self._last_poll < const.MIN_POLL_INTERVAL:
                    if not self._last_poll_ok:
                        raise MelansConnectionError(
                            "last poll failed; minimum poll interval applies"
                        )
                    return dict(self.devices)
            if not self._names:
                await self.discover()
            ids = list(self._names)
            if not ids:
                raise MelansConnectionError("no configured groups were found")
            self._last_poll = time.monotonic()
            self.last_attempt = datetime.now(UTC)
            self._readback_requested = False
            states: dict[str, dict[str, str]] = {}
            seen: dict[str, tuple[float, datetime]] = {}
            sampled_at: dict[str, float] = {}
            self.group_errors = {}
            connection_failures = 0
            try:
                payload = await self._request(protocol.build_state_request(ids))
                received_at = time.perf_counter()
                states = {
                    k: v for k, v in protocol.parse_state(payload).items() if k in self._names
                }
                seen.update({gid: (time.monotonic(), datetime.now(UTC)) for gid in states})
                sampled_at.update(dict.fromkeys(states, received_at))
            except MelansConnectionError:
                connection_failures = 1
            except MelansError:
                pass
            # Some firmware rejects a batch, or returns only its first group.
            for gid in ids:
                if gid in states:
                    continue
                if connection_failures >= 2:
                    self.group_errors[gid] = {"type": "controller_unavailable", "code": None}
                    continue
                try:
                    payload = await self._request(protocol.build_state_request([gid]))
                    received_at = time.perf_counter()
                    result = protocol.parse_state(payload)
                    if gid in result:
                        states[gid] = result[gid]
                        seen[gid] = (time.monotonic(), datetime.now(UTC))
                        sampled_at[gid] = received_at
                    else:
                        self.group_errors[gid] = {"type": "missing_group", "code": None}
                    connection_failures = 0
                except MelansError as err:
                    self.group_errors[gid] = {
                        "type": type(err).__name__,
                        "code": getattr(err, "code", None),
                    }
                    connection_failures = (
                        connection_failures + 1 if isinstance(err, MelansConnectionError) else 0
                    )
            self.failed_groups = set(ids) - set(states)
            for gid, attrs in states.items():
                if gid in self.devices:
                    self.devices[gid].update(attrs)
                else:
                    self.devices[gid] = MelansDevice(
                        gid, self._names.get(gid), attrs, self.group_capabilities.get(gid)
                    )
                self._group_seen[gid], self.group_last_success[gid] = seen[gid]
                self.last_success = seen[gid][1]
                self._confirm_readback(gid, attrs, sampled_at=sampled_at[gid])
            self._last_poll_ok = bool(states)
            if not states:
                raise MelansConnectionError("no groups returned fresh data")
            return dict(self.devices)

    def _confirm_readback(
        self, gid: str, attrs: dict[str, str], *, sampled_at: float | None = None
    ) -> None:
        # A poll may collect this group before a write, then commit it after
        # that write while reading other groups. Only post-ACK samples can
        # confirm the command or clear its pending attributes.
        # perf_counter is monotonic with finer resolution on Windows, where
        # monotonic() may otherwise put a new read in the same 15 ms tick.
        sampled_at = time.perf_counter() if sampled_at is None else sampled_at
        acknowledged_at = self._group_write_ack_at.get(gid)
        if acknowledged_at is not None and sampled_at <= acknowledged_at:
            return

        def matches(key: str, value: str) -> bool:
            if key == "SetTemp":
                try:
                    return float(attrs.get(key, "")) == float(value)
                except ValueError:
                    return False
            return attrs.get(key) == value

        if (
            self.last_command
            and self.last_command.get("group_id") == gid
            and self.last_command.get("status") in ("accepted_unconfirmed", "not_yet_confirmed")
        ):
            confirmed = all(matches(k, v) for k, v in self.last_command["attributes"].items())
            self.last_command["status"] = "confirmed" if confirmed else "not_yet_confirmed"
        # Individual fields may have matched in different earlier samples.
        # Confirm the latest command above even once that tracking is empty:
        # all its fields must appear together in one fresh readback.
        expected = self._pending_writes.get(gid)
        if not expected:
            return
        remaining = {key: value for key, value in expected.items() if not matches(key, value)}
        if remaining:
            self._pending_writes[gid] = remaining
        else:
            self._pending_writes.pop(gid)

    def is_group_available(self, group_id: str) -> bool:
        seen = self._group_seen.get(group_id)
        return (
            seen is not None
            and group_id not in self.failed_groups
            and time.monotonic() - seen <= self.stale_after
        )

    def group_quality(self, group_id: str) -> str:
        if group_id not in self.group_last_success:
            return "unknown"
        return "fresh" if self.is_group_available(group_id) else "stale"

    @property
    def health(self) -> str:
        if self.last_attempt is None:
            return "unknown"
        available = sum(self.is_group_available(gid) for gid in self._names)
        if not available:
            return "offline"
        return "online" if available == len(self._names) else "degraded"

    async def _write(self, group_id: str, attributes: dict[str, str]) -> None:
        payload = protocol.build_set_request(group_id, attributes)
        group_id = str(int(group_id))
        async with self._write_lock:
            delay = self._write_spacing - (time.monotonic() - self._last_write)
            if delay > 0:
                await asyncio.sleep(delay)
            self.last_command = {
                "group_id": group_id,
                "attributes": dict(attributes),
                "at": datetime.now(UTC).isoformat(),
                "status": "pending",
            }
            try:
                response = await self._request(payload)
                protocol.parse_acknowledgement(response)
            except MelansError as err:
                self.last_command["status"] = "failed"
                self.last_command["error_type"] = type(err).__name__
                self.last_command["error_code"] = getattr(err, "code", None)
                raise
            finally:
                self._last_write = time.monotonic()
            self._group_write_ack_at[group_id] = time.perf_counter()
            self.last_command["status"] = "accepted_unconfirmed"
            self._pending_writes.setdefault(group_id, {}).update(attributes)
            self._readback_requested = True

    async def set_climate(
        self,
        group_id: str,
        *,
        mode: str | None = None,
        power: bool | None = None,
        temperature: float | None = None,
    ) -> None:
        """Apply related settings in one checked, rate-limited setRequest.

        Validate every supplied setting before sending anything. This avoids
        turning a unit on in its old mode before a separate mode change fails.
        An acknowledgement still requires a later state read to confirm it.
        """
        attributes: dict[str, str] = {}
        device = self.devices.get(str(int(group_id))) if str(group_id).isdecimal() else None
        if mode is not None:
            mode = mode.upper()
            if mode not in const.ALL_MODES:
                raise ValueError(f"unsupported mode: {mode}")
            if device and device.supported_modes is not None and mode not in device.supported_modes:
                raise ValueError(f"mode is not supported by this group: {mode}")
            attributes["Mode"] = mode
        if power is not None:
            if not isinstance(power, bool):
                raise ValueError("power must be a boolean")
            attributes["Drive"] = const.DRIVE_ON if power else const.DRIVE_OFF
        if temperature is not None:
            if not math.isfinite(temperature):
                raise ValueError("temperature must be finite")
            if device and device.capability_attributes.get("GroupType") == "NEW":
                raise ValueError("NEW group temperature fields require separate validation")
            limits = device.temperature_range(mode) if device else None
            if limits and not limits[0] <= temperature <= limits[1]:
                raise ValueError(f"temperature must be between {limits[0]:g} and {limits[1]:g} °C")
            step = device.temperature_step if device else None
            if step and not math.isclose(temperature / step, round(temperature / step)):
                raise ValueError(f"temperature must use increments of {step:g} °C")
            attributes["SetTemp"] = f"{temperature:g}"
        if not attributes:
            raise ValueError("at least one climate setting is required")
        await self._write(group_id, attributes)

    async def set_power(self, group_id: str, on: bool) -> None:
        await self._write(group_id, {"Drive": const.DRIVE_ON if on else const.DRIVE_OFF})

    async def set_mode(self, group_id: str, mode: str) -> None:
        await self.set_climate(group_id, mode=mode)

    async def set_temperature(self, group_id: str, celsius: float) -> None:
        await self.set_climate(group_id, temperature=celsius)

    async def set_fan_speed(self, group_id: str, speed: str) -> None:
        speed = speed.upper()
        if speed not in const.ALL_FAN_SPEEDS:
            raise ValueError(f"unsupported fan speed: {speed}")
        dev = self.devices.get(str(int(group_id))) if str(group_id).isdecimal() else None
        if dev and dev.supported_fan_speeds is not None and speed not in dev.supported_fan_speeds:
            raise ValueError(f"fan speed is not supported by this group: {speed}")
        await self._write(group_id, {"FanSpeed": speed})

    async def set_air_direction(self, group_id: str, direction: str) -> None:
        direction = direction.upper()
        if direction not in const.AIR_DIR_VALUES:
            raise ValueError(f"unsupported air direction: {direction}")
        dev = self.devices.get(str(int(group_id))) if str(group_id).isdecimal() else None
        if (
            dev
            and dev.supported_air_directions is not None
            and direction not in dev.supported_air_directions
        ):
            raise ValueError(f"air direction is not supported by this group: {direction}")
        await self._write(group_id, {"AirDirection": direction})

    async def set_local_remote(self, group_id: str, permit: bool) -> None:
        await self._write(
            group_id, {"RemoCon": const.REMOCON_PERMIT if permit else const.REMOCON_PROHIBIT}
        )

    @property
    def transport_name(self) -> str:
        return "websocket" if self._uses_ws else "http"
