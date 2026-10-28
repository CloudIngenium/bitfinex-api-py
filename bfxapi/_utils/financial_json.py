"""Opt-in, per-transport decoding; legacy SDK numbers remain unchanged by default."""

from decimal import Decimal
from typing import Any, NoReturn, Self

from bfxapi.exceptions import BfxBaseException


class FinancialDecodeError(BfxBaseException, ValueError):
    """An opted-in client received a number it refuses to decode.

    NaN and Infinity are not amounts. Rejecting them is the point of the
    opt-in, so the error must be catchable as an SDK error: a handler written
    for ``BfxBaseException`` would otherwise miss a bare ``ValueError``.
    """


class FinancialTokenDecimal(Decimal):
    """A wire decimal retaining its original JSON token for financial adapters.

    Arithmetic returns ordinary Decimal values: source precision belongs to the
    observation, and must be explicitly propagated by the accounting domain.
    """

    source_token: str

    def __new__(cls, token: str) -> Self:
        value = super().__new__(cls, token)
        if not value.is_finite():
            raise FinancialDecodeError(
                f"Non-finite financial JSON number: {token}"
            )
        value.source_token = token
        return value

    def __reduce__(self) -> tuple[Any, ...]:
        # Decimal pickles through str(self), which normalises the token
        # ("1e-8" comes back as "1E-8"). Rebuild from the original instead,
        # so the guarantee survives a multiprocessing or queue hop.
        return (type(self), (self.source_token,))


def reject_non_finite(token: str) -> NoReturn:
    raise FinancialDecodeError(f"Non-finite financial JSON number: {token}")


def financial_decode_options(enabled: bool) -> dict[str, Any]:
    """Integer IDs remain Python ints (already lossless); decimals retain tokens."""
    if not enabled:
        return {}
    return {
        "parse_float": FinancialTokenDecimal,
        "parse_constant": reject_non_finite,
    }
