"""Which failures reconnect, which surface, and what `disconnected` reports.

`BfxWebSocketClient`'s connection loop is the most consequential code in the
library and was its least covered (51% before this file). Every branch here
decides whether a client that just lost its socket comes back, and each one
fails silently in a different direction: a missed reconnect looks like a quiet
client, and a swallowed error looks like a healthy one.

The loop is driven by monkeypatching `__connect` with a scripted sequence of
outcomes, so a test says exactly what the socket did and asserts only what the
client did about it. `_StopTheLoop` ends an otherwise-infinite retry.
"""

from socket import gaierror
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from websockets.exceptions import ConnectionClosedError, InvalidStatus
from websockets.frames import Close

from bfxapi.websocket._client import bfx_websocket_client as mod


class _StopTheLoop(Exception):
    """Ends the retry loop so a test cannot hang if a branch misbehaves."""


def _invalid_status(code: int) -> InvalidStatus:
    return InvalidStatus(SimpleNamespace(status_code=code))


def _drive(monkeypatch, outcomes, *, timeout=None, websocket=None):
    """Return (client, emitted) after running start() through `outcomes`.

    `outcomes` is one entry per `__connect` call: an exception to raise, or
    `None` to return normally (a clean close).
    """
    client = mod.BfxWebSocketClient("wss://example.invalid", timeout=timeout)

    remaining = list(outcomes)

    async def _connect(_self=None):
        if not remaining:
            raise _StopTheLoop
        outcome = remaining.pop(0)
        if outcome is not None:
            raise outcome
        client._websocket = websocket

    monkeypatch.setattr(
        client, "_BfxWebSocketClient__connect", _connect, raising=False
    )
    monkeypatch.setattr(mod._Delay, "next", lambda self: 0.0)
    monkeypatch.setattr(mod._Delay, "peek", lambda self: 0.0)

    class _Loop:
        def call_later(self, delay, callback):
            return MagicMock()

    monkeypatch.setattr(mod.asyncio, "get_event_loop", lambda: _Loop())

    emitted = []
    emitter = client._BfxWebSocketClient__event_emitter
    monkeypatch.setattr(
        emitter,
        "emit",
        lambda event, *a, **k: emitted.append((event, a)),
    )

    return client, emitted


def _closed(code: int, reason: str = "x") -> ConnectionClosedError:
    return ConnectionClosedError(Close(code, reason), None)


class TestCloseCodesThatReconnect:
    """1006 and 1012 are the two the exchange actually produces.

    1012 is the scheduled one: Bitfinex announces a WSS restart with 20051 and
    then closes. Dropping it from this tuple would leave every client offline
    across a routine server restart, which is the worst time to be offline and
    the least likely to be noticed in a test.
    """

    @pytest.mark.parametrize("code", [1006, 1012])
    @pytest.mark.asyncio
    async def test_reconnects(self, monkeypatch, code):
        client, _ = _drive(monkeypatch, [_closed(code), _StopTheLoop()])

        with pytest.raises(_StopTheLoop):
            await client.start()

        # Reaching the second __connect at all means the loop retried rather
        # than emitting `disconnected` and breaking.

    @pytest.mark.asyncio
    async def test_a_reconnecting_close_does_not_report_disconnected(
        self, monkeypatch
    ):
        """`disconnected` means "gave up", so it must not fire mid-recovery."""
        client, emitted = _drive(monkeypatch, [_closed(1006), _StopTheLoop()])

        with pytest.raises(_StopTheLoop):
            await client.start()

        assert [e for e, _ in emitted if e == "disconnected"] == []


class TestCloseCodesThatDoNot:
    @pytest.mark.parametrize("code", [1000, 1001, 1011, 4000])
    @pytest.mark.asyncio
    async def test_an_unrecognised_close_surfaces(self, monkeypatch, code):
        """Anything outside (1006, 1012) hits `else: raise`.

        Silently reconnecting on an unknown close would turn a protocol fault
        into an endless loop against a server that is refusing us on purpose.
        """
        error = _closed(code)
        client, _ = _drive(monkeypatch, [error])

        with pytest.raises(ConnectionClosedError) as caught:
            await client.start()

        assert caught.value is error


class TestDisconnectedReporting:
    """A clean exit from __connect is the only path that reports `disconnected`."""

    @pytest.mark.asyncio
    async def test_reports_the_close_code_and_reason(self, monkeypatch):
        socket = SimpleNamespace(close_code=1000, close_reason="bye")
        client, emitted = _drive(monkeypatch, [None], websocket=socket)

        await client.start()

        assert ("disconnected", (1000, "bye")) in emitted

    @pytest.mark.asyncio
    async def test_a_socket_with_neither_reports_the_documented_defaults(
        self, monkeypatch
    ):
        """`None` code and `""` reason, never a raw `None` reason."""
        socket = SimpleNamespace(close_code=None, close_reason=None)
        client, emitted = _drive(monkeypatch, [None], websocket=socket)

        await client.start()

        assert ("disconnected", (None, "")) in emitted


class TestRetryErrorsDuringRecovery:
    """408 and DNS failure are expected WHILE reconnecting, and only then."""

    @pytest.mark.parametrize(
        "error",
        [
            pytest.param(_invalid_status(408), id="HTTP 408"),
            pytest.param(gaierror("name resolution failed"), id="DNS failure"),
        ],
    )
    @pytest.mark.asyncio
    async def test_is_retried_while_reconnecting(self, monkeypatch, error):
        client, _ = _drive(monkeypatch, [_closed(1006), error, _StopTheLoop()])

        with pytest.raises(_StopTheLoop):
            await client.start()

    @pytest.mark.asyncio
    async def test_attempts_are_counted(self, monkeypatch):
        """The counter drives the backoff and the operator-facing log."""
        client, _ = _drive(
            monkeypatch,
            [
                _closed(1006),
                _invalid_status(408),
                _invalid_status(408),
                _StopTheLoop(),
            ],
        )

        with pytest.raises(_StopTheLoop):
            await client.start()

        assert client._BfxWebSocketClient__reconnection["attempts"] == 3

    @pytest.mark.parametrize(
        "error",
        [
            pytest.param(_invalid_status(408), id="HTTP 408"),
            pytest.param(gaierror("name resolution failed"), id="DNS failure"),
        ],
    )
    @pytest.mark.asyncio
    async def test_surfaces_when_not_reconnecting(self, monkeypatch, error):
        """Same errors, no recovery in flight: these are a failed FIRST connect.

        Retrying here would hide a bad host or a down network behind a loop
        that never reports anything.
        """
        client, _ = _drive(monkeypatch, [error])

        with pytest.raises(type(error)) as caught:
            await client.start()

        assert caught.value is error

    @pytest.mark.asyncio
    async def test_a_non_408_status_surfaces_even_while_reconnecting(
        self, monkeypatch
    ):
        """Only 408 means "try again"; 503 is the server saying stop."""
        error = _invalid_status(503)
        client, _ = _drive(monkeypatch, [_closed(1006), error])

        with pytest.raises(InvalidStatus) as caught:
            await client.start()

        assert caught.value is error
