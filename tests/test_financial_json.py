import json
from decimal import Decimal
from unittest.mock import patch

import pytest
import requests
from pyee import EventEmitter

from bfxapi import Client
from bfxapi._utils.financial_json import (
    FinancialTokenDecimal,
    financial_decode_options,
)
from bfxapi.types import serializers
from bfxapi.types.labeler import set_decimal_mode
from bfxapi.websocket._handlers.auth_events_handler import AuthEventsHandler


def response(body: str) -> requests.Response:
    result = requests.Response()
    result.status_code = 200
    result._content = body.encode()
    result.encoding = "utf-8"
    return result


def test_decode_preserves_token_and_does_not_change_legacy_instances():
    exact = Client(lossless_financial_decode=True)
    legacy = Client()
    token = "0.0000001000000000000007400"
    wire = response(f"[9007199254740993, {token}]")
    with patch("requests.post", return_value=wire):
        values = exact.rest.auth._m.post("auth/r/ledgers/hist")
        old = legacy.rest.auth._m.post("auth/r/ledgers/hist")
        again = exact.rest.auth._m.post("auth/r/ledgers/hist")
    assert values[0] == 9007199254740993
    assert isinstance(values[0], int)
    assert values[1] == Decimal(token)
    assert values[1].source_token == token
    assert isinstance(old[1], float)
    assert again[1].source_token == token


def test_decimal_reaches_sdk_ledger_without_binary_conversion():
    # ID, currency, placeholder, timestamp, placeholder, amount, balance,
    # placeholder, description (actual SDK serializer shape).
    labels = serializers.Ledger.get_labels()
    assert "amount" in labels
    token = "-0.0000000000000000001"
    client = Client(lossless_financial_decode=True)
    with patch(
        "requests.post",
        return_value=response(
            f'[[101,"USD",null,1720000000000,null,{token},1,null,"Funding payment"]]'
        ),
    ):
        records = client.rest.auth.get_ledgers("USD")
    assert records[0].amount == Decimal(token)
    assert records[0].amount.source_token == token


def test_ws_decode_and_actual_model_dispatch_retain_precision():
    token = "1.0000000000000000000000001"
    client = Client(lossless_financial_decode=True)
    options = client.wss._BfxWebSocketClient__decode_options
    frame = json.loads(
        f'[0,"wu",["funding","USD",{token},0,1,null,null]]', **options
    )
    observed = []
    emitter = EventEmitter()
    emitter.on("wallet_update", observed.append)
    AuthEventsHandler(emitter).handle(frame[1], frame[2])
    assert observed[0].balance == Decimal(token)
    assert observed[0].balance.source_token == token
    assert isinstance(observed[0].available_balance, int)


def test_lossless_constructor_does_not_modify_global_legacy_decimal_mode():
    from bfxapi.types import labeler

    set_decimal_mode(True)
    try:
        Client(lossless_financial_decode=True)
        assert labeler._decimal_mode is True
    finally:
        set_decimal_mode(False)


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_input_rejected(token):
    with pytest.raises(ValueError, match="Non-finite"):
        json.loads(f"[{token}]", **financial_decode_options(True))


def test_raw_identity_preserves_official_indices():
    identity = [None] * 55
    identity[0], identity[16], identity[21], identity[23] = 42, 7, 1, 0
    client = Client("test-key", "test-secret", lossless_financial_decode=True)
    with patch(
        "requests.post", return_value=response(json.dumps(identity))
    ) as post:
        result = client.rest.auth.get_user_info_raw()
    assert result[0] == 42
    assert result[16] == 7
    assert result[21] == 1
    assert post.call_args.kwargs["url"].endswith("/auth/r/info/user")
    assert "bfx-signature" in post.call_args.kwargs["headers"]


def test_token_does_not_falsely_propagate_after_arithmetic():
    value = FinancialTokenDecimal("1.2300")
    result = value + Decimal("1")
    assert isinstance(result, Decimal)
    assert not hasattr(result, "source_token")
