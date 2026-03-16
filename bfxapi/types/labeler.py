from collections.abc import Callable, Iterable
from decimal import Decimal
from typing import Any, Generic, TypeVar, cast

T = TypeVar("T", bound="_Type")

# Field names that represent monetary/financial values.
# When decimal_mode is enabled, these are converted from float to Decimal.
MONETARY_FIELDS: frozenset[str] = frozenset(
    {
        "amount",
        "amount_orig",
        "ask",
        "ask_size",
        "available_balance",
        "balance",
        "base_price",
        "bid",
        "bid_size",
        "buy",
        "collateral",
        "collateral_min",
        "current_pos",
        "daily_change",
        "daily_change_relative",
        "deriv_price",
        "exec_amount",
        "exec_price",
        "fee",
        "fees",
        "frr",
        "frr_amount_available",
        "funding",
        "funding_amount",
        "funding_amount_used",
        "funding_avail",
        "funding_below_threshold",
        "funding_required",
        "funding_required_currency",
        "funding_value",
        "funding_value_currency",
        "gross_balance",
        "high",
        "insurance_fund_balance",
        "last_price",
        "leverage",
        "low",
        "margin_balance",
        "margin_funding",
        "margin_net",
        "margin_min",
        "mark_price",
        "max_pos",
        "min_collateral",
        "max_collateral",
        "next_funding_accrued",
        "next_funding_step",
        "open_interest",
        "order_price",
        "pl",
        "pl_perc",
        "price",
        "price_avg",
        "price_liq",
        "price_trailing",
        "price_aux_limit",
        "rate",
        "rate_avg",
        "sell",
        "spot_price",
        "tradable_balance",
        "tradable_balance_base_currency",
        "tradable_balance_base_total",
        "tradable_balance_quote_currency",
        "tradable_balance_quote_total",
        "unsettled_interest",
        "user_pl",
        "user_swaps",
        "value",
        "volume",
        "withdrawal_fee",
        "yield_lend",
        "yield_loan",
        "aum",
        "aum_net",
        "current_funding",
        "base_currency_balance",
        "clamp_min",
        "clamp_max",
    }
)

# Module-level flag — set by Client when decimal_mode=True
_decimal_mode: bool = False


def set_decimal_mode(enabled: bool) -> None:
    """Enable or disable Decimal conversion for monetary fields."""
    global _decimal_mode
    _decimal_mode = enabled


def compose(
    *decorators: Callable[[type[Any]], type[Any]],
) -> Callable[[type[Any]], type[Any]]:
    def wrapper(function: type[Any]) -> type[Any]:
        for decorator in reversed(decorators):
            function = decorator(function)
        return function

    return wrapper


def partial(cls: type[Any]) -> type[Any]:
    def __init__(self: Any, **kwargs: Any) -> None:
        for annotation in self.__annotations__.keys():
            if annotation not in kwargs:
                self.__setattr__(annotation, None)
            else:
                self.__setattr__(annotation, kwargs[annotation])

            kwargs.pop(annotation, None)

        if len(kwargs) != 0:
            raise TypeError(
                f"{cls.__name__}.__init__() got an unexpected keyword argument "
                f"'{list(kwargs.keys())[0]}'"
            )

    cls.__init__ = __init__

    return cls


class _Type:
    """
    Base class for any dataclass serializable by the _Serializer generic class.
    """


class _Serializer(Generic[T]):
    def __init__(
        self,
        name: str,
        klass: type[_Type],
        labels: list[str],
        *,
        flat: bool = False,
    ) -> None:
        self.name, self.klass, self.__labels, self.__flat = (
            name,
            klass,
            labels,
            flat,
        )

    def _serialize(self, *args: Any) -> Iterable[tuple[str, Any]]:
        if self.__flat:
            args = tuple(_Serializer.__flatten(list(args)))

        if len(self.__labels) > len(args):
            raise AssertionError(
                f"{self.name} -> <labels> and <*args> "
                "arguments should contain the same amount of elements."
            )

        for index, label in enumerate(self.__labels):
            if label != "_PLACEHOLDER":
                value = args[index]
                if (
                    _decimal_mode
                    and label in MONETARY_FIELDS
                    and isinstance(value, (int, float))
                ):
                    value = Decimal(str(value))
                yield label, value

    def parse(self, *values: Any) -> T:
        return cast(T, self.klass(**dict(self._serialize(*values))))

    def get_labels(self) -> list[str]:
        return [label for label in self.__labels if label != "_PLACEHOLDER"]

    @classmethod
    def __flatten(cls, array: list[Any]) -> list[Any]:
        if len(array) == 0:
            return array

        if isinstance(array[0], list):
            return cls.__flatten(array[0]) + cls.__flatten(array[1:])

        return array[:1] + cls.__flatten(array[1:])


class _RecursiveSerializer(_Serializer[T], Generic[T]):
    def __init__(
        self,
        name: str,
        klass: type[_Type],
        labels: list[str],
        *,
        serializers: dict[str, _Serializer[Any]],
        flat: bool = False,
    ) -> None:
        super().__init__(name, klass, labels, flat=flat)

        self.serializers = serializers

    def parse(self, *values: Any) -> T:
        serialization = dict(self._serialize(*values))

        for key in serialization:
            if key in self.serializers.keys():
                serialization[key] = self.serializers[key].parse(
                    *serialization[key]
                )

        return cast(T, self.klass(**serialization))


def generate_labeler_serializer(
    name: str, klass: type[T], labels: list[str], *, flat: bool = False
) -> _Serializer[T]:
    return _Serializer[T](name, klass, labels, flat=flat)


def generate_recursive_serializer(
    name: str,
    klass: type[T],
    labels: list[str],
    *,
    serializers: dict[str, _Serializer[Any]],
    flat: bool = False,
) -> _RecursiveSerializer[T]:
    return _RecursiveSerializer[T](
        name, klass, labels, serializers=serializers, flat=flat
    )
