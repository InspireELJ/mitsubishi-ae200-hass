"""Exercise actual transport code against loopback-only fixture servers."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import aiohttp
import pytest
from aiohttp import web
from pymelans.exceptions import MelansConnectionError, MelansProtocolError
from pymelans.protocol import build_set_request, build_state_request
from pymelans.transport import HttpTransport, WebsocketTransport
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from pymelans import MelansClient

RESPONSE = '<Packet><Command>getResponse</Command><DatabaseManager><Mnet Group="1" Drive="OFF" /></DatabaseManager></Packet>'
NOTIFICATION = '<Packet><Command>notifyRequest</Command><DatabaseManager><Mnet Address="1" FanStatus="ON" /></DatabaseManager></Packet>'


@pytest.mark.asyncio
@pytest.mark.parametrize('write', [False, True])
async def test_notification_is_not_a_response_and_request_is_sent_once(write):
    received = []
    expected = (RESPONSE if not write else
                '<Packet><Command>setResponse</Command><DatabaseManager /></Packet>')

    async def handler(ws):
        received.append(await ws.recv())
        await ws.send('<Packet><Command>KeepAlive</Command></Packet>')
        await ws.send(NOTIFICATION)
        await ws.send(expected.encode())
        async for message in ws:
            received.append(message)

    async with serve(handler, '127.0.0.1', 0, subprotocols=['b_xmlproc']) as server:
        transport = WebsocketTransport('127.0.0.1', port=server.sockets[0].getsockname()[1])
        request = build_set_request(1, {'Drive':'OFF'}) if write else build_state_request([1])
        assert await transport.request(request) == expected
        assert received == [request]


@pytest.mark.asyncio
@pytest.mark.parametrize('response', [
    '<Packet><Command>getErrorResponse</Command><DatabaseManager><ERROR Code="0101" /></DatabaseManager></Packet>',
    RESPONSE,
])
async def test_expected_response_after_notifications_preserves_errors(response):
    async def handler(ws):
        await ws.recv()
        await ws.send(NOTIFICATION)
        await ws.send(response)

    async with serve(handler, '127.0.0.1', 0, subprotocols=['b_xmlproc']) as server:
        transport = WebsocketTransport('127.0.0.1', port=server.sockets[0].getsockname()[1])
        assert await transport.request(build_state_request([1])) == response


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', [
    'not XML', '<Packet><Command>setResponse</Command></Packet>', b'\xff',
    '<Packet><Command>getResponse</Command><Command>notifyRequest</Command></Packet>',
    '<!DOCTYPE Packet [<!ENTITY c "getResponse">]><Packet><Command>&c;</Command></Packet>',
])
async def test_malformed_or_wrong_command_is_protocol_failure(bad):
    async def handler(ws):
        await ws.recv()
        await ws.send(bad)

    async with serve(handler, '127.0.0.1', 0, subprotocols=['b_xmlproc']) as server:
        transport = WebsocketTransport('127.0.0.1', port=server.sockets[0].getsockname()[1])
        with pytest.raises(MelansProtocolError):
            await transport.request(build_state_request([1]))


@pytest.mark.asyncio
async def test_notifications_have_one_deadline():
    async def handler(ws):
        await ws.recv()
        try:
            for _ in range(15):
                await ws.send(NOTIFICATION)
                await asyncio.sleep(0.02)
        except ConnectionClosed:
            pass

    async with serve(handler, '127.0.0.1', 0, subprotocols=['b_xmlproc']) as server:
        transport = WebsocketTransport('127.0.0.1', port=server.sockets[0].getsockname()[1], timeout=0.1)
        started = time.monotonic()
        with pytest.raises(MelansConnectionError):
            await transport.request(build_state_request([1]))
        assert time.monotonic() - started < 0.25


@pytest.mark.asyncio
async def test_notification_frame_limit_is_bounded():
    async def handler(ws):
        await ws.recv()
        try:
            for _ in range(34):
                await ws.send(NOTIFICATION)
        except ConnectionClosed:
            pass

    async with serve(handler, '127.0.0.1', 0, subprotocols=['b_xmlproc']) as server:
        transport = WebsocketTransport('127.0.0.1', port=server.sockets[0].getsockname()[1])
        with pytest.raises(MelansProtocolError, match='too many notifications'):
            await transport.request(build_state_request([1]))


@pytest.mark.asyncio
@pytest.mark.parametrize('rejection', ['oversize', 1002, 1007, 1009])
async def test_rejected_websocket_frames_never_retry_a_write_over_http(rejection):
    received = []

    async def handler(ws):
        received.append(await ws.recv())
        if rejection == 'oversize':
            await ws.send('x' * (1024 * 1024 + 1))
        else:
            await ws.close(code=rejection)

    async with serve(handler, '127.0.0.1', 0, subprotocols=['b_xmlproc']) as server:
        async with MelansClient('127.0.0.1', port=server.sockets[0].getsockname()[1]) as client:
            client._http.request = AsyncMock()
            request = build_set_request(1, {'Drive':'OFF'})
            with pytest.raises(MelansProtocolError):
                await client._request(request)
            client._http.request.assert_not_called()
            assert received == [request]


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
