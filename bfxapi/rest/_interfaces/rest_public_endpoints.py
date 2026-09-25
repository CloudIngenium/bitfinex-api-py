from decimal import Decimal
from typing import Any, Literal, cast

from bfxapi.rest._interface import Interface
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
    PairInfo,
    PlatformStatus,
    Statistic,
    TickersHistory,
    TradingMarketAveragePrice,
    TradingPairBook,
    TradingPairRawBook,
    TradingPairTicker,
    TradingPairTrade,
    serializers,
)


def _candle_key(
    tf: str,
    symbol: str,
    period: int | None,
    aggregation: int | None,
    period_start: int | None,
    period_end: int | None,
) -> str:
    """Build the ``trade:<tf>:<symbol>[:selector]`` candle key.

    Bitfinex accepts two funding selector forms: a single period (``:p30``) or
    an aggregate over a range (``:a30:p2:p30``). The aggregate form needs all
    three values — an aggregation without a range yields a key the API answers
    with an empty array, so raise instead of issuing it.
    """
    key = f"trade:{tf}:{symbol}"

    if aggregation is not None:
        if period_start is None or period_end is None:
            raise ValueError(
                "aggregation requires both period_start and period_end"
            )
        return f"{key}:a{aggregation}:p{period_start}:p{period_end}"

    if period is not None:
        return f"{key}:p{period}"

    return key


class RestPublicEndpoints(Interface):
    def conf(self, config: str) -> Any:
        return self._m.get(f"conf/{config}")[0]

    def get_platform_status(self) -> PlatformStatus:
        return serializers.PlatformStatus.parse(*self._m.get("platform/status"))

    def get_pairs_info(
        self, *, include_futures: bool = True
    ) -> dict[str, PairInfo]:
        """Trading-pair metadata from ``pub:info:pair``.

        Carries each pair's minimum and maximum order size, its margin
        requirements and — since the 2026-09-23 API update — the timestamp
        of its first trade.

        :param include_futures: also request ``pub:info:pair:futures``.
        :return: pair symbol (e.g. ``"BTCUSD"``) mapped to its PairInfo.
        """
        config = "pub:info:pair"

        if include_futures:
            config += ",pub:info:pair:futures"

        blocks = cast(list[list[Any]], self._m.get(f"conf/{config}"))

        return {
            cast(str, sub_data[0]): serializers.PairInfo.parse(*sub_data)
            for block in blocks
            for sub_data in block
        }

    def get_tickers(
        self, symbols: list[str]
    ) -> dict[str, TradingPairTicker | FundingCurrencyTicker]:
        data = self._m.get("tickers", params={"symbols": ",".join(symbols)})

        parsers = {
            "t": serializers.TradingPairTicker.parse,
            "f": serializers.FundingCurrencyTicker.parse,
        }

        return {
            symbol: cast(
                TradingPairTicker | FundingCurrencyTicker,
                parsers[symbol[0]](*sub_data),
            )
            for sub_data in data
            if (symbol := sub_data.pop(0))
        }

    def get_t_tickers(
        self, symbols: list[str] | Literal["ALL"]
    ) -> dict[str, TradingPairTicker]:
        if isinstance(symbols, str) and symbols == "ALL":
            return {
                symbol: cast(TradingPairTicker, sub_data)
                for symbol, sub_data in self.get_tickers(["ALL"]).items()
                if symbol.startswith("t")
            }

        data = self.get_tickers(list(symbols))

        return cast(dict[str, TradingPairTicker], data)

    def get_f_tickers(
        self, symbols: list[str] | Literal["ALL"]
    ) -> dict[str, FundingCurrencyTicker]:
        if isinstance(symbols, str) and symbols == "ALL":
            return {
                symbol: cast(FundingCurrencyTicker, sub_data)
                for symbol, sub_data in self.get_tickers(["ALL"]).items()
                if symbol.startswith("f")
            }

        data = self.get_tickers(list(symbols))

        return cast(dict[str, FundingCurrencyTicker], data)

    def get_t_ticker(self, symbol: str) -> TradingPairTicker:
        return serializers.TradingPairTicker.parse(
            *self._m.get(f"ticker/{symbol}")
        )

    def get_f_ticker(self, symbol: str) -> FundingCurrencyTicker:
        return serializers.FundingCurrencyTicker.parse(
            *self._m.get(f"ticker/{symbol}")
        )

    def get_tickers_history(
        self,
        symbols: list[str],
        *,
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
    ) -> list[TickersHistory]:
        return [
            serializers.TickersHistory.parse(*sub_data)
            for sub_data in self._m.get(
                "tickers/hist",
                params={
                    "symbols": ",".join(symbols),
                    "start": start,
                    "end": end,
                    "limit": limit,
                },
            )
        ]

    def get_t_trades(
        self,
        pair: str,
        *,
        limit: int | None = None,
        start: str | None = None,
        end: str | None = None,
        sort: int | None = None,
    ) -> list[TradingPairTrade]:
        params = {"limit": limit, "start": start, "end": end, "sort": sort}
        data = self._m.get(f"trades/{pair}/hist", params=params)
        return [
            serializers.TradingPairTrade.parse(*sub_data) for sub_data in data
        ]

    def get_f_trades(
        self,
        currency: str,
        *,
        limit: int | None = None,
        start: str | None = None,
        end: str | None = None,
        sort: int | None = None,
    ) -> list[FundingCurrencyTrade]:
        params = {"limit": limit, "start": start, "end": end, "sort": sort}
        data = self._m.get(f"trades/{currency}/hist", params=params)
        return [
            serializers.FundingCurrencyTrade.parse(*sub_data)
            for sub_data in data
        ]

    def get_t_book(
        self,
        pair: str,
        precision: Literal["P0", "P1", "P2", "P3", "P4"],
        *,
        len: Literal[1, 25, 100] | None = None,
    ) -> list[TradingPairBook]:
        return [
            serializers.TradingPairBook.parse(*sub_data)
            for sub_data in self._m.get(
                f"book/{pair}/{precision}", params={"len": len}
            )
        ]

    def get_f_book(
        self,
        currency: str,
        precision: Literal["P0", "P1", "P2", "P3", "P4"],
        *,
        len: Literal[1, 25, 100] | None = None,
    ) -> list[FundingCurrencyBook]:
        return [
            serializers.FundingCurrencyBook.parse(*sub_data)
            for sub_data in self._m.get(
                f"book/{currency}/{precision}", params={"len": len}
            )
        ]

    def get_t_raw_book(
        self, pair: str, *, len: Literal[1, 25, 100] | None = None
    ) -> list[TradingPairRawBook]:
        return [
            serializers.TradingPairRawBook.parse(*sub_data)
            for sub_data in self._m.get(f"book/{pair}/R0", params={"len": len})
        ]

    def get_f_raw_book(
        self, currency: str, *, len: Literal[1, 25, 100] | None = None
    ) -> list[FundingCurrencyRawBook]:
        return [
            serializers.FundingCurrencyRawBook.parse(*sub_data)
            for sub_data in self._m.get(
                f"book/{currency}/R0", params={"len": len}
            )
        ]

    def get_stats_hist(
        self,
        resource: str,
        *,
        sort: int | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
    ) -> list[Statistic]:
        params = {"sort": sort, "start": start, "end": end, "limit": limit}
        data = self._m.get(f"stats1/{resource}/hist", params=params)
        return [serializers.Statistic.parse(*sub_data) for sub_data in data]

    def get_stats_last(
        self,
        resource: str,
        *,
        sort: int | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
    ) -> Statistic:
        params = {"sort": sort, "start": start, "end": end, "limit": limit}
        data = self._m.get(f"stats1/{resource}/last", params=params)
        return serializers.Statistic.parse(*data)

    def get_candles_hist(
        self,
        symbol: str,
        tf: str = "1m",
        *,
        period: int | None = None,
        aggregation: int | None = None,
        period_start: int | None = None,
        period_end: int | None = None,
        sort: int | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
    ) -> list[Candle]:
        """Historical candles.

        Funding symbols need a period selector appended to the candle key.
        Pass ``period`` for a single period, or ``aggregation`` together with
        ``period_start``/``period_end`` for an aggregated range — do not splice
        the suffix into ``symbol`` yourself. Bitfinex answers a malformed
        selector with an empty array rather than an error, so a typo here is
        indistinguishable from "no data".
        """
        key = _candle_key(
            tf, symbol, period, aggregation, period_start, period_end
        )
        params = {"sort": sort, "start": start, "end": end, "limit": limit}
        data = self._m.get(f"candles/{key}/hist", params=params)
        return [serializers.Candle.parse(*sub_data) for sub_data in data]

    def get_candles_last(
        self,
        symbol: str,
        tf: str = "1m",
        *,
        period: int | None = None,
        aggregation: int | None = None,
        period_start: int | None = None,
        period_end: int | None = None,
        sort: int | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
    ) -> Candle:
        """Most recent candle. See :meth:`get_candles_hist` for the period
        selector arguments."""
        key = _candle_key(
            tf, symbol, period, aggregation, period_start, period_end
        )
        params = {"sort": sort, "start": start, "end": end, "limit": limit}
        data = self._m.get(f"candles/{key}/last", params=params)
        return serializers.Candle.parse(*data)

    def get_derivatives_status(
        self, keys: list[str] | Literal["ALL"]
    ) -> dict[str, DerivativesStatus]:
        if keys == "ALL":
            params = {"keys": "ALL"}
        else:
            params = {"keys": ",".join(keys)}

        data = self._m.get("status/deriv", params=params)

        return {
            key: serializers.DerivativesStatus.parse(*sub_data)
            for sub_data in data
            if (key := sub_data.pop(0))
        }

    def get_derivatives_status_history(
        self,
        key: str,
        *,
        sort: int | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
    ) -> list[DerivativesStatus]:
        params = {"sort": sort, "start": start, "end": end, "limit": limit}
        data = self._m.get(f"status/deriv/{key}/hist", params=params)
        return [
            serializers.DerivativesStatus.parse(*sub_data) for sub_data in data
        ]

    def get_liquidations(
        self,
        *,
        sort: int | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
    ) -> list[Liquidation]:
        params = {"sort": sort, "start": start, "end": end, "limit": limit}
        data = self._m.get("liquidations/hist", params=params)
        return [
            serializers.Liquidation.parse(*sub_data[0]) for sub_data in data
        ]

    def get_seed_candles(
        self,
        symbol: str,
        tf: str = "1m",
        *,
        sort: int | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
    ) -> list[Candle]:
        return self.get_candles_hist(
            symbol, tf, sort=sort, start=start, end=end, limit=limit
        )

    def get_leaderboards_hist(
        self,
        resource: str,
        *,
        sort: int | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
    ) -> list[Leaderboard]:
        params = {"sort": sort, "start": start, "end": end, "limit": limit}
        data = self._m.get(f"rankings/{resource}/hist", params=params)
        return [serializers.Leaderboard.parse(*sub_data) for sub_data in data]

    def get_leaderboards_last(
        self,
        resource: str,
        *,
        sort: int | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
    ) -> Leaderboard:
        params = {"sort": sort, "start": start, "end": end, "limit": limit}
        data = self._m.get(f"rankings/{resource}/last", params=params)
        return serializers.Leaderboard.parse(*data)

    def get_funding_stats(
        self,
        symbol: str,
        *,
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
    ) -> list[FundingStatistic]:
        params = {"start": start, "end": end, "limit": limit}
        data = self._m.get(f"funding/stats/{symbol}/hist", params=params)
        return [
            serializers.FundingStatistic.parse(*sub_data) for sub_data in data
        ]

    def get_trading_market_average_price(
        self,
        symbol: str,
        amount: str | float | Decimal,
        *,
        price_limit: str | float | Decimal | None = None,
    ) -> TradingMarketAveragePrice:
        return serializers.TradingMarketAveragePrice.parse(
            *self._m.post(
                "calc/trade/avg",
                body={
                    "symbol": symbol,
                    "amount": amount,
                    "price_limit": price_limit,
                },
            )
        )

    def get_funding_market_average_price(
        self,
        symbol: str,
        amount: str | float | Decimal,
        period: int,
        *,
        rate_limit: str | float | Decimal | None = None,
    ) -> FundingMarketAveragePrice:
        return serializers.FundingMarketAveragePrice.parse(
            *self._m.post(
                "calc/trade/avg",
                body={
                    "symbol": symbol,
                    "amount": amount,
                    "period": period,
                    "rate_limit": rate_limit,
                },
            )
        )

    def get_fx_rate(self, ccy1: str, ccy2: str) -> FxRate:
        return serializers.FxRate.parse(
            *self._m.post("calc/fx", body={"ccy1": ccy1, "ccy2": ccy2})
        )
