"""The test suite must not talk to the exchange unless it says so.

Measured on 2026-09-26, by instrumenting the real ``socket`` calls rather than reading the
sources: ``pytest tests/`` makes **zero** off-loopback connections (339 passed), and
``LIVE_WIRE=1 pytest tests/test_wire_contract.py`` reaches Bitfinex. So the suite is already
hermetic.

But that is a CONVENTION, not a rule. It holds because every test that could reach the network
voluntarily checks ``LIVE_WIRE`` itself, and because the endpoint tests happen to construct
``RestPublicEndpoints("https://api.example.com")`` with a ``MagicMock`` transport. A new test
that used the real default URL would sail straight past, and nothing would say so.

The sibling repo shows the cost. In ``bitfinex-api-node`` five tests filed under "unit" dialled
Bitfinex production on every run, because ``openSocket()`` with no url defaults there. CI failed
with ``Unexpected server response: 429`` -- rate limited, all five at once. That suite had looked
hermetic because of *where its tests lived*, not what they did. Nobody had checked, because
nothing could.

WHY THIS HOOKS TWO LAYERS INSTEAD OF ONE
----------------------------------------
``socket.socket.connect`` is the real network boundary, so that is where the block belongs. But
by the time Python reaches it the hostname is **gone**: the address is already resolved, and a
guard that hooks only ``connect`` can say no more than

    Blocked a live network connection to 104.16.164.90

which is exactly the wrong-diagnostic trap -- it sends the reader hunting through Cloudflare
address space instead of naming ``api-pub.bitfinex.com``. (Those three IPs are real; they are
what the probe reported when this was measured.)

So ``getaddrinfo`` is hooked too, purely to learn the ip -> host mapping that ``connect`` then
reports. Detection stays at the boundary; only the *naming* comes from the resolver.

WHY VIOLATIONS ARE RECORDED AS WELL AS RAISED
---------------------------------------------
``websockets.asyncio`` dials inside a task. An exception raised there does not necessarily
surface as a test failure -- it can be swallowed into a connection-failed path or an
event-loop "exception was never retrieved" warning, and the test then fails by timeout or not
at all. The same thing happens in the JS siblings for different reasons (``fetch`` re-wraps the
throw as a bare ``TypeError: fetch failed``; ``ws`` turns it into an ``'error'`` event). Three
transports, three ways to eat the diagnostic. The teardown hook below re-raises what was
swallowed, attributed to the test that did it.

NOT A THIRD COPY. The TypeScript guards in bitfinex-api-node and bfx-api-node-rest carry a note
capping that duplication at two, with "at three, extract it". This is not that third copy: it is
a different language and a different package ecosystem, it cannot share a line of code with
them, and its whole mechanism (the resolver map) exists to solve a problem those two do not
have. The shared thing here is the *idea*, and ideas do not deduplicate into a package.
"""

import asyncio
import json
import os
import socket
from types import SimpleNamespace
from typing import Any

import pytest

#: The guard reads the SAME variable the live tests read (``test_wire_contract.py``), so it is
#: active in exactly the runs where they skip themselves, and silent in the runs where they are
#: supposed to reach Bitfinex. A guard keyed to a different variable would either block the live
#: suite or guard nothing at all.
_ALLOW_LIVE = os.environ.get("LIVE_WIRE") == "1"

_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost", "0.0.0.0", "", "::"})

_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex
_real_getaddrinfo = socket.getaddrinfo

#: resolved address -> the hostname it was resolved from, so ``connect`` can name what it blocks.
_resolved: dict[str, str] = {}

#: Violations are collected here and re-raised at teardown; see the module docstring.
_violations: list[str] = []


def _describe(address: Any) -> str | None:
    """Return a human name for an off-loopback address, or ``None`` if it is not the network.

    A ``str`` address is an ``AF_UNIX`` path -- a filesystem socket, not the network. Reading one
    as a hostname is not hypothetical: the equivalent JS guard's first version reported
    ``/tmp/tsx-1000/561052.pipe`` as "a live network connection", which is a wrong answer
    delivered confidently.
    """
    if not isinstance(address, tuple) or not address:
        return None

    host = address[0]
    if not isinstance(host, str) or host in _LOOPBACK:
        return None

    name = _resolved.get(host)
    return f"{name} ({host})" if name and name != host else host


def _spy_getaddrinfo(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
    results = _real_getaddrinfo(host, port, *args, **kwargs)
    if isinstance(host, str):
        for entry in results:
            sockaddr = entry[4]
            if (
                isinstance(sockaddr, tuple)
                and sockaddr
                and isinstance(sockaddr[0], str)
            ):
                _resolved[sockaddr[0]] = host
    return results


def _blocked(where: str, address: Any) -> RuntimeError:
    target = _describe(address)
    message = (
        f"Blocked a live network connection to {target} (via {where}) from the test suite.\n"
        "Unit tests must not reach the exchange. Use a MagicMock transport (see "
        "tests/test_rest_public_endpoints.py) or a recorded fixture (see "
        "tests/test_wire_contract.py). If this test is genuinely meant to hit the live API, "
        "mark it with @_needs_live and run it under LIVE_WIRE=1."
    )
    _violations.append(f"{target} (via {where})")
    return RuntimeError(message)


def _spy_connect(self: socket.socket, address: Any) -> Any:
    if _describe(address) is not None:
        raise _blocked("socket.connect", address)
    return _real_connect(self, address)


def _spy_connect_ex(self: socket.socket, address: Any) -> Any:
    if _describe(address) is not None:
        raise _blocked("socket.connect_ex", address)
    return _real_connect_ex(self, address)


if not _ALLOW_LIVE:
    socket.getaddrinfo = _spy_getaddrinfo  # type: ignore[assignment]
    socket.socket.connect = _spy_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = _spy_connect_ex  # type: ignore[method-assign]


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: Any) -> Any:
    """Fail a test whose transport SWALLOWED the block, attributed to the test that made it.

    Only the swallowed case. When the ``RuntimeError`` propagated normally the test has already
    failed with it in the traceback, and adding a second report would turn one defect into two
    entries -- the sort of noise that trains people to skim.
    """
    before = len(_violations)
    # If the call phase raises, this propagates and the check below is skipped -- which is the
    # point: that test already failed with the RuntimeError in its traceback.
    result = yield
    escaped = _violations[before:]
    if escaped:
        raise AssertionError(
            "This test opened a live network connection: "
            + ", ".join(escaped)
            + " -- and the transport swallowed the block, so the test would otherwise have "
            "passed or hung. See tests/conftest.py."
        )
    return result


def pytest_report_header(config: Any) -> str:
    if _ALLOW_LIVE:
        return "no-live-network: DISABLED (LIVE_WIRE=1) -- tests may reach api.bitfinex.com"
    return "no-live-network: active -- off-loopback connections are blocked"


# --- Shared doubles for the websocket transport ------------------------------
#
# These stand in for the socket, never for the code under test: a test using
# them still drives the real `subscribe`/`start`/`__connect` paths, so what it
# pins is the client's behaviour rather than a restatement of the fake's.


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

    def deliver(self, frame: str) -> None:
        """Hand the bucket one more frame after its loop is already running.

        `incoming` can only stage frames that exist before `start`; a test
        about what happens to a request made mid-connection has to be able to
        answer it mid-connection.
        """
        self._outbox.put_nowait(frame)

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
