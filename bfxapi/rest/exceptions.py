"""Errors raised by the REST middleware.

Compatibility contract: every class added here subclasses the class (or
builtin) that the same failure raised before, so an existing `except`
clause keeps catching it. `UnexpectedApiError` is still a `RuntimeError`,
`InvalidResponseError` is still a `requests.exceptions.JSONDecodeError`,
and `NonFiniteNumberError` is both a `RequestParameterError` and a
`ValueError`.
"""

from typing import Any

import requests

from bfxapi.exceptions import BfxBaseException


class BfxApiError(BfxBaseException):
    """Base of the errors the REST middleware raises.

    Attributes:
        code: The Bitfinex error code, when the reply carried one.
        raw: The decoded error reply, or the body text that would not
            decode.
        status_code: The HTTP status, when the error came from a response.
    """

    status_code: int | None = None

    def __init__(
        self,
        *args: object,
        code: int | None = None,
        raw: Any = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(*args)
        self.code = code
        self.raw = raw
        self.status_code = status_code


class RequestParameterError(BfxApiError):
    pass


class NonFiniteNumberError(RequestParameterError, ValueError):
    """A REST query param or JSON body held NaN or an infinity.

    Raised before the request is built, so nothing reached Bitfinex: it is
    never `retryable` and its outcome is never unknown.

    Two bases, both for compatibility. Before NaN and infinities were
    rejected locally, they went out as ``"NaN"`` / ``"Infinity"`` and a
    server rejection came back as a `RequestParameterError`; callers that
    catch that (or `BfxApiError`) keep catching this. The local check first
    raised a plain `ValueError`, so callers that catch that keep catching
    this too.

    Attributes:
        raw: The non-finite value that was rejected.
        value: The same object as `raw`, under the constructor's name.
    """

    retryable: bool = False
    outcome_unknown: bool = False

    def __init__(self, message: str, *, value: Any = None) -> None:
        super().__init__(message, raw=value)

    @property
    def value(self) -> Any:
        return self.raw


class GenericError(BfxApiError):
    pass


class RateLimitError(BfxApiError):
    """Raised when Bitfinex returns HTTP 429 or error code 10010.

    Attributes:
        retry_after_ms: Suggested wait time in milliseconds (the
            Retry-After header when present).
    """

    def __init__(
        self,
        message: str,
        retry_after_ms: int = 60_000,
        *,
        code: int | None = None,
        raw: Any = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message, code=code, raw=raw, status_code=status_code)
        self.retry_after_ms = retry_after_ms


class InsufficientFundsError(BfxApiError):
    """Raised when Bitfinex returns error code 10001."""

    pass


class NetworkError(BfxApiError):
    """Raised on connection errors, timeouts, DNS failures, and replies that
    are not a Bitfinex answer (a non-JSON body, a 5xx with no error body).

    Attributes:
        retryable: Whether sending the same request again is safe.
        outcome_unknown: The request may have reached Bitfinex and been
            applied (a write that timed out while reading the reply, lost
            its connection mid-request, or got a 5xx with no error body).
            Such an error is never `retryable`: reconcile (by cid for
            orders, by listing offers for funding) instead of resending.
        retry_after_ms: The server's Retry-After, when it sent one.
    """

    outcome_unknown: bool = False
    retry_after_ms: int | None = None

    def __init__(
        self,
        message: str,
        retryable: bool = True,
        *,
        outcome_unknown: bool = False,
        retry_after_ms: int | None = None,
        code: int | None = None,
        raw: Any = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message, code=code, raw=raw, status_code=status_code)
        self._set_transport_state(retryable, outcome_unknown, retry_after_ms)

    def _set_transport_state(
        self,
        retryable: bool,
        outcome_unknown: bool,
        retry_after_ms: int | None,
    ) -> None:
        self.outcome_unknown = outcome_unknown
        self.retryable = retryable and not outcome_unknown
        self.retry_after_ms = retry_after_ms


class InvalidResponseError(NetworkError, requests.exceptions.JSONDecodeError):
    """The reply body is not JSON (a proxy's HTML error page, an empty body).

    Still a `requests.exceptions.JSONDecodeError` (so also a `ValueError`,
    a `json.JSONDecodeError` and a `requests.RequestException`), which is
    what `response.json()` raised here before, with the same message.
    """

    def __init__(
        self,
        msg: str,
        doc: str,
        pos: int,
        *,
        retryable: bool = True,
        outcome_unknown: bool = False,
        retry_after_ms: int | None = None,
        status_code: int | None = None,
        response: requests.Response | None = None,
    ) -> None:
        requests.exceptions.JSONDecodeError.__init__(
            self, msg, doc, pos, response=response
        )
        self.code = None
        self.raw = doc
        self.status_code = status_code
        self._set_transport_state(retryable, outcome_unknown, retry_after_ms)


class UnexpectedApiError(BfxApiError, RuntimeError):
    """Bitfinex rejected the request with an error code this client does not
    map to a narrower class. Still a `RuntimeError`, as it was before."""

    pass


class NonceError(UnexpectedApiError):
    """Error 10114 (`nonce: small`). Bitfinex rejected the request before
    applying it, so sending it again with a fresh nonce is safe."""

    retryable: bool = True


class ApiRateLimitError(RateLimitError, UnexpectedApiError):
    """Error 11010 (`ratelimit: error`). A `RateLimitError` that is still
    the `RuntimeError` this code raised before."""

    pass
