"""Exercise actual transport code against loopback-only fixture servers."""

from __future__ import annotations

import aiohttp
import pytest
from aiohttp import web
from pymelans.exceptions import MelansConnectionError
from pymelans.protocol import build_state_request
from pymelans.transport import HttpTransport, WebsocketTransport
from websockets.asyncio.server import serve

RESPONSE = '<Packet><Command>getResponse</Command><DatabaseManager><Mnet Group="1" Drive="OFF" /></DatabaseManager></Packet>'


@pytest.mark.asyncio
async def test_websocket_wire_protocol_round_trip():
    received = []

    async def handler(ws):
        received.append(await ws.recv())
        assert ws.subprotocol == "b_xmlproc"
        await ws.send(RESPONSE)

    async with serve(handler, "127.0.0.1", 0, subprotocols=["b_xmlproc"]) as server:
        port = server.sockets[0].getsockname()[1]
        transport = WebsocketTransport("127.0.0.1", port=port, timeout=2)
        reply = await transport.request(build_state_request([1]))
        assert reply == RESPONSE
        assert "getRequest" in received[0]


@pytest.mark.asyncio
async def test_http_round_trip_and_external_session_lifecycle():
    received = []

    async def handler(request):
        received.append(await request.text())
        assert request.headers["Content-Type"] == "text/xml"
        return web.Response(text=RESPONSE, content_type="text/xml")

    app = web.Application()
    app.router.add_post("/servlet/MIMEReceiveServlet", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        port = site._server.sockets[0].getsockname()[1]
        async with aiohttp.ClientSession() as session:
            transport = HttpTransport("127.0.0.1", port=port, session=session)
            assert await transport.request(build_state_request([1])) == RESPONSE
            await transport.close()
            assert not session.closed
        assert len(received) == 1
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_http_status_error_is_normalised():
    async def handler(_):
        return web.Response(status=503)

    app = web.Application()
    app.router.add_post("/servlet/MIMEReceiveServlet", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    transport = HttpTransport("127.0.0.1", port=site._server.sockets[0].getsockname()[1])
    try:
        with pytest.raises(MelansConnectionError):
            await transport.request(build_state_request([1]))
    finally:
        await transport.close()
        await runner.cleanup()
