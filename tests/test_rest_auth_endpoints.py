from typing import Any
from unittest.mock import MagicMock

from bfxapi.rest._interfaces.rest_auth_endpoints import RestAuthEndpoints
from bfxapi.types import (
    BalanceAvailable,
    BaseMarginInfo,
    DepositAddress,
    DerivativePositionCollateral,
    DerivativePositionCollateralLimits,
    FundingAutoRenew,
    FundingCredit,
    FundingInfo,
    FundingLoan,
    FundingOffer,
    FundingTrade,
    Ledger,
    LightningNetworkInvoice,
    LoginHistory,
    Movement,
    Notification,
    Order,
    OrderTrade,
    Position,
    PositionAudit,
    PositionClaim,
    PositionHistory,
    PositionIncrease,
    PositionIncreaseInfo,
    PositionSnapshot,
    SymbolMarginInfo,
    Trade,
    Transfer,
    UserInfo,
    Wallet,
    Withdrawal,
)


def _make_endpoint() -> tuple[RestAuthEndpoints, MagicMock]:
    ep = RestAuthEndpoints(
        "https://api.example.com", api_key="k", api_secret="s"
    )
    ep._m = MagicMock()
    return ep, ep._m


def _row(size: int, values: dict[int, Any]) -> list[Any]:
    """Build a positional payload with exactly ``size`` slots.

    The serializers raise AssertionError when the array is shorter than
    their ``labels`` list, so every fixture is sized from that list and
    only the mapped indexes are filled.
    """
    row: list[Any] = [None] * size
    for index, value in values.items():
        row[index] = value
    return row


def _notification(
    data: Any, *, kind: str = "on-req", status: str = "SUCCESS"
) -> list[Any]:
    """Wrap ``data`` in the 8-slot Bitfinex notification envelope."""
    return [
        1609459200000,
        kind,
        123456789,
        None,
        data,
        None,
        status,
        "Success.",
    ]


# 32 labels — see serializers.Order.
_ORDER = _row(
    32,
    {
        0: 1234567,
        1: 0,
        2: 987654,
        3: "tBTCUSD",
        4: 1609459200000,
        5: 1609459300000,
        6: 0.5,
        7: 1.0,
        8: "EXCHANGE LIMIT",
        9: "EXCHANGE MARKET",
        10: 1609545600000,
        12: 4096,
        13: "ACTIVE",
        16: 30000.0,
        17: 0.0,
        18: 0.0,
        19: 0.0,
        23: 0,
        24: 0,
        25: 0,
        28: "API>BFX",
        31: {"aff_code": "bfxapi"},
    },
)

_ORDER_2 = _row(
    32,
    {
        0: 7654321,
        1: 0,
        2: 456789,
        3: "tETHUSD",
        4: 1609459400000,
        5: 1609459500000,
        6: 2.0,
        7: 2.0,
        8: "LIMIT",
        12: 0,
        13: "ACTIVE",
        16: 750.0,
        31: None,
    },
)

# 20 labels — see serializers.Position.
_POSITION = _row(
    20,
    {
        0: "tBTCUSD",
        1: "ACTIVE",
        2: 0.5,
        3: 29000.0,
        4: 12.5,
        5: 0,
        6: 500.0,
        7: 3.4,
        8: 21000.0,
        9: 3.3,
        11: 111222,
        12: 1609459200000,
        13: 1609459300000,
        15: 1,
        17: 4500.0,
        18: 1200.0,
        19: {"reason": "TRADE"},
    },
)

# 21 labels — see serializers.FundingOffer.
_FUNDING_OFFER = _row(
    21,
    {
        0: 41238905,
        1: "fUSD",
        2: 1609459200000,
        3: 1609459300000,
        4: 1000.0,
        5: 1000.0,
        6: "LIMIT",
        9: 0,
        10: "ACTIVE",
        14: 0.0002,
        15: 30,
        16: 0,
        17: 0,
        19: 0,
    },
)

# 21 labels — see serializers.FundingLoan.
_FUNDING_LOAN = _row(
    21,
    {
        0: 2995368,
        1: "fUSD",
        2: -1,
        3: 1609459200000,
        4: 1609459300000,
        5: 200.0,
        6: 0,
        7: "ACTIVE",
        8: "FIXED",
        11: 0.00019,
        12: 2,
        13: 1609459210000,
        14: 1609459220000,
        15: 0,
        16: 0,
        18: 0,
        20: 0,
    },
)

# 22 labels — see serializers.FundingCredit.
_FUNDING_CREDIT = _row(
    22,
    {
        0: 26190108,
        1: "fUSD",
        2: 1,
        3: 1609459200000,
        4: 1609459300000,
        5: 350.0,
        6: 0,
        7: "ACTIVE",
        8: "FIXED",
        11: 0.00022,
        12: 7,
        13: 1609459210000,
        14: 1609459220000,
        15: 0,
        16: 0,
        18: 1,
        20: 0,
        21: "tBTCUSD",
    },
)


class TestUserInfo:
    def test_get_user_info(self):
        ep, mock_m = _make_endpoint()
        # 55 labels — see serializers.UserInfo.
        mock_m.post.return_value = _row(
            55,
            {
                0: 1000001,
                1: "trader@example.com",
                2: "jcarlos",
                3: 1609459200000,
                4: 1,
                5: 3,
                7: "Mexico City",
                8: "es",
                9: "bitfinex",
                10: 1,
                14: 1577836800000,
                15: 2,
                23: 1,
                24: 0,
                25: 1,
                26: ["u2f"],
                34: 0,
                38: 1,
                39: 0,
                44: 1735689600000,
                47: 0,
                49: ["MX"],
                50: ["MX"],
                54: 0,
            },
        )
        result = ep.get_user_info()
        mock_m.post.assert_called_once_with("auth/r/info/user")
        assert isinstance(result, UserInfo)
        assert result.id == 1000001
        assert result.email == "trader@example.com"
        assert result.username == "jcarlos"
        assert result.two_factors_authentication_modes == ["u2f"]

    def test_get_login_history(self):
        ep, mock_m = _make_endpoint()
        # 8 labels — see serializers.LoginHistory.
        mock_m.post.return_value = [
            _row(
                8,
                {
                    0: 55555,
                    2: 1609459200000,
                    4: "203.0.113.7",
                    7: {"user_agent": "curl/8.5.0"},
                },
            )
        ]
        result = ep.get_login_history()
        mock_m.post.assert_called_once_with("auth/r/logins/hist")
        assert len(result) == 1
        assert isinstance(result[0], LoginHistory)
        assert result[0].id == 55555
        assert result[0].ip == "203.0.113.7"
        assert result[0].extra_info == {"user_agent": "curl/8.5.0"}


class TestWallets:
    def test_get_wallets(self):
        ep, mock_m = _make_endpoint()
        # 7 labels — see serializers.Wallet.
        mock_m.post.return_value = [
            [
                "exchange",
                "BTC",
                1.5,
                0.0,
                1.5,
                "Exchange 0.5 BTC for USD",
                {"reason": "TRADE"},
            ]
        ]
        result = ep.get_wallets()
        mock_m.post.assert_called_once_with("auth/r/wallets")
        assert len(result) == 1
        assert isinstance(result[0], Wallet)
        assert result[0].wallet_type == "exchange"
        assert result[0].currency == "BTC"
        assert result[0].available_balance == 1.5

    def test_get_balance_available_for_orders_or_offers(self):
        ep, mock_m = _make_endpoint()
        # 1 label — see serializers.BalanceAvailable.
        mock_m.post.return_value = [12345.678]
        result = ep.get_balance_available_for_orders_or_offers(
            "tBTCUSD", "EXCHANGE"
        )
        mock_m.post.assert_called_once_with(
            "auth/calc/order/avail",
            body={
                "symbol": "tBTCUSD",
                "type": "EXCHANGE",
                "dir": None,
                "rate": None,
                "lev": None,
            },
        )
        assert isinstance(result, BalanceAvailable)
        assert result.amount == 12345.678

    def test_get_balance_available_with_optional_args(self):
        ep, mock_m = _make_endpoint()
        mock_m.post.return_value = [42.0]
        result = ep.get_balance_available_for_orders_or_offers(
            "tBTCUSD", "MARGIN", dir=1, rate="30000", lev="10"
        )
        mock_m.post.assert_called_once_with(
            "auth/calc/order/avail",
            body={
                "symbol": "tBTCUSD",
                "type": "MARGIN",
                "dir": 1,
                "rate": "30000",
                "lev": "10",
            },
        )
        assert isinstance(result, BalanceAvailable)
        assert result.amount == 42.0


class TestOrders:
    def test_get_orders_without_symbol(self):
        ep, mock_m = _make_endpoint()
        mock_m.post.return_value = [_ORDER]
        result = ep.get_orders()
        mock_m.post.assert_called_once_with("auth/r/orders", body={"id": None})
        assert len(result) == 1
        assert isinstance(result[0], Order)
        assert result[0].id == 1234567
        assert result[0].symbol == "tBTCUSD"
        assert result[0].order_status == "ACTIVE"

    def test_get_orders_with_symbol(self):
        ep, mock_m = _make_endpoint()
        mock_m.post.return_value = [_ORDER]
        result = ep.get_orders(symbol="tBTCUSD", ids=["1234567"])
        mock_m.post.assert_called_once_with(
            "auth/r/orders/tBTCUSD", body={"id": ["1234567"]}
        )
        assert result[0].price == 30000.0
        assert result[0].order_type == "EXCHANGE LIMIT"

    def test_submit_order(self):
        ep, mock_m = _make_endpoint()
        mock_m.post.return_value = _notification(_ORDER)
        result = ep.submit_order("EXCHANGE LIMIT", "tBTCUSD", "0.5", "30000")
        mock_m.post.assert_called_once_with(
            "auth/w/order/submit",
            body={
                "type": "EXCHANGE LIMIT",
                "symbol": "tBTCUSD",
                "amount": "0.5",
                "price": "30000",
                "lev": None,
                "price_trailing": None,
                "price_aux_limit": None,
                "price_oco_stop": None,
                "gid": None,
                "cid": None,
                "flags": None,
                "tif": None,
                "meta": None,
            },
        )
        assert isinstance(result, Notification)
        assert result.status == "SUCCESS"
        assert result.mts == 1609459200000
        assert isinstance(result.data, Order)
        assert result.data.id == 1234567
        assert result.data.amount == 0.5

    def test_submit_order_with_optional_args(self):
        ep, mock_m = _make_endpoint()
        mock_m.post.return_value = _notification(_ORDER)
        result = ep.submit_order(
            "LIMIT",
            "tBTCUSD",
            "0.5",
            "30000",
            lev=10,
            price_trailing="10",
            price_aux_limit="29000",
            price_oco_stop="28000",
            gid=1,
            cid=2,
            flags=4096,
            tif="2021-01-01 00:00:00",
            meta={"aff_code": "bfxapi"},
        )
        mock_m.post.assert_called_once_with(
            "auth/w/order/submit",
            body={
                "type": "LIMIT",
                "symbol": "tBTCUSD",
                "amount": "0.5",
                "price": "30000",
                "lev": 10,
                "price_trailing": "10",
                "price_aux_limit": "29000",
                "price_oco_stop": "28000",
                "gid": 1,
                "cid": 2,
                "flags": 4096,
                "tif": "2021-01-01 00:00:00",
                "meta": {"aff_code": "bfxapi"},
            },
        )
        assert result.data.symbol == "tBTCUSD"
        assert result.data.flags == 4096

    def test_update_order(self):
        ep, mock_m = _make_endpoint()
        mock_m.post.return_value = _notification(_ORDER, kind="ou-req")
        result = ep.update_order(1234567, amount="0.25", price="31000")
        mock_m.post.assert_called_once_with(
            "auth/w/order/update",
            body={
                "id": 1234567,
                "amount": "0.25",
                "price": "31000",
                "cid": None,
                "cid_date": None,
                "gid": None,
                "flags": None,
                "lev": None,
                "delta": None,
                "price_aux_limit": None,
                "price_trailing": None,
                "tif": None,
            },
        )
        assert result.type == "ou-req"
        assert isinstance(result.data, Order)
        assert result.data.id == 1234567
        assert result.data.mts_update == 1609459300000

    def test_cancel_order(self):
        ep, mock_m = _make_endpoint()
        mock_m.post.return_value = _notification(_ORDER, kind="oc-req")
        result = ep.cancel_order(id=1234567)
        mock_m.post.assert_called_once_with(
            "auth/w/order/cancel",
            body={"id": 1234567, "cid": None, "cid_date": None},
        )
        assert result.type == "oc-req"
        assert result.data.id == 1234567
        assert result.data.order_status == "ACTIVE"

    def test_cancel_order_by_cid(self):
        ep, mock_m = _make_endpoint()
        mock_m.post.return_value = _notification(_ORDER, kind="oc-req")
        result = ep.cancel_order(cid=987654, cid_date="2021-01-01")
        mock_m.post.assert_called_once_with(
            "auth/w/order/cancel",
            body={"id": None, "cid": 987654, "cid_date": "2021-01-01"},
        )
        assert result.status == "SUCCESS"
        assert result.data.cid == 987654

    def test_cancel_order_multi(self):
        ep, mock_m = _make_endpoint()
        mock_m.post.return_value = _notification(
            [_ORDER, _ORDER_2], kind="oc_multi-req"
        )
        result = ep.cancel_order_multi(id=[1234567, 7654321])
        mock_m.post.assert_called_once_with(
            "auth/w/order/cancel/multi",
            body={
                "id": [1234567, 7654321],
                "cid": None,
                "gid": None,
                "all": None,
            },
        )
        assert result.type == "oc_multi-req"
        assert len(result.data) == 2
        assert isinstance(result.data[0], Order)
        assert result.data[0].id == 1234567
        assert result.data[1].symbol == "tETHUSD"

    def test_cancel_order_multi_all(self):
        ep, mock_m = _make_endpoint()
        mock_m.post.return_value = _notification([], kind="oc_multi-req")
        result = ep.cancel_order_multi(all=True)
        mock_m.post.assert_called_once_with(
            "auth/w/order/cancel/multi",
            body={"id": None, "cid": None, "gid": None, "all": True},
        )
        assert result.data == []
        assert result.status == "SUCCESS"

    def test_get_orders_history_without_symbol(self):
        ep, mock_m = _make_endpoint()
        mock_m.post.return_value = [_ORDER]
        result = ep.get_orders_history()
        mock_m.post.assert_called_once_with(
            "auth/r/orders/hist",
            body={"id": None, "start": None, "end": None, "limit": None},
        )
        assert len(result) == 1
        assert result[0].id == 1234567
        assert result[0].mts_create == 1609459200000

    def test_get_orders_history_with_symbol(self):
        ep, mock_m = _make_endpoint()
        mock_m.post.return_value = [_ORDER]
        result = ep.get_orders_history(
            symbol="tBTCUSD",
            ids=[1234567],
            start="1609459200000",
            end="1609545600000",
            limit=25,
        )
        mock_m.post.assert_called_once_with(
            "auth/r/orders/tBTCUSD/hist",
            body={
                "id": [1234567],
                "start": "1609459200000",
                "end": "1609545600000",
                "limit": 25,
            },
        )
        assert isinstance(result[0], Order)
        assert result[0].amount_orig == 1.0
        assert result[0].routing == "API>BFX"

    def test_get_order_trades(self):
        ep, mock_m = _make_endpoint()
        # 12 labels — see serializers.OrderTrade.
        mock_m.post.return_value = [
            [
                7654321,
                "tBTCUSD",
                1609459250000,
                1234567,
                0.5,
                30000.0,
                None,
                None,
                1,
                -0.6,
                "USD",
                987654,
            ]
        ]
        result = ep.get_order_trades("tBTCUSD", 1234567)
        mock_m.post.assert_called_once_with(
            "auth/r/order/tBTCUSD:1234567/trades"
        )
        assert len(result) == 1
        assert isinstance(result[0], OrderTrade)
        assert result[0].id == 7654321
        assert result[0].exec_price == 30000.0
        assert result[0].fee_currency == "USD"


class TestTrades:
    def test_get_trades_history_without_symbol(self):
        ep, mock_m = _make_endpoint()
        # 12 labels — see serializers.Trade.
        mock_m.post.return_value = [
            [
                402088407,
                "tBTCUSD",
                1609459250000,
                1234567,
                0.5,
                30000.0,
                "EXCHANGE LIMIT",
                30000.0,
                1,
                -0.6,
                "USD",
                987654,
            ]
        ]
        result = ep.get_trades_history()
        mock_m.post.assert_called_once_with(
            "auth/r/trades/hist",
            body={"sort": None, "start": None, "end": None, "limit": None},
        )
        assert len(result) == 1
        assert isinstance(result[0], Trade)
        assert result[0].id == 402088407
        assert result[0].exec_amount == 0.5
        assert result[0].order_type == "EXCHANGE LIMIT"

    def test_get_trades_history_with_symbol(self):
        ep, mock_m = _make_endpoint()
        mock_m.post.return_value = [
            [
                402088407,
                "tBTCUSD",
                1609459250000,
                1234567,
                -0.5,
                30500.0,
                "LIMIT",
                30500.0,
                0,
                -0.61,
                "USD",
                None,
            ]
        ]
        result = ep.get_trades_history(
            symbol="tBTCUSD",
            sort=-1,
            start="1609459200000",
            end="1609545600000",
            limit=50,
        )
        mock_m.post.assert_called_once_with(
            "auth/r/trades/tBTCUSD/hist",
            body={
                "sort": -1,
                "start": "1609459200000",
                "end": "1609545600000",
                "limit": 50,
            },
        )
        assert result[0].symbol == "tBTCUSD"
        assert result[0].maker == 0
        assert result[0].exec_price == 30500.0


class TestLedgers:
    def test_get_ledgers_without_currency(self):
        ep, mock_m = _make_endpoint()
        # 9 labels — see serializers.Ledger.
        mock_m.post.return_value = [
            [
                2531822314,
                "USD",
                None,
                1609459200000,
                None,
                -15000.0,
                85000.0,
                None,
                "Trading fees for 0.5 BTC",
            ]
        ]
        result = ep.get_ledgers()
        mock_m.post.assert_called_once_with(
            "auth/r/ledgers/hist",
            body={
                "category": None,
                "start": None,
                "end": None,
                "limit": None,
            },
        )
        assert len(result) == 1
        assert isinstance(result[0], Ledger)
        assert result[0].id == 2531822314
        assert result[0].balance == 85000.0
        assert result[0].description == "Trading fees for 0.5 BTC"

    def test_get_ledgers_with_currency(self):
        ep, mock_m = _make_endpoint()
        mock_m.post.return_value = [
            [
                2531822315,
                "BTC",
                None,
                1609459300000,
                None,
                0.5,
                2.5,
                None,
                "Exchange 0.5 BTC for USD",
            ]
        ]
        result = ep.get_ledgers(
            "BTC",
            category=5,
            start="1609459200000",
            end="1609545600000",
            limit=10,
        )
        mock_m.post.assert_called_once_with(
            "auth/r/ledgers/BTC/hist",
            body={
                "category": 5,
                "start": "1609459200000",
                "end": "1609545600000",
                "limit": 10,
            },
        )
        assert result[0].currency == "BTC"
        assert result[0].amount == 0.5
        assert result[0].mts == 1609459300000


class TestMarginInfo:
    def test_get_base_margin_info(self):
        ep, mock_m = _make_endpoint()
        # 6 labels, flat=True — the nested block is spliced in.
        mock_m.post.return_value = [
            "base",
            [100.0, 200.0, 300.0, 400.0, 500.0],
        ]
        result = ep.get_base_margin_info()
        mock_m.post.assert_called_once_with("auth/r/info/margin/base")
        assert isinstance(result, BaseMarginInfo)
        assert result.user_pl == 100.0
        assert result.margin_balance == 300.0
        assert result.margin_min == 500.0

    def test_get_symbol_margin_info(self):
        ep, mock_m = _make_endpoint()
        # 6 labels, flat=True.
        mock_m.post.return_value = [
            "sym",
            "tBTCUSD",
            [1000.0, 2000.0, 500.0, 600.0],
        ]
        result = ep.get_symbol_margin_info("tBTCUSD")
        mock_m.post.assert_called_once_with("auth/r/info/margin/tBTCUSD")
        assert isinstance(result, SymbolMarginInfo)
        assert result.symbol == "tBTCUSD"
        assert result.tradable_balance == 1000.0
        assert result.sell == 600.0

    def test_get_all_symbols_margin_info(self):
        ep, mock_m = _make_endpoint()
        mock_m.post.return_value = [
            ["sym", "tBTCUSD", [1000.0, 2000.0, 500.0, 600.0]],
            ["sym", "tETHUSD", [10.0, 20.0, 5.0, 6.0]],
        ]
        result = ep.get_all_symbols_margin_info()
        mock_m.post.assert_called_once_with("auth/r/info/margin/sym_all")
        assert len(result) == 2
        assert isinstance(result[0], SymbolMarginInfo)
        assert result[0].symbol == "tBTCUSD"
        assert result[1].symbol == "tETHUSD"
        assert result[1].gross_balance == 20.0


class TestPositions:
    def test_get_positions(self):
        ep, mock_m = _make_endpoint()
        mock_m.post.return_value = [_POSITION]
        result = ep.get_positions()
        mock_m.post.assert_called_once_with("auth/r/positions")
        assert len(result) == 1
        assert isinstance(result[0], Position)
        assert result[0].symbol == "tBTCUSD"
        assert result[0].position_id == 111222
        assert result[0].pl == 500.0

    def test_claim_position(self):
        ep, mock_m = _make_endpoint()
        # 20 labels — see serializers.PositionClaim.
        claim = _row(
            20,
            {
                0: "tBTCUSD",
                1: "ACTIVE",
                2: 0.5,
                3: 29000.0,
                4: 12.5,
                5: 0,
                11: 111222,
                12: 1609459200000,
                13: 1609459300000,
                15: 1,
                17: "4500.0",
                18: "1200.0",
                19: {"reason": "TRADE"},
            },
        )
        mock_m.post.return_value = _notification(claim, kind="pos-claim-req")
        result = ep.claim_position(111222, amount="0.5")
        mock_m.post.assert_called_once_with(
            "auth/w/position/claim", body={"id": 111222, "amount": "0.5"}
        )
        assert result.type == "pos-claim-req"
        assert isinstance(result.data, PositionClaim)
        assert result.data.position_id == 111222
        assert result.data.position_status == "ACTIVE"

    def test_increase_position(self):
        ep, mock_m = _make_endpoint()
        # 4 labels — see serializers.PositionIncrease.
        mock_m.post.return_value = _notification(
            ["tBTCUSD", None, 0.1, 29500.0], kind="pos-incr-req"
        )
        result = ep.increase_position("tBTCUSD", "0.1")
        mock_m.post.assert_called_once_with(
            "auth/w/position/increase",
            body={"symbol": "tBTCUSD", "amount": "0.1"},
        )
        assert result.type == "pos-incr-req"
        assert isinstance(result.data, PositionIncrease)
        assert result.data.symbol == "tBTCUSD"
        assert result.data.base_price == 29500.0

    def test_get_increase_position_info(self):
        ep, mock_m = _make_endpoint()
        # 18 labels, flat=True — nested blocks are spliced in.
        mock_m.post.return_value = [
            2.5,
            0.5,
            [10.0, 20000.0, 25000.0, 1.2, 1.5],
            None,
            None,
            None,
            None,
            [9000.0],
            None,
            None,
            [1500.0, 750.0, "USD", "USD"],
        ]
        result = ep.get_increase_position_info("tBTCUSD", "0.1")
        mock_m.post.assert_called_once_with(
            "auth/r/position/increase/info",
            body={"symbol": "tBTCUSD", "amount": "0.1"},
        )
        assert isinstance(result, PositionIncreaseInfo)
        assert result.max_pos == 2.5
        assert result.base_currency_balance == 10.0
        assert result.funding_avail == 9000.0
        assert result.funding_required_currency == "USD"

    def test_get_positions_history(self):
        ep, mock_m = _make_endpoint()
        # 14 labels — see serializers.PositionHistory.
        mock_m.post.return_value = [
            _row(
                14,
                {
                    0: "tBTCUSD",
                    1: "CLOSED",
                    2: 0.0,
                    3: 29000.0,
                    4: 12.5,
                    5: 0,
                    11: 111222,
                    12: 1609459200000,
                    13: 1609545600000,
                },
            )
        ]
        result = ep.get_positions_history(limit=10)
        mock_m.post.assert_called_once_with(
            "auth/r/positions/hist",
            body={"start": None, "end": None, "limit": 10},
        )
        assert len(result) == 1
        assert isinstance(result[0], PositionHistory)
        assert result[0].status == "CLOSED"
        assert result[0].position_id == 111222

    def test_get_positions_snapshot(self):
        ep, mock_m = _make_endpoint()
        # 14 labels — see serializers.PositionSnapshot.
        mock_m.post.return_value = [
            _row(
                14,
                {
                    0: "tETHUSD",
                    1: "ACTIVE",
                    2: 3.0,
                    3: 750.0,
                    4: 1.25,
                    5: 1,
                    11: 333444,
                    12: 1609459200000,
                    13: 1609545600000,
                },
            )
        ]
        result = ep.get_positions_snapshot(
            start="1609459200000", end="1609545600000", limit=5
        )
        mock_m.post.assert_called_once_with(
            "auth/r/positions/snap",
            body={
                "start": "1609459200000",
                "end": "1609545600000",
                "limit": 5,
            },
        )
        assert isinstance(result[0], PositionSnapshot)
        assert result[0].symbol == "tETHUSD"
        assert result[0].base_price == 750.0

    def test_get_positions_audit(self):
        ep, mock_m = _make_endpoint()
        # 20 labels — see serializers.PositionAudit.
        mock_m.post.return_value = [
            _row(
                20,
                {
                    0: "tBTCUSD",
                    1: "ACTIVE",
                    2: 0.5,
                    3: 29000.0,
                    4: 12.5,
                    5: 0,
                    11: 111222,
                    12: 1609459200000,
                    13: 1609459300000,
                    15: 1,
                    17: 4500.0,
                    18: 1200.0,
                    19: {"reason": "TRADE"},
                },
            )
        ]
        result = ep.get_positions_audit(ids=[111222], limit=1)
        mock_m.post.assert_called_once_with(
            "auth/r/positions/audit",
            body={
                "ids": [111222],
                "start": None,
                "end": None,
                "limit": 1,
            },
        )
        assert isinstance(result[0], PositionAudit)
        assert result[0].position_id == 111222
        assert result[0].collateral == 4500.0

    def test_set_derivative_position_collateral(self):
        ep, mock_m = _make_endpoint()
        # The endpoint unwraps the outer array before parsing (1 label).
        mock_m.post.return_value = [[1]]
        result = ep.set_derivative_position_collateral("tBTCF0:USTF0", "5000")
        mock_m.post.assert_called_once_with(
            "auth/w/deriv/collateral/set",
            body={"symbol": "tBTCF0:USTF0", "collateral": "5000"},
        )
        assert isinstance(result, DerivativePositionCollateral)
        assert result.status == 1

    def test_get_derivative_position_collateral_limits(self):
        ep, mock_m = _make_endpoint()
        # 2 labels — see DerivativePositionCollateralLimits.
        mock_m.post.return_value = [1200.5, 48000.0]
        result = ep.get_derivative_position_collateral_limits("tBTCF0:USTF0")
        mock_m.post.assert_called_once_with(
            "auth/calc/deriv/collateral/limit",
            body={"symbol": "tBTCF0:USTF0"},
        )
        assert isinstance(result, DerivativePositionCollateralLimits)
        assert result.min_collateral == 1200.5
        assert result.max_collateral == 48000.0


class TestFunding:
    def test_get_funding_offers_without_symbol(self):
        ep, mock_m = _make_endpoint()
        mock_m.post.return_value = [_FUNDING_OFFER]
        result = ep.get_funding_offers()
        mock_m.post.assert_called_once_with("auth/r/funding/offers")
        assert len(result) == 1
        assert isinstance(result[0], FundingOffer)
        assert result[0].id == 41238905
        assert result[0].symbol == "fUSD"
        assert result[0].rate == 0.0002

    def test_get_funding_offers_with_symbol(self):
        ep, mock_m = _make_endpoint()
        mock_m.post.return_value = [_FUNDING_OFFER]
        result = ep.get_funding_offers(symbol="fUSD")
        mock_m.post.assert_called_once_with("auth/r/funding/offers/fUSD")
        assert result[0].period == 30
        assert result[0].offer_status == "ACTIVE"

    def test_submit_funding_offer(self):
        ep, mock_m = _make_endpoint()
        mock_m.post.return_value = _notification(_FUNDING_OFFER, kind="fon-req")
        result = ep.submit_funding_offer(
            "LIMIT", "fUSD", "1000", "0.0002", 30, flags=0
        )
        mock_m.post.assert_called_once_with(
            "auth/w/funding/offer/submit",
            body={
                "type": "LIMIT",
                "symbol": "fUSD",
                "amount": "1000",
                "rate": "0.0002",
                "period": 30,
                "flags": 0,
            },
        )
        assert result.type == "fon-req"
        assert isinstance(result.data, FundingOffer)
        assert result.data.id == 41238905
        assert result.data.amount == 1000.0

    def test_cancel_funding_offer(self):
        ep, mock_m = _make_endpoint()
        mock_m.post.return_value = _notification(_FUNDING_OFFER, kind="foc-req")
        result = ep.cancel_funding_offer(41238905)
        mock_m.post.assert_called_once_with(
            "auth/w/funding/offer/cancel", body={"id": 41238905}
        )
        assert result.type == "foc-req"
        assert isinstance(result.data, FundingOffer)
        assert result.data.id == 41238905
        assert result.data.offer_type == "LIMIT"

    def test_cancel_all_funding_offers(self):
        ep, mock_m = _make_endpoint()
        mock_m.post.return_value = _notification(None, kind="foc_all-req")
        result = ep.cancel_all_funding_offers("USD")
        mock_m.post.assert_called_once_with(
            "auth/w/funding/offer/cancel/all", body={"currency": "USD"}
        )
        assert result.type == "foc_all-req"
        assert result.status == "SUCCESS"
        assert result.data is None

    def test_submit_funding_close(self):
        ep, mock_m = _make_endpoint()
        mock_m.post.return_value = _notification(None, kind="fclose-req")
        result = ep.submit_funding_close(2995368)
        mock_m.post.assert_called_once_with(
            "auth/w/funding/close", body={"id": 2995368}
        )
        assert result.type == "fclose-req"
        assert result.message_id == 123456789
        assert result.data is None

    def test_toggle_auto_renew(self):
        ep, mock_m = _make_endpoint()
        # 4 labels — see serializers.FundingAutoRenew.
        mock_m.post.return_value = _notification(
            ["USD", 2, 0.0002, 5000.0], kind="fa-req"
        )
        result = ep.toggle_auto_renew(
            True, "USD", amount="5000", rate=1, period=2
        )
        mock_m.post.assert_called_once_with(
            "auth/w/funding/auto",
            body={
                "status": True,
                "currency": "USD",
                "amount": "5000",
                "rate": 1,
                "period": 2,
            },
        )
        assert isinstance(result.data, FundingAutoRenew)
        assert result.data.currency == "USD"
        assert result.data.threshold == 5000.0

    def test_toggle_keep_funding(self):
        ep, mock_m = _make_endpoint()
        mock_m.post.return_value = _notification(None, kind="fkeep-req")
        result = ep.toggle_keep_funding(
            "credit", ids=[2995368], changes={2995368: 1}
        )
        mock_m.post.assert_called_once_with(
            "auth/w/funding/keep",
            body={
                "type": "credit",
                "id": [2995368],
                "changes": {2995368: 1},
            },
        )
        assert result.type == "fkeep-req"
        assert result.status == "SUCCESS"
        assert result.data is None

    def test_get_funding_offers_history_without_symbol(self):
        ep, mock_m = _make_endpoint()
        mock_m.post.return_value = [_FUNDING_OFFER]
        result = ep.get_funding_offers_history()
        mock_m.post.assert_called_once_with(
            "auth/r/funding/offers/hist",
            body={"start": None, "end": None, "limit": None},
        )
        assert isinstance(result[0], FundingOffer)
        assert result[0].id == 41238905
        assert result[0].mts_create == 1609459200000

    def test_get_funding_offers_history_with_symbol(self):
        ep, mock_m = _make_endpoint()
        mock_m.post.return_value = [_FUNDING_OFFER]
        result = ep.get_funding_offers_history(
            symbol="fUSD",
            start="1609459200000",
            end="1609545600000",
            limit=25,
        )
        mock_m.post.assert_called_once_with(
            "auth/r/funding/offers/fUSD/hist",
            body={
                "start": "1609459200000",
                "end": "1609545600000",
                "limit": 25,
            },
        )
        assert result[0].symbol == "fUSD"
        assert result[0].amount_orig == 1000.0

    def test_get_funding_loans_without_symbol(self):
        ep, mock_m = _make_endpoint()
        mock_m.post.return_value = [_FUNDING_LOAN]
        result = ep.get_funding_loans()
        mock_m.post.assert_called_once_with("auth/r/funding/loans")
        assert len(result) == 1
        assert isinstance(result[0], FundingLoan)
        assert result[0].id == 2995368
        assert result[0].rate == 0.00019

    def test_get_funding_loans_with_symbol(self):
        ep, mock_m = _make_endpoint()
        mock_m.post.return_value = [_FUNDING_LOAN]
        result = ep.get_funding_loans(symbol="fUSD")
        mock_m.post.assert_called_once_with("auth/r/funding/loans/fUSD")
        assert result[0].symbol == "fUSD"
        assert result[0].rate_type == "FIXED"

    def test_get_funding_loans_history_without_symbol(self):
        ep, mock_m = _make_endpoint()
        mock_m.post.return_value = [_FUNDING_LOAN]
        result = ep.get_funding_loans_history()
        mock_m.post.assert_called_once_with(
            "auth/r/funding/loans/hist",
            body={"start": None, "end": None, "limit": None},
        )
        assert isinstance(result[0], FundingLoan)
        assert result[0].status == "ACTIVE"
        assert result[0].period == 2

    def test_get_funding_loans_history_with_symbol(self):
        ep, mock_m = _make_endpoint()
        mock_m.post.return_value = [_FUNDING_LOAN]
        result = ep.get_funding_loans_history(
            symbol="fUSD",
            start="1609459200000",
            end="1609545600000",
            limit=10,
        )
        mock_m.post.assert_called_once_with(
            "auth/r/funding/loans/fUSD/hist",
            body={
                "start": "1609459200000",
                "end": "1609545600000",
                "limit": 10,
            },
        )
        assert result[0].amount == 200.0
        assert result[0].no_close == 0

    def test_get_funding_credits_without_symbol(self):
        ep, mock_m = _make_endpoint()
        mock_m.post.return_value = [_FUNDING_CREDIT]
        result = ep.get_funding_credits()
        mock_m.post.assert_called_once_with("auth/r/funding/credits")
        assert len(result) == 1
        assert isinstance(result[0], FundingCredit)
        assert result[0].id == 26190108
        assert result[0].position_pair == "tBTCUSD"

    def test_get_funding_credits_with_symbol(self):
        ep, mock_m = _make_endpoint()
        mock_m.post.return_value = [_FUNDING_CREDIT]
        result = ep.get_funding_credits(symbol="fUSD")
        mock_m.post.assert_called_once_with("auth/r/funding/credits/fUSD")
        assert result[0].symbol == "fUSD"
        assert result[0].amount == 350.0

    def test_get_funding_credits_history_without_symbol(self):
        ep, mock_m = _make_endpoint()
        mock_m.post.return_value = [_FUNDING_CREDIT]
        result = ep.get_funding_credits_history()
        mock_m.post.assert_called_once_with(
            "auth/r/funding/credits/hist",
            body={"start": None, "end": None, "limit": None},
        )
        assert isinstance(result[0], FundingCredit)
        assert result[0].rate == 0.00022
        assert result[0].period == 7

    def test_get_funding_credits_history_with_symbol(self):
        ep, mock_m = _make_endpoint()
        mock_m.post.return_value = [_FUNDING_CREDIT]
        result = ep.get_funding_credits_history(
            symbol="fUSD",
            start="1609459200000",
            end="1609545600000",
            limit=20,
        )
        mock_m.post.assert_called_once_with(
            "auth/r/funding/credits/fUSD/hist",
            body={
                "start": "1609459200000",
                "end": "1609545600000",
                "limit": 20,
            },
        )
        assert result[0].status == "ACTIVE"
        assert result[0].renew == 1

    def test_get_funding_trades_history_without_symbol(self):
        ep, mock_m = _make_endpoint()
        # 7 labels — see serializers.FundingTrade.
        mock_m.post.return_value = [
            [636854, "fUSD", 1609459200000, 41238905, 1000.0, 0.0002, 30]
        ]
        result = ep.get_funding_trades_history()
        mock_m.post.assert_called_once_with(
            "auth/r/funding/trades/hist",
            body={"sort": None, "start": None, "end": None, "limit": None},
        )
        assert len(result) == 1
        assert isinstance(result[0], FundingTrade)
        assert result[0].id == 636854
        assert result[0].offer_id == 41238905
        assert result[0].period == 30

    def test_get_funding_trades_history_with_symbol(self):
        ep, mock_m = _make_endpoint()
        mock_m.post.return_value = [
            [636855, "fUSD", 1609459300000, 41238906, -500.0, 0.00025, 2]
        ]
        result = ep.get_funding_trades_history(
            symbol="fUSD",
            sort=-1,
            start="1609459200000",
            end="1609545600000",
            limit=15,
        )
        mock_m.post.assert_called_once_with(
            "auth/r/funding/trades/fUSD/hist",
            body={
                "sort": -1,
                "start": "1609459200000",
                "end": "1609545600000",
                "limit": 15,
            },
        )
        assert result[0].currency == "fUSD"
        assert result[0].rate == 0.00025

    def test_get_funding_info(self):
        ep, mock_m = _make_endpoint()
        # 6 labels, flat=True — the nested block is spliced in.
        mock_m.post.return_value = [
            "sym",
            "fUSD",
            [0.00019, 0.00022, 2.5, 7.5],
        ]
        result = ep.get_funding_info("fUSD")
        mock_m.post.assert_called_once_with("auth/r/info/funding/fUSD")
        assert isinstance(result, FundingInfo)
        assert result.symbol == "fUSD"
        assert result.yield_loan == 0.00019
        assert result.duration_lend == 7.5


class TestTransfersAndDeposits:
    def test_transfer_between_wallets(self):
        ep, mock_m = _make_endpoint()
        # 8 labels — see serializers.Transfer.
        mock_m.post.return_value = _notification(
            [
                1609459200000,
                "exchange",
                "margin",
                None,
                "BTC",
                "BTC",
                None,
                0.25,
            ],
            kind="acc_tf",
        )
        result = ep.transfer_between_wallets(
            "exchange", "margin", "BTC", "BTC", "0.25"
        )
        mock_m.post.assert_called_once_with(
            "auth/w/transfer",
            body={
                "from": "exchange",
                "to": "margin",
                "currency": "BTC",
                "currency_to": "BTC",
                "amount": "0.25",
            },
        )
        assert result.type == "acc_tf"
        assert isinstance(result.data, Transfer)
        assert result.data.wallet_from == "exchange"
        assert result.data.amount == 0.25

    def test_submit_wallet_withdrawal(self):
        ep, mock_m = _make_endpoint()
        # 9 labels — see serializers.Withdrawal.
        mock_m.post.return_value = _notification(
            [
                13080092,
                None,
                "bitcoin",
                None,
                "exchange",
                0.001,
                None,
                None,
                0.0004,
            ],
            kind="acc_wd-req",
        )
        result = ep.submit_wallet_withdrawal(
            "exchange", "bitcoin", "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2", "0.001"
        )
        mock_m.post.assert_called_once_with(
            "auth/w/withdraw",
            body={
                "wallet": "exchange",
                "method": "bitcoin",
                "address": "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2",
                "amount": "0.001",
            },
        )
        assert result.type == "acc_wd-req"
        assert isinstance(result.data, Withdrawal)
        assert result.data.withdrawal_id == 13080092
        assert result.data.withdrawal_fee == 0.0004

    def test_get_deposit_address(self):
        ep, mock_m = _make_endpoint()
        # 6 labels — see serializers.DepositAddress.
        mock_m.post.return_value = _notification(
            [
                None,
                "bitcoin",
                "BTC",
                None,
                "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2",
                None,
            ],
            kind="acc_dep",
        )
        result = ep.get_deposit_address("exchange", "bitcoin")
        mock_m.post.assert_called_once_with(
            "auth/w/deposit/address",
            body={
                "wallet": "exchange",
                "method": "bitcoin",
                "op_renew": False,
            },
        )
        assert isinstance(result.data, DepositAddress)
        assert result.data.currency_code == "BTC"
        assert result.data.address == "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2"

    def test_get_deposit_address_with_renew(self):
        ep, mock_m = _make_endpoint()
        mock_m.post.return_value = _notification(
            [None, "bitcoin", "BTC", None, "bc1qnewaddress", None],
            kind="acc_dep",
        )
        result = ep.get_deposit_address("margin", "bitcoin", True)
        mock_m.post.assert_called_once_with(
            "auth/w/deposit/address",
            body={"wallet": "margin", "method": "bitcoin", "op_renew": True},
        )
        assert result.data.method == "bitcoin"
        assert result.data.address == "bc1qnewaddress"

    def test_generate_deposit_invoice(self):
        ep, mock_m = _make_endpoint()
        # 5 labels — see serializers.LightningNetworkInvoice.
        mock_m.post.return_value = [
            "0x9f1e2c",
            "lnbc1000n1ps...",
            None,
            None,
            "0.00001",
        ]
        result = ep.generate_deposit_invoice("exchange", "LNX", "0.00001")
        mock_m.post.assert_called_once_with(
            "auth/w/deposit/invoice",
            body={
                "wallet": "exchange",
                "currency": "LNX",
                "amount": "0.00001",
            },
        )
        assert isinstance(result, LightningNetworkInvoice)
        assert result.invoice_hash == "0x9f1e2c"
        assert result.amount == "0.00001"


class TestMovements:
    def test_get_movements_without_currency(self):
        ep, mock_m = _make_endpoint()
        # 22 labels — see serializers.Movement.
        mock_m.post.return_value = [
            _row(
                22,
                {
                    0: "13080092",
                    1: "BTC",
                    2: "BITCOIN",
                    5: 1609459200000,
                    6: 1609459300000,
                    9: "COMPLETED",
                    12: -0.001,
                    13: -0.0004,
                    16: "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2",
                    20: "0xdeadbeef",
                    21: "cold storage",
                },
            )
        ]
        result = ep.get_movements()
        mock_m.post.assert_called_once_with(
            "auth/r/movements/hist",
            body={"start": None, "end": None, "limit": None},
        )
        assert len(result) == 1
        assert isinstance(result[0], Movement)
        assert result[0].id == "13080092"
        assert result[0].status == "COMPLETED"
        assert result[0].transaction_id == "0xdeadbeef"

    def test_get_movements_with_currency(self):
        ep, mock_m = _make_endpoint()
        mock_m.post.return_value = [
            _row(
                22,
                {
                    0: "13080093",
                    1: "USD",
                    2: "WIRE",
                    5: 1609459200000,
                    6: 1609459300000,
                    9: "PENDING",
                    12: 5000.0,
                    13: -25.0,
                    16: "DE89370400440532013000",
                    20: "wire-ref-1",
                    21: "salary",
                },
            )
        ]
        result = ep.get_movements(
            currency="USD",
            start="1609459200000",
            end="1609545600000",
            limit=30,
        )
        mock_m.post.assert_called_once_with(
            "auth/r/movements/USD/hist",
            body={
                "start": "1609459200000",
                "end": "1609545600000",
                "limit": 30,
            },
        )
        assert result[0].currency == "USD"
        assert result[0].currency_name == "WIRE"
        assert result[0].fees == -25.0
