"""Shared doubles for the websocket transport.

These stand in for the socket, never for the code under test: a test
using them still drives the real `subscribe`/`start`/`__connect` paths,
so what it pins is the client's behaviour rather than a restatement of
the fake's.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

_STOP = object()


class FakeWebSocket:
    """A socket that records what was sent and replays canned frames."""

    def __init__(
        self,
        incoming: list[str] | None = None,
        hold: bool = False,
        raises: BaseException | None = None,
        confirms: bool = False,
    ) -> None:
        self.sent: list[str] = []
        self.closed: tuple[int, str] | None = None
        self.state = SimpleNamespace(name="OPEN")
        self.close_code: int | None = None
        self.close_reason: str | None = None
        self._incoming = list(incoming or [])
        self._hold = hold
        self._raises = raises
        self._confirms = confirms
        self._next_chan_id = 1
        self._outbox: asyncio.Queue = asyncio.Queue()
        # A held-open socket must still end when it is closed, or every
        # bucket task outlives its test and the loop is torn down under it.
        self._shutdown = asyncio.Event()

    async def send(self, message: str) -> None:
        self.sent.append(message)
        if self._confirms:
            self._confirm(json.loads(message))

    def _confirm(self, frame: dict) -> None:
        """Answer a subscribe the way Bitfinex does, with a channel id.

        Without a confirmation every subscription stays pending forever,
        and half the client's surface - unsubscribe, resubscribe, bucket
        reuse - is unreachable, because it all keys off confirmed
        subscriptions rather than in-flight ones.
        """
        if frame.get("event") != "subscribe":
            return
        chan_id, self._next_chan_id = self._next_chan_id, self._next_chan_id + 1
        self._outbox.put_nowait(
            json.dumps(
                {
                    **{
                        key: value
                        for key, value in frame.items()
                        if key != "subId"
                    },
                    "event": "subscribed",
                    "chanId": chan_id,
                    "subId": frame["subId"],
                }
            )
        )

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)
        self.close_code, self.close_reason = code, reason
        self.state = SimpleNamespace(name="CLOSED")
        self._shutdown.set()
        self._outbox.put_nowait(_STOP)

    async def __aiter__(self):
        for message in self._incoming:
            yield message
        if self._raises is not None:
            # A connection-closed fault leaves the socket closed; the
            # client's timeout handler reads exactly that to decide
            # whether it is still offline.
            self.state = SimpleNamespace(name="CLOSED")
            raise self._raises
        if not (self._hold or self._confirms):
            return
        while True:
            frame = await self._outbox.get()
            if frame is _STOP:
                return
            yield frame

    @property
    def events(self) -> list[dict]:
        """Every frame sent, decoded. Frames are JSON by contract."""
        return [json.loads(message) for message in self.sent]


class FakeConnect:
    """The async context manager `websockets.connect(...)` returns."""

    def __init__(self, websocket: FakeWebSocket) -> None:
        self._websocket = websocket

    async def __aenter__(self) -> FakeWebSocket:
        return self._websocket

    async def __aexit__(self, *_: object) -> bool:
        return False


@pytest.fixture
def connect(monkeypatch):
    """Hand every `connect()` in the transport a socket of our choosing.

    The client and the bucket both reach `websockets.asyncio.client`, so
    patching it once covers the client socket and every bucket socket it
    opens underneath.
    """

    def _install(websocket: FakeWebSocket) -> FakeWebSocket:
        monkeypatch.setattr(
            "websockets.asyncio.client.connect",
            lambda *_a, **_k: FakeConnect(websocket),
        )
        return websocket

    return _install


@pytest.fixture
def connect_each(monkeypatch):
    """Give every `connect()` call its own socket, in call order.

    The client opens one socket for itself and one per bucket; a single
    shared double would make it impossible to say which of them a frame
    was sent on.
    """
    sockets: list[FakeWebSocket] = []

    def _install(factory=None) -> list[FakeWebSocket]:
        make = factory or (lambda: FakeWebSocket(confirms=True))

        def _connect(*_a, **_k):
            websocket = make()
            sockets.append(websocket)
            return FakeConnect(websocket)

        monkeypatch.setattr("websockets.asyncio.client.connect", _connect)
        return sockets

    return _install


async def settle(predicate, timeout: float = 2.0) -> None:
    """Yield until a background task has caught up, or fail loudly.

    Bucket sockets are driven by their own tasks, so a test that asserts
    straight after `subscribe` races the confirmation. Polling a public
    predicate keeps the wait bounded instead of sprinkling bare sleeps.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("background task never caught up")
        await asyncio.sleep(0.001)
