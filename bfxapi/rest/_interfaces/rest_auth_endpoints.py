from decimal import Decimal
from typing import Any, Literal

from bfxapi.rest._interface import Interface
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
    serializers,
)
from bfxapi.types.notification import _Notification


class RestAuthEndpoints(Interface):
    def get_user_info(self) -> UserInfo:
        return serializers.UserInfo.parse(*self._m.post("auth/r/info/user"))

    def get_login_history(self) -> list[LoginHistory]:
        return [
            serializers.LoginHistory.parse(*sub_data)
            for sub_data in self._m.post("auth/r/logins/hist")
        ]

    def get_balance_available_for_orders_or_offers(
        self,
        symbol: str,
        type: str,
        *,
        dir: int | None = None,
        rate: str | None = None,
        lev: str | None = None,
    ) -> BalanceAvailable:
        body = {
            "symbol": symbol,
            "type": type,
            "dir": dir,
            "rate": rate,
            "lev": lev,
        }

        return serializers.BalanceAvailable.parse(
            *self._m.post("auth/calc/order/avail", body=body)
        )

    def get_wallets(self) -> list[Wallet]:
        return [
            serializers.Wallet.parse(*sub_data)
            for sub_data in self._m.post("auth/r/wallets")
        ]

    def get_orders(
        self, *, symbol: str | None = None, ids: list[str] | None = None
    ) -> list[Order]:
        if symbol is None:
            endpoint = "auth/r/orders"
        else:
            endpoint = f"auth/r/orders/{symbol}"

        return [
            serializers.Order.parse(*sub_data)
            for sub_data in self._m.post(endpoint, body={"id": ids})
        ]

    def submit_order(
        self,
        type: str,
        symbol: str,
        amount: str | float | Decimal,
        price: str | float | Decimal,
        *,
        lev: int | None = None,
        price_trailing: str | float | Decimal | None = None,
        price_aux_limit: str | float | Decimal | None = None,
        price_oco_stop: str | float | Decimal | None = None,
        gid: int | None = None,
        cid: int | None = None,
        flags: int | None = None,
        tif: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> Notification[Order]:
        body = {
            "type": type,
            "symbol": symbol,
            "amount": amount,
            "price": price,
            "lev": lev,
            "price_trailing": price_trailing,
            "price_aux_limit": price_aux_limit,
            "price_oco_stop": price_oco_stop,
            "gid": gid,
            "cid": cid,
            "flags": flags,
            "tif": tif,
            "meta": meta,
        }

        return _Notification[Order](serializers.Order).parse(
            *self._m.post("auth/w/order/submit", body=body)
        )

    def update_order(
        self,
        id: int,
        *,
        amount: str | float | Decimal | None = None,
        price: str | float | Decimal | None = None,
        cid: int | None = None,
        cid_date: str | None = None,
        gid: int | None = None,
        flags: int | None = None,
        lev: int | None = None,
        delta: str | float | Decimal | None = None,
        price_aux_limit: str | float | Decimal | None = None,
        price_trailing: str | float | Decimal | None = None,
        tif: str | None = None,
    ) -> Notification[Order]:
        body = {
            "id": id,
            "amount": amount,
            "price": price,
            "cid": cid,
            "cid_date": cid_date,
            "gid": gid,
            "flags": flags,
            "lev": lev,
            "delta": delta,
            "price_aux_limit": price_aux_limit,
            "price_trailing": price_trailing,
            "tif": tif,
        }

        return _Notification[Order](serializers.Order).parse(
            *self._m.post("auth/w/order/update", body=body)
        )

    def cancel_order(
        self,
        *,
        id: int | None = None,
        cid: int | None = None,
        cid_date: str | None = None,
    ) -> Notification[Order]:
        return _Notification[Order](serializers.Order).parse(
            *self._m.post(
                "auth/w/order/cancel",
                body={"id": id, "cid": cid, "cid_date": cid_date},
            )
        )

    def cancel_order_multi(
        self,
        *,
        id: list[int] | None = None,
        cid: list[tuple[int, str]] | None = None,
        gid: list[int] | None = None,
        all: bool | None = None,
    ) -> Notification[list[Order]]:
        body = {"id": id, "cid": cid, "gid": gid, "all": all}

        return _Notification[list[Order]](
            serializers.Order, is_iterable=True
        ).parse(*self._m.post("auth/w/order/cancel/multi", body=body))

    def get_orders_history(
        self,
        *,
        symbol: str | None = None,
        ids: list[int] | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
    ) -> list[Order]:
        if symbol is None:
            endpoint = "auth/r/orders/hist"
        else:
            endpoint = f"auth/r/orders/{symbol}/hist"

        body = {"id": ids, "start": start, "end": end, "limit": limit}

        return [
            serializers.Order.parse(*sub_data)
            for sub_data in self._m.post(endpoint, body=body)
        ]

    def get_order_trades(self, symbol: str, id: int) -> list[OrderTrade]:
        return [
            serializers.OrderTrade.parse(*sub_data)
            for sub_data in self._m.post(f"auth/r/order/{symbol}:{id}/trades")
        ]

    def get_trades_history(
        self,
        *,
        symbol: str | None = None,
        sort: int | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
    ) -> list[Trade]:
        if symbol is None:
            endpoint = "auth/r/trades/hist"
        else:
            endpoint = f"auth/r/trades/{symbol}/hist"

        body = {"sort": sort, "start": start, "end": end, "limit": limit}

        return [
            serializers.Trade.parse(*sub_data)
            for sub_data in self._m.post(endpoint, body=body)
        ]

    def get_ledgers(
        self,
        currency: str | None = None,
        *,
        category: int | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
    ) -> list[Ledger]:
        if currency is None:
            endpoint = "auth/r/ledgers/hist"
        else:
            endpoint = f"auth/r/ledgers/{currency}/hist"

        body = {
            "category": category,
            "start": start,
            "end": end,
            "limit": limit,
        }

        return [
            serializers.Ledger.parse(*sub_data)
            for sub_data in self._m.post(endpoint, body=body)
        ]

    def get_base_margin_info(self) -> BaseMarginInfo:
        return serializers.BaseMarginInfo.parse(
            *self._m.post("auth/r/info/margin/base")
        )

    def get_symbol_margin_info(self, symbol: str) -> SymbolMarginInfo:
        return serializers.SymbolMarginInfo.parse(
            *self._m.post(f"auth/r/info/margin/{symbol}")
        )

    def get_all_symbols_margin_info(self) -> list[SymbolMarginInfo]:
        return [
            serializers.SymbolMarginInfo.parse(*sub_data)
            for sub_data in self._m.post("auth/r/info/margin/sym_all")
        ]

    def get_positions(self) -> list[Position]:
        return [
            serializers.Position.parse(*sub_data)
            for sub_data in self._m.post("auth/r/positions")
        ]

    def claim_position(
        self, id: int, *, amount: str | float | Decimal | None = None
    ) -> Notification[PositionClaim]:
        return _Notification[PositionClaim](serializers.PositionClaim).parse(
            *self._m.post(
                "auth/w/position/claim", body={"id": id, "amount": amount}
            )
        )

    def increase_position(
        self, symbol: str, amount: str | float | Decimal
    ) -> Notification[PositionIncrease]:
        return _Notification[PositionIncrease](
            serializers.PositionIncrease
        ).parse(
            *self._m.post(
                "auth/w/position/increase",
                body={"symbol": symbol, "amount": amount},
            )
        )

    def get_increase_position_info(
        self, symbol: str, amount: str | float | Decimal
    ) -> PositionIncreaseInfo:
        return serializers.PositionIncreaseInfo.parse(
            *self._m.post(
                "auth/r/position/increase/info",
                body={"symbol": symbol, "amount": amount},
            )
        )

    def get_positions_history(
        self,
        *,
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
    ) -> list[PositionHistory]:
        return [
            serializers.PositionHistory.parse(*sub_data)
            for sub_data in self._m.post(
                "auth/r/positions/hist",
                body={"start": start, "end": end, "limit": limit},
            )
        ]

    def get_positions_snapshot(
        self,
        *,
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
    ) -> list[PositionSnapshot]:
        return [
            serializers.PositionSnapshot.parse(*sub_data)
            for sub_data in self._m.post(
                "auth/r/positions/snap",
                body={"start": start, "end": end, "limit": limit},
            )
        ]

    def get_positions_audit(
        self,
        *,
        ids: list[int] | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
    ) -> list[PositionAudit]:
        body = {"ids": ids, "start": start, "end": end, "limit": limit}

        return [
            serializers.PositionAudit.parse(*sub_data)
            for sub_data in self._m.post("auth/r/positions/audit", body=body)
        ]

    def set_derivative_position_collateral(
        self, symbol: str, collateral: str | float | Decimal
    ) -> DerivativePositionCollateral:
        return serializers.DerivativePositionCollateral.parse(
            *(
                self._m.post(
                    "auth/w/deriv/collateral/set",
                    body={"symbol": symbol, "collateral": collateral},
                )[0]
            )
        )

    def get_derivative_position_collateral_limits(
        self, symbol: str
    ) -> DerivativePositionCollateralLimits:
        return serializers.DerivativePositionCollateralLimits.parse(
            *self._m.post(
                "auth/calc/deriv/collateral/limit", body={"symbol": symbol}
            )
        )

    def get_funding_offers(
        self, *, symbol: str | None = None
    ) -> list[FundingOffer]:
        if symbol is None:
            endpoint = "auth/r/funding/offers"
        else:
            endpoint = f"auth/r/funding/offers/{symbol}"

        return [
            serializers.FundingOffer.parse(*sub_data)
            for sub_data in self._m.post(endpoint)
        ]

    def submit_funding_offer(
        self,
        type: str,
        symbol: str,
        amount: str | float | Decimal,
        rate: str | float | Decimal,
        period: int,
        *,
        flags: int | None = None,
    ) -> Notification[FundingOffer]:
        body = {
            "type": type,
            "symbol": symbol,
            "amount": amount,
            "rate": rate,
            "period": period,
            "flags": flags,
        }

        return _Notification[FundingOffer](serializers.FundingOffer).parse(
            *self._m.post("auth/w/funding/offer/submit", body=body)
        )

    def cancel_funding_offer(self, id: int) -> Notification[FundingOffer]:
        return _Notification[FundingOffer](serializers.FundingOffer).parse(
            *self._m.post("auth/w/funding/offer/cancel", body={"id": id})
        )

    def cancel_all_funding_offers(
        self, currency: str
    ) -> Notification[Literal[None]]:
        return _Notification[Literal[None]](None).parse(
            *self._m.post(
                "auth/w/funding/offer/cancel/all", body={"currency": currency}
            )
        )

    def submit_funding_close(self, id: int) -> Notification[Literal[None]]:
        return _Notification[Literal[None]](None).parse(
            *self._m.post("auth/w/funding/close", body={"id": id})
        )

    def toggle_auto_renew(
        self,
        status: bool,
        currency: str,
        *,
        amount: str | None = None,
        rate: int | None = None,
        period: int | None = None,
    ) -> Notification[FundingAutoRenew]:
        body = {
            "status": status,
            "currency": currency,
            "amount": amount,
            "rate": rate,
            "period": period,
        }

        return _Notification[FundingAutoRenew](
            serializers.FundingAutoRenew
        ).parse(*self._m.post("auth/w/funding/auto", body=body))

    def toggle_keep_funding(
        self,
        type: Literal["credit", "loan"],
        *,
        ids: list[int] | None = None,
        changes: dict[int, Literal[1, 2]] | None = None,
    ) -> Notification[Literal[None]]:
        return _Notification[Literal[None]](None).parse(
            *self._m.post(
                "auth/w/funding/keep",
                body={"type": type, "id": ids, "changes": changes},
            )
        )

    def get_funding_offers_history(
        self,
        *,
        symbol: str | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
    ) -> list[FundingOffer]:
        if symbol is None:
            endpoint = "auth/r/funding/offers/hist"
        else:
            endpoint = f"auth/r/funding/offers/{symbol}/hist"

        return [
            serializers.FundingOffer.parse(*sub_data)
            for sub_data in self._m.post(
                endpoint, body={"start": start, "end": end, "limit": limit}
            )
        ]

    def get_funding_loans(
        self, *, symbol: str | None = None
    ) -> list[FundingLoan]:
        if symbol is None:
            endpoint = "auth/r/funding/loans"
        else:
            endpoint = f"auth/r/funding/loans/{symbol}"

        return [
            serializers.FundingLoan.parse(*sub_data)
            for sub_data in self._m.post(endpoint)
        ]

    def get_funding_loans_history(
        self,
        *,
        symbol: str | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
    ) -> list[FundingLoan]:
        if symbol is None:
            endpoint = "auth/r/funding/loans/hist"
        else:
            endpoint = f"auth/r/funding/loans/{symbol}/hist"

        return [
            serializers.FundingLoan.parse(*sub_data)
            for sub_data in self._m.post(
                endpoint, body={"start": start, "end": end, "limit": limit}
            )
        ]

    def get_funding_credits(
        self, *, symbol: str | None = None
    ) -> list[FundingCredit]:
        if symbol is None:
            endpoint = "auth/r/funding/credits"
        else:
            endpoint = f"auth/r/funding/credits/{symbol}"

        return [
            serializers.FundingCredit.parse(*sub_data)
            for sub_data in self._m.post(endpoint)
        ]

    def get_funding_credits_history(
        self,
        *,
        symbol: str | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
    ) -> list[FundingCredit]:
        if symbol is None:
            endpoint = "auth/r/funding/credits/hist"
        else:
            endpoint = f"auth/r/funding/credits/{symbol}/hist"

        return [
            serializers.FundingCredit.parse(*sub_data)
            for sub_data in self._m.post(
                endpoint, body={"start": start, "end": end, "limit": limit}
            )
        ]

    def get_funding_trades_history(
        self,
        *,
        symbol: str | None = None,
        sort: int | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
    ) -> list[FundingTrade]:
        if symbol is None:
            endpoint = "auth/r/funding/trades/hist"
        else:
            endpoint = f"auth/r/funding/trades/{symbol}/hist"

        body = {"sort": sort, "start": start, "end": end, "limit": limit}

        return [
            serializers.FundingTrade.parse(*sub_data)
            for sub_data in self._m.post(endpoint, body=body)
        ]

    def get_funding_info(self, key: str) -> FundingInfo:
        return serializers.FundingInfo.parse(
            *self._m.post(f"auth/r/info/funding/{key}")
        )

    def transfer_between_wallets(
        self,
        from_wallet: str,
        to_wallet: str,
        currency: str,
        currency_to: str,
        amount: str | float | Decimal,
    ) -> Notification[Transfer]:
        body = {
            "from": from_wallet,
            "to": to_wallet,
            "currency": currency,
            "currency_to": currency_to,
            "amount": amount,
        }

        return _Notification[Transfer](serializers.Transfer).parse(
            *self._m.post("auth/w/transfer", body=body)
        )

    def submit_wallet_withdrawal(
        self,
        wallet: str,
        method: str,
        address: str,
        amount: str | float | Decimal,
    ) -> Notification[Withdrawal]:
        body = {
            "wallet": wallet,
            "method": method,
            "address": address,
            "amount": amount,
        }

        return _Notification[Withdrawal](serializers.Withdrawal).parse(
            *self._m.post("auth/w/withdraw", body=body)
        )

    def get_deposit_address(
        self, wallet: str, method: str, op_renew: bool = False
    ) -> Notification[DepositAddress]:
        return _Notification[DepositAddress](serializers.DepositAddress).parse(
            *self._m.post(
                "auth/w/deposit/address",
                body={"wallet": wallet, "method": method, "op_renew": op_renew},
            )
        )

    def generate_deposit_invoice(
        self, wallet: str, currency: str, amount: str | float | Decimal
    ) -> LightningNetworkInvoice:
        return serializers.LightningNetworkInvoice.parse(
            *self._m.post(
                "auth/w/deposit/invoice",
                body={"wallet": wallet, "currency": currency, "amount": amount},
            )
        )

    def get_movements(
        self,
        *,
        currency: str | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
    ) -> list[Movement]:
        if currency is None:
            endpoint = "auth/r/movements/hist"
        else:
            endpoint = f"auth/r/movements/{currency}/hist"

        return [
            serializers.Movement.parse(*sub_data)
            for sub_data in self._m.post(
                endpoint, body={"start": start, "end": end, "limit": limit}
            )
        ]
