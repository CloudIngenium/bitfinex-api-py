from unittest.mock import MagicMock

from bfxapi.types import dataclasses
from bfxapi.websocket._handlers.auth_events_handler import AuthEventsHandler
from bfxapi.websocket._handlers.public_channels_handler import (
    PublicChannelsHandler,
)


class TestPublicChannelsHandler:
    def setup_method(self):
        self.ee = MagicMock()
        self.handler = PublicChannelsHandler(event_emitter=self.ee)

    def test_trading_ticker(self):
        subscription = {
            "channel": "ticker",
            "sub_id": "abc",
            "symbol": "tBTCUSD",
        }
        stream = [
            [
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
        ]
        self.handler.handle(subscription, stream)

        self.ee.emit.assert_called_once()
        args = self.ee.emit.call_args
        assert args[0][0] == "t_ticker_update"
        assert args[0][1] == subscription
        assert isinstance(args[0][2], dataclasses.TradingPairTicker)
        assert args[0][2].bid == 50000.0

    def test_funding_ticker(self):
        subscription = {"channel": "ticker", "sub_id": "abc", "symbol": "fUSD"}
        stream = [
            [
                0.0001,
                0.00009,
                2,
                100.0,
                0.00011,
                30,
                200.0,
                0.00001,
                0.01,
                0.0001,
                5000.0,
                0.00012,
                0.00008,
                None,
                None,
                10000.0,
            ]
        ]
        self.handler.handle(subscription, stream)

        args = self.ee.emit.call_args
        assert args[0][0] == "f_ticker_update"
        assert isinstance(args[0][2], dataclasses.FundingCurrencyTicker)

    def test_trading_ticker_with_first_trade(self):
        """The live ws ticker channel appends FIRST_TRADE (11 elements)."""
        subscription = {
            "channel": "ticker",
            "sub_id": "abc",
            "symbol": "tBTCUSD",
        }
        stream = [
            [
                83837,
                1.90,
                83849,
                2.15,
                -568,
                -0.0067,
                83841,
                1200.84,
                85172,
                83157,
                1358182043000,
            ]
        ]
        self.handler.handle(subscription, stream)

        ticker = self.ee.emit.call_args[0][2]
        assert isinstance(ticker, dataclasses.TradingPairTicker)
        assert ticker.low == 83157
        assert ticker.first_trade == 1358182043000

    def test_funding_ticker_with_first_trade(self):
        """The live ws funding ticker channel is 17 elements."""
        subscription = {"channel": "ticker", "sub_id": "abc", "symbol": "fUSD"}
        stream = [
            [
                0.0003,
                0.00027,
                120,
                17037596.04,
                0.00014,
                2,
                3442183.24,
                -0.00015,
                -0.5137,
                0.00014,
                358212127.47,
                0.00035,
                0.000028,
                None,
                None,
                120625392.51,
                1469734163000,
            ]
        ]
        self.handler.handle(subscription, stream)

        ticker = self.ee.emit.call_args[0][2]
        assert isinstance(ticker, dataclasses.FundingCurrencyTicker)
        assert ticker.frr_amount_available == 120625392.51
        assert ticker.first_trade == 1469734163000

    def test_trading_trade_execution(self):
        subscription = {
            "channel": "trades",
            "sub_id": "abc",
            "symbol": "tBTCUSD",
        }
        stream = ["te", [12345, 1000000, 0.5, 50000.0]]
        self.handler.handle(subscription, stream)

        args = self.ee.emit.call_args
        assert args[0][0] == "t_trade_execution"
        assert isinstance(args[0][2], dataclasses.TradingPairTrade)

    def test_trading_trade_execution_update(self):
        subscription = {
            "channel": "trades",
            "sub_id": "abc",
            "symbol": "tBTCUSD",
        }
        stream = ["tu", [12345, 1000000, 0.5, 50000.0]]
        self.handler.handle(subscription, stream)

        args = self.ee.emit.call_args
        assert args[0][0] == "t_trade_execution_update"

    def test_funding_trade_execution(self):
        subscription = {"channel": "trades", "sub_id": "abc", "symbol": "fUSD"}
        stream = ["fte", [67890, 1000000, 100.0, 0.0001, 30]]
        self.handler.handle(subscription, stream)

        args = self.ee.emit.call_args
        assert args[0][0] == "f_trade_execution"
        assert isinstance(args[0][2], dataclasses.FundingCurrencyTrade)

    def test_funding_trade_execution_update(self):
        subscription = {"channel": "trades", "sub_id": "abc", "symbol": "fUSD"}
        stream = ["ftu", [67890, 1000000, 100.0, 0.0001, 30]]
        self.handler.handle(subscription, stream)

        args = self.ee.emit.call_args
        assert args[0][0] == "f_trade_execution_update"

    def test_trading_trades_snapshot(self):
        subscription = {
            "channel": "trades",
            "sub_id": "abc",
            "symbol": "tBTCUSD",
        }
        stream = [
            [[12345, 1000000, 0.5, 50000.0], [12346, 1000001, -0.3, 49999.0]]
        ]
        self.handler.handle(subscription, stream)

        args = self.ee.emit.call_args
        assert args[0][0] == "t_trades_snapshot"
        assert len(args[0][2]) == 2
        assert all(
            isinstance(t, dataclasses.TradingPairTrade) for t in args[0][2]
        )

    def test_funding_trades_snapshot(self):
        subscription = {"channel": "trades", "sub_id": "abc", "symbol": "fUSD"}
        stream = [
            [
                [67890, 1000000, 100.0, 0.0001, 30],
                [67891, 1000001, 200.0, 0.0002, 15],
            ]
        ]
        self.handler.handle(subscription, stream)

        args = self.ee.emit.call_args
        assert args[0][0] == "f_trades_snapshot"
        assert len(args[0][2]) == 2

    def test_trading_book_snapshot(self):
        subscription = {
            "channel": "book",
            "sub_id": "abc",
            "symbol": "tBTCUSD",
            "prec": "P0",
            "freq": "F0",
            "len": "25",
        }
        stream = [[[50000.0, 3, 1.5], [49999.0, 2, 0.8]]]
        self.handler.handle(subscription, stream)

        args = self.ee.emit.call_args
        assert args[0][0] == "t_book_snapshot"
        assert len(args[0][2]) == 2
        assert all(
            isinstance(b, dataclasses.TradingPairBook) for b in args[0][2]
        )

    def test_trading_book_update(self):
        subscription = {
            "channel": "book",
            "sub_id": "abc",
            "symbol": "tBTCUSD",
            "prec": "P0",
            "freq": "F0",
            "len": "25",
        }
        stream = [[50000.0, 3, 1.5]]
        self.handler.handle(subscription, stream)

        args = self.ee.emit.call_args
        assert args[0][0] == "t_book_update"
        assert isinstance(args[0][2], dataclasses.TradingPairBook)

    def test_funding_book_snapshot(self):
        subscription = {
            "channel": "book",
            "sub_id": "abc",
            "symbol": "fUSD",
            "prec": "P0",
            "freq": "F0",
            "len": "25",
        }
        stream = [[[0.0001, 30, 5, 1000.0], [0.0002, 15, 3, 500.0]]]
        self.handler.handle(subscription, stream)

        args = self.ee.emit.call_args
        assert args[0][0] == "f_book_snapshot"
        assert len(args[0][2]) == 2

    def test_funding_book_update(self):
        subscription = {
            "channel": "book",
            "sub_id": "abc",
            "symbol": "fUSD",
            "prec": "P0",
            "freq": "F0",
            "len": "25",
        }
        stream = [[0.0001, 30, 5, 1000.0]]
        self.handler.handle(subscription, stream)

        args = self.ee.emit.call_args
        assert args[0][0] == "f_book_update"
        assert isinstance(args[0][2], dataclasses.FundingCurrencyBook)

    def test_trading_raw_book_snapshot(self):
        subscription = {
            "channel": "book",
            "sub_id": "abc",
            "symbol": "tBTCUSD",
            "prec": "R0",
            "freq": "F0",
            "len": "25",
        }
        stream = [[[111, 50000.0, 0.5], [222, 49999.0, -0.3]]]
        self.handler.handle(subscription, stream)

        args = self.ee.emit.call_args
        assert args[0][0] == "t_raw_book_snapshot"
        assert all(
            isinstance(b, dataclasses.TradingPairRawBook) for b in args[0][2]
        )

    def test_trading_raw_book_update(self):
        subscription = {
            "channel": "book",
            "sub_id": "abc",
            "symbol": "tBTCUSD",
            "prec": "R0",
            "freq": "F0",
            "len": "25",
        }
        stream = [[111, 50000.0, 0.5]]
        self.handler.handle(subscription, stream)

        args = self.ee.emit.call_args
        assert args[0][0] == "t_raw_book_update"

    def test_funding_raw_book_snapshot(self):
        subscription = {
            "channel": "book",
            "sub_id": "abc",
            "symbol": "fUSD",
            "prec": "R0",
            "freq": "F0",
            "len": "25",
        }
        stream = [[[222, 30, 0.0001, 500.0], [333, 15, 0.0002, 300.0]]]
        self.handler.handle(subscription, stream)

        args = self.ee.emit.call_args
        assert args[0][0] == "f_raw_book_snapshot"

    def test_funding_raw_book_update(self):
        subscription = {
            "channel": "book",
            "sub_id": "abc",
            "symbol": "fUSD",
            "prec": "R0",
            "freq": "F0",
            "len": "25",
        }
        stream = [[222, 30, 0.0001, 500.0]]
        self.handler.handle(subscription, stream)

        args = self.ee.emit.call_args
        assert args[0][0] == "f_raw_book_update"

    def test_candles_snapshot(self):
        subscription = {
            "channel": "candles",
            "sub_id": "abc",
            "key": "trade:1m:tBTCUSD",
        }
        stream = [
            [
                [1000000, 50000, 50100, 51000, 49000, 1234.5],
                [1000060, 50100, 50200, 51100, 49100, 1235.5],
            ]
        ]
        self.handler.handle(subscription, stream)

        args = self.ee.emit.call_args
        assert args[0][0] == "candles_snapshot"
        assert len(args[0][2]) == 2
        assert all(isinstance(c, dataclasses.Candle) for c in args[0][2])

    def test_candles_update(self):
        subscription = {
            "channel": "candles",
            "sub_id": "abc",
            "key": "trade:1m:tBTCUSD",
        }
        stream = [[1000000, 50000, 50100, 51000, 49000, 1234.5]]
        self.handler.handle(subscription, stream)

        args = self.ee.emit.call_args
        assert args[0][0] == "candles_update"
        assert isinstance(args[0][2], dataclasses.Candle)

    def test_derivatives_status(self):
        subscription = {
            "channel": "status",
            "sub_id": "abc",
            "key": "deriv:tBTCF0:USTF0",
        }
        stream = [
            [
                1000000,
                None,
                50000.0,
                49900.0,
                None,
                1000000.0,
                None,
                2000000,
                0.0001,
                100,
                None,
                0.001,
                None,
                None,
                50050.0,
                None,
                None,
                5000.0,
                None,
                None,
                None,
                -0.5,
                0.5,
            ]
        ]
        self.handler.handle(subscription, stream)

        args = self.ee.emit.call_args
        assert args[0][0] == "derivatives_status_update"
        assert isinstance(args[0][2], dataclasses.DerivativesStatus)

    def test_liquidation_feed(self):
        subscription = {
            "channel": "status",
            "sub_id": "abc",
            "key": "liq:global",
        }
        stream = [
            [
                [
                    None,
                    111,
                    1000000,
                    None,
                    "tBTCUSD",
                    0.5,
                    50000.0,
                    None,
                    1,
                    0,
                    None,
                    49000.0,
                ]
            ]
        ]
        self.handler.handle(subscription, stream)

        args = self.ee.emit.call_args
        assert args[0][0] == "liquidation_feed_update"
        assert isinstance(args[0][2], dataclasses.Liquidation)

    def test_checksum(self):
        subscription = {
            "channel": "book",
            "sub_id": "abc",
            "symbol": "tBTCUSD",
            "prec": "P0",
            "freq": "F0",
            "len": "25",
        }
        stream = ["cs", 123456789]
        self.handler.handle(subscription, stream)

        args = self.ee.emit.call_args
        assert args[0][0] == "checksum"
        assert args[0][2] == 123456789 & 0xFFFFFFFF


class TestAuthEventsHandler:
    def setup_method(self):
        self.ee = MagicMock()
        self.handler = AuthEventsHandler(event_emitter=self.ee)

    def test_order_snapshot(self):
        orders = [
            [
                1001,
                0,
                123,
                "tBTCUSD",
                1000000,
                1000001,
                0.5,
                1.0,
                "EXCHANGE LIMIT",
                "LIMIT",
                0,
                None,
                0,
                "ACTIVE",
                None,
                None,
                50000.0,
                50000.0,
                0,
                0,
                None,
                None,
                None,
                0,
                0,
                0,
                None,
                None,
                "API>2",
                None,
                None,
                {},
            ],
        ]
        self.handler.handle("os", orders)

        args = self.ee.emit.call_args
        assert args[0][0] == "order_snapshot"
        assert isinstance(args[0][1], list)
        assert isinstance(args[0][1][0], dataclasses.Order)

    def test_order_new(self):
        order = [
            1001,
            0,
            123,
            "tBTCUSD",
            1000000,
            1000001,
            0.5,
            1.0,
            "EXCHANGE LIMIT",
            "LIMIT",
            0,
            None,
            0,
            "ACTIVE",
            None,
            None,
            50000.0,
            50000.0,
            0,
            0,
            None,
            None,
            None,
            0,
            0,
            0,
            None,
            None,
            "API>2",
            None,
            None,
            {},
        ]
        self.handler.handle("on", order)

        args = self.ee.emit.call_args
        assert args[0][0] == "order_new"
        assert isinstance(args[0][1], dataclasses.Order)

    def test_order_update(self):
        order = [
            1001,
            0,
            123,
            "tBTCUSD",
            1000000,
            1000001,
            0.3,
            1.0,
            "EXCHANGE LIMIT",
            "LIMIT",
            0,
            None,
            0,
            "PARTIALLY FILLED",
            None,
            None,
            50000.0,
            50000.0,
            0,
            0,
            None,
            None,
            None,
            0,
            0,
            0,
            None,
            None,
            "API>2",
            None,
            None,
            {},
        ]
        self.handler.handle("ou", order)

        args = self.ee.emit.call_args
        assert args[0][0] == "order_update"

    def test_order_cancel(self):
        order = [
            1001,
            0,
            123,
            "tBTCUSD",
            1000000,
            1000001,
            0.5,
            1.0,
            "EXCHANGE LIMIT",
            "LIMIT",
            0,
            None,
            0,
            "CANCELED",
            None,
            None,
            50000.0,
            0,
            0,
            0,
            None,
            None,
            None,
            0,
            0,
            0,
            None,
            None,
            "API>2",
            None,
            None,
            {},
        ]
        self.handler.handle("oc", order)

        args = self.ee.emit.call_args
        assert args[0][0] == "order_cancel"

    def test_position_snapshot(self):
        positions = [
            [
                "tBTCUSD",
                "ACTIVE",
                0.5,
                50000.0,
                0.0,
                0,
                100.0,
                0.002,
                45000.0,
                2.0,
                None,
                9999,
                1000000,
                1000001,
                None,
                0,
                None,
                1000.0,
                500.0,
                {},
            ],
        ]
        self.handler.handle("ps", positions)

        args = self.ee.emit.call_args
        assert args[0][0] == "position_snapshot"
        assert isinstance(args[0][1], list)
        assert isinstance(args[0][1][0], dataclasses.Position)

    def test_wallet_snapshot(self):
        wallets = [
            ["exchange", "BTC", 1.5, 0.0, 1.5, "2024-01-01", {}],
            ["exchange", "USD", 50000.0, 0.0, 50000.0, "2024-01-01", {}],
        ]
        self.handler.handle("ws", wallets)

        args = self.ee.emit.call_args
        assert args[0][0] == "wallet_snapshot"
        assert len(args[0][1]) == 2
        assert all(isinstance(w, dataclasses.Wallet) for w in args[0][1])

    def test_wallet_update(self):
        wallet = ["exchange", "BTC", 1.6, 0.0, 1.6, "2024-01-02", {}]
        self.handler.handle("wu", wallet)

        args = self.ee.emit.call_args
        assert args[0][0] == "wallet_update"
        assert isinstance(args[0][1], dataclasses.Wallet)

    def test_trade_execution(self):
        trade = [
            5001,
            "tBTCUSD",
            1000000,
            1001,
            0.5,
            50000.0,
            "EXCHANGE LIMIT",
            50000.0,
            1,
            -0.001,
            "USD",
            123,
        ]
        self.handler.handle("te", trade)

        args = self.ee.emit.call_args
        assert args[0][0] == "trade_execution"
        assert isinstance(args[0][1], dataclasses.Trade)

    def test_funding_offer_snapshot(self):
        offers = [
            [
                2001,
                "fUSD",
                1000000,
                1000001,
                1000.0,
                1000.0,
                "LIMIT",
                None,
                None,
                0,
                "ACTIVE",
                None,
                None,
                None,
                0.0001,
                30,
                0,
                0,
                None,
                0,
                None,
            ],
        ]
        self.handler.handle("fos", offers)

        args = self.ee.emit.call_args
        assert args[0][0] == "funding_offer_snapshot"
        assert isinstance(args[0][1][0], dataclasses.FundingOffer)

    def test_funding_credit_new(self):
        credit = [
            3001,
            "fUSD",
            1,
            1000000,
            1000001,
            500.0,
            0,
            "ACTIVE",
            "FIXED",
            None,
            None,
            0.0001,
            30,
            1000000,
            1000000,
            0,
            0,
            None,
            0,
            None,
            0,
            "tBTCUSD",
        ]
        self.handler.handle("fcn", credit)

        args = self.ee.emit.call_args
        assert args[0][0] == "funding_credit_new"
        assert isinstance(args[0][1], dataclasses.FundingCredit)

    def test_funding_loan_update(self):
        loan = [
            4001,
            "fUSD",
            1,
            1000000,
            1000001,
            500.0,
            0,
            "ACTIVE",
            "FIXED",
            None,
            None,
            0.0001,
            30,
            1000000,
            1000000,
            0,
            0,
            None,
            0,
            None,
            0,
        ]
        self.handler.handle("flu", loan)

        args = self.ee.emit.call_args
        assert args[0][0] == "funding_loan_update"
        assert isinstance(args[0][1], dataclasses.FundingLoan)

    def test_balance_update(self):
        balance = [100000.0, 95000.0]
        self.handler.handle("bu", balance)

        args = self.ee.emit.call_args
        assert args[0][0] == "balance_update"
        assert isinstance(args[0][1], dataclasses.BalanceInfo)

    def test_base_margin_info(self):
        stream = ["base", 100.0, 200.0, 300.0, 400.0, 500.0]
        self.handler.handle("miu", stream)

        args = self.ee.emit.call_args
        assert args[0][0] == "base_margin_info"
        assert isinstance(args[0][1], dataclasses.BaseMarginInfo)

    def test_symbol_margin_info(self):
        stream = ["sym", "tBTCUSD", 1000.0, 2000.0, 500.0, 600.0]
        self.handler.handle("miu", stream)

        args = self.ee.emit.call_args
        assert args[0][0] == "symbol_margin_info"
        assert isinstance(args[0][1], dataclasses.SymbolMarginInfo)

    def test_notification_plain(self):
        stream = [1000000, "info", None, None, "data", 0, "SUCCESS", "ok"]
        self.handler.handle("n", stream)

        args = self.ee.emit.call_args
        assert args[0][0] == "notification"

    def test_notification_on_req(self):
        order_data = [
            1001,
            0,
            123,
            "tBTCUSD",
            1000000,
            1000001,
            0.5,
            1.0,
            "EXCHANGE LIMIT",
            "LIMIT",
            0,
            None,
            0,
            "ACTIVE",
            None,
            None,
            50000.0,
            50000.0,
            0,
            0,
            None,
            None,
            None,
            0,
            0,
            0,
            None,
            None,
            "API>2",
            None,
            None,
            {},
        ]
        stream = [
            1000000,
            "on-req",
            None,
            None,
            [order_data],
            0,
            "SUCCESS",
            "Submitted",
        ]
        self.handler.handle("n", stream)

        args = self.ee.emit.call_args
        assert args[0][0] == "on-req-notification"

    def test_notification_fon_req(self):
        offer_data = [
            2001,
            "fUSD",
            1000000,
            1000001,
            1000.0,
            1000.0,
            "LIMIT",
            None,
            None,
            0,
            "ACTIVE",
            None,
            None,
            None,
            0.0001,
            30,
            0,
            0,
            None,
            0,
            None,
        ]
        stream = [
            1000000,
            "fon-req",
            None,
            None,
            [offer_data],
            0,
            "SUCCESS",
            "Submitted",
        ]
        self.handler.handle("n", stream)

        args = self.ee.emit.call_args
        assert args[0][0] == "fon-req-notification"


class TestAuthEventsHandlerAbbreviations:
    """Verify all abbreviation mappings are complete."""

    def test_all_abbreviations_have_serializers(self):
        abbrevs = AuthEventsHandler._AuthEventsHandler__ABBREVIATIONS
        serializer_abbrevs = set(
            AuthEventsHandler._AuthEventsHandler__SERIALIZERS.keys()
        )

        for abbrev in abbrevs:
            assert abbrev in serializer_abbrevs, (
                f"Abbreviation '{abbrev}' has no serializer mapping"
            )
