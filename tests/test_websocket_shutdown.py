"""Shutdown, bucket starvation and recovery, after the #42 review.

Each class pins a sequence the review found where the client either ignored
its owner or quietly lost something:

* `close()` during an outage raised `ConnectionNotOpen` and `start()`
  reconnected anyway; `close()` with a bucket supervisor asleep resurrected
  the bucket the moment the supervisor woke.
* A bucket that never came back held the main socket's `open`, its auth
  message and its read loop hostage, with no deadline.
* A bucket closed by the server mid-recovery dropped every subscription it
  had popped but not yet re-sent.
"""

import asyncio
import json
from functools import partial
from types import SimpleNamespace

import pytest
from websockets.exceptions import ConnectionClosedError
from websockets.frames import Close

from bfxapi.websocket._client import bfx_websocket_client as mod
from bfxapi.websocket._client.bfx_websocket_bucket import BfxWebSocketBucket
from bfxapi.websocket._client.bfx_websocket_client import (
    BfxWebSocketClient,
    _Delay,
)

from .conftest import FakeWebSocket, settle
from .test_websocket_close_codes import _closed, _drive, _invalid_status


def _client(**kwargs):
    client = BfxWebSocketClient("wss://example.invalid", **kwargs)
    captured: list = []
    for event in (
        "open",
        "authenticated",
        "subscribed",
        "disconnected",
        "error",
    ):
        client.on(event, partial(lambda e, *a: captured.append((e, *a)), event))
    return client, captured


def _events(captured, name):
    return [args for event, *args in captured if event == name]


def _subscribes(websocket: FakeWebSocket) -> list[str]:
    return [
        frame["subId"]
        for frame in websocket.events
        if frame.get("event") == "subscribe"
    ]


class _ClosingConnect:
    """`connect()` the way websockets does it: leaving the block closes."""

    def __init__(self, websocket: FakeWebSocket) -> None:
        self._websocket = websocket

    async def __aenter__(self) -> FakeWebSocket:
        return self._websocket

    async def __aexit__(self, *_: object) -> bool:
        self._websocket.state = SimpleNamespace(name="CLOSED")
        return False


class _RefusedConnect:
    """A handshake the server answers with a status it means."""

    def __init__(self, error: BaseException) -> None:
        self._error = error

    async def __aenter__(self) -> FakeWebSocket:
        raise self._error

    async def __aexit__(self, *_: object) -> bool:  # pragma: no cover
        return False


def _supervisors(client) -> list:
    return list(client._BfxWebSocketClient__buckets.values())


class TestCloseStopsAnOutage:
    async def test_close_while_reconnecting_ends_start(self, monkeypatch):
        """Sequence A: the socket drops, then the owner closes."""
        client, emitted = _drive(monkeypatch, [_closed(1006)])
        # A long backoff: start() is asleep when close() arrives.
        monkeypatch.setattr(_Delay, "next", lambda self: 30.0)
        attempts = []
        connect = client._BfxWebSocketClient__connect

        async def _counting():
            attempts.append(1)
            await connect()

        monkeypatch.setattr(
            client, "_BfxWebSocketClient__connect", _counting, raising=False
        )

        running = asyncio.create_task(client.start())
        await settle(lambda: client._BfxWebSocketClient__reconnection)

        await client.close(1000, "bye")
        await asyncio.wait_for(running, 1)

        assert attempts == [1], "start() reconnected a client its owner closed"
        assert ("disconnected", (1000, "bye")) in emitted

    async def test_close_with_a_bucket_supervisor_asleep(
        self, monkeypatch, connect_each
    ):
        """Sequence B: close() must not raise, and the bucket stays down."""
        monkeypatch.setattr(_Delay, "next", lambda self: 0.0)
        sockets = connect_each()
        client, captured = _client()
        running = asyncio.create_task(client.start())
        await settle(lambda: client.open)
        await client.subscribe("ticker", sub_id="t1", symbol="tBTCUSD")
        await settle(lambda: _events(captured, "subscribed"))

        # The bucket drops; its supervisor sleeps before restarting it.
        monkeypatch.setattr(_Delay, "next", lambda self: 30.0)
        sockets[1].fail(ConnectionClosedError(None, None))
        await asyncio.sleep(0.02)

        await client.close()
        await asyncio.wait_for(running, 1)

        assert len(sockets) == 2, "a closed client restarted a bucket"
        assert _supervisors(client) == [None]
        assert len(_events(captured, "disconnected")) == 1

    async def test_a_closing_bucket_never_reconnects(self, connect_each):
        sockets = connect_each()
        bucket = BfxWebSocketBucket("wss://example.invalid", None)  # type: ignore[arg-type]
        await bucket.close()

        await bucket.start()

        assert sockets == []
        assert bucket._closing is True

    async def test_close_before_the_handshake_completes(self, monkeypatch):
        """close() during connect() hangs up the socket it was waiting for."""
        gate = asyncio.Event()
        websocket = FakeWebSocket(hold=True)

        class _Slow:
            async def __aenter__(self):
                await gate.wait()
                return websocket

            async def __aexit__(self, *_):
                return False

        monkeypatch.setattr(
            "websockets.asyncio.client.connect", lambda *_a, **_k: _Slow()
        )
        client, captured = _client()
        running = asyncio.create_task(client.start())
        await asyncio.sleep(0.01)

        await client.close(1000, "bye")
        gate.set()
        await asyncio.wait_for(running, 1)

        assert websocket.closed == (1000, "bye")
        assert _events(captured, "open") == []
        assert _events(captured, "disconnected") == [[1000, "bye"]]

    async def test_close_that_ends_without_a_close_frame_is_clean(
        self, monkeypatch
    ):
        client, emitted = _drive(monkeypatch, [])

        async def _connect(_self=None):
            client._websocket = FakeWebSocket()
            await client.close()
            raise ConnectionClosedError(None, None)

        monkeypatch.setattr(
            client, "_BfxWebSocketClient__connect", _connect, raising=False
        )

        await client.start()

        assert [e for e, _ in emitted if e == "disconnected"] == [
            "disconnected"
        ]

    async def test_close_while_a_reconnect_attempt_fails(self, monkeypatch):
        """The attempt in flight fails after close(): end, don't retry."""
        client, emitted = _drive(monkeypatch, [_closed(1006)])

        async def _connect(_self=None):
            await client.close()
            raise _invalid_status(503)

        async def _first(_self=None):
            monkeypatch.setattr(
                client, "_BfxWebSocketClient__connect", _connect, raising=False
            )
            raise _closed(1006)

        monkeypatch.setattr(
            client, "_BfxWebSocketClient__connect", _first, raising=False
        )

        await asyncio.wait_for(client.start(), 1)

        assert [e for e, _ in emitted if e == "disconnected"] == [
            "disconnected"
        ]


class TestNormalExitStopsBuckets:
    async def test_a_server_goodbye_stops_every_supervisor(
        self, monkeypatch, connect_each
    ):
        monkeypatch.setattr(_Delay, "next", lambda self: 0.0)
        sockets = connect_each()
        client, captured = _client()
        running = asyncio.create_task(client.start())
        await settle(lambda: client.open)
        await client.subscribe("ticker", sub_id="t1", symbol="tBTCUSD")
        await settle(lambda: _events(captured, "subscribed"))
        task = _supervisors(client)[0]

        await sockets[0].close(1000, "bye")
        await asyncio.wait_for(running, 1)

        assert _supervisors(client) == [None]
        assert task.done()


class TestAFailingBucketDoesNotStarveTheMainSocket:
    @pytest.mark.parametrize(
        "error",
        [
            pytest.param(_invalid_status(403), id="non-retryable 403"),
            pytest.param(RuntimeError("bucket bug"), id="recurring exception"),
        ],
    )
    async def test_open_auth_and_reads_go_ahead(self, monkeypatch, error):
        monkeypatch.setattr(_Delay, "next", lambda self: 0.01)
        monkeypatch.setattr(mod, "_BUCKET_READY_TIMEOUT", 0.05, raising=False)
        auth_ok = json.dumps({"event": "auth", "status": "OK", "userId": 1})
        main0 = FakeWebSocket(incoming=[auth_ok], hold=True)
        bucket0 = FakeWebSocket(confirms=True)
        main1 = FakeWebSocket(incoming=[auth_ok], hold=True)
        calls: list = []

        def _connect(*_a, **_k):
            calls.append(1)
            n = len(calls)
            if n == 1:
                return _ClosingConnect(main0)
            if n == 2:
                return _ClosingConnect(bucket0)
            if n == 3:
                return _ClosingConnect(main1)
            return _RefusedConnect(error)

        monkeypatch.setattr("websockets.asyncio.client.connect", _connect)
        client, captured = _client(
            credentials={"api_key": "k", "api_secret": "s", "filters": None}
        )
        running = asyncio.create_task(client.start())
        await settle(lambda: client.open)
        await client.subscribe("ticker", sub_id="t1", symbol="tBTCUSD")
        await settle(lambda: _events(captured, "subscribed"))

        main0.fail(ConnectionClosedError(None, None))

        await settle(lambda: len(_events(captured, "open")) == 2)
        await settle(lambda: len(_events(captured, "authenticated")) == 2)
        assert any(frame.get("event") == "auth" for frame in main1.events)

        await client.close()
        await asyncio.wait_for(running, 1)


class _ClosesMidRecovery(FakeWebSocket):
    """The server hangs up after the first frame of a recovery."""

    async def send(self, message: str) -> None:
        if self.state.name != "OPEN":
            raise ConnectionClosedError(Close(1012, "restart"), None)
        self.sent.append(message)
        if json.loads(message).get("event") == "subscribe":
            self.state = SimpleNamespace(name="CLOSED")
            self._outbox.put_nowait(
                ConnectionClosedError(Close(1012, "restart"), None)
            )


class TestRecoveryKeepsSubscriptions:
    async def test_a_close_mid_recovery_loses_nothing(
        self, monkeypatch, connect_each
    ):
        monkeypatch.setattr(_Delay, "next", lambda self: 0.0)
        made: list = []

        def _factory():
            made.append(1)
            if len(made) == 2:
                return _ClosesMidRecovery(hold=True)
            return FakeWebSocket(confirms=True)

        sockets = connect_each(_factory)
        client, captured = _client()
        client._websocket = FakeWebSocket()
        await client.subscribe("ticker", sub_id="t1", symbol="tBTCUSD")
        await client.subscribe("ticker", sub_id="t2", symbol="tETHUSD")
        await settle(lambda: len(_events(captured, "subscribed")) == 2)

        sockets[0].fail(ConnectionClosedError(None, None))

        await settle(
            lambda: len(sockets) >= 3 and len(_subscribes(sockets[2])) >= 2
        )
        assert sorted(_subscribes(sockets[2])) == ["t1", "t2"]
        await settle(lambda: len(_events(captured, "subscribed")) == 4)
        (bucket,) = list(client._BfxWebSocketClient__buckets)
        assert sorted(bucket.ids) == ["t1", "t2"]
        await client.close()


class TestCancelOnlySwallowsItsOwnCancellation:
    async def test_a_caller_cancelled_while_stopping_buckets_stays_cancelled(
        self,
    ):
        client = BfxWebSocketClient("wss://example.invalid")

        async def _stubborn():
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                await asyncio.sleep(0.05)
                raise

        bucket = BfxWebSocketBucket("wss://example.invalid", None)  # type: ignore[arg-type]
        client._BfxWebSocketClient__buckets[bucket] = asyncio.create_task(
            _stubborn()
        )
        await asyncio.sleep(0)

        caller = asyncio.create_task(client._BfxWebSocketClient__stop_buckets())
        await asyncio.sleep(0.01)
        caller.cancel()

        with pytest.raises(asyncio.CancelledError):
            await caller


class TestEdgesOfShutdown:
    async def test_close_while_buckets_come_back_ends_the_wait(
        self, monkeypatch
    ):
        """close() during the bounded bucket wait: no open, no 15 s hang."""
        monkeypatch.setattr(_Delay, "next", lambda self: 0.01)
        main0 = FakeWebSocket(hold=True)
        bucket0 = FakeWebSocket(confirms=True)
        main1 = FakeWebSocket(hold=True)
        calls: list = []

        def _connect(*_a, **_k):
            calls.append(1)
            if len(calls) <= 3:
                return _ClosingConnect([main0, bucket0, main1][len(calls) - 1])
            return _RefusedConnect(_invalid_status(403))

        monkeypatch.setattr("websockets.asyncio.client.connect", _connect)
        client, captured = _client()
        running = asyncio.create_task(client.start())
        await settle(lambda: client.open)
        await client.subscribe("ticker", sub_id="t1", symbol="tBTCUSD")
        await settle(lambda: _events(captured, "subscribed"))

        main0.fail(ConnectionClosedError(None, None))
        await settle(lambda: len(calls) >= 4)

        await client.close()
        await asyncio.wait_for(running, 1)

        assert len(_events(captured, "open")) == 1
        assert len(_events(captured, "disconnected")) == 1

    async def test_a_restart_after_close_brings_the_buckets_back(
        self, monkeypatch, connect_each
    ):
        monkeypatch.setattr(_Delay, "next", lambda self: 0.0)
        sockets = connect_each()
        client, captured = _client()
        running = asyncio.create_task(client.start())
        await settle(lambda: client.open)
        await client.subscribe("ticker", sub_id="t1", symbol="tBTCUSD")
        await settle(lambda: _events(captured, "subscribed"))
        await client.close()
        await asyncio.wait_for(running, 1)

        running = asyncio.create_task(client.start())
        await settle(lambda: len(sockets) == 4 and _subscribes(sockets[3]))

        assert _subscribes(sockets[3]) == ["t1"]
        await client.close()
        await asyncio.wait_for(running, 1)

    async def test_a_supervisor_that_wakes_to_a_closed_bucket_ends(
        self, monkeypatch, connect_each
    ):
        monkeypatch.setattr(_Delay, "next", lambda self: 0.05)
        sockets = connect_each()
        client, captured = _client()
        client._websocket = FakeWebSocket()
        await client.subscribe("ticker", sub_id="t1", symbol="tBTCUSD")
        await settle(lambda: _events(captured, "subscribed"))
        (bucket,) = list(client._BfxWebSocketClient__buckets)
        task = _supervisors(client)[0]

        sockets[0].fail(ConnectionClosedError(None, None))
        await asyncio.sleep(0.01)
        bucket._closing = True

        await settle(task.done)
        assert len(sockets) == 1
        await client.close()

    async def test_stopping_buckets_from_inside_a_supervisor(self):
        """close() called from a supervisor's own task must not await itself."""
        client = BfxWebSocketClient("wss://example.invalid")
        bucket = BfxWebSocketBucket("wss://example.invalid", None)  # type: ignore[arg-type]

        async def _from_inside():
            await client._BfxWebSocketClient__stop_buckets()

        task = asyncio.create_task(_from_inside())
        client._BfxWebSocketClient__buckets[bucket] = task

        await asyncio.wait_for(task, 1)

    async def test_a_bucket_closed_during_its_handshake_stays_down(
        self, monkeypatch
    ):
        gate = asyncio.Event()
        websocket = FakeWebSocket(confirms=True)

        class _Slow:
            async def __aenter__(self):
                await gate.wait()
                return websocket

            async def __aexit__(self, *_):
                return False

        monkeypatch.setattr(
            "websockets.asyncio.client.connect", lambda *_a, **_k: _Slow()
        )
        bucket = BfxWebSocketBucket("wss://example.invalid", None)  # type: ignore[arg-type]
        running = asyncio.create_task(bucket.start())
        await asyncio.sleep(0.01)

        await bucket.close()
        gate.set()
        await asyncio.wait_for(running, 1)

        assert websocket.sent == []
        assert bucket._connections == 0


class TestCloseInAnyState:
    async def test_a_client_that_never_opened_closes_quietly(self):
        client, captured = _client()

        await client.close()

        assert captured == []

    async def test_a_bucket_that_never_opened_closes_quietly(self):
        bucket = BfxWebSocketBucket("wss://example.invalid", None)  # type: ignore[arg-type]

        await bucket.close()

        assert bucket._closing is True
