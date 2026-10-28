import functools
import inspect
from collections.abc import Callable
from typing import Any

from bfxapi.types.labeler import decimal_mode_scope

from .middleware import Middleware


def _in_decimal_scope(method: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(method)
    def wrapper(self: "Interface", *args: Any, **kwargs: Any) -> Any:
        with decimal_mode_scope(self._decimal_mode):
            return method(self, *args, **kwargs)

    return wrapper


class Interface:
    """Base of the REST endpoint groups.

    Every public endpoint method of a subclass runs inside this instance's
    decimal-mode scope, so parsing follows the owning Client's
    ``decimal_mode`` and never another Client's.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        for name, attr in list(vars(cls).items()):
            if inspect.isfunction(attr) and not name.startswith("_"):
                setattr(cls, name, _in_decimal_scope(attr))

    def __init__(
        self,
        host: str,
        api_key: str | None = None,
        api_secret: str | None = None,
        *,
        lossless_financial_decode: bool = False,
        decimal_mode: bool | None = None,
    ):
        self._m = Middleware(
            host,
            api_key,
            api_secret,
            lossless_financial_decode=lossless_financial_decode,
        )
        # None: no scope of its own (a bare endpoint group built outside a
        # Client follows the process-wide default, as before).
        self._decimal_mode = decimal_mode
