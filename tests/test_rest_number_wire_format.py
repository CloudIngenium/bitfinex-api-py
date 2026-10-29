"""Numbers on the REST wire: plain decimals, never an exponent, never NaN.

``str(0.00001)`` is ``"1e-05"``, which Bitfinex does not read as a number.
Query params and JSON bodies must send floats and Decimals as plain decimal
strings, and a NaN or infinity must fail before any request goes out.
"""

import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from bfxapi._utils.json_encoder import (
    JSONEncoder,
    _FiniteJSONEncoder,
    _plain_decimal,
)
from bfxapi.rest._interface.middleware import Middleware
from bfxapi.rest._interfaces.rest_public_endpoints import (
    RestPublicEndpoints,
    _query,
)

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

    def test_decimal_precision_beyond_context_is_kept(self):
        # No normalize()/quantize: digits past the 28-digit context survive.
        value = Decimal("0.12345678901234567890123456789012345")
        assert _plain_decimal(value) == str(value)

    @pytest.mark.parametrize("value", _NON_FINITE)
    def test_non_finite_raises_value_error(self, value):
        with pytest.raises(ValueError, match="finite"):
            _plain_decimal(value)


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
        with pytest.raises(ValueError):
            _query(amount=value)


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
        with pytest.raises(ValueError):
            ep.get_trading_market_average_price("tBTCUSD", value)
        mock_m.post.assert_not_called()


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
        with pytest.raises(ValueError):
            json.dumps({"orders": [{"price": value}]}, cls=_FiniteJSONEncoder)

    def test_lenient_encoder_unchanged_for_websocket(self):
        # The WebSocket client still uses JSONEncoder, which never raised.
        assert json.dumps(float("nan"), cls=JSONEncoder) == '"NaN"'

    @patch("bfxapi.rest._interface.middleware.requests.post")
    def test_post_rejects_nan_before_network(self, mock_post):
        m = Middleware(
            "https://api.example.com", api_key="key", api_secret="secret"
        )
        with pytest.raises(ValueError):
            m.post(
                "auth/w/order/submit",
                body={"amount": "1", "price": float("nan")},
            )
        mock_post.assert_not_called()

    @patch("bfxapi.rest._interface.middleware.requests.post")
    def test_post_body_wire_format(self, mock_post):
        response = MagicMock()
        response.json.return_value = [1]
        mock_post.return_value = response
        m = Middleware("https://api.example.com")
        m.post("calc/fx", body={"amount": 1e-05, "n": 3})
        sent = json.loads(mock_post.call_args.kwargs["data"])
        assert sent == {"amount": "0.00001", "n": 3}
