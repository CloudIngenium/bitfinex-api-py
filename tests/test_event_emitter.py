import pytest

from bfxapi.websocket._event_emitter import BfxEventEmitter
from bfxapi.websocket.exceptions import UnknownEventError


class TestBfxEventEmitter:
    def test_register_known_event(self):
        ee = BfxEventEmitter(loop=None)

        @ee.on("t_ticker_update")
        def handler(*args):
            pass

        assert ee._has_listeners("t_ticker_update")

    def test_register_unknown_event_raises(self):
        ee = BfxEventEmitter(loop=None)

        with pytest.raises(UnknownEventError, match="unknown event"):

            @ee.on("totally_fake_event")
            def handler(*args):
                pass

    def test_has_listeners_false_when_no_listeners(self):
        ee = BfxEventEmitter(loop=None)
        assert ee._has_listeners("t_ticker_update") is False

    def test_once_per_connection_events(self):
        ee = BfxEventEmitter(loop=None)
        calls = []

        @ee.on("open")
        def handler():
            calls.append(1)

        # First emit should trigger
        ee.emit("open")
        # Second emit should be suppressed (once per connection)
        ee.emit("open")

        # Only called once because second emit is suppressed
        assert len(calls) == 1

    def test_once_per_subscription_events(self):
        ee = BfxEventEmitter(loop=None)
        calls = []

        @ee.on("subscribed")
        def handler(subscription):
            calls.append(subscription)

        sub1 = {"sub_id": "abc", "channel": "ticker"}
        sub2 = {"sub_id": "def", "channel": "trades"}

        # First emit for sub1
        ee.emit("subscribed", sub1)
        # Second emit for sub1 should be suppressed
        ee.emit("subscribed", sub1)
        # First emit for sub2 should go through
        ee.emit("subscribed", sub2)

        assert len(calls) == 2
        assert calls[0]["sub_id"] == "abc"
        assert calls[1]["sub_id"] == "def"

    def test_common_events_always_emit(self):
        ee = BfxEventEmitter(loop=None)
        calls = []

        @ee.on("t_ticker_update")
        def handler(sub, data):
            calls.append(data)

        sub = {"sub_id": "abc"}
        ee.emit("t_ticker_update", sub, "data1")
        ee.emit("t_ticker_update", sub, "data2")
        ee.emit("t_ticker_update", sub, "data3")

        assert len(calls) == 3

    def test_all_known_events_can_be_registered(self):
        ee = BfxEventEmitter(loop=None)
        for event in BfxEventEmitter._EVENTS:

            @ee.on(event)
            def handler(*args, **kwargs):
                pass

    def test_events_list_not_empty(self):
        assert len(BfxEventEmitter._EVENTS) > 0

    def test_on_returns_handler(self):
        ee = BfxEventEmitter(loop=None)

        def my_handler(*args):
            pass

        result = ee.on("order_new", my_handler)
        assert result is my_handler


class TestConnectionScopeReset:
    """`once per connection` has to mean the connection, not the client.

    The ledger backing both deduplication rules was only ever appended to,
    so a reconnected client kept suppressing `open`, `authenticated` and
    every snapshot while continuing to deliver the updates layered on top
    of them - a consumer applying deltas to state it is never told to
    replace.
    """

    def test_once_per_connection_events_are_re_armed(self):
        emitter = BfxEventEmitter()
        seen = []
        emitter.on("open", lambda: seen.append("open"))

        emitter.emit("open")
        emitter.emit("open")
        assert seen == ["open"], "still deduplicated within one connection"

        emitter.reset_connection_scope()
        emitter.emit("open")

        assert seen == ["open", "open"]

    def test_snapshots_are_re_announced_after_a_reset(self):
        emitter = BfxEventEmitter()
        seen = []
        emitter.on("wallet_snapshot", lambda payload: seen.append(payload))

        emitter.emit("wallet_snapshot", ["before"])
        emitter.emit("wallet_snapshot", ["ignored"])
        emitter.reset_connection_scope()
        emitter.emit("wallet_snapshot", ["after"])

        assert seen == [["before"], ["after"]]

    def test_once_per_subscription_events_are_re_armed(self):
        """A reconnect re-subscribes under the same sub_id.

        The server answers with a fresh `subscribed` and a fresh snapshot,
        so keying the ledger on sub_id alone would drop both and leave the
        consumer's book permanently stale.
        """
        emitter = BfxEventEmitter()
        seen = []
        emitter.on("subscribed", lambda payload: seen.append(payload))

        emitter.emit("subscribed", {"sub_id": "abc"})
        emitter.emit("subscribed", {"sub_id": "abc"})
        assert len(seen) == 1

        emitter.reset_connection_scope()
        emitter.emit("subscribed", {"sub_id": "abc"})

        assert len(seen) == 2

    def test_reset_on_a_fresh_emitter_changes_nothing(self):
        emitter = BfxEventEmitter()
        seen = []
        emitter.on("open", lambda: seen.append("open"))

        emitter.reset_connection_scope()
        emitter.emit("open")
        emitter.emit("open")

        assert seen == ["open"]
