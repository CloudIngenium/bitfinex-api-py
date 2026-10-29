"""Lifecycle follow-ups to #44: bounded bucket waits, lost closes, backoff.

Each class pins one sequence the #44 re-review left open:

* `subscribe()` that creates a bucket waited for it with no bound, and
  `close()` did not wake it.
* `subscribe()` took the first bucket that was not full, open or not.
* `create_task(client.start()); await client.close()` lost the close.
* A close that landed during the bucket wait reported 1000.
* A connection that flapped reset the reconnect backoff every time.
* A bucket that failed deterministically was restarted forever.
* An exception from a fire-and-forget `start()` vanished.
"""

import asyncio
import inspect
from functools import partial
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from websockets.exceptions import ConnectionClosedError

from bfxapi.exceptions import InvalidCredentialError
from bfxapi.websocket._client import bfx_websocket_client as mod
from bfxapi.websocket._client.bfx_websocket_bucket import BfxWebSocketBucket
from bfxapi.websocket._client.bfx_websocket_client import (
    BfxWebSocketClient,
    _Delay,
)
from bfxapi.websocket.exceptions import (
    BucketRestartLimitError,
    ConnectionNotOpen,
)

from .conftest import FakeConnect, FakeWebSocket, settle
from .test_websocket_close_codes import _invalid_status
from .test_websocket_shutdown import _ClosingConnect, _RefusedConnect


def _client(**kwargs):
    client = BfxWebSocketClient("wss://example.invalid", **kwargs)
    captured: list = []
    for event in ("open", "subscribed", "disconnected", "error"):
        client.on(event, partial(lambda e, *a: captured.append((e, *a)), event))
    return client, captured


def _events(captured, name):
    return [args for event, *args in captured if event == name]


def _buckets(client) -> dict:
    return client._BfxWebSocketClient__buckets


def _subscribes(websocket: FakeWebSocket) -> list[str]:
    return [
        frame["subId"]
        for frame in websocket.events
        if frame.get("event") == "subscribe"
    ]


class _Gated:
    """A handshake that completes only when the test says so."""

    def __init__(self, websocket: FakeWebSocket, gate: asyncio.Event) -> None:
        self._websocket = websocket
        self._gate = gate

    async def __aenter__(self) -> FakeWebSocket:
        await self._gate.wait()
        return self._websocket

    async def __aexit__(self, *_: object) -> bool:
        return False


def _plan(monkeypatch, *steps):
    """Answer each `connect()` with the next step; the last one repeats."""
    calls: list = []

    def _connect(*_a, **_k):
        calls.append(1)
        step = steps[min(len(calls), len(steps)) - 1]
        return step() if callable(step) else step

    monkeypatch.setattr("websockets.asyncio.client.connect", _connect)
    return calls


def _down_bucket(client) -> BfxWebSocketBucket:
    bucket = BfxWebSocketBucket(
        "wss://example.invalid", client._BfxWebSocketClient__event_emitter
    )
    bucket._websocket = FakeWebSocket()
    bucket._websocket.state = SimpleNamespace(name="CLOSED")
    _buckets(client)[bucket] = None
    return bucket


def _up_bucket(client) -> BfxWebSocketBucket:
    bucket = BfxWebSocketBucket(
        "wss://example.invalid", client._BfxWebSocketClient__event_emitter
    )
    bucket._websocket = FakeWebSocket()
    _buckets(client)[bucket] = None
    return bucket


class TestANewBucketWaitIsBounded:
    async def test_a_bucket_that_never_opens_times_out(self, monkeypatch):
        monkeypatch.setattr(_Delay, "next", lambda self: 0.01)
        monkeypatch.setattr(mod, "_BUCKET_READY_TIMEOUT", 0.05)
        _plan(monkeypatch, lambda: _RefusedConnect(OSError("refused")))
        client, _ = _client()
        client._websocket = FakeWebSocket()

        with pytest.raises(ConnectionNotOpen):
            await asyncio.wait_for(client.subscribe("ticker", sub_id="t1"), 1)

        # The empty bucket is torn down, not left retrying forever.
        assert _buckets(client) == {}

    async def test_close_wakes_a_subscribe_waiting_for_its_bucket(
        self, monkeypatch
    ):
        gate = asyncio.Event()
        _plan(monkeypatch, lambda: _Gated(FakeWebSocket(confirms=True), gate))
        client, _ = _client()
        client._websocket = FakeWebSocket()

        pending = asyncio.create_task(client.subscribe("ticker", sub_id="t1"))
        await asyncio.sleep(0.01)
        await client.close()

        with pytest.raises(ConnectionNotOpen):
            await asyncio.wait_for(pending, 1)
        assert all(task is None for task in _buckets(client).values())

    async def test_the_happy_path_still_subscribes(self, monkeypatch):
        sockets: list = []

        def _make():
            sockets.append(FakeWebSocket(confirms=True))
            return FakeConnect(sockets[-1])

        _plan(monkeypatch, _make)
        client, captured = _client()
        client._websocket = FakeWebSocket()

        await asyncio.wait_for(client.subscribe("ticker", sub_id="t1"), 1)
        await settle(lambda: _events(captured, "subscribed"))

        assert _subscribes(sockets[0]) == ["t1"]
        await client.close()


class TestSubscribePrefersOpenBuckets:
    async def test_an_open_bucket_wins_over_a_down_one(self):
        client, _ = _client()
        client._websocket = FakeWebSocket()
        _down_bucket(client)
        up = _up_bucket(client)

        await client.subscribe("ticker", sub_id="t1")

        assert _subscribes(up._websocket) == ["t1"]

    async def test_waits_for_a_bucket_that_is_coming_back(self, monkeypatch):
        monkeypatch.setattr(_Delay, "next", lambda self: 0.0)
        gate = asyncio.Event()
        bucket0 = FakeWebSocket(confirms=True)
        bucket1 = FakeWebSocket(confirms=True)
        calls = _plan(monkeypatch, FakeConnect(bucket0), _Gated(bucket1, gate))
        client, captured = _client()
        client._websocket = FakeWebSocket()
        await client.subscribe("ticker", sub_id="t1")
        await settle(lambda: _events(captured, "subscribed"))

        bucket0.fail(ConnectionClosedError(None, None))
        await settle(lambda: len(calls) == 2)

        pending = asyncio.create_task(client.subscribe("ticker", sub_id="t2"))
        await asyncio.sleep(0.01)
        assert not pending.done()
        gate.set()
        await asyncio.wait_for(pending, 1)

        assert "t2" in _subscribes(bucket1)
        assert len(_buckets(client)) == 1
        await client.close()

    async def test_a_bucket_that_stays_down_raises_after_the_bound(
        self, monkeypatch
    ):
        monkeypatch.setattr(_Delay, "next", lambda self: 0.0)
        monkeypatch.setattr(mod, "_BUCKET_READY_TIMEOUT", 0.05)
        bucket0 = FakeWebSocket(confirms=True)
        calls = _plan(
            monkeypatch,
            FakeConnect(bucket0),
            _Gated(FakeWebSocket(), asyncio.Event()),
        )
        client, captured = _client()
        client._websocket = FakeWebSocket()
        await client.subscribe("ticker", sub_id="t1")
        await settle(lambda: _events(captured, "subscribed"))

        bucket0.fail(ConnectionClosedError(None, None))
        await settle(lambda: len(calls) == 2)

        with pytest.raises(ConnectionNotOpen):
            await asyncio.wait_for(client.subscribe("ticker", sub_id="t2"), 1)
        assert len(_buckets(client)) == 1
        await client.close()

    async def test_a_client_that_drops_meanwhile_ends_the_wait(
        self, monkeypatch
    ):
        """The full 15 s bound applies: the dropped client must end it."""
        monkeypatch.setattr(_Delay, "next", lambda self: 0.0)
        bucket0 = FakeWebSocket(confirms=True)
        calls = _plan(
            monkeypatch,
            FakeConnect(bucket0),
            _Gated(FakeWebSocket(), asyncio.Event()),
        )
        client, captured = _client()
        client._websocket = FakeWebSocket()
        await client.subscribe("ticker", sub_id="t1")
        await settle(lambda: _events(captured, "subscribed"))
        bucket0.fail(ConnectionClosedError(None, None))
        await settle(lambda: len(calls) == 2)

        pending = asyncio.create_task(client.subscribe("ticker", sub_id="t2"))
        await asyncio.sleep(0.01)
        # What start() does when the main socket drops.
        client._websocket.state = SimpleNamespace(name="CLOSED")
        await client._BfxWebSocketClient__stop_buckets()

        with pytest.raises(ConnectionNotOpen, match="No open connection"):
            await asyncio.wait_for(pending, 1)
        await client.close()

    async def test_an_unsupervised_down_bucket_gets_a_new_one(
        self, monkeypatch
    ):
        sockets: list = []

        def _make():
            sockets.append(FakeWebSocket(confirms=True))
            return FakeConnect(sockets[-1])

        _plan(monkeypatch, _make)
        client, _ = _client()
        client._websocket = FakeWebSocket()
        _down_bucket(client)

        await asyncio.wait_for(client.subscribe("ticker", sub_id="t1"), 1)

        assert len(_buckets(client)) == 2
        assert _subscribes(sockets[0]) == ["t1"]
        await client.close()


class TestCloseWinsOverAScheduledStart:
    async def test_a_close_before_start_runs_wins(self, monkeypatch):
        calls = _plan(
            monkeypatch, lambda: FakeConnect(FakeWebSocket(hold=True))
        )
        client, captured = _client()

        running = asyncio.create_task(client.start())
        await client.close(1000, "bye")
        await asyncio.wait_for(running, 1)

        assert calls == [], "start() connected a client its owner closed"
        assert _events(captured, "open") == []
        assert _events(captured, "disconnected") == [[1000, "bye"]]

    async def test_a_start_after_a_completed_close_runs(self, monkeypatch):
        _plan(monkeypatch, lambda: FakeConnect(FakeWebSocket(hold=True)))
        client, captured = _client()
        await client.close()

        running = asyncio.create_task(client.start())
        await settle(lambda: client.open)

        await client.close()
        await asyncio.wait_for(running, 1)
        assert len(_events(captured, "open")) == 1

    async def test_a_restart_after_a_lost_start_runs(self, monkeypatch):
        _plan(monkeypatch, lambda: FakeConnect(FakeWebSocket(hold=True)))
        client, captured = _client()
        lost = asyncio.create_task(client.start())
        await client.close()
        await asyncio.wait_for(lost, 1)

        running = asyncio.create_task(client.start())
        await settle(lambda: client.open)

        await client.close()
        await asyncio.wait_for(running, 1)
        assert len(_events(captured, "open")) == 1

    def test_start_is_still_a_coroutine_function(self):
        client, _ = _client()
        assert inspect.iscoroutinefunction(client.start)


class TestCloseCodeDuringTheBucketWait:
    async def test_the_requested_code_is_sent_and_reported(self, monkeypatch):
        monkeypatch.setattr(_Delay, "next", lambda self: 0.0)
        main0 = FakeWebSocket(hold=True)
        bucket0 = FakeWebSocket(confirms=True)
        main1 = FakeWebSocket(hold=True)
        calls = _plan(
            monkeypatch,
            _ClosingConnect(main0),
            _ClosingConnect(bucket0),
            _ClosingConnect(main1),
            _Gated(FakeWebSocket(), asyncio.Event()),
        )
        client, captured = _client()
        running = asyncio.create_task(client.start())
        await settle(lambda: client.open)
        await client.subscribe("ticker", sub_id="t1")
        await settle(lambda: _events(captured, "subscribed"))

        main0.fail(ConnectionClosedError(None, None))
        await settle(lambda: len(calls) >= 4)

        # A bucket close that takes a moment (a real socket's handshake):
        # the woken __connect gets there before close() reaches the socket.
        stop = client._BfxWebSocketClient__stop_buckets

        async def _slow_stop():
            await asyncio.sleep(0.02)
            await stop()

        monkeypatch.setattr(
            client, "_BfxWebSocketClient__stop_buckets", _slow_stop
        )

        await client.close(4001, "maintenance")
        await asyncio.wait_for(running, 1)

        assert main1.closed == (4001, "maintenance")
        assert _events(captured, "disconnected") == [[4001, "maintenance"]]


class TestBackoffSurvivesAFlappingServer:
    async def _run(self, monkeypatch, drops: int) -> int:
        resets: list = []
        reset = _Delay.reset
        monkeypatch.setattr(
            _Delay, "reset", lambda self: (resets.append(1), reset(self))
        )
        monkeypatch.setattr(_Delay, "next", lambda self: 0.0)
        mains = [FakeWebSocket(hold=True) for _ in range(drops + 1)]
        calls = _plan(monkeypatch, *[_ClosingConnect(m) for m in mains])
        client, _ = _client()
        running = asyncio.create_task(client.start())
        for n in range(drops):
            await settle(lambda n=n: len(calls) == n + 1 and client.open)
            mains[n].fail(ConnectionClosedError(None, None))
        await settle(lambda: len(calls) == drops + 1 and client.open)
        await client.close()
        await asyncio.wait_for(running, 1)
        return len(resets)

    async def test_a_quick_drop_keeps_the_backoff(self, monkeypatch):
        assert await self._run(monkeypatch, drops=3) == 0

    async def test_a_stable_connection_resets_it(self, monkeypatch):
        monkeypatch.setattr(mod, "_STABLE_CONNECTION_TIME", 0.0)
        assert await self._run(monkeypatch, drops=3) == 3

    async def test_an_attempt_that_never_opened_is_not_uptime(
        self, monkeypatch
    ):
        """Up 200 s, then the next attempt dies in its handshake."""
        now = SimpleNamespace(t=0.0)
        monkeypatch.setattr(mod, "_clock", lambda: now.t)
        resets: list = []
        reset = _Delay.reset
        monkeypatch.setattr(
            _Delay, "reset", lambda self: (resets.append(1), reset(self))
        )
        monkeypatch.setattr(_Delay, "next", lambda self: 0.0)
        main0 = FakeWebSocket(hold=True)
        main2 = FakeWebSocket(hold=True)
        calls = _plan(
            monkeypatch,
            _ClosingConnect(main0),
            _RefusedConnect(ConnectionClosedError(None, None)),
            _ClosingConnect(main2),
        )
        client, _ = _client()
        running = asyncio.create_task(client.start())
        await settle(lambda: client.open)

        # Stable when it drops; the failed attempt right after must not
        # measure its own uptime from main0's open.
        now.t = 200.0
        main0.fail(ConnectionClosedError(None, None))
        await settle(lambda: len(calls) == 3 and client.open)

        await client.close()
        await asyncio.wait_for(running, 1)
        assert len(resets) == 1


class TestABucketThatKeepsFailingGivesUp:
    async def _supervise(self, client, bucket):
        _buckets(client)[bucket] = asyncio.create_task(
            client._BfxWebSocketClient__supervise(bucket)
        )
        return _buckets(client)[bucket]

    async def test_a_deterministic_failure_stops_and_is_loud(self, monkeypatch):
        monkeypatch.setattr(_Delay, "next", lambda self: 0.0)
        calls = _plan(monkeypatch, lambda: _RefusedConnect(RuntimeError("bug")))
        client, captured = _client()
        client._websocket = FakeWebSocket()
        bucket = BfxWebSocketBucket(
            "wss://example.invalid", client._BfxWebSocketClient__event_emitter
        )

        task = await self._supervise(client, bucket)
        await asyncio.wait_for(task, 1)

        assert len(calls) == mod._BUCKET_RESTART_LIMIT
        errors = [error for (error,) in _events(captured, "error")]
        assert isinstance(errors[-1], BucketRestartLimitError)
        assert isinstance(errors[-1], ConnectionNotOpen)
        assert isinstance(errors[-1].__cause__, RuntimeError)

    async def test_a_refused_handshake_counts_too(self, monkeypatch):
        monkeypatch.setattr(_Delay, "next", lambda self: 0.0)
        _plan(monkeypatch, lambda: _RefusedConnect(_invalid_status(403)))
        client, captured = _client()
        client._websocket = FakeWebSocket()
        bucket = BfxWebSocketBucket(
            "wss://example.invalid", client._BfxWebSocketClient__event_emitter
        )

        task = await self._supervise(client, bucket)
        await asyncio.wait_for(task, 1)

        assert isinstance(
            _events(captured, "error")[-1][0], BucketRestartLimitError
        )

    async def test_network_drops_never_give_up(self, monkeypatch):
        monkeypatch.setattr(_Delay, "next", lambda self: 0.0)
        calls = _plan(monkeypatch, lambda: _RefusedConnect(OSError("down")))
        client, captured = _client()
        client._websocket = FakeWebSocket()
        bucket = BfxWebSocketBucket(
            "wss://example.invalid", client._BfxWebSocketClient__event_emitter
        )

        task = await self._supervise(client, bucket)
        await settle(lambda: len(calls) > 3 * mod._BUCKET_RESTART_LIMIT)

        assert not task.done()
        assert _events(captured, "error") == []
        await client.close()

    async def test_a_stable_run_resets_the_count(self, monkeypatch):
        monkeypatch.setattr(_Delay, "next", lambda self: 0.0)
        monkeypatch.setattr(mod, "_STABLE_CONNECTION_TIME", 0.0)
        calls = _plan(
            monkeypatch,
            lambda: FakeConnect(FakeWebSocket(raises=RuntimeError("late"))),
        )
        client, captured = _client()
        client._websocket = FakeWebSocket()
        bucket = BfxWebSocketBucket(
            "wss://example.invalid", client._BfxWebSocketClient__event_emitter
        )

        task = await self._supervise(client, bucket)
        await settle(lambda: len(calls) > 2 * mod._BUCKET_RESTART_LIMIT)

        assert not task.done()
        await client.close()


class TestStartErrorsAreSurfaced:
    async def test_awaited_start_raises_and_emits(self, monkeypatch):
        logger = MagicMock()
        client, captured = _client(logger=logger)
        error = InvalidCredentialError("bad key")

        async def _connect(_self=None):
            raise error

        monkeypatch.setattr(
            client, "_BfxWebSocketClient__connect", _connect, raising=False
        )

        with pytest.raises(InvalidCredentialError):
            await client.start()

        assert _events(captured, "error") == [[error]]
        assert logger.critical.called

    async def test_fire_and_forget_start_is_not_silent(self, monkeypatch):
        client, captured = _client()

        async def _connect(_self=None):
            raise ValueError("boom")

        monkeypatch.setattr(
            client, "_BfxWebSocketClient__connect", _connect, raising=False
        )

        task = asyncio.create_task(client.start())
        await settle(lambda: _events(captured, "error"))

        assert isinstance(_events(captured, "error")[0][0], ValueError)
        with pytest.raises(ValueError):
            await task

    async def test_a_clean_close_emits_no_error(self, monkeypatch):
        _plan(monkeypatch, lambda: FakeConnect(FakeWebSocket(hold=True)))
        client, captured = _client()
        running = asyncio.create_task(client.start())
        await settle(lambda: client.open)

        await client.close()
        await asyncio.wait_for(running, 1)

        assert _events(captured, "error") == []
