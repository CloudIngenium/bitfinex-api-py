import pytest

from bfxapi.types import dataclasses, serializers
from bfxapi.types.labeler import _Serializer, generate_labeler_serializer
from bfxapi.types.notification import Notification, _Notification


class TestSerializerCore:
    """Tests for the _Serializer base class."""

    def test_simple_parse(self):
        s = generate_labeler_serializer(
            name="PlatformStatus",
            klass=dataclasses.PlatformStatus,
            labels=["status"],
        )
        result = s.parse(1)
        assert isinstance(result, dataclasses.PlatformStatus)
        assert result.status == 1

    def test_placeholder_filtering(self):
        s = generate_labeler_serializer(
            name="TickersHistory",
            klass=dataclasses.TickersHistory,
            labels=[
                "symbol",
                "bid",
                "_PLACEHOLDER",
                "ask",
                "_PLACEHOLDER",
                "_PLACEHOLDER",
                "_PLACEHOLDER",
                "_PLACEHOLDER",
                "_PLACEHOLDER",
                "_PLACEHOLDER",
                "_PLACEHOLDER",
                "_PLACEHOLDER",
                "mts",
            ],
        )
        result = s.parse(
            "tBTCUSD", 50000.0, None, 50001.0, *([None] * 8), 1234567890
        )
        assert result.symbol == "tBTCUSD"
        assert result.bid == 50000.0
        assert result.ask == 50001.0
        assert result.mts == 1234567890

    def test_get_labels_excludes_placeholders(self):
        s = generate_labeler_serializer(
            name="Test",
            klass=dataclasses.FxRate,
            labels=["_PLACEHOLDER", "current_rate"],
        )
        assert s.get_labels() == ["current_rate"]

    def test_mismatched_args_raises(self):
        s = generate_labeler_serializer(
            name="Test",
            klass=dataclasses.TradingPairBook,
            labels=["price", "count", "amount"],
        )
        with pytest.raises(AssertionError, match="<labels> and <\\*args>"):
            s.parse(1.0, 2)  # Missing one arg

    def test_extra_trailing_args_are_ignored(self):
        """A field Bitfinex appends but this SDK does not map is dropped."""
        s = generate_labeler_serializer(
            name="Test",
            klass=dataclasses.TradingPairBook,
            labels=["price", "count", "amount"],
        )
        result = s.parse(1.0, 2, 3.0, "unmapped-tail")
        assert (result.price, result.count, result.amount) == (1.0, 2, 3.0)

    def test_optional_tail_allows_short_payload(self):
        """A peer still sending the pre-append array yields None for the tail."""
        s = generate_labeler_serializer(
            name="Test",
            klass=dataclasses.TradingPairTicker,
            labels=[
                "bid",
                "bid_size",
                "ask",
                "ask_size",
                "daily_change",
                "daily_change_relative",
                "last_price",
                "volume",
                "high",
                "low",
                "first_trade",
            ],
            optional_tail=1,
        )
        result = s.parse(*range(10))
        assert result.low == 9
        assert result.first_trade is None

    def test_optional_tail_still_raises_below_required(self):
        """optional_tail forgives only the tail — a real gap still raises."""
        s = generate_labeler_serializer(
            name="Test",
            klass=dataclasses.TradingPairBook,
            labels=["price", "count", "amount"],
            optional_tail=1,
        )
        with pytest.raises(AssertionError, match="<labels> and <\\*args>"):
            s.parse(1.0)

    def test_optional_tail_yields_none_for_absent_labels(self):
        s = generate_labeler_serializer(
            name="Test",
            klass=dataclasses.TradingPairBook,
            labels=["price", "_PLACEHOLDER", "count", "amount"],
            optional_tail=2,
        )
        result = s.parse(1.0, None)
        assert result.price == 1.0
        assert result.count is None
        assert result.amount is None

    def test_flat_serializer(self):
        result = serializers.SymbolMarginInfo.parse(
            "sym", "tBTCUSD", 1000.0, 2000.0, 500.0, 600.0
        )
        assert isinstance(result, dataclasses.SymbolMarginInfo)
        assert result.symbol == "tBTCUSD"
        assert result.tradable_balance == 1000.0
        assert result.gross_balance == 2000.0
        assert result.buy == 500.0
        assert result.sell == 600.0

    def test_base_margin_info_flat(self):
        result = serializers.BaseMarginInfo.parse(
            "base", 100.0, 200.0, 300.0, 400.0, 500.0
        )
        assert isinstance(result, dataclasses.BaseMarginInfo)
        assert result.user_pl == 100.0
        assert result.margin_net == 400.0


class TestPublicSerializers:
    """Tests for public endpoint serializers."""

    def test_trading_pair_ticker(self):
        result = serializers.TradingPairTicker.parse(
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
        )
        assert isinstance(result, dataclasses.TradingPairTicker)
        assert result.bid == 50000.0
        assert result.bid_size == 1.5
        assert result.ask == 50001.0
        assert result.ask_size == 2.0
        assert result.daily_change == 100.0
        assert result.daily_change_relative == 0.002
        assert result.last_price == 50000.5
        assert result.volume == 10000.0
        assert result.high == 51000.0
        assert result.low == 49000.0
        assert result.first_trade is None

    def test_trading_pair_ticker_with_first_trade(self):
        result = serializers.TradingPairTicker.parse(
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
            1358182043000,
        )
        assert result.low == 49000.0
        assert result.first_trade == 1358182043000

    def test_trading_pair_ticker_null_first_trade(self):
        """A newly listed pair reports FIRST_TRADE as null, not a timestamp."""
        result = serializers.TradingPairTicker.parse(
            *([0.0] * 10),
            None,
        )
        assert result.first_trade is None

    def test_funding_currency_ticker(self):
        result = serializers.FundingCurrencyTicker.parse(
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
        )
        assert isinstance(result, dataclasses.FundingCurrencyTicker)
        assert result.frr == 0.0001
        assert result.bid_period == 2
        assert result.frr_amount_available == 10000.0
        assert result.first_trade is None

    def test_funding_currency_ticker_with_first_trade(self):
        result = serializers.FundingCurrencyTicker.parse(
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
            1469734163000,
        )
        assert result.frr_amount_available == 10000.0
        assert result.first_trade == 1469734163000

    def test_pair_info_spot(self):
        """Spot rows carry 12 details; only the mapped slots are read."""
        result = serializers.PairInfo.parse(
            "BTCUSD",
            [
                1358182043000,
                None,
                None,
                "0.00004",
                "2000.0",
                None,
                None,
                None,
                0.1,
                0.05,
                None,
                None,
            ],
        )
        assert isinstance(result, dataclasses.PairInfo)
        assert result.pair == "BTCUSD"
        assert result.first_trade == 1358182043000
        assert result.min_order_size == "0.00004"
        assert result.max_order_size == "2000.0"
        assert result.initial_margin == 0.1
        assert result.min_margin == 0.05

    def test_pair_info_futures(self):
        """Futures rows carry exactly 10 details and no trailing padding."""
        result = serializers.PairInfo.parse(
            "BTCF0:USTF0",
            [
                1562164542332,
                None,
                None,
                "0.00004",
                "100.0",
                None,
                None,
                None,
                0.01,
                0.005,
            ],
        )
        assert result.pair == "BTCF0:USTF0"
        assert result.first_trade == 1562164542332
        assert result.initial_margin == 0.01
        assert result.min_margin == 0.005

    def test_pair_info_unlisted_pair_has_no_first_trade(self):
        result = serializers.PairInfo.parse(
            "AAVE:USD",
            [None, None, None, "0.02", "5000.0", None, None, None, None, None],
        )
        assert result.first_trade is None
        assert result.min_order_size == "0.02"
        assert result.initial_margin is None
        assert result.min_margin is None

    def test_trading_pair_trade(self):
        result = serializers.TradingPairTrade.parse(
            12345, 1000000, 0.5, 50000.0
        )
        assert isinstance(result, dataclasses.TradingPairTrade)
        assert result.id == 12345
        assert result.mts == 1000000
        assert result.amount == 0.5
        assert result.price == 50000.0

    def test_funding_currency_trade(self):
        result = serializers.FundingCurrencyTrade.parse(
            67890, 1000000, 100.0, 0.0001, 30
        )
        assert isinstance(result, dataclasses.FundingCurrencyTrade)
        assert result.id == 67890
        assert result.rate == 0.0001
        assert result.period == 30

    def test_trading_pair_book(self):
        result = serializers.TradingPairBook.parse(50000.0, 3, 1.5)
        assert isinstance(result, dataclasses.TradingPairBook)
        assert result.price == 50000.0
        assert result.count == 3
        assert result.amount == 1.5

    def test_funding_currency_book(self):
        result = serializers.FundingCurrencyBook.parse(0.0001, 30, 5, 1000.0)
        assert isinstance(result, dataclasses.FundingCurrencyBook)
        assert result.rate == 0.0001
        assert result.period == 30

    def test_trading_pair_raw_book(self):
        result = serializers.TradingPairRawBook.parse(111, 50000.0, 0.5)
        assert isinstance(result, dataclasses.TradingPairRawBook)
        assert result.order_id == 111

    def test_funding_currency_raw_book(self):
        result = serializers.FundingCurrencyRawBook.parse(
            222, 30, 0.0001, 500.0
        )
        assert isinstance(result, dataclasses.FundingCurrencyRawBook)
        assert result.offer_id == 222

    def test_candle(self):
        result = serializers.Candle.parse(
            1000000, 50000, 50100, 51000, 49000, 1234.5
        )
        assert isinstance(result, dataclasses.Candle)
        assert result.mts == 1000000
        assert result.open == 50000
        assert result.close == 50100
        assert result.high == 51000
        assert result.low == 49000
        assert result.volume == 1234.5

    def test_statistic(self):
        result = serializers.Statistic.parse(1000000, 42.5)
        assert isinstance(result, dataclasses.Statistic)
        assert result.mts == 1000000
        assert result.value == 42.5

    def test_fx_rate(self):
        result = serializers.FxRate.parse(1.12)
        assert isinstance(result, dataclasses.FxRate)
        assert result.current_rate == 1.12

    def test_derivatives_status(self):
        result = serializers.DerivativesStatus.parse(
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
        )
        assert isinstance(result, dataclasses.DerivativesStatus)
        assert result.deriv_price == 50000.0
        assert result.spot_price == 49900.0
        assert result.mark_price == 50050.0
        assert result.open_interest == 5000.0

    def test_liquidation(self):
        result = serializers.Liquidation.parse(
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
        )
        assert isinstance(result, dataclasses.Liquidation)
        assert result.pos_id == 111
        assert result.symbol == "tBTCUSD"
        assert result.liquidation_price == 49000.0

    def test_leaderboard(self):
        result = serializers.Leaderboard.parse(
            1000000,
            None,
            "trader1",
            1,
            None,
            None,
            99.5,
            None,
            None,
            "@trader1",
        )
        assert isinstance(result, dataclasses.Leaderboard)
        assert result.username == "trader1"
        assert result.ranking == 1
        assert result.twitter_handle == "@trader1"

    def test_funding_statistic(self):
        result = serializers.FundingStatistic.parse(
            1000000,
            None,
            None,
            0.0001,
            30.0,
            None,
            None,
            5000000.0,
            3000000.0,
            None,
            None,
            1000000.0,
        )
        assert isinstance(result, dataclasses.FundingStatistic)
        assert result.frr == 0.0001
        assert result.avg_period == 30.0
        assert result.funding_amount == 5000000.0

    def test_trading_market_average_price(self):
        result = serializers.TradingMarketAveragePrice.parse(50000.0, 1.5)
        assert isinstance(result, dataclasses.TradingMarketAveragePrice)
        assert result.price_avg == 50000.0
        assert result.amount == 1.5

    def test_funding_market_average_price(self):
        result = serializers.FundingMarketAveragePrice.parse(0.0001, 1000.0)
        assert isinstance(result, dataclasses.FundingMarketAveragePrice)
        assert result.rate_avg == 0.0001


class TestAuthSerializers:
    """Tests for authenticated endpoint serializers."""

    def test_order(self):
        result = serializers.Order.parse(
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
        )
        assert isinstance(result, dataclasses.Order)
        assert result.id == 1001
        assert result.symbol == "tBTCUSD"
        assert result.amount == 0.5
        assert result.order_type == "EXCHANGE LIMIT"
        assert result.order_status == "ACTIVE"
        assert result.price == 50000.0

    def test_position(self):
        result = serializers.Position.parse(
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
        )
        assert isinstance(result, dataclasses.Position)
        assert result.symbol == "tBTCUSD"
        assert result.status == "ACTIVE"
        assert result.leverage == 2.0
        assert result.position_id == 9999

    def test_trade(self):
        result = serializers.Trade.parse(
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
        )
        assert isinstance(result, dataclasses.Trade)
        assert result.id == 5001
        assert result.exec_amount == 0.5
        assert result.fee == -0.001

    def test_wallet(self):
        result = serializers.Wallet.parse(
            "exchange", "BTC", 1.5, 0.0, 1.5, "2024-01-01", {}
        )
        assert isinstance(result, dataclasses.Wallet)
        assert result.wallet_type == "exchange"
        assert result.currency == "BTC"
        assert result.balance == 1.5

    def test_funding_offer(self):
        result = serializers.FundingOffer.parse(
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
        )
        assert isinstance(result, dataclasses.FundingOffer)
        assert result.id == 2001
        assert result.symbol == "fUSD"
        assert result.rate == 0.0001
        assert result.period == 30

    def test_funding_credit(self):
        result = serializers.FundingCredit.parse(
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
        )
        assert isinstance(result, dataclasses.FundingCredit)
        assert result.id == 3001
        assert result.position_pair == "tBTCUSD"

    def test_funding_loan(self):
        result = serializers.FundingLoan.parse(
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
        )
        assert isinstance(result, dataclasses.FundingLoan)
        assert result.id == 4001

    def test_funding_auto_renew(self):
        result = serializers.FundingAutoRenew.parse("USD", 30, 0.0001, 500.0)
        assert isinstance(result, dataclasses.FundingAutoRenew)
        assert result.currency == "USD"
        assert result.period == 30

    def test_funding_info_flat(self):
        result = serializers.FundingInfo.parse(
            "sym", "fUSD", 0.001, 0.002, 10.0, 15.0
        )
        assert isinstance(result, dataclasses.FundingInfo)
        assert result.symbol == "fUSD"
        assert result.yield_loan == 0.001
        assert result.yield_lend == 0.002

    def test_ledger(self):
        result = serializers.Ledger.parse(
            7001, "BTC", None, 1000000, None, 0.5, 1.5, None, "Trade"
        )
        assert isinstance(result, dataclasses.Ledger)
        assert result.id == 7001
        assert result.currency == "BTC"
        assert result.description == "Trade"

    def test_transfer(self):
        result = serializers.Transfer.parse(
            1000000, "exchange", "margin", None, "BTC", "BTC", None, 0.5
        )
        assert isinstance(result, dataclasses.Transfer)
        assert result.wallet_from == "exchange"
        assert result.wallet_to == "margin"

    def test_withdrawal(self):
        result = serializers.Withdrawal.parse(
            8001,
            None,
            "bitcoin",
            "addr123",
            "exchange",
            0.5,
            None,
            None,
            0.0001,
        )
        assert isinstance(result, dataclasses.Withdrawal)
        assert result.withdrawal_id == 8001
        assert result.withdrawal_fee == 0.0001

    def test_deposit_address(self):
        result = serializers.DepositAddress.parse(
            None, "bitcoin", "BTC", None, "1A2B3C4D", "pool_addr"
        )
        assert isinstance(result, dataclasses.DepositAddress)
        assert result.method == "bitcoin"
        assert result.address == "1A2B3C4D"

    def test_movement(self):
        result = serializers.Movement.parse(
            "9001",
            "BTC",
            "Bitcoin",
            None,
            None,
            1000000,
            1000001,
            None,
            None,
            "COMPLETED",
            None,
            None,
            1,
            0,
            None,
            None,
            "1A2B3C",
            None,
            None,
            None,
            "tx123",
            "note",
        )
        assert isinstance(result, dataclasses.Movement)
        assert result.id == "9001"
        assert result.status == "COMPLETED"
        assert result.transaction_id == "tx123"

    def test_balance_info(self):
        result = serializers.BalanceInfo.parse(100000.0, 95000.0)
        assert isinstance(result, dataclasses.BalanceInfo)
        assert result.aum == 100000.0
        assert result.aum_net == 95000.0

    def test_derivative_position_collateral(self):
        result = serializers.DerivativePositionCollateral.parse(1)
        assert isinstance(result, dataclasses.DerivativePositionCollateral)
        assert result.status == 1

    def test_derivative_position_collateral_limits(self):
        result = serializers.DerivativePositionCollateralLimits.parse(
            100.0, 50000.0
        )
        assert isinstance(
            result, dataclasses.DerivativePositionCollateralLimits
        )
        assert result.min_collateral == 100.0
        assert result.max_collateral == 50000.0

    def test_position_increase_info_flat(self):
        result = serializers.PositionIncreaseInfo.parse(
            10,
            5.0,
            1000.0,
            2000.0,
            3000.0,
            4000.0,
            5000.0,
            None,
            None,
            None,
            None,
            6000.0,
            None,
            None,
            7000.0,
            8000.0,
            "USD",
            "BTC",
        )
        assert isinstance(result, dataclasses.PositionIncreaseInfo)
        assert result.max_pos == 10
        assert result.funding_avail == 6000.0
        assert result.funding_value_currency == "USD"


class TestNotificationSerializer:
    """Tests for the _Notification serializer."""

    def test_plain_notification(self):
        s = _Notification(serializer=None)
        result = s.parse(
            1000000, "info", None, None, "data", 0, "SUCCESS", "ok"
        )
        assert isinstance(result, Notification)
        assert result.mts == 1000000
        assert result.type == "info"
        assert result.status == "SUCCESS"
        assert result.text == "ok"
        assert result.data == "data"

    def test_notification_with_order_serializer(self):
        s = _Notification(serializer=serializers.Order)
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
        result = s.parse(
            1000000,
            "on-req",
            None,
            None,
            [order_data],
            0,
            "SUCCESS",
            "Submitted",
        )
        assert isinstance(result, Notification)
        assert isinstance(result.data, dataclasses.Order)
        assert result.data.symbol == "tBTCUSD"

    def test_notification_with_funding_offer_serializer(self):
        s = _Notification(serializer=serializers.FundingOffer)
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
        result = s.parse(
            1000000,
            "fon-req",
            None,
            None,
            [offer_data],
            0,
            "SUCCESS",
            "Submitted",
        )
        assert isinstance(result, Notification)
        assert isinstance(result.data, dataclasses.FundingOffer)


class TestSerializerAllDefined:
    """Verify all declared serializers exist and are functional."""

    def test_all_serializers_in_list_are_accessible(self):
        for name in serializers.__serializers__:
            s = getattr(serializers, name)
            assert isinstance(s, _Serializer), f"{name} is not a _Serializer"
            labels = s.get_labels()
            assert len(labels) > 0, f"{name} has no labels"
