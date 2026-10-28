"""Reconnection, outage deadlines, bucket supervision and maintenance codes.

Each class here pins one way a live client used to go quiet without saying
so: a dropped TCP connection that ended the client (websockets 16 reports it
with no close frame at all), a 503 or a refused connect while reconnecting
that ended it too, the timer of an earlier outage cutting a later one short,
and a subscription bucket whose task died with nobody watching it.

The wire tests run a real `websockets` server on loopback, because the
regression they guard was invisible to tests that built close frames by hand:
RFC 6455 forbids sending 1006, so a "received 1006" never happens.
"""

import asyncio
import json
from functools import partial
from types import SimpleNamespace

import pytest
from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosedError, InvalidMessage
from websockets.frames import Close
from websockets.http11 import Response

from bfxapi._utils.financial_json import FinancialDecodeError
from bfxapi.websocket._client import bfx_websocket_client as mod
from bfxapi.websocket._client.bfx_websocket_client import (
    BfxWebSocketClient,
    _Delay,
)
from bfxapi.websocket.exceptions import ReconnectionTimeoutError

from .conftest import FakeWebSocket, settle
from .test_websocket_close_codes import (
    _closed,
    _drive,
    _invalid_status,
    _StopTheLoop,
)


@pytest.fixture
def no_backoff(monkeypatch):
    monkeypatch.setattr(_Delay, "next", lambda self: 0.0)
    monkeypatch.setattr(_Delay, "peek", lambda self: 0.0)


def _port(server) -> int:
    return int(server.sockets[0].getsockname()[1])


class TestConnectionLostOnTheWire:
    """websockets 16 puts no close frame on a dead connection: `rcvd` is None."""

    async def test_a_tcp_abort_reconnects(self, no_backoff):
        handshakes = 0

        async def handler(ws):
            nonlocal handshakes
            handshakes += 1
            await ws.send(json.dumps({"event": "info", "version": 2}))
            if handshakes == 1:
                await asyncio.sleep(0.05)
                ws.transport.abort()
                return
            await ws.close(1000, "bye")

        async with serve(handler, "127.0.0.1", 0) as server:
            client = BfxWebSocketClient(f"ws://127.0.0.1:{_port(server)}")
            await asyncio.wait_for(client.start(), 5)

        assert handshakes == 2, "the client gave up instead of reconnecting"

    async def test_a_503_while_reconnecting_is_retried(self, no_backoff):
        """Bitfinex answers 503 during maintenance: that is a reason to wait."""
        handshakes = 0

        def process_request(connection, request):
            nonlocal handshakes
            handshakes += 1
            if handshakes == 2:
                return Response(503, "Service Unavailable", Headers(), b"")
            return None

        async def handler(ws):
            if handshakes == 1:
                await ws.send(json.dumps({"event": "info", "code": 20051}))
                await asyncio.sleep(5)
            await ws.close(1000, "bye")

        async with serve(
            handler, "127.0.0.1", 0, process_request=process_request
        ) as server:
            client = BfxWebSocketClient(f"ws://127.0.0.1:{_port(server)}")
            await asyncio.wait_for(client.start(), 5)

        assert handshakes == 3


class TestCloseClassification:
    @pytest.mark.parametrize(
        "error",
        [
            pytest.param(
                ConnectionClosedError(None, None), id="no close frame"
            ),
            pytest.param(
                ConnectionClosedError(
                    None, Close(1011, "keepalive ping timeout")
                ),
                id="keepalive timeout",
            ),
        ],
    )
    async def test_a_lost_connection_reconnects(self, monkeypatch, error):
        client, _ = _drive(monkeypatch, [error, _StopTheLoop()])

        with pytest.raises(_StopTheLoop):
            await client.start()

    async def test_a_close_the_client_asked_for_is_never_reconnected(
        self, monkeypatch
    ):
        """A close handshake that times out also has no received frame.

        Treating it as a lost connection would resurrect a client its owner
        just shut down.
        """
        client, emitted = _drive(monkeypatch, [])

        async def _connect(_self=None):
            client._websocket = FakeWebSocket()
            await client.close()
            raise ConnectionClosedError(None, Close(1000, ""))

        monkeypatch.setattr(
            client, "_BfxWebSocketClient__connect", _connect, raising=False
        )

        with pytest.raises(ConnectionClosedError):
            await client.start()

    async def test_a_server_going_away_reconnects(self, monkeypatch):
        """1001 from the server is an edge restart, not a goodbye."""
        socket = SimpleNamespace(close_code=1001, close_reason="going away")
        client, emitted = _drive(
            monkeypatch, [None, _StopTheLoop()], websocket=socket
        )

        with pytest.raises(_StopTheLoop):
            await client.start()

        assert [e for e, _ in emitted if e == "disconnected"] == []


class TestRetryableFailuresWhileReconnecting:
    @pytest.mark.parametrize(
        "error",
        [
            pytest.param(_invalid_status(429), id="HTTP 429"),
            pytest.param(_invalid_status(500), id="HTTP 500"),
            pytest.param(_invalid_status(502), id="HTTP 502"),
            pytest.param(_invalid_status(503), id="HTTP 503"),
            pytest.param(_invalid_status(520), id="HTTP 520"),
            pytest.param(ConnectionRefusedError(111, "refused"), id="refused"),
            pytest.param(
                TimeoutError("timed out during handshake"), id="timeout"
            ),
            pytest.param(OSError(101, "Network is unreachable"), id="OSError"),
            pytest.param(
                InvalidMessage("did not receive a valid HTTP response"),
                id="InvalidMessage",
            ),
        ],
    )
    async def test_is_retried(self, monkeypatch, error):
        client, _ = _drive(monkeypatch, [_closed(1006), error, _StopTheLoop()])

        with pytest.raises(_StopTheLoop):
            await client.start()

        assert client._BfxWebSocketClient__reconnection["attempts"] == 2

    @pytest.mark.parametrize("status", [400, 401, 403, 404])
    async def test_a_refusal_on_purpose_surfaces(self, monkeypatch, status):
        error = _invalid_status(status)
        client, _ = _drive(monkeypatch, [_closed(1006), error])

        with pytest.raises(type(error)) as caught:
            await client.start()

        assert caught.value is error

    @pytest.mark.parametrize(
        "error",
        [
            pytest.param(_invalid_status(503), id="HTTP 503"),
            pytest.param(ConnectionRefusedError(111, "refused"), id="refused"),
        ],
    )
    async def test_a_first_connect_still_surfaces(self, monkeypatch, error):
        """A failed FIRST connect is a bad host or a down network: say so."""
        client, _ = _drive(monkeypatch, [error])

        with pytest.raises(type(error)):
            await client.start()


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _drive_clocked(monkeypatch, steps, *, timeout):
    """start() through `steps`, each `(time, outcome)` on a fake clock.

    An outcome of "up" is a connection that came up (ending the outage) and
    then dropped with 1006, which is what the real __connect does.
    """
    clock = _Clock()
    monkeypatch.setattr(mod, "_clock", clock)
    client, _ = _drive(monkeypatch, [], timeout=timeout)
    remaining = list(steps)

    async def _connect(_self=None):
        if not remaining:
            raise _StopTheLoop
        clock.now, outcome = remaining.pop(0)
        if outcome == "up":
            client._BfxWebSocketClient__reconnection = None
            outcome = _closed(1006)
        raise outcome

    monkeypatch.setattr(
        client, "_BfxWebSocketClient__connect", _connect, raising=False
    )
    return client


class TestOutageDeadline:
    async def test_an_earlier_outage_does_not_cut_a_later_one(
        self, monkeypatch
    ):
        """Outage A at t=0 recovers; outage B starts at t=7.5 (timeout 8).

        A fire-and-forget timer from A used to end B at t=8, half a second in.
        """
        client = _drive_clocked(
            monkeypatch,
            [
                (0.0, _closed(1006)),
                (1.0, "up"),
                (7.5, _closed(1006)),
                (9.0, _invalid_status(503)),
            ],
            timeout=8,
        )

        with pytest.raises(_StopTheLoop):
            await client.start()

    async def test_an_outage_past_its_deadline_gives_up(self, monkeypatch):
        client = _drive_clocked(
            monkeypatch,
            [
                (0.0, _closed(1006)),
                (4.0, ConnectionRefusedError(111, "refused")),
                (8.5, ConnectionRefusedError(111, "refused")),
            ],
            timeout=8,
        )

        with pytest.raises(ReconnectionTimeoutError, match="timeout: 8s"):
            await client.start()

    async def test_the_backoff_never_sleeps_past_the_deadline(
        self, monkeypatch
    ):
        """A 60 s backoff against a 50 ms deadline ends at the deadline."""
        monkeypatch.setattr(_Delay, "next", lambda self: 60.0)
        client = BfxWebSocketClient("wss://example.invalid", timeout=0.05)  # type: ignore[arg-type]

        async def _connect(_self=None):
            raise _closed(1006)

        monkeypatch.setattr(
            client, "_BfxWebSocketClient__connect", _connect, raising=False
        )

        with pytest.raises(ReconnectionTimeoutError):
            await asyncio.wait_for(client.start(), 2)

    async def test_no_timeout_retries_forever(self, monkeypatch):
        client = _drive_clocked(
            monkeypatch,
            [(0.0, _closed(1006))]
            + [(1e6 * n, _invalid_status(503)) for n in range(1, 4)],
            timeout=None,
        )

        with pytest.raises(_StopTheLoop):
            await client.start()


def _client_with_events(**kwargs):
    client = BfxWebSocketClient("wss://example.invalid", **kwargs)
    captured: list = []
    for event in (
        "subscribed",
        "error",
        "maintenance_start",
        "maintenance_end",
    ):
        client.on(event, partial(lambda e, *a: captured.append((e, *a)), event))
    return client, captured


def _subscribes(websocket: FakeWebSocket) -> list[str]:
    return [
        frame["subId"]
        for frame in websocket.events
        if frame.get("event") == "subscribe"
    ]


class TestBucketSupervision:
    """A bucket socket is its own TCP connection and can die on its own."""

    async def _subscribed(self, connect_each, client, captured, **kwargs):
        sockets = connect_each()
        client._websocket = FakeWebSocket()
        await client.subscribe("ticker", sub_id="t1", symbol="tBTCUSD")
        await settle(lambda: "subscribed" in [e for e, *_ in captured])
        return sockets

    async def test_a_dropped_bucket_reconnects_and_resubscribes(
        self, connect_each, no_backoff
    ):
        client, captured = _client_with_events()
        sockets = await self._subscribed(connect_each, client, captured)

        sockets[0].fail(ConnectionClosedError(None, None))

        await settle(lambda: len(sockets) == 2 and _subscribes(sockets[1]))
        assert _subscribes(sockets[1]) == ["t1"]
        # The fresh confirmation must reach the consumer: the per-subscription
        # ledger would otherwise swallow it as a duplicate.
        await settle(lambda: [e for e, *_ in captured].count("subscribed") == 2)
        assert [e for e, *_ in captured if e == "error"] == []
        await client.close()

    async def test_a_rejected_frame_is_surfaced_and_recovered(
        self, connect_each, no_backoff
    ):
        """The #29 decoder refuses NaN: the consumer must hear about it."""
        client, captured = _client_with_events(lossless_financial_decode=True)
        sockets = await self._subscribed(connect_each, client, captured)

        sockets[0].deliver("[1, [NaN, 1, 2, 3, 4, 5, 6, 7, 8, 9]]")

        await settle(lambda: any(e == "error" for e, *_ in captured))
        errors = [a[0] for e, *a in captured if e == "error"]
        assert isinstance(errors[0], FinancialDecodeError)
        await settle(lambda: len(sockets) == 2 and _subscribes(sockets[1]))
        await client.close()

    async def test_a_server_close_of_a_bucket_reconnects(
        self, connect_each, no_backoff
    ):
        client, captured = _client_with_events()
        sockets = await self._subscribed(connect_each, client, captured)

        await sockets[0].close(1001, "going away")

        await settle(lambda: len(sockets) == 2 and _subscribes(sockets[1]))
        await client.close()

    async def test_an_emptied_bucket_is_not_restarted(
        self, connect_each, no_backoff
    ):
        client, captured = _client_with_events()
        sockets = await self._subscribed(connect_each, client, captured)

        await client.unsubscribe("t1")
        await asyncio.sleep(0.02)

        assert len(sockets) == 1
        await client.close()

    async def test_a_closed_client_does_not_restart_its_buckets(
        self, connect_each, no_backoff
    ):
        client, captured = _client_with_events()
        sockets = await self._subscribed(connect_each, client, captured)

        await client.close()
        await asyncio.sleep(0.02)

        assert len(sockets) == 1

    async def test_a_bucket_dropped_while_the_client_is_down_waits_for_it(
        self, connect_each, no_backoff
    ):
        """The client's own reconnect restarts every bucket: no second path."""
        client, captured = _client_with_events()
        sockets = await self._subscribed(connect_each, client, captured)

        await client._websocket.close()
        sockets[0].fail(RuntimeError("handler bug"))
        await settle(lambda: any(e == "error" for e, *_ in captured))
        await asyncio.sleep(0.02)

        assert len(sockets) == 1

    async def test_a_client_reconnect_restarts_its_buckets_once(
        self, connect_each, no_backoff
    ):
        """The old bucket task is stopped, so no bucket runs twice."""
        sockets = connect_each()
        client, captured = _client_with_events()
        running = asyncio.create_task(client.start())
        await settle(lambda: client.open)
        await client.subscribe("ticker", sub_id="t1", symbol="tBTCUSD")
        await settle(lambda: "subscribed" in [e for e, *_ in captured])

        sockets[0].fail(ConnectionClosedError(None, None))

        await settle(lambda: len(sockets) == 4 and _subscribes(sockets[3]))
        assert _subscribes(sockets[3]) == ["t1"]
        await asyncio.sleep(0.02)
        assert len(sockets) == 4
        await client.close()
        await asyncio.wait_for(running, 2)


class TestMaintenanceCodes:
    async def test_maintenance_start_and_end_are_announced(self, connect_each):
        start = {"event": "info", "code": 20060, "msg": "maintenance"}
        end = {"event": "info", "code": 20061, "msg": "done"}
        connect_each(
            lambda: FakeWebSocket(incoming=[json.dumps(start), json.dumps(end)])
        )
        client, captured = _client_with_events()

        await client.start()

        assert [(e, a) for e, a in captured] == [
            ("maintenance_start", start),
            ("maintenance_end", end),
        ]

    @pytest.mark.parametrize("code", [20051, 20061])
    async def test_a_bucket_resubscribes_on(
        self, connect_each, no_backoff, code
    ):
        """20051 is a restart; after 20061 the docs say resubscribe everything."""
        client, captured = _client_with_events()
        sockets = await TestBucketSupervision()._subscribed(
            connect_each, client, captured
        )

        sockets[0].deliver(json.dumps({"event": "info", "code": code}))

        await settle(lambda: len(sockets) == 2 and _subscribes(sockets[1]))
        assert _subscribes(sockets[1]) == ["t1"]
        assert [e for e, *_ in captured if e == "error"] == []
        await client.close()
