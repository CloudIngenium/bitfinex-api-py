from unittest.mock import MagicMock

from bfxapi.rest._interfaces.rest_public_endpoints import RestPublicEndpoints
from bfxapi.types import (
    Candle,
    DerivativesStatus,
    FundingCurrencyBook,
    FundingCurrencyRawBook,
    FundingCurrencyTicker,
    FundingCurrencyTrade,
    FundingMarketAveragePrice,
    FundingStatistic,
    FxRate,
    Leaderboard,
    Liquidation,
    PlatformStatus,
    Statistic,
    TickersHistory,
    TradingMarketAveragePrice,
    TradingPairBook,
    TradingPairRawBook,
    TradingPairTicker,
    TradingPairTrade,
)


def _make_endpoint() -> tuple[RestPublicEndpoints, MagicMock]:
    ep = RestPublicEndpoints("https://api.example.com")
    ep._m = MagicMock()
    return ep, ep._m


class TestPlatformStatus:
    def test_get_platform_status(self):
        ep, mock_m = _make_endpoint()
        mock_m.get.return_value = [1]
        result = ep.get_platform_status()
        mock_m.get.assert_called_once_with("platform/status")
        assert isinstance(result, PlatformStatus)
        assert result.status == 1


class TestConf:
    def test_conf(self):
        ep, mock_m = _make_endpoint()
        mock_m.get.return_value = [["BTC", "ETH"]]
        result = ep.conf("pub:list:currency")
        mock_m.get.assert_called_once_with("conf/pub:list:currency")
        assert result == ["BTC", "ETH"]


class TestTickers:
    def test_get_tickers_trading(self):
        ep, mock_m = _make_endpoint()
        mock_m.get.return_value = [
            [
                "tBTCUSD",
                10000,
                1.5,
                10001,
                2.0,
                100,
                0.01,
                10000,
                50000,
                10500,
                9500,
            ]
        ]
        result = ep.get_tickers(["tBTCUSD"])
        mock_m.get.assert_called_once_with(
            "tickers", params={"symbols": "tBTCUSD"}
        )
        assert "tBTCUSD" in result
        assert isinstance(result["tBTCUSD"], TradingPairTicker)

    def test_get_tickers_funding(self):
        ep, mock_m = _make_endpoint()
        mock_m.get.return_value = [
            [
                "fUSD",
                0.0002,
                0.00025,
                30,
                1000000,
                0.0002,
                2,
                500000,
                0.0001,
                0.0003,
                100000,
                50000,
                0.001,
                0.0,
                None,
                None,
                500000,
            ]
        ]
        result = ep.get_tickers(["fUSD"])
        assert "fUSD" in result
        assert isinstance(result["fUSD"], FundingCurrencyTicker)

    def test_get_t_ticker(self):
        ep, mock_m = _make_endpoint()
        mock_m.get.return_value = [
            10000,
            1.5,
            10001,
            2.0,
            100,
            0.01,
            10000,
            50000,
            10500,
            9500,
        ]
        result = ep.get_t_ticker("tBTCUSD")
        mock_m.get.assert_called_once_with("ticker/tBTCUSD")
        assert isinstance(result, TradingPairTicker)

    def test_get_f_ticker(self):
        ep, mock_m = _make_endpoint()
        mock_m.get.return_value = [
            0.0002,
            0.00025,
            30,
            1000000,
            0.0002,
            2,
            500000,
            0.0001,
            0.0003,
            100000,
            50000,
            0.001,
            0.0,
            None,
            None,
            500000,
        ]
        result = ep.get_f_ticker("fUSD")
        mock_m.get.assert_called_once_with("ticker/fUSD")
        assert isinstance(result, FundingCurrencyTicker)

    def test_get_t_tickers_with_list(self):
        ep, mock_m = _make_endpoint()
        mock_m.get.return_value = [
            [
                "tBTCUSD",
                10000,
                1.5,
                10001,
                2.0,
                100,
                0.01,
                10000,
                50000,
                10500,
                9500,
            ]
        ]
        result = ep.get_t_tickers(["tBTCUSD"])
        assert "tBTCUSD" in result

    def test_get_f_tickers_with_list(self):
        ep, mock_m = _make_endpoint()
        mock_m.get.return_value = [
            [
                "fUSD",
                0.0002,
                0.00025,
                30,
                1000000,
                0.0002,
                2,
                500000,
                0.0001,
                0.0003,
                100000,
                50000,
                0.001,
                0.0,
                None,
                None,
                500000,
            ]
        ]
        result = ep.get_f_tickers(["fUSD"])
        assert "fUSD" in result


class TestTickersHistory:
    def test_get_tickers_history(self):
        ep, mock_m = _make_endpoint()
        mock_m.get.return_value = [
            ["tBTCUSD", 10000, None, 10001, None, None, None, None, None, None, None, None, 1609459200000]
        ]
        result = ep.get_tickers_history(["tBTCUSD"])
        mock_m.get.assert_called_once_with(
            "tickers/hist",
            params={
                "symbols": "tBTCUSD",
                "start": None,
                "end": None,
                "limit": None,
            },
        )
        assert len(result) == 1
        assert isinstance(result[0], TickersHistory)


class TestTrades:
    def test_get_t_trades(self):
        ep, mock_m = _make_endpoint()
        mock_m.get.return_value = [
            [123456, 1609459200000, 0.5, 10000]
        ]
        result = ep.get_t_trades("tBTCUSD")
        mock_m.get.assert_called_once_with(
            "trades/tBTCUSD/hist",
            params={"limit": None, "start": None, "end": None, "sort": None},
        )
        assert len(result) == 1
        assert isinstance(result[0], TradingPairTrade)

    def test_get_f_trades(self):
        ep, mock_m = _make_endpoint()
        mock_m.get.return_value = [
            [123456, 1609459200000, 1000, 0.0002, 30]
        ]
        result = ep.get_f_trades("fUSD")
        mock_m.get.assert_called_once_with(
            "trades/fUSD/hist",
            params={"limit": None, "start": None, "end": None, "sort": None},
        )
        assert len(result) == 1
        assert isinstance(result[0], FundingCurrencyTrade)

    def test_get_t_trades_with_params(self):
        ep, mock_m = _make_endpoint()
        mock_m.get.return_value = []
        result = ep.get_t_trades("tBTCUSD", limit=10, sort=-1)
        call_kwargs = mock_m.get.call_args
        assert call_kwargs.kwargs["params"]["limit"] == 10
        assert call_kwargs.kwargs["params"]["sort"] == -1


class TestBook:
    def test_get_t_book(self):
        ep, mock_m = _make_endpoint()
        mock_m.get.return_value = [
            [10000, 2, 1.5]
        ]
        result = ep.get_t_book("tBTCUSD", "P0")
        mock_m.get.assert_called_once_with(
            "book/tBTCUSD/P0", params={"len": None}
        )
        assert len(result) == 1
        assert isinstance(result[0], TradingPairBook)

    def test_get_f_book(self):
        ep, mock_m = _make_endpoint()
        mock_m.get.return_value = [
            [0.0002, 30, 2, 1000]
        ]
        result = ep.get_f_book("fUSD", "P0")
        mock_m.get.assert_called_once_with(
            "book/fUSD/P0", params={"len": None}
        )
        assert len(result) == 1
        assert isinstance(result[0], FundingCurrencyBook)

    def test_get_t_raw_book(self):
        ep, mock_m = _make_endpoint()
        mock_m.get.return_value = [
            [12345, 10000, 1.5]
        ]
        result = ep.get_t_raw_book("tBTCUSD")
        mock_m.get.assert_called_once_with(
            "book/tBTCUSD/R0", params={"len": None}
        )
        assert len(result) == 1
        assert isinstance(result[0], TradingPairRawBook)

    def test_get_f_raw_book(self):
        ep, mock_m = _make_endpoint()
        mock_m.get.return_value = [
            [12345, 30, 0.0002, 1000]
        ]
        result = ep.get_f_raw_book("fUSD")
        mock_m.get.assert_called_once_with(
            "book/fUSD/R0", params={"len": None}
        )
        assert len(result) == 1
        assert isinstance(result[0], FundingCurrencyRawBook)

    def test_get_t_book_with_len(self):
        ep, mock_m = _make_endpoint()
        mock_m.get.return_value = []
        ep.get_t_book("tBTCUSD", "P1", len=25)
        mock_m.get.assert_called_once_with(
            "book/tBTCUSD/P1", params={"len": 25}
        )


class TestStats:
    def test_get_stats_hist(self):
        ep, mock_m = _make_endpoint()
        mock_m.get.return_value = [
            [1609459200000, 100]
        ]
        result = ep.get_stats_hist("pos.size:1m:tBTCUSD:long")
        mock_m.get.assert_called_once()
        assert len(result) == 1
        assert isinstance(result[0], Statistic)

    def test_get_stats_last(self):
        ep, mock_m = _make_endpoint()
        mock_m.get.return_value = [1609459200000, 100]
        result = ep.get_stats_last("pos.size:1m:tBTCUSD:long")
        assert isinstance(result, Statistic)


class TestCandles:
    def test_get_candles_hist(self):
        ep, mock_m = _make_endpoint()
        mock_m.get.return_value = [
            [1609459200000, 10000, 10100, 10200, 9900, 500]
        ]
        result = ep.get_candles_hist("tBTCUSD")
        mock_m.get.assert_called_once()
        assert len(result) == 1
        assert isinstance(result[0], Candle)

    def test_get_candles_last(self):
        ep, mock_m = _make_endpoint()
        mock_m.get.return_value = [1609459200000, 10000, 10100, 10200, 9900, 500]
        result = ep.get_candles_last("tBTCUSD")
        assert isinstance(result, Candle)

    def test_get_candles_hist_with_params(self):
        ep, mock_m = _make_endpoint()
        mock_m.get.return_value = []
        ep.get_candles_hist("tBTCUSD", tf="1h", limit=100, sort=-1)
        call_args = mock_m.get.call_args
        assert "candles/trade:1h:tBTCUSD/hist" in call_args.args

    def test_get_seed_candles(self):
        ep, mock_m = _make_endpoint()
        mock_m.get.return_value = [
            [1609459200000, 10000, 10100, 10200, 9900, 500]
        ]
        result = ep.get_seed_candles("tBTCUSD", tf="5m")
        assert len(result) == 1
        assert isinstance(result[0], Candle)


class TestDerivatives:
    def test_get_derivatives_status(self):
        ep, mock_m = _make_endpoint()
        # 23 labels: key + mts, _PH, deriv_price, spot_price, _PH, insurance,
        # _PH, next_funding_evt_mts, next_funding_accrued, next_funding_step,
        # _PH, current_funding, _PH, _PH, mark_price, _PH, _PH, open_interest,
        # _PH, _PH, _PH, clamp_min, clamp_max
        mock_m.get.return_value = [
            ["tBTCF0:USTF0"] + [None] * 2 + [10000, 10100] + [None] * 2
            + [None, 0.0001, 0.0002, None, None, 0.001]
            + [None] * 2 + [10050] + [None] * 2 + [100000]
            + [None] * 2 + [None, -0.001, 0.001]
        ]
        result = ep.get_derivatives_status(["tBTCF0:USTF0"])
        mock_m.get.assert_called_once_with(
            "status/deriv", params={"keys": "tBTCF0:USTF0"}
        )
        assert "tBTCF0:USTF0" in result
        assert isinstance(result["tBTCF0:USTF0"], DerivativesStatus)

    def test_get_derivatives_status_all(self):
        ep, mock_m = _make_endpoint()
        mock_m.get.return_value = []
        ep.get_derivatives_status("ALL")
        mock_m.get.assert_called_once_with(
            "status/deriv", params={"keys": "ALL"}
        )

    def test_get_derivatives_status_history(self):
        ep, mock_m = _make_endpoint()
        # 23 labels (no key prefix for history)
        mock_m.get.return_value = [
            [1609459200000, None, 10000, 10100, None, None, None,
             0.0001, 0.0002, None, None, 0.001, None, None, 10050,
             None, None, 100000, None, None, None, -0.001, 0.001]
        ]
        result = ep.get_derivatives_status_history("tBTCF0:USTF0")
        assert len(result) == 1
        assert isinstance(result[0], DerivativesStatus)


class TestLiquidations:
    def test_get_liquidations(self):
        ep, mock_m = _make_endpoint()
        # 12 labels: _PH, pos_id, mts, _PH, symbol, amount, base_price,
        #            _PH, is_match, is_market_sold, _PH, liquidation_price
        mock_m.get.return_value = [
            [[None, 12345, 1609459200000, None, "tBTCUSD", 0.5, 10000,
              None, 1, 0, None, 9800]]
        ]
        result = ep.get_liquidations()
        mock_m.get.assert_called_once()
        assert len(result) == 1
        assert isinstance(result[0], Liquidation)


class TestLeaderboards:
    def test_get_leaderboards_hist(self):
        ep, mock_m = _make_endpoint()
        # 10 labels: mts, _PH, username, ranking, _PH, _PH, value, _PH, _PH, twitter_handle
        mock_m.get.return_value = [
            [1609459200000, None, "username", 1, None, None, 100000, None, None, "@user"]
        ]
        result = ep.get_leaderboards_hist("plu_diff:1M:tGLOBAL:USD")
        assert len(result) == 1
        assert isinstance(result[0], Leaderboard)

    def test_get_leaderboards_last(self):
        ep, mock_m = _make_endpoint()
        mock_m.get.return_value = [
            1609459200000, None, "username", 1, None, None, 100000, None, None, "@user"
        ]
        result = ep.get_leaderboards_last("plu_diff:1M:tGLOBAL:USD")
        assert isinstance(result, Leaderboard)


class TestFundingStats:
    def test_get_funding_stats(self):
        ep, mock_m = _make_endpoint()
        mock_m.get.return_value = [
            [1609459200000, None, None, 0.0002, 0.00025, None, None, None, None, None, None, None]
        ]
        result = ep.get_funding_stats("fUSD")
        mock_m.get.assert_called_once()
        assert len(result) == 1
        assert isinstance(result[0], FundingStatistic)


class TestMarketAveragePrice:
    def test_get_trading_market_average_price(self):
        ep, mock_m = _make_endpoint()
        mock_m.post.return_value = [10050.5, 0.5]
        result = ep.get_trading_market_average_price("tBTCUSD", "0.5")
        mock_m.post.assert_called_once_with(
            "calc/trade/avg",
            body={"symbol": "tBTCUSD", "amount": "0.5", "price_limit": None},
        )
        assert isinstance(result, TradingMarketAveragePrice)

    def test_get_funding_market_average_price(self):
        ep, mock_m = _make_endpoint()
        mock_m.post.return_value = [0.0002, 1000]
        result = ep.get_funding_market_average_price("fUSD", "1000", 30)
        mock_m.post.assert_called_once_with(
            "calc/trade/avg",
            body={
                "symbol": "fUSD",
                "amount": "1000",
                "period": 30,
                "rate_limit": None,
            },
        )
        assert isinstance(result, FundingMarketAveragePrice)


class TestFxRate:
    def test_get_fx_rate(self):
        ep, mock_m = _make_endpoint()
        mock_m.post.return_value = [1.085]
        result = ep.get_fx_rate("EUR", "USD")
        mock_m.post.assert_called_once_with(
            "calc/fx", body={"ccy1": "EUR", "ccy2": "USD"}
        )
        assert isinstance(result, FxRate)
