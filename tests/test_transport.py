"""Exercise actual transport code against loopback-only fixture servers."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest
import websockets
from aiohttp import web
from pymelans.exceptions import (
    MelansCommandUncertainError,
    MelansConnectionError,
    MelansError,
    MelansProtocolError,
)
from pymelans.protocol import build_set_request, build_state_request
from pymelans.transport import HttpTransport, WebsocketTransport
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed, ConnectionClosedError, ConnectionClosedOK
from websockets.frames import Close

from pymelans import MelansClient, const

RESPONSE = '<Packet><Command>getResponse</Command><DatabaseManager><Mnet Group="1" Drive="OFF" /></DatabaseManager></Packet>'
NOTIFICATION = '<Packet><Command>notifyRequest</Command><DatabaseManager><Mnet Address="1" FanStatus="ON" /></DatabaseManager></Packet>'
ACKNOWLEDGEMENT = '<Packet><Command>setResponse</Command><DatabaseManager /></Packet>'


@pytest.fixture
def memory_socket(monkeypatch):
    """Install an in-memory websocket so write-boundary tests use no network."""
    monkeypatch.setattr(const, "MIN_REQUEST_SPACING", 0)

    def install(*, open_error=None, send_error=None, receive_error=None, response=RESPONSE):
        socket = SimpleNamespace(
            send=AsyncMock(side_effect=send_error),
            recv=AsyncMock(side_effect=receive_error, return_value=response),
        )

        class Connection:
            async def __aenter__(self):
                if open_error is not None:
                    raise open_error
                return socket

            async def __aexit__(self, *_args):
                return False

        monkeypatch.setattr(websockets, "connect", lambda *_args, **_kwargs: Connection())
        return socket

    return install


def test_uncertain_command_is_not_a_retryable_connection_error():
    assert issubclass(MelansCommandUncertainError, MelansError)
    assert not issubclass(MelansCommandUncertainError, MelansConnectionError)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    TimeoutError("response deadline"),
    OSError("connection lost"),
    ConnectionClosedOK(Close(1000, ""), Close(1000, ""), True),
    ConnectionClosedOK(Close(1001, ""), Close(1001, ""), True),
    ConnectionClosedError(None, None, None),
])
async def test_memory_write_after_send_is_uncertain_and_never_falls_back(memory_socket, error):
    socket = memory_socket(receive_error=error)
    async with MelansClient("fixture.invalid") as client:
        client._http.request = AsyncMock(return_value=ACKNOWLEDGEMENT)
        payload = build_set_request(1, {"Drive": "OFF"})
        with pytest.raises(MelansCommandUncertainError):
            await client._request(payload)
        socket.send.assert_awaited_once_with(payload)
        client._http.request.assert_not_awaited()


@pytest.mark.asyncio
async def test_memory_write_marks_send_attempt_before_a_failed_send(memory_socket):
    socket = memory_socket(send_error=OSError("send may have partially completed"))
    async with MelansClient("fixture.invalid") as client:
        client._http.request = AsyncMock(return_value=ACKNOWLEDGEMENT)
        payload = build_set_request(1, {"Mode": "FAN", "Drive": "ON"})
        with pytest.raises(MelansCommandUncertainError):
            await client._request(payload)
        socket.send.assert_awaited_once_with(payload)
        socket.recv.assert_not_awaited()
        client._http.request.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [OSError("connect refused"), TimeoutError("handshake timeout")])
async def test_memory_write_before_send_preserves_http_compatibility(memory_socket, error):
    socket = memory_socket(open_error=error)
    async with MelansClient("fixture.invalid") as client:
        client._http.request = AsyncMock(return_value=ACKNOWLEDGEMENT)
        payload = build_set_request(1, {"Drive": "OFF"})
        assert await client._request(payload) == ACKNOWLEDGEMENT
        socket.send.assert_not_awaited()
        client._http.request.assert_awaited_once_with(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    TimeoutError("response deadline"), OSError("connection lost"),
    ConnectionClosedOK(Close(1000, ""), Close(1000, ""), True),
    ConnectionClosedError(None, None, None),
])
async def test_memory_read_after_send_still_falls_back_to_http(memory_socket, error):
    socket = memory_socket(receive_error=error)
    async with MelansClient("fixture.invalid") as client:
        client._http.request = AsyncMock(return_value=RESPONSE)
        payload = build_state_request([1])
        assert await client._request(payload) == RESPONSE
        socket.send.assert_awaited_once_with(payload)
        client._http.request.assert_awaited_once_with(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["open", "send", "receive"])
async def test_memory_cancellation_is_not_an_uncertain_error_or_a_retry(memory_socket, phase):
    arguments = {f"{phase}_error": asyncio.CancelledError()}
    socket = memory_socket(**arguments)
    async with MelansClient("fixture.invalid") as client:
        client._http.request = AsyncMock(return_value=ACKNOWLEDGEMENT)
        with pytest.raises(asyncio.CancelledError):
            await client._request(build_set_request(1, {"Drive": "OFF"}))
        assert socket.send.await_count == (0 if phase == "open" else 1)
        client._http.request.assert_not_awaited()


@pytest.mark.asyncio
async def test_memory_real_response_deadline_after_send_is_uncertain(memory_socket):
    socket = memory_socket()

    async def wait_without_response():
        await asyncio.Event().wait()

    socket.recv.side_effect = wait_without_response
    async with MelansClient("fixture.invalid") as client:
        client._ws._timeout = 0.01
        client._http.request = AsyncMock(return_value=ACKNOWLEDGEMENT)
        with pytest.raises(MelansCommandUncertainError):
            await client._request(build_set_request(1, {"Drive": "OFF"}))
        socket.send.assert_awaited_once()
        client._http.request.assert_not_awaited()


@pytest.mark.asyncio
async def test_memory_acknowledged_write_returns_the_response_without_retry(memory_socket):
    socket = memory_socket(response=ACKNOWLEDGEMENT)
    async with MelansClient("fixture.invalid") as client:
        client._http.request = AsyncMock()
        payload = build_set_request(1, {"Drive": "OFF"})
        assert await client._request(payload) == ACKNOWLEDGEMENT
        socket.send.assert_awaited_once_with(payload)
        client._http.request.assert_not_awaited()


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
