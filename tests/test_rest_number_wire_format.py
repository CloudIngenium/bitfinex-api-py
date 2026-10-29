"""Numbers on the REST wire: plain decimals, never an exponent, never NaN.

``str(0.00001)`` is ``"1e-05"``, which Bitfinex does not read as a number.
Query params and JSON bodies must send floats and Decimals as plain decimal
strings, and a NaN or infinity must fail before any request goes out, as a
NonFiniteNumberError that existing ``except ValueError`` and ``except
RequestParameterError`` / ``BfxApiError`` clauses both still catch.
"""

import copy
import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from bfxapi._utils.json_encoder import (
    JSONEncoder,
    _FiniteJSONEncoder,
    _NonFiniteNumber,
    _plain_decimal,
)
from bfxapi.exceptions import BfxBaseException
from bfxapi.rest._interface.middleware import Middleware
from bfxapi.rest._interfaces.rest_public_endpoints import (
    RestPublicEndpoints,
    _query,
)
from bfxapi.rest.exceptions import (
    BfxApiError,
    NonFiniteNumberError,
    RequestParameterError,
)
from bfxapi.rest.retry import is_retryable


def _assert_non_finite_error(error: BaseException, value: object) -> None:
    # Callers catch any of these today; the new type must stay inside all.
    assert type(error) is NonFiniteNumberError
    assert isinstance(error, ValueError)
    assert isinstance(error, RequestParameterError)
    assert isinstance(error, BfxApiError)
    assert isinstance(error, BfxBaseException)
    assert "finite" in str(error)
    # Nothing was sent: no server code, no status, never retried.
    assert error.raw is value
    assert error.code is None
    assert error.status_code is None
    assert error.retryable is False
    assert error.outcome_unknown is False
    assert is_retryable(error, idempotent=True) is False
    assert isinstance(error.__cause__, _NonFiniteNumber)


_NON_FINITE = [
    float("nan"),
    float("inf"),
    float("-inf"),
    Decimal("NaN"),
    Decimal("sNaN"),
    Decimal("Infinity"),
    Decimal("-Infinity"),
]


class TestPlainDecimal:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (1e-05, "0.00001"),
            (0.00001, "0.00001"),
            (1e-8, "0.00000001"),
            (1e3, "1000"),
            (-1e-05, "-0.00001"),
            (-2.5e-7, "-0.00000025"),
            (1.5e16, "15000000000000000"),
        ],
    )
    def test_float_exponents_expand(self, value, expected):
        assert _plain_decimal(value) == expected

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (Decimal("1E-5"), "0.00001"),
            (Decimal("1e-8"), "0.00000001"),
            (Decimal("1E+3"), "1000"),
            (Decimal("-1E-5"), "-0.00001"),
            (Decimal("2.50E-6"), "0.0000025"),
            (Decimal("1.000"), "1"),
            (Decimal("1.500"), "1.5"),
            (Decimal("100"), "100"),
            (Decimal("0.00"), "0"),
            (Decimal("-0"), "0"),
        ],
    )
    def test_decimal_exponents_expand_and_trim(self, value, expected):
        assert _plain_decimal(value) == expected

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (0.1, "0.1"),
            (0.3, "0.3"),
            (0.1 + 0.2, "0.30000000000000004"),
            (1.1, "1.1"),
            (123456.789, "123456.789"),
            (1.0, "1"),
            (-0.0, "0"),
        ],
    )
    def test_float_gains_no_binary_noise(self, value, expected):
        # Decimal(repr(x)) keeps the shortest round-trip text; Decimal(x)
        # would send 0.1 as 0.1000000000000000055511151231257827...
        assert _plain_decimal(value) == expected

    def test_float_subclass_with_own_repr(self):
        # np.float64's repr is "np.float64(0.5)"; str() used to send "0.5".
        class Float64(float):
            def __repr__(self) -> str:
                return f"np.float64({float.__repr__(self)})"

        assert _plain_decimal(Float64(0.5)) == "0.5"
        assert _plain_decimal(Float64(1e-05)) == "0.00001"
        assert _query(amount=Float64(0.1)) == {"amount": "0.1"}
        assert json.dumps(Float64(0.1), cls=_FiniteJSONEncoder) == '"0.1"'

    def test_decimal_precision_beyond_context_is_kept(self):
        # No normalize()/quantize: digits past the 28-digit context survive.
        value = Decimal("0.12345678901234567890123456789012345")
        assert _plain_decimal(value) == str(value)

    @pytest.mark.parametrize("value", _NON_FINITE)
    def test_non_finite_raises_value_error(self, value):
        # The internal helper raises the _utils marker; the REST callers
        # turn it into NonFiniteNumberError (tested below).
        with pytest.raises(_NonFiniteNumber, match="finite") as caught:
            _plain_decimal(value)
        assert isinstance(caught.value, ValueError)
        assert caught.value.value is value


class TestQuery:
    def test_numbers_go_out_as_plain_decimals(self):
        assert _query(
            amount=0.00001, rate_limit=Decimal("1E+3"), period=30
        ) == {"amount": "0.00001", "rate_limit": "1000", "period": "30"}

    def test_strings_ints_and_bools_keep_str(self):
        # A caller's own string is sent untouched, trailing zeros included.
        assert _query(amount="1e-05", price="0.50", limit=5, flag=True) == {
            "amount": "1e-05",
            "price": "0.50",
            "limit": "5",
            "flag": "True",
        }

    def test_none_dropped(self):
        assert _query(symbol="tBTCUSD", rate_limit=None) == {
            "symbol": "tBTCUSD"
        }

    @pytest.mark.parametrize("value", _NON_FINITE)
    def test_non_finite_raises(self, value):
        with pytest.raises(NonFiniteNumberError) as caught:
            _query(amount=value)
        _assert_non_finite_error(caught.value, value)


class TestAveragePriceEndpoints:
    def _endpoint(self) -> tuple[RestPublicEndpoints, MagicMock]:
        ep = RestPublicEndpoints("https://api.example.com")
        ep._m = MagicMock()
        return ep, ep._m

    def test_trading_amount_and_cap_without_exponent(self):
        ep, mock_m = self._endpoint()
        mock_m.post.return_value = [10050.5, 0.00001]
        ep.get_trading_market_average_price(
            "tBTCUSD", 0.00001, price_limit=Decimal("1.0E+5")
        )
        mock_m.post.assert_called_once_with(
            "calc/trade/avg",
            params={
                "symbol": "tBTCUSD",
                "amount": "0.00001",
                "rate_limit": "100000",
            },
        )

    def test_funding_negative_amount_and_rate_cap(self):
        ep, mock_m = self._endpoint()
        mock_m.post.return_value = [0.0002, -0.5]
        ep.get_funding_market_average_price(
            "fUSD", -5e-05, 30, rate_limit=Decimal("2E-4")
        )
        mock_m.post.assert_called_once_with(
            "calc/trade/avg",
            params={
                "symbol": "fUSD",
                "amount": "-0.00005",
                "period": "30",
                "rate_limit": "0.0002",
            },
        )

    @pytest.mark.parametrize("value", [float("nan"), Decimal("Infinity")])
    def test_non_finite_amount_never_reaches_middleware(self, value):
        ep, mock_m = self._endpoint()
        with pytest.raises(NonFiniteNumberError) as caught:
            ep.get_trading_market_average_price("tBTCUSD", value)
        _assert_non_finite_error(caught.value, value)
        mock_m.post.assert_not_called()

    @pytest.mark.parametrize("value", [float("-inf"), Decimal("sNaN")])
    def test_non_finite_funding_rate_cap_never_reaches_middleware(self, value):
        ep, mock_m = self._endpoint()
        with pytest.raises(NonFiniteNumberError) as caught:
            ep.get_funding_market_average_price("fUSD", 1, 30, rate_limit=value)
        _assert_non_finite_error(caught.value, value)
        mock_m.post.assert_not_called()

    @patch("bfxapi.rest._interface.middleware.requests.post")
    def test_non_finite_query_never_reaches_network(self, mock_post):
        # Through the real Middleware: the request is never sent.
        ep = RestPublicEndpoints("https://api.example.com")
        with pytest.raises(NonFiniteNumberError):
            ep.get_trading_market_average_price("tBTCUSD", float("nan"))
        mock_post.assert_not_called()


class TestJsonBody:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (1e-05, '"0.00001"'),
            (1e-8, '"0.00000001"'),
            (Decimal("1E+3"), '"1000"'),
            (-1e-05, '"-0.00001"'),
            (0.1, '"0.1"'),
        ],
    )
    def test_body_encoder_has_no_exponent(self, value, expected):
        assert json.dumps(value, cls=JSONEncoder) == expected
        assert json.dumps(value, cls=_FiniteJSONEncoder) == expected

    def test_body_keeps_trailing_zeros_as_before(self):
        # The body encoder's happy path is unchanged: only exponents and
        # non-finite values were ever at issue there.
        body = {"amount": Decimal("1.500"), "flag": True, "skip": None}
        assert json.loads(json.dumps(body, cls=_FiniteJSONEncoder)) == {
            "amount": "1.500",
            "flag": 1,
        }

    @pytest.mark.parametrize("value", _NON_FINITE)
    def test_finite_encoder_rejects_nested_non_finite(self, value):
        with pytest.raises(_NonFiniteNumber):
            json.dumps({"orders": [{"price": value}]}, cls=_FiniteJSONEncoder)

    def test_lenient_encoder_unchanged_for_websocket(self):
        # The WebSocket client still uses JSONEncoder, which never raised.
        assert json.dumps(float("nan"), cls=JSONEncoder) == '"NaN"'

    @pytest.mark.parametrize("value", _NON_FINITE)
    @patch("bfxapi.rest._interface.middleware.requests.post")
    def test_post_rejects_non_finite_before_network(self, mock_post, value):
        m = Middleware(
            "https://api.example.com", api_key="key", api_secret="secret"
        )
        with pytest.raises(NonFiniteNumberError) as caught:
            m.post(
                "auth/w/order/submit",
                body={"amount": "1", "orders": [{"price": value}]},
            )
        _assert_non_finite_error(caught.value, value)
        mock_post.assert_not_called()

    @patch("bfxapi.rest._interface.middleware.requests.post")
    def test_post_rejects_non_finite_before_signing(self, mock_post):
        # The body is encoded before the auth headers are built, so a
        # rejected value never consumes a nonce or gets signed.
        m = Middleware(
            "https://api.example.com", api_key="key", api_secret="secret"
        )
        with (
            patch.object(
                Middleware, "_Middleware__get_authentication_headers"
            ) as sign,
            pytest.raises(NonFiniteNumberError),
        ):
            m.post("auth/w/order/submit", body={"amount": float("nan")})
        sign.assert_not_called()
        mock_post.assert_not_called()

    def test_errors_survive_reduce_round_trip(self):
        # deepcopy rebuilds through __reduce_ex__, the protocol pickle uses
        # (e.g. to return an error from a process pool).
        public = NonFiniteNumberError("not finite", value=Decimal("Infinity"))
        restored = copy.deepcopy(public)
        assert type(restored) is NonFiniteNumberError
        assert str(restored) == "not finite"
        assert restored.raw == Decimal("Infinity")
        assert restored.value == Decimal("Infinity")

        internal = _NonFiniteNumber(Decimal("NaN"))
        restored_internal = copy.deepcopy(internal)
        assert str(restored_internal) == str(internal)
        assert restored_internal.value.is_nan()

    @patch("bfxapi.rest._interface.middleware.requests.post")
    def test_post_non_finite_caught_by_existing_except_clauses(self, mock_post):
        # The shapes callers already use: a ValueError tuple, and a
        # BfxApiError / RuntimeError tuple that has no ValueError in it.
        m = Middleware("https://api.example.com")
        with pytest.raises(ValueError):
            m.post("calc/fx", body={"amount": float("inf")})
        try:
            m.post("calc/fx", body={"amount": float("inf")})
        except (BfxApiError, RuntimeError) as e:
            assert isinstance(e, NonFiniteNumberError)
        else:
            pytest.fail("NonFiniteNumberError was not raised")
        mock_post.assert_not_called()

    @patch("bfxapi.rest._interface.middleware.requests.post")
    def test_post_empty_body_sends_no_data(self, mock_post):
        response = MagicMock()
        response.json.return_value = [1]
        mock_post.return_value = response
        Middleware("https://api.example.com").post("calc/fx", body={})
        assert mock_post.call_args.kwargs["data"] is None

    @patch("bfxapi.rest._interface.middleware.requests.post")
    def test_post_body_wire_format(self, mock_post):
        response = MagicMock()
        response.json.return_value = [1]
        mock_post.return_value = response
        m = Middleware("https://api.example.com")
        m.post("calc/fx", body={"amount": 1e-05, "n": 3})
        sent = json.loads(mock_post.call_args.kwargs["data"])
        assert sent == {"amount": "0.00001", "n": 3}
