"""`async_retry_with_backoff`, which was entirely untested.

This function is a near-copy of its synchronous sibling and differs in exactly
one line: it awaits `asyncio.sleep` where the sibling calls `time.sleep`. That
single line is the entire reason it exists -- a retry helper used from async
code that blocked the event loop between attempts would stall every other
coroutine in the process, including the WebSocket client's keepalive, and it
would do so silently under load rather than failing.

So the sleep mechanism is asserted directly, not inferred from the retry
counts. A regression that swapped the await back for `time.sleep` would leave
every count-based test passing.
"""

import asyncio
import time

import pytest

from bfxapi.exceptions import InvalidCredentialError
from bfxapi.rest.exceptions import RateLimitError
from bfxapi.rest.retry import async_retry_with_backoff, retry_with_backoff


def _raises_then(errors: list[BaseException], result: str):
    """A callable that raises each error in turn, then returns `result`."""
    remaining = list(errors)

    def fn() -> str:
        if remaining:
            raise remaining.pop(0)
        return result

    return fn


@pytest.fixture
def no_sleeping(monkeypatch):
    """Record what the code asked to wait on, and never actually wait."""
    slept: dict[str, list[float]] = {"async": [], "sync": []}

    async def fake_async_sleep(delay: float) -> None:
        slept["async"].append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_async_sleep)
    monkeypatch.setattr(time, "sleep", lambda d: slept["sync"].append(d))
    return slept


class TestItDoesNotBlockTheEventLoop:
    """The one property that distinguishes this function from its sibling."""

    @pytest.mark.asyncio
    async def test_waits_with_asyncio_sleep_and_never_time_sleep(
        self, no_sleeping
    ):
        fn = _raises_then([RateLimitError("slow down")], "ok")

        result = await async_retry_with_backoff(fn, base_delay=0.01)

        assert result == "ok"
        assert len(no_sleeping["async"]) == 1, "did not await asyncio.sleep"
        assert no_sleeping["sync"] == [], (
            "called time.sleep: this blocks the event loop, which is the one "
            "thing the async variant exists to avoid"
        )

    def test_the_sync_sibling_still_uses_time_sleep(self, no_sleeping):
        # The contrast is the point: same retry behaviour, different sleep.
        fn = _raises_then([RateLimitError("slow down")], "ok")

        assert retry_with_backoff(fn, base_delay=0.01) == "ok"
        assert len(no_sleeping["sync"]) == 1
        assert no_sleeping["async"] == []


class TestWhenItRetries:
    @pytest.mark.asyncio
    async def test_a_first_time_success_never_sleeps(self, no_sleeping):
        result = await async_retry_with_backoff(lambda: "ok")

        assert result == "ok"
        assert no_sleeping["async"] == []

    @pytest.mark.asyncio
    async def test_retries_until_it_succeeds(self, no_sleeping):
        fn = _raises_then([RateLimitError("1"), RateLimitError("2")], "ok")

        assert await async_retry_with_backoff(fn, base_delay=0.01) == "ok"
        assert len(no_sleeping["async"]) == 2

    @pytest.mark.asyncio
    async def test_a_generic_error_backs_off_exponentially(self, no_sleeping):
        # Retryable by keyword match, so it takes the default exponential
        # branch rather than a fixed hint.
        fn = _raises_then(
            [Exception(f"connection timeout {i}") for i in range(3)], "ok"
        )

        await async_retry_with_backoff(fn, max_attempts=5, base_delay=1.0)

        assert no_sleeping["async"] == [1.0, 2.0, 4.0]

    @pytest.mark.asyncio
    async def test_a_rate_limit_waits_exactly_as_long_as_the_server_asked(
        self, no_sleeping
    ):
        """The server's own hint must win over our curve.

        Backing off exponentially past a `retry_after_ms` wastes the window;
        backing off less than it earns another 429. Either way the exchange,
        not the client, sets this number.
        """
        error = RateLimitError("slow down")
        error.retry_after_ms = 2500
        fn = _raises_then([error, error], "ok")

        await async_retry_with_backoff(fn, max_attempts=5, base_delay=1.0)

        assert no_sleeping["async"] == [2.5, 2.5]


class TestWhenItGivesUp:
    @pytest.mark.asyncio
    async def test_a_non_retryable_error_surfaces_immediately(
        self, no_sleeping
    ):
        """Bad credentials will not fix themselves; retrying only delays the report."""
        fn = _raises_then([InvalidCredentialError("nope")], "unreachable")

        with pytest.raises(InvalidCredentialError):
            await async_retry_with_backoff(fn, base_delay=0.01)

        assert no_sleeping["async"] == [], "slept before giving up"

    @pytest.mark.asyncio
    async def test_the_last_attempt_raises_rather_than_sleeping(
        self, no_sleeping
    ):
        fn = _raises_then([RateLimitError(str(i)) for i in range(3)], "ok")

        with pytest.raises(RateLimitError):
            await async_retry_with_backoff(fn, max_attempts=3, base_delay=0.01)

        # Two waits for three attempts: the final failure is reported, not slept on.
        assert len(no_sleeping["async"]) == 2

    def test_zero_attempts_raises_in_the_sync_variant(self):
        """The `raise last_error or RuntimeError(...)` fallthrough.

        Unreachable in normal use, which is why it is worth pinning: with
        max_attempts=0 the loop body never runs, so without this line the
        function would return None and a caller would read a call that never
        happened as a successful one.
        """
        called: list[int] = []

        with pytest.raises(RuntimeError, match="Max retry attempts"):
            retry_with_backoff(lambda: called.append(1), max_attempts=0)

        assert called == []

    @pytest.mark.asyncio
    async def test_zero_attempts_raises_in_the_async_variant(self):
        called: list[int] = []

        with pytest.raises(RuntimeError, match="Max retry attempts"):
            await async_retry_with_backoff(
                lambda: called.append(1), max_attempts=0
            )

        assert called == []
