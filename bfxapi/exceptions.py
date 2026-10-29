from typing import Any


class BfxBaseException(Exception):
    """
    Base class for every custom exception thrown by bitfinex-api-py.

    Attributes:
        code: The Bitfinex error code (e.g. 10114) when the error came from
            an `["error", code, message]` reply; otherwise None.
        raw: What the error was built from (the decoded error reply, or the
            body text that would not decode); otherwise None.
    """

    code: int | None = None
    raw: Any = None


class IncompleteCredentialError(BfxBaseException):
    pass


class InvalidCredentialError(BfxBaseException):
    pass


class NonceProviderError(InvalidCredentialError, ValueError):
    """The ``nonce_provider`` raised, or returned something that is not a
    positive int (a bool is not one).

    Raised before the REST request or the WebSocket auth message is signed.
    On REST, nothing reached Bitfinex. On WebSocket, the socket has already
    connected and `open` has fired; only the auth frame is held back. It is
    never `retryable` and its outcome is never unknown. On WebSocket it also
    ends the client for good: it is not one of the errors the client
    reconnects on, so `start()` raises it. The provider's own exception, if
    any, is the ``__cause__``.

    Two bases. It is an `InvalidCredentialError`, which REST and WebSocket
    callers already catch when the key cannot sign or authenticate, and
    which the REST retry helpers never retry. It is also a `ValueError`:
    the provider produced a value that cannot be used.
    """

    retryable: bool = False
    outcome_unknown: bool = False
