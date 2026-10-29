import asyncio
import contextlib
import inspect
import json
import random
import time
import traceback
from asyncio import Task
from collections.abc import Callable, Coroutine
from datetime import datetime
from logging import Logger
from typing import Any, NotRequired, TypedDict

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
    BucketRestartLimitError,
    ConnectionNotOpen,
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
    dms: NotRequired[int]


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

# How long a (re)connected client waits for its subscription buckets before it
# opens without them. A bucket that never comes back must not starve the main
# socket: its supervisor keeps retrying in the background.
_BUCKET_READY_TIMEOUT = 15.0

# How long a connection (the client's or a bucket's) must stay up before its
# drop resets the reconnect backoff. A server that accepts and then hangs up
# at once (a flapping edge) was otherwise redialled at the minimum delay
# forever.
_STABLE_CONNECTION_TIME = 30.0

# Fast, deterministic failures (an unexpected exception, a refusal the server
# means) after which a bucket supervisor gives up and says so on "error". A
# network drop never counts: a reconnect is the cure for those. The bucket
# gets a fresh supervisor when the client itself reconnects.
_BUCKET_RESTART_LIMIT = 5

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
        decimal_mode: bool | None = None,
    ) -> None:
        super().__init__(host)
        self.__lossless_financial_decode = lossless_financial_decode
        self.__decimal_mode = decimal_mode
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

        # What close() asked for, reported by "disconnected" when the client
        # ends without a close handshake of its own (between sockets, or a
        # close that got no frame back).
        self.__close_frame: tuple[int, str] = (1000, "")

        # Set by close(): wakes a reconnect backoff so it ends at once.
        self.__close_requested = asyncio.Event()

        # Bumped by every close(). start() reads it when it is CALLED, so a
        # close that lands before a scheduled start() runs still wins.
        self.__close_epoch = 0

        # `_clock()` reading when the current socket opened: only a socket
        # that stayed up resets the reconnect backoff.
        self.__connected_at: float | None = None

        self.__event_emitter = BfxEventEmitter(loop=None)

        self.__handler = AuthEventsHandler(
            event_emitter=self.__event_emitter, decimal_mode=decimal_mode
        )

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

    @inspect.markcoroutinefunction
    def start(self) -> Coroutine[Any, Any, None]:
        """Run the client until it closes; awaitable exactly as before.

        `create_task(client.start()); await client.close()` must close: the
        close lands before the task first runs, so the session is pinned
        here, at the call, not when the coroutine starts. A start() called
        after a completed close() runs normally.
        """
        return self.__start(self.__close_epoch)

    async def __start(self, epoch: int) -> None:
        if epoch != self.__close_epoch:
            # close() arrived between start() and its first step.
            self.__emit_closed()

            return

        try:
            await self.__run()
        except Exception as error:
            # Run fire-and-forget (`create_task(client.start())`), an
            # exception here was only ever "never retrieved": surface it on
            # "error" (which logs it) and still raise it to an awaiter.
            self.__event_emitter.emit("error", error)

            raise

    async def __run(self) -> None:
        _delay = _Delay(backoff_factor=1.618)

        # A new session: whatever an earlier close() shut down may run again.
        self.__closing = False
        self.__close_requested = asyncio.Event()

        for bucket in self.__buckets:
            bucket._closing = False

        while True:
            if self.__reconnection:
                _sleep = float(int(_delay.next()))

                # Never sleep past the deadline, and never attempt after it.
                if (left := self.__time_left()) is not None:
                    _sleep = min(_sleep, left)

                # close() ends the backoff at once, and an outage with it.
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        self.__close_requested.wait(), _sleep
                    )

                if self.__closing:
                    self.__emit_closed()

                    break

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

                if self.__closing:
                    # The owner closed: whatever the socket died of on the
                    # way out (no close frame, a failed reconnect attempt),
                    # the client is done, not lost.
                    self.__emit_closed()

                    break

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

            # A normal exit (a server 1000, or the owner's close) ends the
            # session: no bucket supervisor may outlive it.
            await self.__stop_buckets()

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

    def __emit_closed(self) -> None:
        self.__event_emitter.emit("disconnected", *self.__close_frame)

    def __is_lost_connection(self, error: ConnectionClosedError) -> bool:
        # websockets 16 reports a dead TCP connection (and a keepalive
        # timeout) with no received close frame at all: RFC 6455 forbids
        # sending 1006, so `rcvd is None` IS the lost connection. A close the
        # client asked for can end the same way: start() checks `__closing`
        # before it ever asks this.
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

        # A connection that dropped soon after it opened is the server
        # flapping, not a recovery: keep backing off from where it was.
        if (
            self.__connected_at is not None
            and _clock() - self.__connected_at >= _STABLE_CONNECTION_TIME
        ):
            delay.reset()

        # Spent: an attempt that dies before its socket opens must not count
        # the whole outage as uptime.
        self.__connected_at = None

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

    @staticmethod
    async def __cancel(task: Task[None]) -> None:
        if task is asyncio.current_task():
            # close() called from inside a supervisor: it ends on its own
            # once it sees the closing flags.
            return

        task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            # Only the supervisor's own cancellation is expected here; one
            # aimed at the caller must reach it.
            if (current := asyncio.current_task()) and current.cancelling():
                raise

    async def __stop_buckets(self) -> None:
        for bucket in list(self.__buckets):
            if task := self.__buckets.get(bucket):
                self.__buckets[bucket] = None

                await BfxWebSocketClient.__cancel(task)

    async def __supervise(self, bucket: BfxWebSocketBucket) -> None:
        """Run one bucket socket and bring it back whenever it drops.

        A bucket is its own TCP connection: it can die while the client's
        socket stays up, and nothing used to notice.
        """
        _delay = _Delay(backoff_factor=1.618)

        connections = bucket._connections

        failures = 0

        while True:
            failure: Exception | None = None

            try:
                await bucket.start()
            except _EXPECTED_DROPS as error:
                self.__logger.warning(
                    f"Bucket connection dropped ({type(error).__name__}: "
                    f"{error}): reconnecting..."
                )

                if isinstance(error, InvalidStatus) and not (
                    _is_retryable_status(error)
                ):
                    failure = error
            except Exception as error:
                self.__event_emitter.emit("error", error)

                failure = error

            if self.__bucket_is_done(bucket):
                return

            # A fresh socket must re-announce every subscription it confirms.
            self.__event_emitter.reset_subscription_scope(bucket.ids)

            stable = (
                bucket._connections > connections
                and bucket._uptime() >= _STABLE_CONNECTION_TIME
            )

            connections = bucket._connections

            if stable:
                _delay.reset()

                failures = 0
            elif failure is not None:
                failures += 1

                if failures >= _BUCKET_RESTART_LIMIT:
                    return self.__give_up(bucket, failure, failures)

            await asyncio.sleep(_delay.next())

            # close() may have arrived while it slept.
            if self.__bucket_is_done(bucket):
                return

    def __give_up(
        self, bucket: BfxWebSocketBucket, failure: Exception, failures: int
    ) -> None:
        """End a supervisor that keeps failing, loudly, instead of looping."""
        error = BucketRestartLimitError(
            f"A subscription bucket failed {failures} times in a row "
            f"({type(failure).__name__}: {failure}): its supervisor stopped. "
            "Its subscriptions are down until the client reconnects."
        )

        error.__cause__ = failure

        # Unsupervised from here: subscribe() no longer waits for it.
        if self.__buckets.get(bucket) is asyncio.current_task():
            self.__buckets[bucket] = None

        self.__event_emitter.emit("error", error)

    def __bucket_is_done(self, bucket: BfxWebSocketBucket) -> bool:
        # Removed by unsubscribe, closed on purpose (the bucket or the whole
        # client), or the client is down itself: its own reconnect restarts
        # every bucket.
        return (
            self.__closing
            or bucket not in self.__buckets
            or bucket._closing
            or not self.open
        )

    async def __buckets_ready(self) -> bool:
        """Wait, within a bound, for every bucket to come back.

        A bucket that never recovers (a 4xx it means, a bug that recurs) must
        not hold the main socket's open, auth and read loop hostage: past
        `_BUCKET_READY_TIMEOUT` the client opens without it while its
        supervisor keeps retrying. close() ends the wait too.
        """
        ready: asyncio.Future[Any] = asyncio.gather(
            *[bucket.wait() for bucket in self.__buckets]
        )
        closed: asyncio.Future[Any] = asyncio.ensure_future(
            self.__close_requested.wait()
        )

        try:
            await asyncio.wait(
                {ready, closed},
                timeout=_BUCKET_READY_TIMEOUT,
                return_when=asyncio.FIRST_COMPLETED,
            )

            return ready.done()
        finally:
            ready.cancel()
            closed.cancel()

    async def __wait_any(
        self, buckets: list[BfxWebSocketBucket], timeout: float
    ) -> None:
        """Wait until one of `buckets` opens, close() is called, one of their
        supervisors ends (the client dropped, or it gave up), or `timeout`.

        The caller re-reads the state: none of these is an error here.
        """
        waiters = {asyncio.ensure_future(bucket.wait()) for bucket in buckets}
        closed = asyncio.ensure_future(self.__close_requested.wait())
        # Watched, never cancelled here: they belong to the client.
        supervisors = {
            task
            for bucket in buckets
            if (task := self.__buckets.get(bucket)) is not None
        }

        try:
            await asyncio.wait(
                {*waiters, closed, *supervisors},
                timeout=max(timeout, 0.0),
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for future in (*waiters, closed):
                future.cancel()

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

            self.__connected_at = _clock()

            if self.__closing:
                # close() arrived during the handshake.
                return await websocket.close(*self.__close_frame)

            for bucket in self.__buckets:
                self.__buckets[bucket] = asyncio.create_task(
                    self.__supervise(bucket)
                )

            if (
                self.__buckets
                and not await self.__buckets_ready()
                and not self.__closing
            ):
                self.__logger.warning(
                    "A subscription bucket is not back yet: opening without "
                    "it while it keeps reconnecting."
                )

            if self.__closing:
                # close() arrived while the buckets were coming back. Hang up
                # with what it asked for: leaving the block would send 1000.
                return await websocket.close(*self.__close_frame)

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
            decimal_mode=self.__decimal_mode,
        )

        self.__buckets[bucket] = asyncio.create_task(self.__supervise(bucket))

        # Bounded, and close() ends it: a bucket that never opens must not
        # hang the subscribe() that asked for it.
        await self.__wait_any([bucket], _BUCKET_READY_TIMEOUT)

        if bucket.open and not self.__closing:
            return bucket

        # An empty bucket has nothing to keep: don't leave it redialling.
        task = self.__buckets.pop(bucket, None)

        await bucket.close(code=1001, reason="Going Away")

        if task is not None:
            await BfxWebSocketClient.__cancel(task)

        raise ConnectionNotOpen(
            "The client closed while a subscription bucket was opening."
            if self.__closing
            else "A new subscription bucket did not open within "
            f"{_BUCKET_READY_TIMEOUT}s."
        )

    def __coming_back(self) -> list[BfxWebSocketBucket]:
        """Buckets with room whose supervisor is still bringing them up."""
        return [
            bucket
            for bucket, task in self.__buckets.items()
            if not bucket.is_full
            and not bucket.open
            and task is not None
            and not task.done()
        ]

    @Connection._require_websocket_connection  # type: ignore[arg-type]
    async def subscribe(
        self, channel: str, sub_id: str | None = None, **kwargs: Any
    ) -> None:
        if channel not in ["ticker", "trades", "book", "candles", "status"]:
            raise UnknownChannelError(
                "Available channels are: ticker, trades, book, candles and status."
            )

        loop = asyncio.get_running_loop()

        deadline = loop.time() + _BUCKET_READY_TIMEOUT

        while True:
            # The client may have closed or dropped while this one waited:
            # never open a bucket for it then.
            if self.__closing or not self.open:
                raise ConnectionNotOpen(
                    "No open connection with the server."
                    if not self.__closing
                    else "The client is closing."
                )

            # Re-read on every pass: a concurrent subscribe() may have taken
            # the sub_id, or filled the bucket, while this one waited.
            for bucket in self.__buckets:
                if sub_id in bucket.ids:
                    raise SubIdError(
                        "sub_id must be unique for all subscriptions."
                    )

            # An open bucket with room first: one that is still down (the
            # client opened without it after _BUCKET_READY_TIMEOUT) would
            # only raise ConnectionNotOpen.
            for bucket in self.__buckets:
                if bucket.open and not bucket.is_full:
                    return await bucket.subscribe(channel, sub_id, **kwargs)

            if not (coming_back := self.__coming_back()):
                await self.__new_bucket()

                continue

            # Wait, bounded, for one that is reconnecting.
            if (left := deadline - loop.time()) <= 0:
                raise ConnectionNotOpen(
                    "No subscription bucket came back within "
                    f"{_BUCKET_READY_TIMEOUT}s."
                )

            await self.__wait_any(coming_back, left)

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

    async def close(self, code: int = 1000, reason: str = "") -> None:
        """Shut the client down, in any state.

        Up, mid-outage or mid-handshake alike: `start()` stops reconnecting,
        ends cleanly and emits "disconnected", and no bucket comes back.
        """
        self.__closing = True
        self.__close_frame = (code, reason)
        self.__close_epoch += 1
        self.__close_requested.set()

        for bucket in list(self.__buckets):
            await bucket.close(code=code, reason=reason)

        # Supervisors asleep in a backoff, or mid-handshake, are stopped too.
        await self.__stop_buckets()

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
