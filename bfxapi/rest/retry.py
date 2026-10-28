"""Retry with exponential backoff for Bitfinex REST API calls.

Errors are classified by type. A write whose outcome is unknown (it may
already have been applied: a read timeout, a connection lost mid-request, a
5xx with no error body) is never retried unless the caller declares the call
`idempotent=True`; reconcile it instead (by cid for orders, by listing offers
for funding). The old message-keyword fallback ("timeout", "connection",
"nonce"...) also applies only to calls declared idempotent.

Example::

    from bfxapi.rest.retry import retry_with_backoff

    wallets = retry_with_backoff(
        lambda: client.rest.auth.get_wallets(),
        max_attempts=5,
        idempotent=True,  # a read: safe to send again
    )
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TypeVar

import requests

from bfxapi.exceptions import InvalidCredentialError
from bfxapi.rest.exceptions import (
    GenericError,
    InsufficientFundsError,
    NetworkError,
    NonceError,
    RateLimitError,
    RequestParameterError,
    UnexpectedApiError,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

_KEYWORDS = ("timeout", "connection", "reset", "refused")

# Bitfinex rejected these before applying anything: resending is safe even
# for a write.
_ALWAYS_RETRYABLE = (RateLimitError, NonceError)

# Resending cannot change the answer.
_NEVER_RETRYABLE = (
    InvalidCredentialError,
    InsufficientFundsError,
    RequestParameterError,
    UnexpectedApiError,
)

# Raised before a connection existed, so nothing was sent.
_NEVER_CONNECTED = (ConnectionRefusedError, requests.exceptions.ConnectTimeout)


def is_retryable(error: BaseException, *, idempotent: bool = False) -> bool:
    """Check if an error is worth retrying.

    Args:
        error: The exception the call raised.
        idempotent: The call is safe to repeat (a read, or a write the
            caller deduplicates). Only then is an outcome-unknown error
            retried, and only then does a message keyword count.
    """
    if isinstance(error, _ALWAYS_RETRYABLE):
        return True
    if isinstance(error, NetworkError):
        if error.outcome_unknown:
            return idempotent
        return error.retryable
    if isinstance(error, _NEVER_RETRYABLE):
        return False
    if isinstance(error, _NEVER_CONNECTED):
        return True
    if not idempotent:
        return False
    # The message-keyword fallback of earlier versions, unchanged.
    msg = str(error).lower()
    if isinstance(error, GenericError):
        return "nonce" in msg
    return any(kw in msg for kw in _KEYWORDS)


def get_backoff_delay(
    error: BaseException,
    attempt: int,
    base_delay: float,
    max_delay: float,
) -> float:
    """Calculate backoff delay for a given error and attempt."""
    if isinstance(error, RateLimitError):
        return min(error.retry_after_ms / 1000.0, max_delay)

    if isinstance(error, NonceError):
        return 1.0

    msg = str(error).lower()
    if "nonce" in msg:
        return 1.0

    if isinstance(error, NetworkError):
        if error.retry_after_ms is not None:
            return min(error.retry_after_ms / 1000.0, max_delay)
        return min(base_delay * (attempt + 1), 60.0)

    # Default: exponential backoff
    delay: float = base_delay * (2**attempt)
    return min(delay, max_delay)


def retry_with_backoff(
    fn: Callable[[], T],
    max_attempts: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 300.0,
    *,
    idempotent: bool = False,
) -> T:
    """Retry a synchronous function with exponential backoff.

    Args:
        fn: Function to retry.
        max_attempts: Maximum number of attempts.
        base_delay: Base delay in seconds for backoff.
        max_delay: Maximum delay cap in seconds.
        idempotent: `fn` is safe to repeat. Leave False for writes (order,
            offer, transfer submits): an error whose outcome is unknown is
            then raised at once instead of resent.

    Returns:
        Result of the function.

    Raises:
        The last exception if all attempts fail.
    """
    import time

    last_error: BaseException | None = None

    for attempt in range(max_attempts):
        try:
            return fn()
        except Exception as e:
            last_error = e

            if attempt == max_attempts - 1:
                raise

            if not is_retryable(e, idempotent=idempotent):
                raise

            delay = get_backoff_delay(e, attempt, base_delay, max_delay)
            logger.warning(
                "Attempt %d/%d failed (%s), retrying in %.1fs",
                attempt + 1,
                max_attempts,
                type(e).__name__,
                delay,
            )
            time.sleep(delay)

    raise last_error or RuntimeError("Max retry attempts exceeded")


async def async_retry_with_backoff(
    fn: Callable[[], T],
    max_attempts: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 300.0,
    *,
    idempotent: bool = False,
) -> T:
    """Retry a synchronous function with async sleep between attempts.

    Useful when the function itself is sync (REST calls) but you want
    non-blocking sleep in an async context.

    Args:
        fn: Synchronous function to retry.
        max_attempts: Maximum number of attempts.
        base_delay: Base delay in seconds for backoff.
        max_delay: Maximum delay cap in seconds.
        idempotent: `fn` is safe to repeat; see `retry_with_backoff`.

    Returns:
        Result of the function.

    Raises:
        The last exception if all attempts fail.
    """
    last_error: BaseException | None = None

    for attempt in range(max_attempts):
        try:
            return fn()
        except Exception as e:
            last_error = e

            if attempt == max_attempts - 1:
                raise

            if not is_retryable(e, idempotent=idempotent):
                raise

            delay = get_backoff_delay(e, attempt, base_delay, max_delay)
            logger.warning(
                "Attempt %d/%d failed (%s), retrying in %.1fs",
                attempt + 1,
                max_attempts,
                type(e).__name__,
                delay,
            )
            await asyncio.sleep(delay)

    raise last_error or RuntimeError("Max retry attempts exceeded")
