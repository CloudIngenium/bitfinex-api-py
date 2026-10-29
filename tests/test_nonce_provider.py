"""py-nonce-01: one nonce source for every component that signs with a key.

Bitfinex rejects a nonce that is not greater than the last one it accepted
for the key (10114). REST and WebSocket auth used to draw from two separate
microsecond clocks; they now share one process-wide monotonic generator, and
a caller can inject its own ``nonce_provider`` for both.
"""

import hashlib
import hmac
import itertools
import json
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from bfxapi import Client
from bfxapi._utils import nonce as nonce_mod
from bfxapi.exceptions import (
    BfxBaseException,
    InvalidCredentialError,
    NonceProviderError,
)
from bfxapi.rest._interface.middleware import Middleware, _next_nonce
from bfxapi.rest.retry import is_retryable
from bfxapi.websocket._client.bfx_websocket_client import (
    BfxWebSocketClient,
    _Delay,
)
from bfxapi.websocket._connection import Connection

from .conftest import FakeWebSocket

_POST = "bfxapi.rest._interface.middleware.requests.post"
_GET = "bfxapi.rest._interface.middleware.requests.get"
_CREDENTIALS = {"api_key": "k", "api_secret": "s", "filters": None}


def _ok() -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    response.headers = {}
    response.json.return_value = []
    return response


def _auth_message(**kwargs: object) -> dict:
    return json.loads(
        Connection._get_authentication_message(
            api_key="k", api_secret="s", **kwargs
        )
    )


class _Counter:
    def __init__(self, start: int) -> None:
        self._values = itertools.count(start)
        self.calls = 0

    def __call__(self) -> int:
        self.calls += 1
        return next(self._values)


class TestInjectedProvider:
    def test_rest_post_uses_the_provider_and_signs_it(self):
        provider = _Counter(1_000)
        client = Client(api_key="k", api_secret="s", nonce_provider=provider)

        with patch(_POST, return_value=_ok()) as post:
            client.rest.auth._m.post("auth/r/wallets", body={"a": 1})

        headers = post.call_args.kwargs["headers"]
        assert headers["bfx-nonce"] == "1000"
        body = post.call_args.kwargs["data"]
        expected = hmac.new(
            b"s", f"/api/v2/auth/r/wallets1000{body}".encode(), hashlib.sha384
        ).hexdigest()
        assert headers["bfx-signature"] == expected
        assert provider.calls == 1

    def test_rest_get_uses_the_provider(self):
        provider = _Counter(77)
        m = Middleware("https://x", "k", "s", nonce_provider=provider)

        with patch(_GET, return_value=_ok()) as get:
            m.get("auth/r/wallets")
            m.get("auth/r/wallets")

        nonces = [c.kwargs["headers"]["bfx-nonce"] for c in get.call_args_list]
        assert nonces == ["77", "78"]

    def test_public_requests_never_call_the_provider(self):
        provider = _Counter(1)
        client = Client(nonce_provider=provider)

        with patch(_GET, return_value=_ok()):
            client.rest.public._m.get("platform/status")

        assert provider.calls == 0

    def test_ws_auth_message_uses_the_provider(self):
        msg = _auth_message(nonce_provider=lambda: 4242)

        assert msg["authNonce"] == 4242
        assert isinstance(msg["authNonce"], int)
        assert msg["authPayload"] == "AUTH4242"
        expected = hmac.new(b"s", b"AUTH4242", hashlib.sha384).hexdigest()
        assert msg["authSig"] == expected

    async def test_client_provider_reaches_the_ws_auth_frame(
        self, connect_each
    ):
        sockets = connect_each(
            lambda: _fake_socket(
                incoming=[json.dumps({"event": "auth", "status": "OK"})]
            )
        )
        provider = _Counter(9_000)
        client = Client(api_key="k", api_secret="s", nonce_provider=provider)

        with patch(_POST, return_value=_ok()) as post:
            client.rest.auth._m.post("auth/r/wallets")
        await client.wss.start()

        frame = json.loads(sockets[0].sent[0])
        assert post.call_args.kwargs["headers"]["bfx-nonce"] == "9000"
        assert frame["authNonce"] == 9001
        assert frame["authPayload"] == "AUTH9001"

    def test_int_subclass_is_rendered_as_a_plain_int(self):
        class Weird(int):
            def __str__(self) -> str:
                return "weird"

            __repr__ = __str__

        m = Middleware("https://x", "k", "s", nonce_provider=lambda: Weird(5))
        with patch(_POST, return_value=_ok()) as post:
            m.post("auth/r/wallets")

        assert post.call_args.kwargs["headers"]["bfx-nonce"] == "5"
        msg = _auth_message(nonce_provider=lambda: Weird(6))
        assert msg["authPayload"] == "AUTH6"

    async def test_ws_reconnect_draws_a_new_nonce(
        self, connect_each, monkeypatch
    ):
        # 20051 makes the client reconnect at once; each socket re-auths.
        monkeypatch.setattr(_Delay, "next", lambda self: 0)
        auth_ok = json.dumps({"event": "auth", "status": "OK"})
        frames = [
            [auth_ok, json.dumps({"event": "info", "code": 20051})],
            [auth_ok],
        ]
        sockets = connect_each(
            lambda: _fake_socket(incoming=frames.pop(0) if frames else [])
        )
        provider = _Counter(3_000)
        client = BfxWebSocketClient(
            "wss://example.invalid",
            credentials=_CREDENTIALS,
            nonce_provider=provider,
        )

        await client.start()

        assert len(sockets) == 2, "the client reconnected exactly once"
        auths = [
            [f for f in s.events if f.get("event") == "auth"] for s in sockets
        ]
        assert [len(a) for a in auths] == [1, 1]
        assert auths[0][0]["authNonce"] == 3_000
        assert auths[1][0]["authNonce"] == 3_001
        assert auths[1][0]["authPayload"] == "AUTH3001"
        assert provider.calls == 2


class _NegativeViaInt(int):
    """Plain value 7, but its own __int__ claims -1."""

    def __int__(self) -> int:
        return -1


class _AlwaysPositive(int):
    """A negative value whose own __le__ says it is never <= anything."""

    def __le__(self, other: object) -> bool:
        return False


class _IntRaises(int):
    """An int subclass whose own __int__ raises."""

    def __int__(self) -> int:
        raise RuntimeError("no int for you")


class TestIntSubclassProvider:
    """An int subclass is judged by its plain int value, never its overrides."""

    def test_own_int_returning_minus_one_is_bypassed(self):
        value = _NegativeViaInt(7)
        assert int(value) == -1  # the override is real

        nonce = nonce_mod.resolve_nonce(lambda: value)

        assert nonce == 7
        assert type(nonce) is int
        assert _auth_message(nonce_provider=lambda: value)["authNonce"] == 7

    def test_own_int_cannot_hide_a_negative_value(self):
        class _PositiveViaInt(int):
            def __int__(self) -> int:
                return 5

        with pytest.raises(NonceProviderError, match="positive int"):
            nonce_mod.resolve_nonce(lambda: _PositiveViaInt(-1))

    @pytest.mark.parametrize("raw", [-1, 0])
    def test_le_always_false_cannot_pass_a_non_positive_value(self, raw):
        value = _AlwaysPositive(raw)
        assert not value <= 0  # the override is real

        m = Middleware("https://x", "k", "s", nonce_provider=lambda: value)
        with (
            patch(_POST) as post,
            pytest.raises(NonceProviderError, match="positive int"),
        ):
            m.post("auth/w/order/submit")
        post.assert_not_called()

        with pytest.raises(NonceProviderError, match="positive int"):
            _auth_message(nonce_provider=lambda: value)

    def test_own_int_that_raises_is_never_called(self):
        value = _IntRaises(11)
        with pytest.raises(RuntimeError):
            int(value)  # the override is real

        assert nonce_mod.resolve_nonce(lambda: value) == 11

    def test_own_int_that_raises_cannot_pass_a_negative_value(self):
        with pytest.raises(NonceProviderError, match="positive int"):
            nonce_mod.resolve_nonce(lambda: _IntRaises(-4))


def _fake_socket(**kwargs):
    return FakeWebSocket(**kwargs)


def _freeze_clock(monkeypatch) -> None:
    """Pin the generator's clock to the microsecond it last issued, so every
    later call lands in a tick that is already used. Only the nonce module's
    view of `time` is replaced, never the global module."""
    frozen = nonce_mod.next_nonce() * 1_000
    monkeypatch.setattr(
        nonce_mod, "time", SimpleNamespace(time_ns=lambda: frozen)
    )


class TestDefaultSharedGenerator:
    def test_next_nonce_is_still_a_string_from_middleware(self):
        value = _next_nonce()
        assert isinstance(value, str)
        assert int(value) > 0

    def test_rest_and_ws_interleaved_never_repeat(self):
        m = Middleware("https://x", "k", "s")
        nonces: list[int] = []

        with patch(_POST, return_value=_ok()) as post:
            for _ in range(500):
                m.post("auth/r/wallets")
                nonces.append(
                    int(post.call_args.kwargs["headers"]["bfx-nonce"])
                )
                nonces.append(_auth_message()["authNonce"])
                nonces.append(int(_next_nonce()))

        assert nonces == sorted(nonces)
        assert len(set(nonces)) == len(nonces)

    def test_same_microsecond_still_strictly_increases(self, monkeypatch):
        # A frozen clock: every call lands in the same microsecond tick.
        _freeze_clock(monkeypatch)
        m = Middleware("https://x", "k", "s")

        with patch(_POST, return_value=_ok()) as post:
            m.post("auth/r/wallets")
            rest = int(post.call_args.kwargs["headers"]["bfx-nonce"])
        ws = _auth_message()["authNonce"]

        assert ws == rest + 1

    def test_threads_rest_and_ws_never_repeat(self, monkeypatch):
        # Frozen clock, so uniqueness rests on the lock, not on time moving.
        _freeze_clock(monkeypatch)
        m = Middleware("https://x", "k", "s")
        seen: list[int] = []
        seen_lock = threading.Lock()
        start = threading.Barrier(8, timeout=30)

        captured: list[str] = []
        # A failed assert inside a thread never reaches pytest on its own.
        failures: list[BaseException] = []

        def _post(**kwargs):
            with seen_lock:
                captured.append(kwargs["headers"]["bfx-nonce"])
            return _ok()

        def rest_worker() -> None:
            start.wait()
            for _ in range(200):
                m.post("auth/r/wallets")

        def ws_worker() -> None:
            start.wait()
            local = [_auth_message()["authNonce"] for _ in range(200)]
            assert local == sorted(local)
            with seen_lock:
                seen.extend(local)

        def guarded(work) -> None:
            try:
                work()
            except BaseException as e:
                with seen_lock:
                    failures.append(e)

        with patch(_POST, side_effect=_post):
            threads = [
                threading.Thread(
                    target=guarded,
                    args=(rest_worker if i % 2 else ws_worker,),
                )
                for i in range(8)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=60)

        assert not any(t.is_alive() for t in threads)
        assert failures == []
        all_nonces = seen + [int(n) for n in captured]
        assert len(all_nonces) == 8 * 200
        assert len(set(all_nonces)) == len(all_nonces)


_BAD_VALUES = [0, -1, True, False, 1.0, "123", None, b"1", 10**3 + 0.5]


class TestBadProvider:
    @pytest.mark.parametrize("value", _BAD_VALUES, ids=repr)
    def test_bad_value_fails_before_any_post(self, value):
        m = Middleware("https://x", "k", "s", nonce_provider=lambda: value)

        with (
            patch(_POST) as post,
            patch("bfxapi.rest._interface.middleware.hmac.new") as sign,
            pytest.raises(NonceProviderError, match="positive int"),
        ):
            m.post("auth/w/order/submit", body={"amount": "1"})

        post.assert_not_called()
        sign.assert_not_called()

    def test_raising_provider_fails_before_any_post(self):
        boom = RuntimeError("clock store down")

        def provider() -> int:
            raise boom

        m = Middleware("https://x", "k", "s", nonce_provider=provider)
        with (
            patch(_POST) as post,
            pytest.raises(NonceProviderError, match="clock store down") as exc,
        ):
            m.post("auth/w/order/submit")

        post.assert_not_called()
        assert exc.value.__cause__ is boom

    def test_bad_provider_fails_before_any_get(self):
        m = Middleware("https://x", "k", "s", nonce_provider=lambda: -5)
        with patch(_GET) as get, pytest.raises(NonceProviderError):
            m.get("auth/r/wallets")
        get.assert_not_called()

    @pytest.mark.parametrize("value", _BAD_VALUES, ids=repr)
    def test_ws_bad_value_fails_before_signing(self, value):
        with (
            patch("bfxapi.websocket._connection.hmac.new") as sign,
            pytest.raises(NonceProviderError),
        ):
            _auth_message(nonce_provider=lambda: value)
        sign.assert_not_called()

    async def test_ws_client_sends_nothing_with_a_bad_provider(
        self, connect_each
    ):
        sockets = connect_each(lambda: _fake_socket(hold=True))
        client = BfxWebSocketClient(
            "wss://example.invalid",
            credentials=_CREDENTIALS,
            nonce_provider=lambda: 0,
        )
        errors: list = []
        client.on("error", errors.append)

        with pytest.raises(NonceProviderError):
            await client.start()

        assert sockets[0].sent == []
        assert len(errors) == 1

    def test_error_is_final_and_caught_by_existing_handlers(self):
        m = Middleware("https://x", "k", "s", nonce_provider=lambda: "x")
        with patch(_POST), pytest.raises(NonceProviderError) as exc:
            m.post("auth/w/order/submit")

        error = exc.value
        assert isinstance(error, InvalidCredentialError)
        assert isinstance(error, ValueError)
        assert isinstance(error, BfxBaseException)
        assert error.retryable is False
        assert error.outcome_unknown is False
        assert is_retryable(error) is False
        assert is_retryable(error, idempotent=True) is False

    def test_client_rejects_a_non_callable_provider(self):
        with pytest.raises(TypeError, match="callable"):
            Client(nonce_provider=123)  # type: ignore[arg-type]
