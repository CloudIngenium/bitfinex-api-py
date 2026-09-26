"""Reconnection behaviour that no test covered until three silent bugs were found.

Every bug here shares a shape: the socket stays healthy, data keeps arriving, and
nothing raises. The consumer simply acts on state that is quietly wrong. That is
why the suite passed on the broken code — these are not crashes to catch but
absences to assert, and an absence has to be named before a test can see it.
"""

import json
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest

from bfxapi.websocket._client.bfx_websocket_bucket import BfxWebSocketBucket
from bfxapi.websocket._event_emitter.bfx_event_emitter import BfxEventEmitter


def _open_bucket() -> tuple[BfxWebSocketBucket, AsyncMock, MagicMock]:
    """A bucket whose `open` is True and whose sends are captured, not sent."""
    emitter = MagicMock()
    bucket = BfxWebSocketBucket("wss://example.invalid", emitter)

    websocket = AsyncMock()
    state = MagicMock()
    state.name = "OPEN"
    type(websocket).state = PropertyMock(return_value=state)
    bucket._websocket = websocket

    return bucket, websocket, emitter


def _frames(websocket: AsyncMock) -> list[dict]:
    """Every JSON frame handed to the socket, in order."""
    out = []
    for call in websocket.send.call_args_list:
        raw = call.kwargs.get("message", call.args[0] if call.args else None)
        if raw is not None:
            out.append(json.loads(raw))
    return out


class TestOncePerConnectionScope:
    """`open`, `authenticated` and every snapshot must fire again after a reconnect.

    The ledgers backing the deduplication are only ever appended to, so without
    an explicit reset the scope is once per CLIENT, not once per connection. The
    failure is invisible: updates keep being delivered, so a consumer goes on
    applying them to a snapshot it is never told to replace.
    """

    def test_once_per_connection_event_is_suppressed_within_a_connection(self):
        emitter = BfxEventEmitter()
        seen = []
        emitter.on("open", lambda: seen.append(1))

        emitter.emit("open")
        emitter.emit("open")

        assert len(seen) == 1, (
            "deduplication within one connection is the point"
        )

    def test_reset_re_arms_a_once_per_connection_event(self):
        emitter = BfxEventEmitter()
        seen = []
        emitter.on("open", lambda: seen.append(1))

        emitter.emit("open")
        emitter.reset_connection_scope()
        emitter.emit("open")

        assert len(seen) == 2, (
            "after a reconnection the client never re-announced `open`"
        )

    def test_reset_re_arms_every_snapshot(self):
        # Named individually because a consumer that misses ONE of these is not
        # partially degraded: it holds a stale view of that entire domain.
        emitter = BfxEventEmitter()
        snapshots = [
            "order_snapshot",
            "position_snapshot",
            "funding_offer_snapshot",
            "funding_credit_snapshot",
            "funding_loan_snapshot",
            "wallet_snapshot",
        ]
        seen = {name: 0 for name in snapshots}
        for name in snapshots:
            emitter.on(
                name, lambda *_a, _n=name: seen.__setitem__(_n, seen[_n] + 1)
            )

        for name in snapshots:
            emitter.emit(name, [])
        emitter.reset_connection_scope()
        for name in snapshots:
            emitter.emit(name, [])

        assert all(count == 2 for count in seen.values()), seen

    def test_reset_re_arms_per_subscription_events(self):
        # Cleared alongside the per-connection ledger: the reconnection
        # re-subscribes under the same sub_id, and the server answers with a
        # fresh `subscribed` plus fresh snapshots.
        emitter = BfxEventEmitter()
        seen = []
        emitter.on("subscribed", lambda sub: seen.append(sub))

        emitter.emit("subscribed", {"sub_id": "abc"})
        emitter.emit("subscribed", {"sub_id": "abc"})
        assert len(seen) == 1

        emitter.reset_connection_scope()
        emitter.emit("subscribed", {"sub_id": "abc"})
        assert len(seen) == 2, (
            "a re-subscribed channel never re-announced itself after a reconnect"
        )


class TestPendingSubscriptionIsReachable:
    """A sub_id must not be simultaneously taken and unknown.

    `ids` counts pendings, so a duplicate subscribe is refused the instant the
    frame goes out. When `has()` counted only CONFIRMED subscriptions, the gap
    between those two moments left a sub_id that raised SubIdError on re-subscribe
    and UnknownSubscriptionError on unsubscribe. `__recover_state` moves every
    subscription back to pending, so after each reconnect the whole set sits in
    that gap until the server confirms them one by one.
    """

    @pytest.mark.asyncio
    async def test_has_is_true_while_the_request_is_pending(self):
        bucket, _ws, _em = _open_bucket()

        await bucket.subscribe(
            channel="ticker", sub_id="pending-1", symbol="tBTCUSD"
        )

        assert "pending-1" in bucket.ids, "ids already counts it"
        assert bucket.has("pending-1") is True, (
            "has() disagreed with ids(): the sub_id is taken but unreachable"
        )

    @pytest.mark.asyncio
    async def test_has_is_true_once_confirmed(self):
        bucket, _ws, _em = _open_bucket()

        await bucket.subscribe(channel="ticker", sub_id="s-1", symbol="tBTCUSD")
        await bucket._BfxWebSocketBucket__on_subscribed(
            {
                "event": "subscribed",
                "chan_id": 7,
                "sub_id": "s-1",
                "channel": "ticker",
            }
        )

        assert bucket.has("s-1") is True

    @pytest.mark.asyncio
    async def test_has_is_false_for_an_unknown_sub_id(self):
        bucket, _ws, _em = _open_bucket()
        assert bucket.has("never-seen") is False


class TestCancellingAPendingSubscription:
    """Unsubscribing before confirmation must still close the channel.

    The subscribe frame is already on the wire, so the server opens the channel
    regardless. The confirmation is the first moment a chan_id exists to close it
    with — and if nothing does, that channel streams forever into a consumer that
    believes it cancelled.
    """

    @pytest.mark.asyncio
    async def test_unsubscribe_while_pending_does_not_raise_or_send(self):
        bucket, ws, _em = _open_bucket()
        await bucket.subscribe(channel="ticker", sub_id="p-1", symbol="tBTCUSD")
        ws.send.reset_mock()

        await bucket.unsubscribe("p-1")

        assert _frames(ws) == [], "there is no chan_id yet to unsubscribe from"
        assert "p-1" not in bucket.ids, "the pending request was withdrawn"

    @pytest.mark.asyncio
    async def test_confirmation_of_a_cancelled_subscription_closes_the_channel(
        self,
    ):
        bucket, ws, emitter = _open_bucket()
        await bucket.subscribe(channel="ticker", sub_id="p-2", symbol="tBTCUSD")
        await bucket.unsubscribe("p-2")
        ws.send.reset_mock()

        await bucket._BfxWebSocketBucket__on_subscribed(
            {
                "event": "subscribed",
                "chan_id": 42,
                "sub_id": "p-2",
                "channel": "ticker",
            }
        )

        assert _frames(ws) == [{"event": "unsubscribe", "chanId": 42}], (
            "the server opened the channel and nothing closed it"
        )
        assert bucket.has("p-2") is False, (
            "a cancelled subscription became live"
        )
        emitter.emit.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_cancellation_is_consumed_once(self):
        # Otherwise a later subscription reusing the sub_id would be cancelled by
        # a ghost from the previous one.
        bucket, ws, _em = _open_bucket()
        await bucket.subscribe(channel="ticker", sub_id="p-3", symbol="tBTCUSD")
        await bucket.unsubscribe("p-3")
        await bucket._BfxWebSocketBucket__on_subscribed(
            {
                "event": "subscribed",
                "chan_id": 9,
                "sub_id": "p-3",
                "channel": "ticker",
            }
        )
        ws.send.reset_mock()

        await bucket.subscribe(channel="ticker", sub_id="p-3", symbol="tBTCUSD")
        await bucket._BfxWebSocketBucket__on_subscribed(
            {
                "event": "subscribed",
                "chan_id": 10,
                "sub_id": "p-3",
                "channel": "ticker",
            }
        )

        assert bucket.has("p-3") is True, (
            "a stale cancellation killed a fresh subscription"
        )

    @pytest.mark.asyncio
    async def test_recovering_state_drops_pending_cancellations(self):
        # A cancellation waits on a confirmation that can only arrive on the
        # socket that just went away, and the pending it referred to was already
        # dropped, so nothing re-requests it either. Keeping it would cancel an
        # unrelated future subscription that reused the sub_id.
        bucket, ws, _em = _open_bucket()
        await bucket.subscribe(channel="ticker", sub_id="p-4", symbol="tBTCUSD")
        await bucket.unsubscribe("p-4")

        await bucket._BfxWebSocketBucket__recover_state()
        ws.send.reset_mock()

        await bucket.subscribe(channel="ticker", sub_id="p-4", symbol="tBTCUSD")
        await bucket._BfxWebSocketBucket__on_subscribed(
            {
                "event": "subscribed",
                "chan_id": 11,
                "sub_id": "p-4",
                "channel": "ticker",
            }
        )

        assert bucket.has("p-4") is True
        assert {"event": "unsubscribe", "chanId": 11} not in _frames(ws)


class TestConfirmedSubscriptionStillUnsubscribes:
    """The pending branch must not shadow the ordinary path."""

    @pytest.mark.asyncio
    async def test_unsubscribe_after_confirmation_sends_the_frame(self):
        bucket, ws, _em = _open_bucket()
        await bucket.subscribe(channel="ticker", sub_id="c-1", symbol="tBTCUSD")
        await bucket._BfxWebSocketBucket__on_subscribed(
            {
                "event": "subscribed",
                "chan_id": 5,
                "sub_id": "c-1",
                "channel": "ticker",
            }
        )
        ws.send.reset_mock()

        await bucket.unsubscribe("c-1")

        assert _frames(ws) == [{"event": "unsubscribe", "chanId": 5}]
        assert bucket.has("c-1") is False


class _StopTheLoop(Exception):
    """Escapes start()'s `while True` once the branch under test has run."""


async def _drive_reconnection(monkeypatch, timeout):
    """Run start() far enough to hit the reconnect branch, and report call_later.

    __connect raises a 1006 close on the first attempt — the branch that arms the
    reconnection timeout — and a sentinel on the second, so the loop exits instead
    of retrying forever. The backoff is flattened so the test does not actually
    sleep through it.
    """
    from websockets.exceptions import ConnectionClosedError
    from websockets.frames import Close

    from bfxapi.websocket._client import bfx_websocket_client as mod

    client = mod.BfxWebSocketClient("wss://example.invalid", timeout=timeout)

    attempts = {"n": 0}

    async def _connect(_self=None):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ConnectionClosedError(Close(1006, "lost"), None)
        raise _StopTheLoop

    monkeypatch.setattr(
        client, "_BfxWebSocketClient__connect", _connect, raising=False
    )
    monkeypatch.setattr(mod._Delay, "next", lambda self: 0.0)

    scheduled = []

    class _Loop:
        def call_later(self, delay, callback):
            scheduled.append(delay)
            return MagicMock()

    monkeypatch.setattr(mod.asyncio, "get_event_loop", lambda: _Loop())

    with pytest.raises(_StopTheLoop):
        await client.start()

    return scheduled


class TestReconnectionTimeoutIsArmed:
    """`timeout=0` must mean "give up at once", not "never give up".

    `None` is the documented way to retry forever. A falsy check swallowed `0`
    as well, so a client configured to fail fast silently reconnected forever —
    the one configuration whose whole purpose is to stop.
    """

    @pytest.mark.asyncio
    async def test_zero_timeout_is_armed(self, monkeypatch):
        scheduled = await _drive_reconnection(monkeypatch, timeout=0)

        assert scheduled == [0], (
            "timeout=0 never armed the deadline: the client reconnects forever"
        )

    @pytest.mark.asyncio
    async def test_positive_timeout_is_armed(self, monkeypatch):
        scheduled = await _drive_reconnection(monkeypatch, timeout=30)

        assert scheduled == [30]

    @pytest.mark.asyncio
    async def test_none_timeout_retries_forever(self, monkeypatch):
        # The documented opt-out, and the reason the check cannot simply be
        # truthiness-free: None must NOT arm a deadline.
        scheduled = await _drive_reconnection(monkeypatch, timeout=None)

        assert scheduled == []
