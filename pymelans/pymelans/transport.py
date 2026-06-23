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

import aiohttp

from . import const
from .exceptions import MelansConnectionError


class HttpTransport:
    """Plain HTTP servlet transport (universal, no group names)."""

    def __init__(self, host: str, *, port: int = const.DEFAULT_PORT,
                 path: str = const.DEFAULT_HTTP_PATH,
                 session: aiohttp.ClientSession | None = None,
                 timeout: int = const.REQUEST_TIMEOUT):
        self._url = f"http://{host}:{port}{path}"
        self._session = session
        self._own_session = session is None
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def request(self, payload: str) -> str:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        try:
            async with self._session.post(
                self._url, data=payload.encode("utf-8"),
                headers={"Content-Type": "text/xml"}, timeout=self._timeout,
            ) as resp:
                resp.raise_for_status()
                return await resp.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise MelansConnectionError(f"HTTP request failed: {exc}") from exc

    async def close(self) -> None:
        if self._own_session and self._session is not None:
            await self._session.close()
            self._session = None


class WebsocketTransport:
    """Websocket transport (group names + full attribute set)."""

    def __init__(self, host: str, *, port: int = const.DEFAULT_PORT,
                 path: str = const.DEFAULT_WS_PATH,
                 timeout: int = const.REQUEST_TIMEOUT):
        self._host = host
        self._uri = f"ws://{host}:{port}{path}"
        self._origin = f"http://{host}:{port}" if port != 80 else f"http://{host}"
        self._timeout = timeout

    async def request(self, payload: str) -> str:
        # Imported lazily so the HTTP-only path has no hard websockets dep.
        try:
            import websockets
            from websockets.extensions import permessage_deflate
        except ImportError as exc:  # pragma: no cover
            raise MelansConnectionError("websockets library not installed") from exc

        try:
            async with websockets.connect(
                self._uri,
                extensions=[permessage_deflate.ClientPerMessageDeflateFactory()],
                origin=websockets.Origin(self._origin),
                subprotocols=[websockets.Subprotocol(const.WS_SUBPROTOCOL)],
                open_timeout=self._timeout,
                close_timeout=self._timeout,
            ) as ws:
                await ws.send(payload)
                data = await asyncio.wait_for(ws.recv(), timeout=self._timeout)
                return data.decode("utf-8") if isinstance(data, bytes) else data
        except Exception as exc:  # noqa: BLE001 - websockets raises many types; normalise
            raise MelansConnectionError(f"websocket request failed: {exc}") from exc

    async def close(self) -> None:  # symmetry with HttpTransport
        return None
