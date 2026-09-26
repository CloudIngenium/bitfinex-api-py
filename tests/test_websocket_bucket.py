"""Tests for BfxWebSocketBucket: subscription accounting and the message loop.

The bucket is the only place where a decoded server frame becomes a typed
event, so the shared fakes in conftest stand in for the socket rather than
for the bucket: every assertion goes through `subscribe`/`start` exactly as
the real client drives them.
"""

import asyncio
import json

import pytest
from pyee import EventEmitter

from bfxapi._utils.financial_json import FinancialTokenDecimal
from bfxapi.websocket._client.bfx_websocket_bucket import BfxWebSocketBucket
from bfxapi.websocket.exceptions import ConnectionNotOpen

from .conftest import FakeConnect, FakeWebSocket

_HOST = "wss://example.invalid/ws/2"

# A trading-pair ticker payload: 10 values, the 11th (first_trade) optional.
_TICKER_STREAM = [0.1, 1.0, 0.2, 2.0, 0.01, 0.1, 0.15, 100.0, 0.3, 0.05]


def make_bucket(**kwargs) -> tuple[BfxWebSocketBucket, EventEmitter, list]:
    emitter = EventEmitter()
    captured: list = []
    emitter.on("subscribed", lambda *a: captured.append(("subscribed", *a)))
    emitter.on(
        "t_ticker_update", lambda *a: captured.append(("t_ticker_update", *a))
    )
    return BfxWebSocketBucket(_HOST, emitter, **kwargs), emitter, captured


def subscribed_frame(chan_id: int, sub_id: str, symbol: str = "tBTCUSD") -> str:
    return json.dumps(
        {
            "event": "subscribed",
            "channel": "ticker",
            "chanId": chan_id,
            "subId": sub_id,
            "symbol": symbol,
            "pair": symbol[1:],
        }
    )


class TestSubscriptionAccounting:
    def test_empty_bucket_is_not_full(self):
        bucket, _, _ = make_bucket()
        assert bucket.count == 0
        assert bucket.is_full is False
        assert bucket.ids == []

    async def test_pending_subscriptions_count_against_capacity(self, connect):
        """A request in flight occupies a slot before the server confirms it.

        Counting only confirmed subscriptions would let a burst of 25
        subscribe calls all pass the capacity check, and Bitfinex closes a
        bucket that exceeds 25 channels.
        """
        bucket, _, _ = make_bucket()
        websocket = connect(FakeWebSocket())
        async with FakeConnect(websocket) as socket:
            bucket._websocket = socket
            for index in range(25):
                await bucket.subscribe("ticker", sub_id=f"s{index}")

        assert bucket.count == 25
        assert bucket.is_full is True

    async def test_ids_span_both_wire_spellings(self, connect):
        """Pendings carry `subId`; confirmed subscriptions carry `sub_id`.

        The outgoing frame uses Bitfinex's camelCase and the decoder
        snake_cases whatever comes back, so `ids` reads two different keys
        for the same concept. A single spelling would silently drop half
        the bucket from every lookup.
        """
        bucket, _, _ = make_bucket()
        connect(FakeWebSocket(incoming=[subscribed_frame(7, "confirmed")]))

        bucket._websocket = FakeWebSocket()
        await bucket.subscribe("ticker", sub_id="confirmed", symbol="tBTCUSD")
        await bucket.subscribe("ticker", sub_id="in-flight", symbol="tETHUSD")
        await bucket.start()

        assert sorted(bucket.ids) == ["confirmed", "in-flight"]
        assert bucket.has("confirmed") is True
        assert bucket.has("in-flight") is False, "still pending, not confirmed"
        assert bucket.has("never-asked") is False

    async def test_confirmation_moves_a_slot_without_creating_one(
        self, connect
    ):
        bucket, _, captured = make_bucket()
        connect(FakeWebSocket(incoming=[subscribed_frame(7, "abc")]))

        bucket._websocket = FakeWebSocket()
        await bucket.subscribe("ticker", sub_id="abc", symbol="tBTCUSD")
        assert bucket.count == 1

        await bucket.start()

        assert bucket.count == 1, "confirming a pending must not add a slot"
        assert [event for event, *_ in captured] == ["subscribed"]
        assert captured[0][1]["sub_id"] == "abc"
        assert "chan_id" not in captured[0][1]


class TestClosedBucketRefusesEveryAction:
    @pytest.mark.parametrize(
        "action",
        [
            lambda b: b.subscribe("ticker", symbol="tBTCUSD"),
            lambda b: b.unsubscribe("abc"),
            lambda b: b.resubscribe("abc"),
            lambda b: b.close(),
        ],
        ids=["subscribe", "unsubscribe", "resubscribe", "close"],
    )
    async def test_raises_connection_not_open(self, action):
        bucket, _, _ = make_bucket()
        with pytest.raises(ConnectionNotOpen, match="No open connection"):
            await action(bucket)


class TestSubscribeFrames:
    async def test_generates_a_sub_id_when_none_is_given(self):
        bucket, _, _ = make_bucket()
        websocket = FakeWebSocket()
        bucket._websocket = websocket

        await bucket.subscribe("ticker", symbol="tBTCUSD")

        (frame,) = websocket.events
        assert frame["event"] == "subscribe"
        assert frame["channel"] == "ticker"
        assert frame["symbol"] == "tBTCUSD"
        assert frame["subId"], "Bitfinex correlates confirmations by subId"
        assert bucket.ids == [frame["subId"]]

    async def test_generated_sub_ids_are_distinct(self):
        bucket, _, _ = make_bucket()
        bucket._websocket = FakeWebSocket()

        await bucket.subscribe("ticker", symbol="tBTCUSD")
        await bucket.subscribe("ticker", symbol="tETHUSD")

        assert len(set(bucket.ids)) == 2

    async def test_explicit_sub_id_is_kept(self):
        bucket, _, _ = make_bucket()
        websocket = FakeWebSocket()
        bucket._websocket = websocket

        await bucket.subscribe("book", sub_id="mine", symbol="tBTCUSD")

        assert websocket.events[0]["subId"] == "mine"

    async def test_unsubscribe_targets_the_confirmed_channel_id(self, connect):
        bucket, _, _ = make_bucket()
        connect(FakeWebSocket(incoming=[subscribed_frame(42, "abc")]))
        bucket._websocket = FakeWebSocket()
        await bucket.subscribe("ticker", sub_id="abc", symbol="tBTCUSD")
        await bucket.start()

        websocket = FakeWebSocket()
        bucket._websocket = websocket
        await bucket.unsubscribe("abc")

        assert websocket.events == [{"event": "unsubscribe", "chanId": 42}]
        assert bucket.has("abc") is False

    async def test_unsubscribe_of_an_unknown_id_sends_nothing(self):
        bucket, _, _ = make_bucket()
        websocket = FakeWebSocket()
        bucket._websocket = websocket

        await bucket.unsubscribe("never-subscribed")

        assert websocket.sent == []

    async def test_resubscribe_reissues_the_same_sub_id(self, connect):
        """The caller's handle must survive a resubscribe.

        `resubscribe` drops the channel and asks again; if the new request
        minted a fresh subId, every consumer holding the old one would stop
        matching its own stream.
        """
        bucket, _, _ = make_bucket()
        connect(FakeWebSocket(incoming=[subscribed_frame(42, "abc")]))
        bucket._websocket = FakeWebSocket()
        await bucket.subscribe("ticker", sub_id="abc", symbol="tBTCUSD")
        await bucket.start()

        websocket = FakeWebSocket()
        bucket._websocket = websocket
        await bucket.resubscribe("abc")

        unsubscribe, subscribe = websocket.events
        assert unsubscribe == {"event": "unsubscribe", "chanId": 42}
        assert subscribe["subId"] == "abc"
        assert subscribe["channel"] == "ticker"
        assert subscribe["symbol"] == "tBTCUSD"

    async def test_close_forwards_code_and_reason(self):
        bucket, _, _ = make_bucket()
        websocket = FakeWebSocket()
        bucket._websocket = websocket

        await bucket.close(code=1001, reason="going away")

        assert websocket.closed == (1001, "going away")


class TestMessageLoop:
    async def test_channel_payload_reaches_the_handler(self, connect):
        bucket, _, captured = make_bucket()
        connect(
            FakeWebSocket(
                incoming=[
                    subscribed_frame(42, "abc"),
                    json.dumps([42, _TICKER_STREAM]),
                ]
            )
        )
        bucket._websocket = FakeWebSocket()
        await bucket.subscribe("ticker", sub_id="abc", symbol="tBTCUSD")
        await bucket.start()

        updates = [e for e in captured if e[0] == "t_ticker_update"]
        assert len(updates) == 1
        assert updates[0][2].bid == 0.1

    async def test_heartbeat_is_not_forwarded(self, connect):
        bucket, _, captured = make_bucket()
        connect(
            FakeWebSocket(
                incoming=[
                    subscribed_frame(42, "abc"),
                    json.dumps([42, "hb"]),
                ]
            )
        )
        bucket._websocket = FakeWebSocket()
        await bucket.subscribe("ticker", sub_id="abc", symbol="tBTCUSD")
        await bucket.start()

        assert [e[0] for e in captured] == ["subscribed"]

    async def test_payload_for_an_unknown_channel_is_dropped(self, connect):
        bucket, _, captured = make_bucket()
        connect(FakeWebSocket(incoming=[json.dumps([999, _TICKER_STREAM])]))

        await bucket.start()

        assert captured == []


class TestLosslessFinancialDecode:
    """The branch's decoder has to reach the websocket path, not just REST."""

    async def test_enabled_keeps_the_wire_token(self, connect):
        bucket, _, captured = make_bucket(lossless_financial_decode=True)
        connect(
            FakeWebSocket(
                incoming=[
                    subscribed_frame(42, "abc"),
                    "[42, [0.10000000000000000001, 1.0, 0.2, 2.0, 0.01, "
                    "0.1, 0.15, 100.0, 0.3, 0.05]]",
                ]
            )
        )
        bucket._websocket = FakeWebSocket()
        await bucket.subscribe("ticker", sub_id="abc", symbol="tBTCUSD")
        await bucket.start()

        bid = [e for e in captured if e[0] == "t_ticker_update"][0][2].bid
        assert isinstance(bid, FinancialTokenDecimal)
        assert bid.source_token == "0.10000000000000000001"

    async def test_disabled_by_default_yields_plain_floats(self, connect):
        """Opting in must stay opt-in: the default path is unchanged.

        A bucket that silently returned Decimals would break every existing
        consumer doing float arithmetic on a ticker.
        """
        bucket, _, captured = make_bucket()
        connect(
            FakeWebSocket(
                incoming=[
                    subscribed_frame(42, "abc"),
                    "[42, [0.10000000000000000001, 1.0, 0.2, 2.0, 0.01, "
                    "0.1, 0.15, 100.0, 0.3, 0.05]]",
                ]
            )
        )
        bucket._websocket = FakeWebSocket()
        await bucket.subscribe("ticker", sub_id="abc", symbol="tBTCUSD")
        await bucket.start()

        bid = [e for e in captured if e[0] == "t_ticker_update"][0][2].bid
        assert type(bid) is float


class TestStateRecoveryOnReconnect:
    async def test_checksum_flag_is_requested_on_every_connect(self, connect):
        bucket, _, _ = make_bucket()
        websocket = connect(FakeWebSocket())

        await bucket.start()

        assert websocket.events == [{"event": "conf", "flags": 131072}]

    async def test_pending_requests_are_replayed(self, connect):
        bucket, _, _ = make_bucket()
        bucket._websocket = FakeWebSocket()
        await bucket.subscribe("ticker", sub_id="abc", symbol="tBTCUSD")

        websocket = connect(FakeWebSocket())
        await bucket.start()

        replayed, conf = websocket.events
        assert replayed["subId"] == "abc"
        assert replayed["symbol"] == "tBTCUSD"
        assert conf == {"event": "conf", "flags": 131072}

    async def test_confirmed_subscriptions_are_re_requested(self, connect):
        """A reconnect invalidates every chan_id, so they are asked again.

        The old chan_id must not survive: the server is free to hand the
        same channel a different id, and a stale mapping would route the
        new stream to the wrong subscriber.
        """
        bucket, _, _ = make_bucket()
        connect(FakeWebSocket(incoming=[subscribed_frame(42, "abc")]))
        bucket._websocket = FakeWebSocket()
        await bucket.subscribe("ticker", sub_id="abc", symbol="tBTCUSD")
        await bucket.start()
        assert bucket.has("abc") is True

        websocket = connect(FakeWebSocket())
        await bucket.start()

        resubscribed, conf = websocket.events
        assert resubscribed["subId"] == "abc"
        assert resubscribed["channel"] == "ticker"
        assert conf == {"event": "conf", "flags": 131072}
        assert bucket.has("abc") is False, "pending again until reconfirmed"
        assert bucket.ids == ["abc"], "the caller's handle is preserved"
        assert bucket.count == 1, "recovery must not double-count the slot"


class TestWait:
    async def test_returns_once_the_socket_is_connected(self, connect):
        bucket, _, _ = make_bucket()
        connect(FakeWebSocket(hold=True))
        assert bucket.open is False

        task = asyncio.create_task(bucket.start())
        try:
            await asyncio.wait_for(bucket.wait(), timeout=2.0)
            assert bucket.open is True
        finally:
            task.cancel()
