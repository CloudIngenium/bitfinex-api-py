import asyncio
import contextlib
import json
import random
import time
import traceback
from asyncio import Task
from collections.abc import Callable
from datetime import datetime
from logging import Logger
from typing import Any, TypedDict

import websockets.asyncio.client
import websockets.frames
from pyee import Handler
from websockets.exceptions import (
    ConnectionClosed,
    ConnectionClosedError,
    InvalidMessage,
    InvalidStatus,
)

from bfxapi._utils.financial_json import financial_decode_options
from bfxapi._utils.json_encoder import JSONEncoder
from bfxapi.exceptions import InvalidCredentialError
from bfxapi.websocket._connection import Connection
from bfxapi.websocket._event_emitter import BfxEventEmitter
from bfxapi.websocket._handlers import AuthEventsHandler
from bfxapi.websocket.exceptions import (
    ReconnectionTimeoutError,
    SubIdError,
    UnknownChannelError,
    UnknownSubscriptionError,
    VersionMismatchError,
)

from .bfx_websocket_bucket import BfxWebSocketBucket
from .bfx_websocket_inputs import BfxWebSocketInputs


class _Credentials(TypedDict):
    api_key: str
    api_secret: str
    filters: list[str] | None


class _Reconnection(TypedDict):
    attempts: int
    reason: str
    timestamp: datetime
    # `_clock()` reading after which this outage gives up; `None` retries
    # forever. Each outage owns its deadline, so a recovered outage can never
    # cut a later one short.
    deadline: float | None


_DEFAULT_LOGGER = Logger("bfxapi.websocket._client", level=0)

# Seam for the outage deadline: monotonic, so a wall-clock jump neither ends
# an outage early nor extends it.
_clock = time.monotonic

# Close codes that mean "the connection is gone, come back": 1006 (abnormal
# closure) and 1012 (service restart, which Bitfinex announces with 20051).
_LOST_CONNECTION_CODES = (1006, 1012)

_GOING_AWAY = 1001

# HTTP statuses a reconnecting client waits out: a request timeout, rate
# limiting, and the 5xx family Bitfinex answers with during maintenance.
_RETRYABLE_STATUSES = frozenset({408, 429})

# What a bucket socket dies of when the network, not the code, failed.
_EXPECTED_DROPS = (ConnectionClosed, OSError, InvalidMessage, InvalidStatus)


def _is_retryable_status(error: InvalidStatus) -> bool:
    status = error.response.status_code

    return status in _RETRYABLE_STATUSES or 500 <= status <= 599


class _Delay:
    __BACKOFF_MIN = 1.92

    __BACKOFF_MAX = 60.0

    def __init__(self, backoff_factor: float) -> None:
        self.__backoff_factor = backoff_factor
        self.__backoff_delay = _Delay.__BACKOFF_MIN
        self.__initial_delay = random.uniform(1.0, 5.0)

    def next(self) -> float:
        _backoff_delay = self.peek()
        __backoff_delay = _backoff_delay * self.__backoff_factor
        self.__backoff_delay = min(__backoff_delay, _Delay.__BACKOFF_MAX)

        return _backoff_delay

    def peek(self) -> float:
        return (
            (self.__backoff_delay == _Delay.__BACKOFF_MIN)
            and self.__initial_delay
            or self.__backoff_delay
        )

    def reset(self) -> None:
        self.__backoff_delay = _Delay.__BACKOFF_MIN


class BfxWebSocketClient(Connection):
    def __init__(
        self,
        host: str,
        *,
        credentials: _Credentials | None = None,
        timeout: int | None = 60 * 15,
        logger: Logger = _DEFAULT_LOGGER,
        lossless_financial_decode: bool = False,
    ) -> None:
        super().__init__(host)
        self.__lossless_financial_decode = lossless_financial_decode
        self.__decode_options = financial_decode_options(
            lossless_financial_decode
        )

        self.__credentials, self.__timeout, self.__logger = (
            credentials,
            timeout,
            logger,
        )

        self.__buckets: dict[BfxWebSocketBucket, Task[None] | None] = {}

        self.__reconnection: _Reconnection | None = None

        # Set by close(): a close the owner asked for is never mistaken for a
        # lost connection, even when its handshake times out without a frame.
        self.__closing = False

        self.__event_emitter = BfxEventEmitter(loop=None)

        self.__handler = AuthEventsHandler(event_emitter=self.__event_emitter)

        self.__inputs = BfxWebSocketInputs(
            handle_websocket_input=self.__handle_websocket_input
        )

        @self.__event_emitter.listens_to("error")
        def error(exception: Exception) -> None:
            header = f"{type(exception).__name__}: {str(exception)}"

            stack_trace = traceback.format_exception(
                type(exception), exception, exception.__traceback__
            )

            self.__logger.critical(f"{header}\n" + "".join(stack_trace)[:-1])

    @property
    def inputs(self) -> BfxWebSocketInputs:
        return self.__inputs

    def run(self) -> None:
        return asyncio.get_event_loop().run_until_complete(self.start())

    async def start(self) -> None:
        _delay = _Delay(backoff_factor=1.618)

        self.__closing = False

        while True:
            if self.__reconnection:
                _sleep = float(int(_delay.next()))

                # Never sleep past the deadline, and never attempt after it.
                if (left := self.__time_left()) is not None:
                    _sleep = min(_sleep, left)

                await asyncio.sleep(_sleep)

                self.__time_left()

            try:
                await self.__connect()
            except (
                ConnectionClosedError,
                InvalidStatus,
                InvalidMessage,
                OSError,
            ) as error:
                await self.__stop_buckets()

                if isinstance(
                    error, ConnectionClosedError
                ) and self.__is_lost_connection(error):
                    if error.rcvd and error.rcvd.code == 1012:
                        self.__logger.warning(
                            "WSS server is restarting: all "
                            "clients need to reconnect (server sent 20051)."
                        )
                    else:
                        self.__logger.error(
                            "Connection lost: trying to reconnect..."
                        )

                    self.__begin_outage(
                        error.rcvd.reason if error.rcvd else "connection lost",
                        _delay,
                    )
                elif self.__reconnection and self.__is_retryable(error):
                    self.__logger.warning(
                        "Reconnection attempt unsuccessful (no."
                        f"{self.__reconnection['attempts']}): next attempt in "
                        f"~{int(_delay.peek())}.0s."
                    )

                    self.__logger.info(
                        f"The client has been offline for "
                        f"{datetime.now() - self.__reconnection['timestamp']}."
                    )

                    self.__reconnection["attempts"] += 1
                else:
                    raise error

                continue

            if not self.__closing and self._websocket.close_code == _GOING_AWAY:
                # The server is going away (an edge restart or a deploy) and
                # the client did not ask for it: come back, don't hang up.
                await self.__stop_buckets()

                self.__logger.warning(
                    "WSS server is going away: trying to reconnect..."
                )

                self.__begin_outage(
                    self._websocket.close_reason or "going away", _delay
                )

                continue

            close_code = None
            close_reason = ""
            if self._websocket.close_code is not None:
                close_code = self._websocket.close_code
            if self._websocket.close_reason is not None:
                close_reason = self._websocket.close_reason

            self.__event_emitter.emit(
                "disconnected",
                close_code,
                close_reason,
            )

            break

    def __is_lost_connection(self, error: ConnectionClosedError) -> bool:
        # websockets 16 reports a dead TCP connection (and a keepalive
        # timeout) with no received close frame at all: RFC 6455 forbids
        # sending 1006, so `rcvd is None` IS the lost connection. A close the
        # client asked for can end the same way, hence `__closing`.
        if self.__closing:
            return False

        return error.rcvd is None or error.rcvd.code in _LOST_CONNECTION_CODES

    @staticmethod
    def __is_retryable(error: Exception) -> bool:
        if isinstance(error, InvalidStatus):
            return _is_retryable_status(error)

        return isinstance(error, (OSError, InvalidMessage))

    def __begin_outage(self, reason: str, delay: _Delay) -> None:
        # A lost connection can only follow a connection that came up (which
        # cleared the previous outage), so every one starts a fresh outage
        # with its own deadline.
        #
        # `None` is the documented way to retry forever; a falsy check also
        # swallowed `timeout=0`, turning "give up at once" into "never give
        # up".
        deadline = None if self.__timeout is None else _clock() + self.__timeout

        self.__reconnection = {
            "attempts": 1,
            "reason": reason,
            "timestamp": datetime.now(),
            "deadline": deadline,
        }

        self._authentication = False

        delay.reset()

    def __time_left(self) -> float | None:
        """Seconds left in the current outage, or raise once it is over."""
        assert self.__reconnection is not None

        if (deadline := self.__reconnection["deadline"]) is None:
            return None

        if (left := deadline - _clock()) <= 0:
            raise ReconnectionTimeoutError(
                "Connection has been offline for too long "
                f"without being able to reconnect (timeout: {self.__timeout}s)."
            )

        return left

    async def __stop_buckets(self) -> None:
        async def _cancel(task: Task[None]) -> None:
            task.cancel()

            with contextlib.suppress(asyncio.CancelledError):
                await task

        for bucket in self.__buckets:
            if task := self.__buckets[bucket]:
                self.__buckets[bucket] = None

                await _cancel(task)

    async def __supervise(self, bucket: BfxWebSocketBucket) -> None:
        """Run one bucket socket and bring it back whenever it drops.

        A bucket is its own TCP connection: it can die while the client's
        socket stays up, and nothing used to notice.
        """
        _delay = _Delay(backoff_factor=1.618)

        connections = bucket._connections

        while True:
            try:
                await bucket.start()
            except _EXPECTED_DROPS as error:
                self.__logger.warning(
                    f"Bucket connection dropped ({type(error).__name__}: "
                    f"{error}): reconnecting..."
                )
            except Exception as error:
                self.__event_emitter.emit("error", error)

            # Removed by unsubscribe, closed on purpose, or the client is
            # down itself: its own reconnect restarts every bucket.
            if bucket not in self.__buckets or bucket._closing or not self.open:
                return

            # A fresh socket must re-announce every subscription it confirms.
            self.__event_emitter.reset_subscription_scope(bucket.ids)

            if bucket._connections > connections:
                connections = bucket._connections

                _delay.reset()

            await asyncio.sleep(_delay.next())

    async def __connect(self) -> None:
        async with websockets.asyncio.client.connect(self._host) as websocket:
            # A new socket is a new connection scope: without this the
            # once-per-connection events (open, authenticated, and every
            # snapshot) stay latched from the previous one, and a recovered
            # client silently never re-announces its state.
            self.__event_emitter.reset_connection_scope()

            if self.__reconnection:
                self.__logger.warning(
                    "Reconnection attempt successful (no."
                    f"{self.__reconnection['attempts']}): recovering "
                    "connection state..."
                )

                self.__reconnection = None

            self._websocket = websocket

            for bucket in self.__buckets:
                self.__buckets[bucket] = asyncio.create_task(
                    self.__supervise(bucket)
                )

            if len(self.__buckets) == 0 or (
                await asyncio.gather(
                    *[bucket.wait() for bucket in self.__buckets]
                )
            ):
                self.__event_emitter.emit("open")

            if self.__credentials:
                authentication = Connection._get_authentication_message(
                    **self.__credentials
                )

                await self._websocket.send(authentication)

            async for _message in self._websocket:
                message = json.loads(_message, **self.__decode_options)

                if isinstance(message, dict):
                    if message["event"] == "info" and "version" in message:
                        if message["version"] != 2:
                            raise VersionMismatchError(
                                "Mismatch between the client and the server version: "
                                "please update bitfinex-api-py to the latest version "
                                f"to resolve this error (client version: 2, server "
                                f"version: {message['version']})."
                            )
                    elif (
                        message["event"] == "info"
                        and message.get("code") == 20051
                    ):
                        rcvd = websockets.frames.Close(
                            1012,
                            "Stop/Restart WebSocket Server (please reconnect).",
                        )

                        raise ConnectionClosedError(rcvd=rcvd, sent=None)
                    elif (
                        message["event"] == "info"
                        and message.get("code") == 20060
                    ):
                        self.__event_emitter.emit("maintenance_start", message)
                    elif (
                        message["event"] == "info"
                        and message.get("code") == 20061
                    ):
                        self.__event_emitter.emit("maintenance_end", message)
                    elif message["event"] == "auth":
                        if message["status"] != "OK":
                            raise InvalidCredentialError(
                                "Can't authenticate with given API-KEY and API-SECRET."
                            )

                        self.__event_emitter.emit("authenticated", message)

                        self._authentication = True

                if (
                    isinstance(message, list)
                    and message[0] == 0
                    and message[1] != Connection._HEARTBEAT
                ):
                    self.__handler.handle(message[1], message[2])

    async def __new_bucket(self) -> BfxWebSocketBucket:
        bucket = BfxWebSocketBucket(
            self._host,
            self.__event_emitter,
            lossless_financial_decode=self.__lossless_financial_decode,
        )

        self.__buckets[bucket] = asyncio.create_task(self.__supervise(bucket))

        await bucket.wait()

        return bucket

    @Connection._require_websocket_connection  # type: ignore[arg-type]
    async def subscribe(
        self, channel: str, sub_id: str | None = None, **kwargs: Any
    ) -> None:
        if channel not in ["ticker", "trades", "book", "candles", "status"]:
            raise UnknownChannelError(
                "Available channels are: ticker, trades, book, candles and status."
            )

        for bucket in self.__buckets:
            if sub_id in bucket.ids:
                raise SubIdError("sub_id must be unique for all subscriptions.")

        for bucket in self.__buckets:
            if not bucket.is_full:
                return await bucket.subscribe(channel, sub_id, **kwargs)

        bucket = await self.__new_bucket()

        return await bucket.subscribe(channel, sub_id, **kwargs)

    @Connection._require_websocket_connection
    async def unsubscribe(self, sub_id: str) -> None:
        for bucket in self.__buckets:
            if bucket.has(sub_id):
                if bucket.count == 1:
                    del self.__buckets[bucket]

                    return await bucket.close(code=1001, reason="Going Away")

                return await bucket.unsubscribe(sub_id)

        raise UnknownSubscriptionError(
            f"Unable to find a subscription with sub_id <{sub_id}>."
        )

    @Connection._require_websocket_connection
    async def resubscribe(self, sub_id: str) -> None:
        for bucket in self.__buckets:
            if bucket.has(sub_id):
                return await bucket.resubscribe(sub_id)

        raise UnknownSubscriptionError(
            f"Unable to find a subscription with sub_id <{sub_id}>."
        )

    @Connection._require_websocket_connection
    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.__closing = True

        for bucket in self.__buckets:
            await bucket.close(code=code, reason=reason)

        if self.open:
            await self._websocket.close(code=code, reason=reason)

    @Connection._require_websocket_authentication  # type: ignore[arg-type]
    async def notify(
        self, info: Any, message_id: int | None = None, **kwargs: Any
    ) -> None:
        await self._websocket.send(
            json.dumps(
                [
                    0,
                    "n",
                    message_id,
                    {"type": "ucm-test", "info": info, **kwargs},
                ]
            )
        )

    @Connection._require_websocket_authentication
    async def __handle_websocket_input(self, event: str, data: Any) -> None:
        await self._websocket.send(
            json.dumps([0, event, None, data], cls=JSONEncoder)
        )

    def on(
        self, event: str, callback: Handler | None = None
    ) -> Handler | Callable[[Handler], Handler]:
        if callback is None:
            return self.__event_emitter.on(event)
        return self.__event_emitter.on(event, callback)
