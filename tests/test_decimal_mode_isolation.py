"""decimal_mode is per Client: two clients never leak it into each other.

Before the fix, `Client(decimal_mode=...)` wrote a process-wide flag, so the
last Client built decided how every other client parsed money.
"""

import threading
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from bfxapi import Client
from bfxapi.types import labeler
from bfxapi.websocket._client.bfx_websocket_bucket import BfxWebSocketBucket

_WALLET_ROW = ["funding", "USD", 1000.5, 0, 1000.5, None, None]
_TICKER = [
    50000.0,
    1.5,
    50001.0,
    2.0,
    100.0,
    0.002,
    50000.5,
    10000.0,
    51000.0,
    49000.0,
]


def _rest_balance(client: Client) -> object:
    client.rest.auth._m = MagicMock()
    client.rest.auth._m.post.return_value = [list(_WALLET_ROW)]
    return client.rest.auth.get_wallets()[0].balance


def _rest_volume(client: Client) -> object:
    client.rest.public._m = MagicMock()
    client.rest.public._m.get.return_value = list(_TICKER)
    return client.rest.public.get_t_ticker("tBTCUSD").volume


def _ws_balance(client: Client) -> object:
    handler = client.wss._BfxWebSocketClient__handler
    emitter = MagicMock()
    handler._AuthEventsHandler__event_emitter = emitter
    handler.handle("wu", list(_WALLET_ROW))
    return emitter.emit.call_args[0][1].balance


def _ws_bucket_volume(decimal_mode: bool | None) -> object:
    emitter = MagicMock()
    bucket = BfxWebSocketBucket(
        "wss://example.invalid", emitter, decimal_mode=decimal_mode
    )
    handler = bucket._BfxWebSocketBucket__handler
    subscription = {"channel": "ticker", "sub_id": "x", "symbol": "tBTCUSD"}
    handler.handle(subscription, [list(_TICKER)])
    return emitter.emit.call_args[0][2].volume


@pytest.fixture(autouse=True)
def _default_off():
    labeler.set_decimal_mode(False)
    yield
    labeler.set_decimal_mode(False)


class TestRestIsolation:
    def test_decimal_client_survives_a_later_plain_client(self):
        decimal_client = Client("k", "s", decimal_mode=True)
        plain_client = Client("k", "s")
        assert isinstance(_rest_balance(decimal_client), Decimal)
        assert isinstance(_rest_volume(decimal_client), Decimal)
        assert isinstance(_rest_balance(plain_client), float)
        assert isinstance(_rest_volume(plain_client), float)

    def test_plain_client_survives_a_later_decimal_client(self):
        plain_client = Client("k", "s")
        decimal_client = Client("k", "s", decimal_mode=True)
        assert isinstance(_rest_balance(plain_client), float)
        assert isinstance(_rest_volume(plain_client), float)
        assert isinstance(_rest_balance(decimal_client), Decimal)

    def test_plain_client_ignores_the_process_default(self):
        plain_client = Client("k", "s")
        labeler.set_decimal_mode(True)
        assert isinstance(_rest_balance(plain_client), float)

    def test_scope_does_not_outlive_the_call(self):
        decimal_client = Client("k", "s", decimal_mode=True)
        _rest_balance(decimal_client)
        assert labeler.decimal_mode_enabled() is False

    def test_concurrent_threads_keep_their_own_mode(self):
        decimal_client = Client("k", "s", decimal_mode=True)
        plain_client = Client("k", "s")
        decimal_client.rest.auth._m = MagicMock()
        plain_client.rest.auth._m = MagicMock()
        barrier = threading.Barrier(2)

        def rows() -> list[list[object]]:
            # Both threads are inside their scope at the same time here.
            barrier.wait(timeout=5)
            return [list(_WALLET_ROW)]

        decimal_client.rest.auth._m.post.side_effect = lambda *a, **k: rows()
        plain_client.rest.auth._m.post.side_effect = lambda *a, **k: rows()
        results: dict[str, object] = {}

        def run(name: str, client: Client) -> None:
            results[name] = client.rest.auth.get_wallets()[0].balance

        threads = [
            threading.Thread(target=run, args=("decimal", decimal_client)),
            threading.Thread(target=run, args=("plain", plain_client)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert isinstance(results["decimal"], Decimal)
        assert isinstance(results["plain"], float)


class TestWebSocketIsolation:
    def test_decimal_client_survives_a_later_plain_client(self):
        decimal_client = Client("k", "s", decimal_mode=True)
        plain_client = Client("k", "s")
        assert isinstance(_ws_balance(decimal_client), Decimal)
        assert isinstance(_ws_balance(plain_client), float)

    def test_plain_client_survives_a_later_decimal_client(self):
        plain_client = Client("k", "s")
        decimal_client = Client("k", "s", decimal_mode=True)
        assert isinstance(_ws_balance(plain_client), float)
        assert isinstance(_ws_balance(decimal_client), Decimal)

    def test_public_bucket_follows_its_client_mode(self):
        assert isinstance(_ws_bucket_volume(True), Decimal)
        assert isinstance(_ws_bucket_volume(False), float)
        # A bucket built outside a Client follows the process default.
        labeler.set_decimal_mode(True)
        assert isinstance(_ws_bucket_volume(None), Decimal)

    def test_client_hands_its_mode_to_new_buckets(self):
        decimal_client = Client(decimal_mode=True)
        plain_client = Client()
        assert decimal_client.wss._BfxWebSocketClient__decimal_mode is True
        assert plain_client.wss._BfxWebSocketClient__decimal_mode is False
