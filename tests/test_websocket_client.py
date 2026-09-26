import json
import random
from contextlib import asynccontextmanager
from functools import partial
from logging import Logger

import pytest
import websockets.frames
from websockets.exceptions import ConnectionClosedError

from bfxapi._utils.financial_json import FinancialTokenDecimal
from bfxapi.exceptions import InvalidCredentialError
from bfxapi.websocket._client.bfx_websocket_client import (
    BfxWebSocketClient,
    _Delay,
)
from bfxapi.websocket.exceptions import (
    ActionRequiresAuthentication,
    ConnectionNotOpen,
    ReconnectionTimeoutError,
    SubIdError,
    UnknownChannelError,
    UnknownSubscriptionError,
    VersionMismatchError,
)

from .conftest import FakeWebSocket, settle


class TestDelay:
    """Tests for the exponential backoff _Delay class."""

    def test_initial_delay_is_random(self):
        random.seed(42)
        d = _Delay(backoff_factor=1.618)
        first = d.next()
        assert 1.0 <= first <= 5.0

    def test_backoff_increases(self):
        random.seed(42)
        d = _Delay(backoff_factor=2.0)
        d.next()  # initial random delay
        second = d.next()  # should be 1.92 * 2.0 = 3.84
        third = d.next()  # should be 3.84 * 2.0 = 7.68

        # After initial, delays should grow
        assert second < third

    def test_backoff_max_cap(self):
        d = _Delay(backoff_factor=100.0)
        # Force past initial
        d.next()  # initial
        # Each subsequent call multiplies by 100, but max is 60
        for _ in range(10):
            val = d.next()
        assert val <= 60.0

    def test_peek_does_not_advance(self):
        random.seed(42)
        d = _Delay(backoff_factor=1.618)
        peek1 = d.peek()
        peek2 = d.peek()
        assert peek1 == peek2

    def test_next_advances_past_peek(self):
        random.seed(42)
        d = _Delay(backoff_factor=1.618)
        peek_val = d.peek()
        next_val = d.next()
        assert next_val == peek_val
        # After next, peek should return a different (larger) value
        assert d.peek() != peek_val

    def test_reset(self):
        random.seed(42)
        d = _Delay(backoff_factor=2.0)
        d.next()  # initial
        d.next()  # advance
        d.next()  # advance more

        d.reset()
        # After reset, peek returns initial delay again
        peek = d.peek()
        assert 1.0 <= peek <= 5.0

    def test_backoff_factor_applied(self):
        d = _Delay(backoff_factor=2.0)
        d.next()  # initial random delay
        val1 = d.next()  # 1.92 * 2 = 3.84
        val2 = d.next()  # 3.84 * 2 = 7.68
        assert abs(val2 / val1 - 2.0) < 0.01


_HOST = "wss://example.invalid/ws/2"

_CREDENTIALS = {"api_key": "k", "api_secret": "s", "filters": None}


def make_client(**kwargs) -> tuple[BfxWebSocketClient, list]:
    """A client whose own socket is already open, with events captured."""
    client = BfxWebSocketClient(_HOST, **kwargs)
    captured: list = []
    for event in (
        "open",
        "disconnected",
        "authenticated",
        "balance_update",
        "wallet_snapshot",
        "subscribed",
    ):
        client.on(event, partial(_capture, captured, event))
    return client, captured


def _capture(captured: list, event: str, *args) -> None:
    captured.append((event, *args))


async def subscribe_confirmed(client, captured, sub_id, **kwargs) -> None:
    """Subscribe and wait for the server double to confirm it.

    Everything downstream of a subscription - unsubscribe, resubscribe,
    bucket reuse - keys off the CONFIRMED set, so a test that asserts
    straight after `subscribe` is asserting against a pending request.
    """
    await client.subscribe("ticker", sub_id=sub_id, **kwargs)
    await settle(
        lambda: any(
            event == "subscribed" and payload["sub_id"] == sub_id
            for event, payload, *_ in captured
        )
    )


@asynccontextmanager
async def opened(client: BfxWebSocketClient):
    """Drive the client as if its own socket were already connected.

    `subscribe` and friends sit behind `_require_websocket_connection`, so
    a test of routing would otherwise have to run the whole reconnection
    loop just to reach them.
    """
    client._websocket = FakeWebSocket()
    try:
        yield client
    finally:
        if client.open:
            await client.close()


class TestSubscriptionRouting:
    async def test_unknown_channel_is_rejected(self, connect_each):
        connect_each()
        client, _ = make_client()
        async with opened(client):
            with pytest.raises(UnknownChannelError, match="ticker, trades"):
                await client.subscribe("trollbox", symbol="tBTCUSD")

    async def test_duplicate_sub_id_is_rejected(self, connect_each):
        """sub_id is the caller's handle, so it has to be unique globally.

        The check spans every bucket: two buckets each holding "abc" would
        make `unsubscribe("abc")` act on whichever happened to be first.
        """
        connect_each()
        client, _ = make_client()
        async with opened(client):
            await client.subscribe("ticker", sub_id="abc", symbol="tBTCUSD")
            with pytest.raises(SubIdError, match="must be unique"):
                await client.subscribe("ticker", sub_id="abc", symbol="tETHUSD")

    async def test_a_sub_id_is_never_both_taken_and_unknown(self, connect_each):
        """The gap between `subscribe` and its confirmation used to be a trap.

        The duplicate check reads `bucket.ids`, which counts requests in
        flight, so the sub_id is taken the moment the frame leaves. But
        `unsubscribe` read `bucket.has`, which counted only confirmed ones -
        so a caller that changed its mind inside that window could neither
        cancel the subscription nor ask for it again, and no amount of
        retrying would clear it.

        The window is not one round trip: a reconnect moves every
        subscription the client holds back to pending.
        """
        connect_each()
        client, _ = make_client()
        async with opened(client):
            await client.subscribe("ticker", sub_id="abc", symbol="tBTCUSD")

            with pytest.raises(SubIdError, match="must be unique"):
                await client.subscribe("ticker", sub_id="abc", symbol="tETHUSD")

            # Taken, therefore cancellable - and reusable once cancelled.
            await client.unsubscribe("abc")
            await client.subscribe("ticker", sub_id="abc", symbol="tETHUSD")

    async def test_a_bucket_is_filled_before_a_second_one_is_opened(
        self, connect_each
    ):
        """25 channels per socket is Bitfinex's limit, and the reason
        buckets exist at all. Opening a socket per subscription would work
        in a test and get the client throttled in production."""
        sockets = connect_each()
        client, _ = make_client()
        async with opened(client):
            for index in range(25):
                await client.subscribe(
                    "ticker", sub_id=f"s{index}", symbol="tBTCUSD"
                )
            assert len(sockets) == 1, "all 25 belong on one socket"

            await client.subscribe("ticker", sub_id="s25", symbol="tBTCUSD")
            assert len(sockets) == 2, "the 26th needs a second socket"

    async def test_unsubscribe_closes_a_bucket_it_empties(self, connect_each):
        sockets = connect_each()
        client, captured = make_client()
        async with opened(client):
            await subscribe_confirmed(client, captured, "abc", symbol="tBTCUSD")
            await client.unsubscribe("abc")

            assert sockets[0].closed == (1001, "Going Away")

            await client.subscribe("ticker", sub_id="def", symbol="tBTCUSD")
            assert len(sockets) == 2, "the emptied bucket was discarded"

    async def test_unsubscribe_keeps_a_bucket_that_still_has_work(
        self, connect_each
    ):
        sockets = connect_each()
        client, captured = make_client()
        async with opened(client):
            await subscribe_confirmed(client, captured, "abc", symbol="tBTCUSD")
            await subscribe_confirmed(client, captured, "def", symbol="tETHUSD")
            await client.unsubscribe("abc")

            assert sockets[0].closed is None
            assert len(sockets) == 1

    @pytest.mark.parametrize("action", ["unsubscribe", "resubscribe"])
    async def test_acting_on_an_unknown_sub_id_raises(
        self, connect_each, action
    ):
        connect_each()
        client, _ = make_client()
        async with opened(client):
            with pytest.raises(UnknownSubscriptionError, match="<nope>"):
                await getattr(client, action)("nope")

    async def test_resubscribe_reaches_the_owning_bucket(self, connect_each):
        sockets = connect_each()
        client, captured = make_client()
        async with opened(client):
            await subscribe_confirmed(client, captured, "abc", symbol="tBTCUSD")
            await client.resubscribe("abc")

        frames = [event["event"] for event in sockets[0].events]
        assert frames == ["conf", "subscribe", "unsubscribe", "subscribe"]
        assert sockets[0].events[-1]["subId"] == "abc"

    async def test_close_closes_every_bucket_and_the_client_socket(
        self, connect_each
    ):
        sockets = connect_each()
        client, _ = make_client()
        client._websocket = FakeWebSocket()
        for index in range(26):
            await client.subscribe(
                "ticker", sub_id=f"s{index}", symbol="tBTCUSD"
            )

        await client.close(code=1001, reason="bye")

        assert len(sockets) == 2
        assert all(socket.closed == (1001, "bye") for socket in sockets)
        assert client.open is False


class TestClosedClientRefusesEveryAction:
    @pytest.mark.parametrize(
        "action",
        [
            lambda c: c.subscribe("ticker", symbol="tBTCUSD"),
            lambda c: c.unsubscribe("abc"),
            lambda c: c.resubscribe("abc"),
            lambda c: c.close(),
        ],
        ids=["subscribe", "unsubscribe", "resubscribe", "close"],
    )
    async def test_raises_connection_not_open(self, action):
        client, _ = make_client()
        with pytest.raises(ConnectionNotOpen, match="No open connection"):
            await action(client)


class TestAuthenticatedActions:
    async def test_notify_requires_authentication(self):
        client, _ = make_client()
        client._websocket = FakeWebSocket()
        with pytest.raises(ActionRequiresAuthentication, match="API_KEY"):
            await client.notify("hello")

    async def test_inputs_require_authentication(self):
        """The inputs facade shares the client's auth gate.

        It reaches the socket through a private callback rather than
        through `notify`, so it needs its own proof that an unauthenticated
        order cannot be submitted.
        """
        client, _ = make_client()
        client._websocket = FakeWebSocket()
        with pytest.raises(ActionRequiresAuthentication, match="API_KEY"):
            await client.inputs.submit_order(
                type="EXCHANGE LIMIT", symbol="tBTCUSD", amount=1, price=1
            )

    async def test_notify_sends_a_ucm_frame(self):
        client, _ = make_client()
        websocket = FakeWebSocket()
        client._websocket = websocket
        client._authentication = True

        await client.notify("hello", message_id=7, extra="x")

        assert websocket.events == [
            [0, "n", 7, {"type": "ucm-test", "info": "hello", "extra": "x"}]
        ]

    async def test_inputs_send_on_the_client_socket(self):
        client, _ = make_client()
        websocket = FakeWebSocket()
        client._websocket = websocket
        client._authentication = True

        await client.inputs.cancel_order(id=123)

        channel, event, placeholder, data = websocket.events[0]
        assert (channel, event, placeholder) == (0, "oc", None)
        assert data["id"] == 123


class TestMessageLoop:
    async def test_open_is_emitted_before_the_first_frame(self, connect_each):
        connect_each(lambda: FakeWebSocket())
        client, captured = make_client()

        await client.start()

        assert [event for event, *_ in captured][0] == "open"

    async def test_clean_close_reports_code_and_reason(self, connect_each):
        def socket():
            fake = FakeWebSocket()
            fake.close_code, fake.close_reason = 1000, "server done"
            return fake

        connect_each(socket)
        client, captured = make_client()

        await client.start()

        assert ("disconnected", 1000, "server done") in captured

    async def test_version_mismatch_stops_the_client(self, connect_each):
        """A protocol bump is not a reconnectable fault.

        Retrying would loop forever against a server that will never speak
        version 2 again, so this has to escape the reconnection handler.
        """
        connect_each(
            lambda: FakeWebSocket(
                incoming=[json.dumps({"event": "info", "version": 3})]
            )
        )
        client, _ = make_client()

        with pytest.raises(VersionMismatchError, match="server version: 3"):
            await client.start()

    async def test_bad_credentials_stop_the_client(self, connect_each):
        connect_each(
            lambda: FakeWebSocket(
                incoming=[json.dumps({"event": "auth", "status": "FAILED"})]
            )
        )
        client, _ = make_client(credentials=_CREDENTIALS)

        with pytest.raises(InvalidCredentialError, match="API-KEY"):
            await client.start()

    async def test_successful_auth_is_announced_and_recorded(
        self, connect_each
    ):
        sockets = connect_each(
            lambda: FakeWebSocket(
                incoming=[
                    json.dumps({"event": "auth", "status": "OK", "userId": 1})
                ]
            )
        )
        client, captured = make_client(credentials=_CREDENTIALS)

        await client.start()

        assert json.loads(sockets[0].sent[0])["event"] == "auth"
        assert (
            "authenticated",
            {"event": "auth", "status": "OK", "userId": 1},
        ) in captured
        assert client.authentication is True

    async def test_auth_channel_payload_reaches_the_handler(self, connect_each):
        connect_each(
            lambda: FakeWebSocket(incoming=[json.dumps([0, "bu", [1.5, 2.5]])])
        )
        client, captured = make_client()

        await client.start()

        updates = [e for e in captured if e[0] == "balance_update"]
        assert len(updates) == 1
        assert (updates[0][1].aum, updates[0][1].aum_net) == (1.5, 2.5)

    async def test_auth_channel_heartbeat_is_ignored(self, connect_each):
        connect_each(lambda: FakeWebSocket(incoming=[json.dumps([0, "hb"])]))
        client, captured = make_client()

        await client.start()

        assert [e for e in captured if e[0] == "balance_update"] == []

    async def test_lossless_decode_reaches_the_auth_channel(self, connect_each):
        """The opt-in has to survive the client's own socket too.

        The client decodes its frames itself rather than through a bucket,
        so covering only the bucket would leave every authenticated
        balance and order amount silently rounded to a float.
        """
        connect_each(
            lambda: FakeWebSocket(
                incoming=['[0, "bu", [1.00000000000000000001, 2.5]]']
            )
        )
        client, captured = make_client(lossless_financial_decode=True)

        await client.start()

        aum = [e for e in captured if e[0] == "balance_update"][0][1].aum
        assert isinstance(aum, FinancialTokenDecimal)
        assert aum.source_token == "1.00000000000000000001"


class TestReconnection:
    async def test_server_restart_reconnects_and_recovers(
        self, connect_each, monkeypatch
    ):
        """Bitfinex code 20051 means "reconnect", not "give up".

        The client turns it into a synthetic 1012 close so the same
        reconnection path handles it as a dropped socket would.
        """
        monkeypatch.setattr(_Delay, "next", lambda self: 0)

        frames = [[json.dumps({"event": "info", "code": 20051})], []]
        sockets = connect_each(
            lambda: FakeWebSocket(incoming=frames.pop(0) if frames else [])
        )
        client, captured = make_client()

        await client.start()

        assert len(sockets) == 2, "the client reconnected exactly once"
        assert [e for e, *_ in captured].count("open") == 2
        assert [e for e, *_ in captured][-1] == "disconnected"

    async def test_unexpected_close_is_not_swallowed(self, connect_each):
        """Only 1006 and 1012 are reconnectable; anything else must escape.

        Retrying a 1008 policy violation or a 1011 server error would hide
        a fault the caller has to act on behind an endless backoff.
        """
        connect_each(
            lambda: FakeWebSocket(
                raises=ConnectionClosedError(
                    rcvd=websockets.frames.Close(1008, "policy violation"),
                    sent=None,
                )
            )
        )
        client, _ = make_client()

        with pytest.raises(ConnectionClosedError):
            await client.start()

    async def test_snapshots_are_re_announced_after_a_reconnect(
        self, connect_each, monkeypatch
    ):
        """The reconnect is only useful if the consumer is resynced.

        Suppressing the second wallet_snapshot leaves a bot trading on
        balances from before the disconnect, with a healthy-looking socket
        delivering updates on top of them.
        """
        monkeypatch.setattr(_Delay, "next", lambda self: 0)

        wallet = ["exchange", "USD", 1.0, 0.0, 1.0, None, None]
        frames = [
            [
                json.dumps([0, "ws", [wallet]]),
                json.dumps({"event": "info", "code": 20051}),
            ],
            [json.dumps([0, "ws", [wallet]])],
        ]
        connect_each(
            lambda: FakeWebSocket(incoming=frames.pop(0) if frames else [])
        )
        client, captured = make_client()

        await client.start()

        snapshots = [e for e in captured if e[0] == "wallet_snapshot"]
        assert len(snapshots) == 2, "the recovered client must resync"
        assert snapshots[1][1][0].currency == "USD"

    async def test_a_lost_connection_is_retried(self, connect_each):
        """1006 is an abnormal close: the socket died without a goodbye."""
        faults = [
            ConnectionClosedError(
                rcvd=websockets.frames.Close(1006, "abnormal"), sent=None
            )
        ]
        sockets = connect_each(
            lambda: FakeWebSocket(raises=faults.pop(0) if faults else None)
        )
        client, captured = make_client()

        await client.start()

        assert len(sockets) == 2
        assert [e for e, *_ in captured].count("open") == 2

    async def test_offline_too_long_gives_up(self, connect_each):
        """An unreachable server must eventually surface, not retry forever.

        A bot that never hears about a permanently dead socket keeps
        reporting healthy while doing nothing, which is worse than a crash
        its supervisor can act on.

        `timeout=0` is the sharpest form of the rule and used to be the
        one case that broke it: a falsy check read it as "no timeout
        configured", so asking to give up immediately asked to never give
        up. `None` is the documented way to retry forever.
        """
        connect_each(
            lambda: FakeWebSocket(
                raises=ConnectionClosedError(
                    rcvd=websockets.frames.Close(1006, "abnormal"), sent=None
                )
            )
        )
        client, _ = make_client(timeout=0)

        with pytest.raises(ReconnectionTimeoutError, match="timeout: 0s"):
            await client.start()


class TestErrorReporting:
    def test_an_emitted_error_is_logged_with_its_stack(self):
        """pyee swallows listener exceptions unless something reports them.

        Without this listener an exception raised inside a user callback
        disappears: no log line, no crash, and a bot that looks healthy
        while it has stopped reacting.
        """
        records: list[str] = []
        logger = Logger("test", level=0)
        logger.critical = lambda message: records.append(message)

        client, _ = make_client(logger=logger)
        try:
            raise ValueError("boom")
        except ValueError as error:
            client._BfxWebSocketClient__event_emitter.emit("error", error)

        assert len(records) == 1
        assert records[0].startswith("ValueError: boom\n")
        assert "raise ValueError" in records[0]


class TestOn:
    def test_decorator_and_direct_forms_both_register(self, connect_each):
        client, _ = make_client()
        seen: list[str] = []

        client.on("open", lambda: seen.append("direct"))

        @client.on("open")
        def _decorated() -> None:
            seen.append("decorated")

        client._BfxWebSocketClient__event_emitter.emit("open")

        assert seen == ["direct", "decorated"]
