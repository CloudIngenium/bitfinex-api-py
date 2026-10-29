import asyncio
import json
import time
import uuid
from typing import Any, cast

import websockets.asyncio.client
from pyee import EventEmitter
from websockets.exceptions import ConnectionClosedError
from websockets.frames import Close

from bfxapi._utils.financial_json import financial_decode_options
from bfxapi._utils.json_decoder import JSONDecoder
from bfxapi.websocket._connection import Connection
from bfxapi.websocket._handlers import PublicChannelsHandler
from bfxapi.websocket.subscriptions import Subscription

_CHECKSUM_FLAG_VALUE = 131_072

_SERVICE_RESTART = 1012

_RESTART_CODES: dict[int | None, str] = {
    20051: "Stop/Restart WebSocket Server (please reconnect).",
    20061: "Maintenance ended (please resubscribe all channels).",
}


def _subscribe_frame(
    channel: str, sub_id: str | None = None, **kwargs: Any
) -> dict[str, Any]:
    return {
        **kwargs,
        "event": "subscribe",
        "channel": channel,
        "subId": sub_id or str(uuid.uuid4()),
    }


def _strip(message: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    return {key: value for key, value in message.items() if key not in keys}


class BfxWebSocketBucket(Connection):
    __MAXIMUM_SUBSCRIPTIONS_AMOUNT = 25

    # Read by the client's bucket supervisor. `_closing` tells a close the
    # client asked for from one the server forced; `_connections` grows on
    # every socket that opened, and `_opened_at` says when the last one did,
    # so only a restart that then STAYED up resets the supervisor's backoff.
    _closing: bool = False

    _connections: int = 0

    _opened_at: float = 0.0

    def __init__(
        self,
        host: str,
        event_emitter: EventEmitter,
        *,
        lossless_financial_decode: bool = False,
        decimal_mode: bool | None = None,
    ) -> None:
        super().__init__(host)
        self.__decode_options = financial_decode_options(
            lossless_financial_decode
        )

        self.__event_emitter = event_emitter
        self.__pendings: list[dict[str, Any]] = []
        self.__subscriptions: dict[int, Subscription] = {}
        self.__cancellations: set[str] = set()

        self.__condition = asyncio.locks.Condition()

        self.__handler = PublicChannelsHandler(
            event_emitter=self.__event_emitter, decimal_mode=decimal_mode
        )

    @property
    def count(self) -> int:
        return len(self.__pendings) + len(self.__subscriptions)

    @property
    def is_full(self) -> bool:
        return self.count == BfxWebSocketBucket.__MAXIMUM_SUBSCRIPTIONS_AMOUNT

    @property
    def ids(self) -> list[str]:
        return [pending["subId"] for pending in self.__pendings] + [
            subscription["sub_id"]
            for subscription in self.__subscriptions.values()
        ]

    async def start(self) -> None:
        # A bucket closed on purpose stays closed: only its owner (the
        # client, when it starts a new session) clears `_closing`. Clearing it
        # here let a supervisor that woke after `close()` bring it back.
        if self._closing:
            return

        async with websockets.asyncio.client.connect(self._host) as websocket:
            self._websocket = websocket

            if self._closing:
                # close() arrived during the handshake.
                return

            self._connections += 1

            self._opened_at = time.monotonic()

            await self.__recover_state()

            # Every waiter: the client's readiness wait and any subscribe()
            # waiting for this bucket can be waiting at once.
            async with self.__condition:
                self.__condition.notify_all()

            async for _message in self._websocket:
                message = json.loads(
                    _message, cls=JSONDecoder, **self.__decode_options
                )

                if isinstance(message, dict):
                    if message["event"] == "subscribed":
                        await self.__on_subscribed(message)
                    elif message["event"] == "info" and (
                        reason := _RESTART_CODES.get(message.get("code"))
                    ):
                        # 20051 (server restart) and 20061 (maintenance end,
                        # after which Bitfinex advises resubscribing every
                        # channel): end this socket as a restart, so the
                        # client reconnects it and __recover_state
                        # resubscribes everything on a fresh connection.
                        raise ConnectionClosedError(
                            rcvd=Close(_SERVICE_RESTART, reason), sent=None
                        )

                if isinstance(message, list):
                    if (
                        (chan_id := cast(int, message[0]))
                        and (subscription := self.__subscriptions.get(chan_id))
                        and (message[1] != Connection._HEARTBEAT)
                    ):
                        self.__handler.handle(subscription, message[1:])

    async def __on_subscribed(self, message: dict[str, Any]) -> None:
        chan_id = cast(int, message["chan_id"])

        sub_id = cast(str, message["sub_id"])

        self.__pendings = [
            pending for pending in self.__pendings if pending["subId"] != sub_id
        ]

        if sub_id in self.__cancellations:
            # Unsubscribed while still pending. The subscribe frame was
            # already on the wire, so the server opened the channel anyway -
            # and this confirmation is the first moment a chan_id exists to
            # close it with. Without this the channel streams forever into a
            # consumer that believes it cancelled the subscription.
            self.__cancellations.discard(sub_id)

            await self._websocket.send(
                message=json.dumps({"event": "unsubscribe", "chanId": chan_id})
            )

            return

        subscription = cast(
            Subscription,
            _strip(message, keys=["chan_id", "event", "pair", "currency"]),
        )

        self.__subscriptions[chan_id] = subscription

        self.__event_emitter.emit("subscribed", subscription)

    async def __recover_state(self) -> None:
        # A cancellation waits for a confirmation that can only arrive on the
        # socket that has just gone; the pending it referred to was dropped
        # when it was recorded, so nothing re-requests it either.
        self.__cancellations.clear()

        # Every confirmed subscription becomes pending BEFORE anything is
        # sent. The server can hang up mid-recovery (20051, maintenance): a
        # subscription popped and not yet re-sent was lost for good, while a
        # pending one is re-sent on the next restart until it is confirmed.
        for chan_id in list(self.__subscriptions.keys()):
            subscription: dict[str, Any] = dict(
                self.__subscriptions.pop(chan_id)
            )

            self.__pendings.append(
                _subscribe_frame(
                    cast(str, subscription.pop("channel")),
                    cast(str, subscription.pop("sub_id")),
                    **subscription,
                )
            )

        for pending in list(self.__pendings):
            await self._websocket.send(message=json.dumps(pending))

        await self.__set_config([_CHECKSUM_FLAG_VALUE])

    async def __set_config(self, flags: list[int]) -> None:
        await self._websocket.send(
            json.dumps({"event": "conf", "flags": sum(flags)})
        )

    @Connection._require_websocket_connection  # type: ignore[arg-type]
    async def subscribe(
        self, channel: str, sub_id: str | None = None, **kwargs: Any
    ) -> None:
        subscription = _subscribe_frame(channel, sub_id, **kwargs)

        self.__pendings.append(subscription)

        await self._websocket.send(message=json.dumps(subscription))

    @Connection._require_websocket_connection
    async def unsubscribe(self, sub_id: str) -> None:
        for pending in self.__pendings:
            if pending["subId"] == sub_id:
                # No chan_id exists yet, so there is nothing to unsubscribe
                # from: drop the request here and close the channel the moment
                # the server confirms it (see __on_subscribed).
                self.__pendings.remove(pending)

                self.__cancellations.add(sub_id)

                return

        for chan_id, subscription in list(self.__subscriptions.items()):
            if subscription["sub_id"] == sub_id:
                unsubscription = {"event": "unsubscribe", "chanId": chan_id}

                del self.__subscriptions[chan_id]

                await self._websocket.send(message=json.dumps(unsubscription))

    @Connection._require_websocket_connection
    async def resubscribe(self, sub_id: str) -> None:
        # Only confirmed subscriptions are reissued. A pending one is already
        # being established and its frame has not been answered yet: sending
        # it again would open two channels under a single sub_id.
        for subscription in list(self.__subscriptions.values()):
            if subscription["sub_id"] == sub_id:
                await self.unsubscribe(sub_id)

                await self.subscribe(**subscription)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        # Works in any state: a bucket between sockets (its supervisor asleep,
        # or mid-handshake) must still be told to stay down.
        self._closing = True

        if self.open:
            await self._websocket.close(code, reason)

    def has(self, sub_id: str) -> bool:
        # `ids` counts pendings, so a duplicate `subscribe` is refused from the
        # instant the frame is sent. `has` counted only confirmed ones, which
        # left a sub_id that was simultaneously "taken" (SubIdError) and
        # "unknown" (UnknownSubscriptionError) - unusable and uncancellable.
        # The window is not a few milliseconds either: __recover_state moves
        # EVERY subscription back to pending, so after each reconnection the
        # whole set is unreachable until the server has confirmed it.
        if any(pending["subId"] == sub_id for pending in self.__pendings):
            return True

        return any(
            subscription["sub_id"] == sub_id
            for subscription in self.__subscriptions.values()
        )

    def _uptime(self) -> float:
        """Seconds since the last socket opened (or, once it is gone, until
        now): what the supervisor reads right after it dropped."""
        return time.monotonic() - self._opened_at

    async def wait(self) -> None:
        async with self.__condition:
            await self.__condition.wait_for(lambda: self.open)
