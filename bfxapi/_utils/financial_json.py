"""Opt-in, per-transport decoding; legacy SDK numbers remain unchanged by default."""

from decimal import Decimal
from typing import Any, Self


class FinancialTokenDecimal(Decimal):
    """A wire decimal retaining its original JSON token for financial adapters.

    Arithmetic returns ordinary Decimal values: source precision belongs to the
    observation, and must be explicitly propagated by the accounting domain.
    """

    source_token: str

    def __new__(cls, token: str) -> Self:
        value = super().__new__(cls, token)
        if not value.is_finite():
            raise ValueError("Non-finite financial JSON number")
        value.source_token = token
        return value


def reject_non_finite(token: str) -> Any:
    raise ValueError(f"Non-finite financial JSON number: {token}")


def financial_decode_options(enabled: bool) -> dict[str, Any]:
    """Integer IDs remain Python ints (already lossless); decimals retain tokens."""
    if not enabled:
        return {}
    return {
        "parse_float": FinancialTokenDecimal,
        "parse_constant": reject_non_finite,
    }
