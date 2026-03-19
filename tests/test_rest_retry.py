import pytest

from bfxapi.exceptions import InvalidCredentialError
from bfxapi.rest.exceptions import (
    GenericError,
    InsufficientFundsError,
    NetworkError,
    RateLimitError,
)
from bfxapi.rest.retry import (
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
