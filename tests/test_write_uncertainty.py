"""HTTP write failures and redirects never cause a second transmission."""

from __future__ import annotations

import asyncio
import time
import xml.etree.ElementTree as ET
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest
from pymelans.exceptions import (
    MelansCommandUncertainError,
    MelansConnectionError,
    MelansProtocolError,
)
from pymelans.protocol import build_set_request, build_state_request
from pymelans.transport import HttpTransport

from pymelans import MelansClient, MelansDevice, const

ACK = "<Packet><Command>setResponse</Command><DatabaseManager /></Packet>"
READ = ('<Packet><Command>getResponse</Command><DatabaseManager>'
        '<Mnet Group="1" Drive="OFF" /></DatabaseManager></Packet>')
RAW = {"RemoCon": "PERMIT", "DriveItem": "CHK_ON", "ModeItem": "CHK_ON", "SetTempItem": "CHK_ON", "FilterItem": "CHK_ON"}
EXPECTED = {"RemoCon": "PROHIBIT", "DriveItem": "CHK_OFF", "ModeItem": "CHK_OFF", "SetTempItem": "CHK_OFF", "FilterItem": "CHK_ON"}


class Response:
    """The async response context is entirely in memory."""

    def __init__(self, body=ACK, *, enter_error=None, text_error=None, status=200):
        self.body = body
        self.enter_error = enter_error
        self.text_error = text_error
        self.status = status
        self.text_calls = 0

    async def __aenter__(self):
        if self.enter_error is not None:
            raise self.enter_error
        return self

    async def __aexit__(self, *_args):
        return False

    def raise_for_status(self):
        if self.status >= 400:
            raise aiohttp.ClientResponseError(
                SimpleNamespace(real_url="http://fixture.invalid/servlet"), (), status=self.status,
            )

    async def text(self):
        self.text_calls += 1
        if self.text_error is not None:
            raise self.text_error
        return self.body


class Session:
    def __init__(self, response, *, post_error=None):
        self.response = response
        self.post_error = post_error
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.post_error is not None:
            raise self.post_error
        return self.response


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    TimeoutError("deadline"),
    aiohttp.ServerDisconnectedError("closed"),
    aiohttp.ClientPayloadError("truncated body"),
    aiohttp.ClientOSError(10054, "reset"),
])
@pytest.mark.parametrize("phase", ["enter", "text"])
async def test_http_write_timeout_or_client_error_after_post_begin_is_uncertain(error, phase):
    response = Response(**{f"{phase}_error": error})
    session = Session(response)
    transport = HttpTransport("fixture.invalid", session=session)
    payload = build_set_request("1", {"Drive": "OFF"})
    with pytest.raises(MelansCommandUncertainError):
        await transport.request(payload)
    assert len(session.calls) == 1
    assert session.calls[0][1]["data"] == payload.encode("utf-8")
    assert session.calls[0][1]["allow_redirects"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [TimeoutError(), aiohttp.ServerDisconnectedError(), aiohttp.ClientPayloadError()])
async def test_http_read_errors_keep_connection_error_semantics(error):
    session = Session(Response(text_error=error))
    with pytest.raises(MelansConnectionError) as raised:
        await HttpTransport("fixture.invalid", session=session).request(build_state_request(["1"]))
    assert not isinstance(raised.value, MelansCommandUncertainError)
    assert len(session.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    aiohttp.ClientConnectorError(SimpleNamespace(host="fixture.invalid", port=80, ssl=False), OSError(10061, "refused")),
    aiohttp.InvalidURL("http://fixture.invalid/invalid"),
])
@pytest.mark.parametrize("phase", ["post", "enter"])
async def test_known_pre_send_connector_or_invalid_url_errors_remain_connection_errors(error, phase):
    session = Session(Response(enter_error=error if phase == "enter" else None), post_error=error if phase == "post" else None)
    with pytest.raises(MelansConnectionError) as raised:
        await HttpTransport("fixture.invalid", session=session).request(build_set_request("1", {"Drive": "OFF"}))
    assert not isinstance(raised.value, MelansCommandUncertainError)
    assert len(session.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["enter", "text"])
async def test_http_cancellation_propagates_without_retry_or_conversion(phase):
    session = Session(Response(**{f"{phase}_error": asyncio.CancelledError()}))
    with pytest.raises(asyncio.CancelledError):
        await HttpTransport("fixture.invalid", session=session).request(build_set_request("1", {"Drive": "OFF"}))
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_http_invalid_response_encoding_becomes_a_bounded_protocol_failure():
    session = Session(Response(text_error=UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid byte")))
    with pytest.raises(MelansProtocolError):
        await HttpTransport("fixture.invalid", session=session).request(build_state_request(["1"]))
    assert len(session.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [307, 308])
@pytest.mark.parametrize("write", [False, True])
async def test_http_never_follows_redirect_for_reads_or_writes(status, write):
    class RedirectSession(Session):
        def __init__(self):
            super().__init__(Response("redirect body", status=status))
            self.destination_requests = 0

        def post(self, url, **kwargs):
            result = super().post(url, **kwargs)
            if kwargs.get("allow_redirects", True):
                self.destination_requests += 1
                return Response(ACK)
            return result

    session = RedirectSession()
    payload = build_set_request("1", {"Drive": "OFF"}) if write else build_state_request(["1"])
    assert await HttpTransport("fixture.invalid", session=session).request(payload) == "redirect body"
    assert len(session.calls) == 1 and session.destination_requests == 0
    assert session.calls[0][1]["allow_redirects"] is False


class TransactionSession:
    """Simulate HTTP-only guarded transactions, counting every POST by command."""

    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []
        self.raw = dict(RAW)
        self.destination_requests = 0

    def post(self, url, **kwargs):
        root = ET.fromstring(kwargs["data"])
        command = root.findtext("./Command")
        node = root.find("./DatabaseManager")[0]
        self.calls.append((url, command, node.tag, dict(node.attrib), kwargs["allow_redirects"]))
        if command == "setRequest":
            self.raw = {key: value for key, value in node.attrib.items() if key != "Group"}
            if self.outcome == "timeout":
                return Response(text_error=TimeoutError("ACK lost"))
            if self.outcome == "invalid-encoding":
                return Response(text_error=UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid byte"))
            if self.outcome in (307, 308):
                if kwargs["allow_redirects"]:
                    self.destination_requests += 1
                    return Response(ACK)
                return Response("<html>redirect</html>", status=self.outcome)
            raise AssertionError("unhandled fixture write outcome")
        attributes = {"AllTurnOff": "OFF"} if node.tag == "SystemData" else (
            {"Group": "1", "TurnOff": "OFF", "BACnetTurnOff": "OFF"} if "TurnOff" in node.attrib
            else {"Group": "1", **self.raw}
        )
        packet = ET.Element("Packet")
        ET.SubElement(packet, "Command").text = "getResponse"
        ET.SubElement(ET.SubElement(packet, "DatabaseManager"), node.tag, attributes)
        return Response(ET.tostring(packet, encoding="unicode"))


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["timeout", "invalid-encoding", 307, 308])
async def test_http_only_transaction_performs_one_set_and_one_bounded_observation_after_uncertainty(monkeypatch, outcome):
    monkeypatch.setattr(const, "MIN_REQUEST_SPACING", 0)
    monkeypatch.setattr(const, "MIN_WRITE_SPACING", 0)
    session = TransactionSession(outcome)
    c = MelansClient("fixture.invalid", prefer_websocket=False, session=session, write_spacing=0)
    c._ws.request = AsyncMock(side_effect=AssertionError("HTTP-only client used websocket"))
    c._names = {"1": None}
    c.devices["1"] = MelansDevice("1", None, {"Drive": "OFF", **RAW}, {"Model": "IC", "SubModel": "", "IcKind": "NORMAL", "GroupType": "OLD"})
    c._group_seen["1"] = time.monotonic()
    result = await c.async_set_local_restrictions("1", {"filter_reset": True})
    assert result["status"] == "outcome_unknown" and result["readback_matches"] is True
    assert result["after_raw"] == EXPECTED
    assert result["write_error"] == {"type": "MelansCommandUncertainError", "code": None}
    assert [call[1] for call in session.calls] == ["getRequest", "getRequest", "getRequest", "setRequest", "getRequest"]
    assert session.calls[-1][3] == {"Group": "1", **dict.fromkeys(RAW, "*")}
    assert all(call[0] == c._http._url and call[4] is False for call in session.calls)
    assert session.destination_requests == 0
    assert c.last_command["status"] == "outcome_unknown" and not c._pending_writes
    c._ws.request.assert_not_awaited()
