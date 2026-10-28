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
