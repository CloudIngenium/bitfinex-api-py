import json
from decimal import Decimal
from typing import Any

_ExtJSON = (
    dict[str, "_ExtJSON"]
    | list["_ExtJSON"]
    | bool
    | int
    | float
    | str
    | Decimal
    | None
)

_StrictJSON = dict[str, "_StrictJSON"] | list["_StrictJSON"] | int | str | None


def _clear(dictionary: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in dictionary.items() if value is not None
    }


def _as_decimal(value: float | Decimal) -> Decimal:
    # float.__repr__ is the shortest round-trip text, so 0.1 stays 0.1
    # instead of Decimal(0.1)'s 55-digit binary expansion. Called unbound so
    # a float subclass with its own repr (np.float64(0.5)) still parses.
    if isinstance(value, float):
        return Decimal(float.__repr__(value))
    return value


class _NonFiniteNumber(ValueError):
    """NaN or an infinity met by the finite (REST) encoding.

    Internal: the REST layer re-raises it as
    ``bfxapi.rest.exceptions.NonFiniteNumberError``, which callers see.
    It lives here because ``_utils`` must not import ``bfxapi.rest``.
    """

    def __init__(self, value: float | Decimal) -> None:
        super().__init__(f"Bitfinex accepts finite numbers only, got {value!r}")
        self.value = value

    def __reduce__(
        self,
    ) -> tuple[type["_NonFiniteNumber"], tuple[float | Decimal]]:
        # Unpickling calls cls(*args); args holds the message, not the value.
        return (type(self), (self.value,))


def _require_finite(value: float | Decimal) -> Decimal:
    number = _as_decimal(value)
    if not number.is_finite():
        raise _NonFiniteNumber(value)
    return number


def _plain_decimal(value: float | Decimal) -> str:
    """A float or Decimal as a plain decimal string for the wire.

    Fixed-point, never an exponent (``1e-05`` -> ``0.00001``, ``1E+3`` ->
    ``1000``), trailing fractional zeros trimmed (``1.500`` -> ``1.5``) and
    negative zero sent as ``0``. NaN and infinities raise _NonFiniteNumber
    (a ValueError), so a bad value fails before the request is built.
    """
    text = format(_require_finite(value), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def _adapter(data: _ExtJSON, *, finite: bool = False) -> _StrictJSON:
    if isinstance(data, bool):
        return int(data)
    if isinstance(data, float | Decimal):
        # format "f" never emits an exponent. ``finite`` rejects NaN and
        # infinities, which would otherwise go out as "NaN" / "Infinity".
        number = _require_finite(data) if finite else _as_decimal(data)
        return format(number, "f")

    if isinstance(data, list):
        return [_adapter(sub_data, finite=finite) for sub_data in data]
    if isinstance(data, dict):
        return _clear(
            {key: _adapter(value, finite=finite) for key, value in data.items()}
        )

    return data


class JSONEncoder(json.JSONEncoder):
    def encode(self, o: _ExtJSON) -> str:
        return super().encode(_adapter(o))


class _FiniteJSONEncoder(JSONEncoder):
    """JSONEncoder that raises _NonFiniteNumber on NaN or an infinity.

    Used for REST bodies so a non-finite number fails before the request is
    signed and sent; the middleware re-raises it as NonFiniteNumberError.
    The WebSocket client keeps the lenient JSONEncoder.
    """

    def encode(self, o: _ExtJSON) -> str:
        return json.JSONEncoder.encode(self, _adapter(o, finite=True))
