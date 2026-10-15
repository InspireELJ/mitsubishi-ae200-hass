"""Transports for talking to a MELANS controller.

Two transports implement the same tiny interface (``request(payload) -> str``):

* :class:`WebsocketTransport` — ``ws://<host>/b_xmlproc/`` with the
  ``b_xmlproc`` subprotocol and permessage-deflate. Required to read group
  *names* (``GroupNameWeb``); newer firmware (AE-200/EW-50) supports it.
* :class:`HttpTransport` — ``POST /servlet/MIMEReceiveServlet`` with
  ``text/xml``. Works on every generation back to the G-50, but does not expose
  group names.

The client prefers websocket and falls back to HTTP automatically.
"""

from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET

import aiohttp

from . import const
from .exceptions import MelansCommandUncertainError, MelansConnectionError, MelansProtocolError


def _packet_command(payload: str) -> str | None:
    """Validate a bounded command envelope before classifying a frame."""
    if len(payload) > 1024 * 1024 or re.search(
        r"<!\s*(?:DOCTYPE|ENTITY)\b", payload, re.IGNORECASE
    ):
        raise MelansProtocolError("oversized frame or unsupported XML declaration")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise MelansProtocolError("invalid websocket response XML") from exc
    if root.tag != "Packet" or len(root.findall("./Command")) != 1:
        raise MelansProtocolError("expected one websocket packet command")
    return root.findtext("./Command")


class HttpTransport:
    """Plain HTTP servlet transport (universal, no group names)."""

    def __init__(
        self,
        host: str,
        *,
        port: int = const.DEFAULT_PORT,
        path: str = const.DEFAULT_HTTP_PATH,
        session: aiohttp.ClientSession | None = None,
        timeout: int = const.REQUEST_TIMEOUT,
    ):
        self._url = f"http://{host}:{port}{path}"
        self._session = session
        self._own_session = session is None
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def request(self, payload: str) -> str:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        try:
            async with self._session.post(
                self._url,
                data=payload.encode("utf-8"),
                headers={"Content-Type": "text/xml"},
                timeout=self._timeout,
            ) as resp:
                resp.raise_for_status()
                return await resp.text()
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise MelansConnectionError(f"HTTP request failed: {exc}") from exc

    async def close(self) -> None:
        if self._own_session and self._session is not None:
            await self._session.close()
            self._session = None


class WebsocketTransport:
    """Websocket transport (group names + full attribute set)."""

    def __init__(
        self,
        host: str,
        *,
        port: int = const.DEFAULT_PORT,
        path: str = const.DEFAULT_WS_PATH,
        timeout: int = const.REQUEST_TIMEOUT,
    ):
        self._host = host
        self._uri = f"ws://{host}:{port}{path}"
        self._origin = f"http://{host}:{port}" if port != 80 else f"http://{host}"
        self._timeout = timeout

    async def request(self, payload: str) -> str:
        # Imported lazily so the HTTP-only path has no hard websockets dep.
        try:
            import websockets
            from websockets.exceptions import ConnectionClosedError, PayloadTooBig
            from websockets.exceptions import ProtocolError as WebsocketProtocolError
            from websockets.extensions import permessage_deflate
        except ImportError as exc:  # pragma: no cover
            raise MelansConnectionError("websockets library not installed") from exc

        command = _packet_command(payload)
        expected = {
            "getRequest": {"getResponse", "getErrorResponse"},
            "setRequest": {"setResponse", "setErrorResponse"},
        }.get(command)
        if expected is None:
            raise MelansProtocolError("unsupported websocket request command")
        send_attempted = False
        try:
            async with websockets.connect(
                self._uri,
                extensions=[permessage_deflate.ClientPerMessageDeflateFactory()],
                origin=websockets.Origin(self._origin),
                subprotocols=[websockets.Subprotocol(const.WS_SUBPROTOCOL)],
                open_timeout=self._timeout,
                close_timeout=self._timeout,
            ) as ws:
                # A failed send can still have delivered some or all bytes.
                # Mark the boundary before awaiting, not after send succeeds.
                send_attempted = True
                await ws.send(payload)
                # A controller may push a notification before this request's
                # response, even on a newly opened socket. Do not use that
                # partial notification as a read sample or write acknowledgement.
                # One deadline and a frame cap bound a noisy notification stream.
                async with asyncio.timeout(self._timeout):
                    for _ in range(33):
                        data = await ws.recv()
                        try:
                            response = data.decode("utf-8") if isinstance(data, bytes) else data
                        except UnicodeDecodeError as exc:
                            raise MelansProtocolError("invalid websocket response encoding") from exc
                        response_command = _packet_command(response)
                        if response_command in expected:
                            return response
                        if response_command not in {
                            "notifyRequest", "KeepAlive"
                        }:
                            raise MelansProtocolError("unexpected websocket response command")
                    raise MelansProtocolError("too many notifications before response")
        except MelansProtocolError:
            raise
        except (PayloadTooBig, WebsocketProtocolError) as exc:
            raise MelansProtocolError("invalid websocket frame") from exc
        except ConnectionClosedError as exc:
            # The websocket library rejects oversized or malformed frames before
            # recv returns. Do not bypass that rejection by repeating via HTTP.
            if any(
                close is not None and close.code in (1002, 1007, 1009)
                for close in (exc.sent, exc.rcvd)
            ):
                raise MelansProtocolError("websocket rejected response frame") from exc
            if command == "setRequest" and send_attempted:
                raise MelansCommandUncertainError(
                    "websocket closed after command transmission began; outcome is unknown"
                ) from exc
            raise MelansConnectionError(f"websocket connection closed: {exc}") from exc
        except Exception as exc:
            if command == "setRequest" and send_attempted:
                raise MelansCommandUncertainError(
                    "websocket command transmission began but acknowledgement was not received"
                ) from exc
            raise MelansConnectionError(f"websocket request failed: {exc}") from exc

    async def close(self) -> None:  # symmetry with HttpTransport
        return None
