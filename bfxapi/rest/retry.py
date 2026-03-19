"""Retry with exponential backoff for Bitfinex REST API calls.

Understands Bitfinex-specific error patterns and applies appropriate
backoff strategies.

Example::

    from bfxapi.rest.retry import retry_with_backoff

    result = await retry_with_backoff(
        lambda: client.rest.get_wallets(),
        max_attempts=5,
    )
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TypeVar

from bfxapi.exceptions import InvalidCredentialError
from bfxapi.rest.exceptions import (
    GenericError,
    InsufficientFundsError,
    NetworkError,
    RateLimitError,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


def is_retryable(error: BaseException) -> bool:
    """Check if an error is worth retrying."""
    if isinstance(error, RateLimitError):
        return True
    if isinstance(error, NetworkError):
        return error.retryable
    if isinstance(error, InvalidCredentialError):
        return False
    if isinstance(error, InsufficientFundsError):
        return False
    if isinstance(error, GenericError):
        msg = str(error).lower()
        # Nonce errors are transient
        if "nonce" in msg:
            return True
        return False
    # Network-level errors from requests
    msg = str(error).lower()
    return any(
        kw in msg for kw in ("timeout", "connection", "reset", "refused")
    )


def get_backoff_delay(
    error: BaseException,
    attempt: int,
    base_delay: float,
    max_delay: float,
) -> float:
    """Calculate backoff delay for a given error and attempt."""
    if isinstance(error, RateLimitError):
        return min(error.retry_after_ms / 1000.0, max_delay)

    msg = str(error).lower()
    if "nonce" in msg:
        return 1.0

    if isinstance(error, NetworkError):
        return min(base_delay * (attempt + 1), 60.0)

    # Default: exponential backoff
    delay: float = base_delay * (2**attempt)
    return min(delay, max_delay)


def retry_with_backoff(
    fn: Callable[[], T],
    max_attempts: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 300.0,
) -> T:
    """Retry a synchronous function with exponential backoff.

    Args:
        fn: Function to retry.
        max_attempts: Maximum number of attempts.
        base_delay: Base delay in seconds for backoff.
        max_delay: Maximum delay cap in seconds.

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

            if not is_retryable(e):
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
) -> T:
    """Retry a synchronous function with async sleep between attempts.

    Useful when the function itself is sync (REST calls) but you want
    non-blocking sleep in an async context.

    Args:
        fn: Synchronous function to retry.
        max_attempts: Maximum number of attempts.
        base_delay: Base delay in seconds for backoff.
        max_delay: Maximum delay cap in seconds.

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

            if not is_retryable(e):
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
