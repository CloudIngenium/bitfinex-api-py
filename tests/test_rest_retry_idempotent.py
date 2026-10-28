"""retry_with_backoff classifies by exception type and never repeats a write.

A write whose outcome is unknown (the body may have reached Bitfinex) must
not be retried: resubmitting a funding offer after a read timeout leaves two
live offers. Only a caller that declares the call `idempotent=True` opts into
retrying it, and only that caller gets the old message-keyword fallback.
"""

import asyncio
import time

import pytest
import requests

from bfxapi.exceptions import BfxBaseException
from bfxapi.rest.exceptions import (
    ApiRateLimitError,
    GenericError,
    NetworkError,
    NonceError,
    RequestParameterError,
    UnexpectedApiError,
)
from bfxapi.rest.retry import (
    async_retry_with_backoff,
    get_backoff_delay,
    is_retryable,
    retry_with_backoff,
)


def _unknown() -> NetworkError:
    return NetworkError(
        "Request timeout: x", retryable=False, outcome_unknown=True
    )


def _counting(errors: list[BaseException], result: str = "ok"):
    calls = {"n": 0}
    remaining = list(errors)

    def fn() -> str:
        calls["n"] += 1
        if remaining:
            raise remaining.pop(0)
        return result

    return fn, calls


@pytest.fixture
def no_sleeping(monkeypatch):
    slept: list[float] = []

    async def fake_async_sleep(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_async_sleep)
    monkeypatch.setattr(time, "sleep", slept.append)
    return slept


class TestClassification:
    def test_outcome_unknown_is_only_retried_when_idempotent(self):
        assert not is_retryable(_unknown())
        assert is_retryable(_unknown(), idempotent=True)

    def test_outcome_unknown_wins_over_a_stray_retryable_flag(self):
        err = NetworkError("x", retryable=True, outcome_unknown=True)
        assert err.retryable is False
        assert not is_retryable(err)

    def test_a_plain_network_error_still_uses_its_flag(self):
        assert is_retryable(NetworkError("x"))
        assert not is_retryable(NetworkError("x", retryable=False))
        assert not is_retryable(
            NetworkError("x", retryable=False), idempotent=True
        )

    def test_typed_errors_ignore_idempotent(self):
        nonce = NonceError("nonce", code=10114)
        rate = ApiRateLimitError("rate", code=11010)
        for idem in (False, True):
            assert is_retryable(nonce, idempotent=idem)
            assert is_retryable(rate, idempotent=idem)
            assert not is_retryable(
                RequestParameterError("timeout"), idempotent=idem
            )
            assert not is_retryable(
                UnexpectedApiError("connection reset"), idempotent=idem
            )
        # An untyped library error only matches by keyword, so only when
        # the call is idempotent.
        assert not is_retryable(BfxBaseException("connection reset"))
        assert is_retryable(
            BfxBaseException("connection reset"), idempotent=True
        )
        assert not is_retryable(GenericError("timeout"), idempotent=True)

    def test_keyword_fallback_needs_idempotent(self):
        for err in (
            Exception("connection refused"),
            TimeoutError("timeout"),
            GenericError("nonce: small"),
            requests.exceptions.ReadTimeout("read timeout"),
        ):
            assert not is_retryable(err), err
            assert is_retryable(err, idempotent=True), err

    def test_never_connected_is_retryable_by_type(self):
        assert is_retryable(ConnectionRefusedError(111, "x"))
        assert is_retryable(requests.exceptions.ConnectTimeout("x"))

    def test_nonce_error_backs_off_one_second(self):
        assert get_backoff_delay(NonceError("x"), 5, 1.0, 300.0) == 1.0

    def test_network_error_honors_retry_after(self):
        err = NetworkError("x", retry_after_ms=7_000)
        assert get_backoff_delay(err, 0, 1.0, 300.0) == 7.0
        assert get_backoff_delay(err, 0, 1.0, 3.0) == 3.0


class TestSyncRetry:
    def test_an_ambiguous_write_is_raised_after_one_call(self, no_sleeping):
        fn, calls = _counting([_unknown()])
        with pytest.raises(NetworkError):
            retry_with_backoff(fn, max_attempts=5)
        assert calls["n"] == 1
        assert no_sleeping == []

    def test_an_idempotent_call_retries_an_ambiguous_error(self, no_sleeping):
        fn, calls = _counting([_unknown()])
        assert retry_with_backoff(fn, idempotent=True) == "ok"
        assert calls["n"] == 2

    def test_a_nonce_error_is_retried_by_default(self, no_sleeping):
        fn, calls = _counting([NonceError("nonce: small")])
        assert retry_with_backoff(fn) == "ok"
        assert no_sleeping == [1.0]

    def test_keyword_errors_are_not_retried_by_default(self, no_sleeping):
        fn, calls = _counting([Exception("connection reset")])
        with pytest.raises(Exception, match="connection reset"):
            retry_with_backoff(fn)
        assert calls["n"] == 1


class TestAsyncRetry:
    @pytest.mark.asyncio
    async def test_an_ambiguous_write_is_raised_after_one_call(
        self, no_sleeping
    ):
        fn, calls = _counting([_unknown()])
        with pytest.raises(NetworkError):
            await async_retry_with_backoff(fn, max_attempts=5)
        assert calls["n"] == 1

    @pytest.mark.asyncio
    async def test_an_idempotent_call_retries_it(self, no_sleeping):
        fn, calls = _counting([_unknown(), Exception("connection reset")])
        assert await async_retry_with_backoff(fn, idempotent=True) == "ok"
        assert calls["n"] == 3
