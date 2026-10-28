import asyncio
import time

import pytest

from bfxapi.exceptions import InvalidCredentialError
from bfxapi.rest.exceptions import (
    GenericError,
    InsufficientFundsError,
    NetworkError,
    RateLimitError,
)
from bfxapi.rest.retry import (
    async_retry_with_backoff,
    get_backoff_delay,
    is_retryable,
    retry_with_backoff,
)


class TestIsRetryable:
    def test_rate_limit_is_retryable(self):
        assert is_retryable(RateLimitError("limit"))

    def test_network_error_retryable(self):
        assert is_retryable(NetworkError("timeout", retryable=True))

    def test_network_error_not_retryable(self):
        assert not is_retryable(NetworkError("bad host", retryable=False))

    def test_invalid_credentials_not_retryable(self):
        assert not is_retryable(InvalidCredentialError("bad key"))

    def test_insufficient_funds_not_retryable(self):
        assert not is_retryable(InsufficientFundsError("no funds"))

    def test_generic_error_not_retryable(self):
        assert not is_retryable(GenericError("something"))

    def test_nonce_error_retryable(self):
        assert is_retryable(GenericError("nonce too small"))

    def test_connection_string_retryable(self):
        assert is_retryable(Exception("connection refused"))

    def test_unknown_error_not_retryable(self):
        assert not is_retryable(Exception("something else"))


class TestGetBackoffDelay:
    def test_rate_limit_uses_retry_after(self):
        err = RateLimitError("limit", retry_after_ms=30000)
        assert get_backoff_delay(err, 0, 1.0, 300.0) == 30.0

    def test_nonce_short_delay(self):
        err = GenericError("nonce too small")
        assert get_backoff_delay(err, 0, 1.0, 300.0) == 1.0

    def test_network_linear_backoff(self):
        err = NetworkError("timeout")
        assert get_backoff_delay(err, 0, 1.0, 300.0) == 1.0
        assert get_backoff_delay(err, 1, 1.0, 300.0) == 2.0
        assert get_backoff_delay(err, 2, 1.0, 300.0) == 3.0

    def test_default_exponential(self):
        err = Exception("something")
        assert get_backoff_delay(err, 0, 1.0, 300.0) == 1.0
        assert get_backoff_delay(err, 1, 1.0, 300.0) == 2.0
        assert get_backoff_delay(err, 2, 1.0, 300.0) == 4.0

    def test_caps_at_max(self):
        err = Exception("something")
        assert get_backoff_delay(err, 20, 1.0, 5.0) == 5.0


class TestRetryWithBackoff:
    def test_returns_on_first_success(self):
        assert retry_with_backoff(lambda: 42) == 42

    def test_retries_on_retryable_error(self):
        calls = 0

        def fn():
            nonlocal calls
            calls += 1
            if calls < 3:
                raise RateLimitError("limit", retry_after_ms=10)
            return "ok"

        result = retry_with_backoff(fn, max_attempts=5, base_delay=0.01)
        assert result == "ok"
        assert calls == 3

    def test_raises_after_max_attempts(self):
        with pytest.raises(RateLimitError):
            retry_with_backoff(
                lambda: (_ for _ in ()).throw(
                    RateLimitError("limit", retry_after_ms=10)
                ),
                max_attempts=2,
                base_delay=0.01,
            )

    def test_fails_fast_on_non_retryable(self):
        calls = 0

        def fn():
            nonlocal calls
            calls += 1
            raise InsufficientFundsError("no funds")

        with pytest.raises(InsufficientFundsError):
            retry_with_backoff(fn, max_attempts=5, base_delay=0.01)
        assert calls == 1


class TestRetryExhaustsWithoutCalling:
    """`max_attempts=0` is the only way to reach the post-loop raise.

    Every other path leaves the loop through `raise`, so the trailing
    `raise last_error or RuntimeError(...)` only fires when the loop body
    never ran. A caller that computes `max_attempts` from config can land
    on 0, and it must not return None as if the call had succeeded.
    """

    def test_zero_attempts_raises_instead_of_returning_none(self):
        calls = 0

        def fn():
            nonlocal calls
            calls += 1
            return "never reached"

        with pytest.raises(RuntimeError, match="Max retry attempts exceeded"):
            retry_with_backoff(fn, max_attempts=0)
        assert calls == 0

    async def test_zero_attempts_raises_in_async_variant_too(self):
        calls = 0

        def fn():
            nonlocal calls
            calls += 1
            return "never reached"

        with pytest.raises(RuntimeError, match="Max retry attempts exceeded"):
            await async_retry_with_backoff(fn, max_attempts=0)
        assert calls == 0


class TestAsyncRetryWithBackoff:
    """The async twin carries the same contract as the sync one.

    It is the variant a live async bot actually calls, so each rule the
    sync tests above pin is re-pinned here rather than assumed to be
    shared: the two functions are copies, not one implementation.
    """

    async def test_returns_on_first_success(self):
        assert await async_retry_with_backoff(lambda: 42) == 42

    async def test_retries_on_retryable_error(self):
        calls = 0

        def fn():
            nonlocal calls
            calls += 1
            if calls < 3:
                raise RateLimitError("limit", retry_after_ms=10)
            return "ok"

        result = await async_retry_with_backoff(
            fn, max_attempts=5, base_delay=0.01
        )
        assert result == "ok"
        assert calls == 3

    async def test_raises_after_max_attempts(self):
        calls = 0

        def fn():
            nonlocal calls
            calls += 1
            raise RateLimitError("limit", retry_after_ms=10)

        with pytest.raises(RateLimitError):
            await async_retry_with_backoff(fn, max_attempts=2, base_delay=0.01)
        assert calls == 2

    async def test_fails_fast_on_non_retryable(self):
        calls = 0

        def fn():
            nonlocal calls
            calls += 1
            raise InsufficientFundsError("no funds")

        with pytest.raises(InsufficientFundsError):
            await async_retry_with_backoff(fn, max_attempts=5, base_delay=0.01)
        assert calls == 1

    async def test_backoff_yields_to_the_event_loop(self):
        """The whole reason this variant exists: the wait must not block.

        Swapping `await asyncio.sleep` for `time.sleep` keeps every other
        test in this class passing — same retries, same result, same call
        counts — while freezing the bot's websocket feed for the length of
        the backoff. Only a concurrent task can tell the two apart.
        """
        ticks = 0

        async def ticker() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.001)
                ticks += 1

        calls = 0

        def fn():
            nonlocal calls
            calls += 1
            if calls == 1:
                # 100 ms of backoff: hundreds of ticks if the loop is free,
                # exactly zero if the sleep is synchronous.
                raise RateLimitError("limit", retry_after_ms=100)
            return "ok"

        task = asyncio.create_task(ticker())
        try:
            started = time.monotonic()
            assert await async_retry_with_backoff(fn, max_attempts=3) == "ok"
            elapsed = time.monotonic() - started
        finally:
            task.cancel()

        assert elapsed >= 0.1, "the backoff was skipped, not awaited"
        assert ticks > 0, "the event loop was blocked during the backoff"
