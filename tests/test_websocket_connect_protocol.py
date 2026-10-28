"""What `__connect` does with the frames the exchange actually sends.

The connection loop (tests/test_websocket_close_codes.py) decides what to do
about a socket that died. This file covers the half that decides *when* one
should die, and it is where the two halves meet: Bitfinex announces a restart
with an `info` frame carrying code 20051, and `__connect` turns that frame into
the 1012 close the loop reconnects on. Neither half is meaningful alone --
without the translation the loop never sees 1012, and 1012 handling would be
dead code.

`__connect` is driven directly rather than through `start()`, so a raised close
is observable instead of being swallowed by the retry it is supposed to cause.
"""

import json
from typing import Any

import pytest
from websockets.exceptions import ConnectionClosedError

from bfxapi.exceptions import InvalidCredentialError
from bfxapi.websocket._client import bfx_websocket_client as mod
from bfxapi.websocket.exceptions import VersionMismatchError


class _FakeSocket:
    """An async-iterable socket that replays scripted frames, then closes."""

    def __init__(self, frames: list[Any]) -> None:
        self._frames = [
            f if isinstance(f, str) else json.dumps(f) for f in frames
        ]
        self.sent: list[str] = []
        self.close_code: int | None = None
        self.close_reason: str | None = None

    async def send(self, message: str) -> None:
        self.sent.append(message)

    def __aiter__(self) -> "_FakeSocket":
        self._iter = iter(self._frames)
        return self

    async def __anext__(self) -> str:
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration from None


class _FakeConnect:
    def __init__(self, socket: _FakeSocket) -> None:
        self._socket = socket

    def __call__(self, _host: str) -> "_FakeConnect":
        return self

    async def __aenter__(self) -> _FakeSocket:
        return self._socket

    async def __aexit__(self, *_exc: object) -> bool:
        return False


def _client(monkeypatch, frames, **kwargs):
    socket = _FakeSocket(frames)
    monkeypatch.setattr(
        mod.websockets.asyncio.client, "connect", _FakeConnect(socket)
    )
    client = mod.BfxWebSocketClient("wss://example.invalid", **kwargs)
    return client, socket


async def _connect(client) -> None:
    await client._BfxWebSocketClient__connect()


CREDS = {"credentials": {"api_key": "k", "api_secret": "s", "filters": None}}


class TestServerRestart:
    """`info` 20051 is the exchange saying "reconnect", in band."""

    @pytest.mark.asyncio
    async def test_20051_becomes_a_1012_close(self, monkeypatch):
        client, _ = _client(monkeypatch, [{"event": "info", "code": 20051}])

        with pytest.raises(ConnectionClosedError) as caught:
            await _connect(client)

        assert caught.value.rcvd.code == 1012, (
            "a restart notice that is not translated to 1012 leaves the client "
            "offline across every scheduled Bitfinex restart"
        )

    @pytest.mark.asyncio
    async def test_other_info_codes_are_ignored(self, monkeypatch):
        """Only 20051 means restart; the rest are routine notices."""
        client, _ = _client(
            monkeypatch,
            [
                {"event": "info", "code": 20060},
                {"event": "info", "code": 20061},
            ],
        )

        await _connect(client)


class TestVersionGuard:
    @pytest.mark.asyncio
    async def test_a_mismatched_version_refuses_to_run(self, monkeypatch):
        client, _ = _client(monkeypatch, [{"event": "info", "version": 3}])

        with pytest.raises(VersionMismatchError):
            await _connect(client)

    @pytest.mark.asyncio
    async def test_version_2_proceeds(self, monkeypatch):
        client, _ = _client(monkeypatch, [{"event": "info", "version": 2}])

        await _connect(client)


class TestAuthentication:
    @pytest.mark.asyncio
    async def test_credentials_are_sent_on_open(self, monkeypatch):
        client, socket = _client(monkeypatch, [], **CREDS)

        await _connect(client)

        assert len(socket.sent) == 1
        assert json.loads(socket.sent[0])["event"] == "auth"

    @pytest.mark.asyncio
    async def test_no_credentials_sends_nothing(self, monkeypatch):
        client, socket = _client(monkeypatch, [])

        await _connect(client)

        assert socket.sent == []

    @pytest.mark.asyncio
    async def test_a_rejected_auth_raises(self, monkeypatch):
        client, _ = _client(
            monkeypatch,
            [{"event": "auth", "status": "FAILED"}],
            **CREDS,
        )

        with pytest.raises(InvalidCredentialError):
            await _connect(client)

    @pytest.mark.asyncio
    async def test_a_successful_auth_is_announced_and_recorded(
        self, monkeypatch
    ):
        client, _ = _client(
            monkeypatch,
            [{"event": "auth", "status": "OK"}],
            **CREDS,
        )
        emitted = []
        monkeypatch.setattr(
            client._BfxWebSocketClient__event_emitter,
            "emit",
            lambda event, *a, **k: emitted.append(event),
        )

        await _connect(client)

        assert "authenticated" in emitted
        assert client._authentication is True


class TestConnectionScopeIsReArmed:
    @pytest.mark.asyncio
    async def test_every_new_socket_resets_the_scope(self, monkeypatch):
        """Without this a recovered client never re-announces open/auth/snapshots.

        The emitter deduplicates once-per-connection events against a ledger
        that is only appended to, so the scope is once per *client* unless the
        ledger is cleared for each socket.
        """
        client, _ = _client(monkeypatch, [])
        calls = []
        monkeypatch.setattr(
            client._BfxWebSocketClient__event_emitter,
            "reset_connection_scope",
            lambda: calls.append(1),
        )

        await _connect(client)

        assert calls == [1]


class TestChannelZeroDispatch:
    @pytest.mark.asyncio
    async def test_a_heartbeat_is_not_dispatched(self, monkeypatch):
        client, _ = _client(monkeypatch, [[0, "hb"]])
        handled = []
        monkeypatch.setattr(
            client._BfxWebSocketClient__handler,
            "handle",
            lambda *a: handled.append(a),
        )

        await _connect(client)

        assert handled == []

    @pytest.mark.asyncio
    async def test_a_real_frame_is_dispatched(self, monkeypatch):
        client, _ = _client(monkeypatch, [[0, "wu", ["exchange", "USD"]]])
        handled = []
        monkeypatch.setattr(
            client._BfxWebSocketClient__handler,
            "handle",
            lambda *a: handled.append(a),
        )

        await _connect(client)

        assert handled == [("wu", ["exchange", "USD"])]


class TestOpenIsAnnounced:
    @pytest.mark.asyncio
    async def test_a_bucketless_client_still_reports_open(self, monkeypatch):
        """`len(buckets) == 0` short-circuits the gather; `open` must still fire."""
        client, _ = _client(monkeypatch, [])
        emitted = []
        monkeypatch.setattr(
            client._BfxWebSocketClient__event_emitter,
            "emit",
            lambda event, *a, **k: emitted.append(event),
        )

        await _connect(client)

        assert "open" in emitted
