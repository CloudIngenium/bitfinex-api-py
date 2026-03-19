from bfxapi.exceptions import BfxBaseException


class RequestParameterError(BfxBaseException):
    pass


class GenericError(BfxBaseException):
    pass


class RateLimitError(BfxBaseException):
    """Raised when Bitfinex returns HTTP 429 or error code 10010.

    Attributes:
        retry_after_ms: Suggested wait time in milliseconds.
    """

    def __init__(self, message: str, retry_after_ms: int = 60_000):
        super().__init__(message)
        self.retry_after_ms = retry_after_ms


class InsufficientFundsError(BfxBaseException):
    """Raised when Bitfinex returns error code 10001."""

    pass


class NetworkError(BfxBaseException):
    """Raised on connection errors, timeouts, DNS failures."""

    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable
