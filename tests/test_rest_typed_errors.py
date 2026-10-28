"""Typed REST middleware errors, write ambiguity, and their compatibility.

Every error the middleware raised before this change must still be caught by
the same `except` clauses a consumer already has. BfxLendingBot alone has
about 180 `except RuntimeError` sites, and it catches the requests and json
decode errors through `except ValueError` / `except requests.RequestException`.
So each new class subclasses the old one, and the compat tests below catch
every new error with the clause that caught it before.
"""

import json
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from unittest.mock import patch

import pytest
import requests
from requests.structures import CaseInsensitiveDict
from urllib3.exceptions import MaxRetryError, NewConnectionError, ProtocolError

from bfxapi.exceptions import BfxBaseException, InvalidCredentialError
from bfxapi.rest._interface.middleware import Middleware, RateLimitInfo
from bfxapi.rest.exceptions import (
    ApiRateLimitError,
    BfxApiError,
    GenericError,
    InsufficientFundsError,
    InvalidResponseError,
    NetworkError,
    NonceError,
    RateLimitError,
    RequestParameterError,
    UnexpectedApiError,
)
from bfxapi.rest.retry import is_retryable

GET = "bfxapi.rest._interface.middleware.requests.get"
POST = "bfxapi.rest._interface.middleware.requests.post"
WRITE = "auth/w/funding/offer/submit"
READ = "auth/r/wallets"


def _response(
    status: int, body: bytes | str, headers: dict[str, str] | None = None
) -> requests.Response:
    """A real requests.Response, so headers are a CaseInsensitiveDict."""
    r = requests.Response()
    r.status_code = status
    r._content = body.encode() if isinstance(body, str) else body
    r.encoding = "utf-8"
    r.headers = CaseInsensitiveDict(headers or {})
    return r


def _error_body(code: int, msg: str) -> str:
    return json.dumps(["error", code, msg])


def _public() -> Middleware:
    return Middleware("https://api.example.com")


def _auth() -> Middleware:
    return Middleware("https://api.example.com", api_key="k", api_secret="s")


def _get(body_or_exc, status: int = 500, headers=None, endpoint=READ):
    with patch(GET) as g:
        if isinstance(body_or_exc, BaseException):
            g.side_effect = body_or_exc
        else:
            g.return_value = _response(status, body_or_exc, headers)
        return _auth().get(endpoint)


def _post(body_or_exc, status: int = 500, headers=None, endpoint=WRITE):
    with patch(POST) as p:
        if isinstance(body_or_exc, BaseException):
            p.side_effect = body_or_exc
        else:
            p.return_value = _response(status, body_or_exc, headers)
        return _auth().post(endpoint, body={"a": 1})


def _raised(fn, *args, **kwargs) -> BaseException:
    with pytest.raises(BaseException) as info:
        fn(*args, **kwargs)
    return info.value


# --------------------------------------------------------------------------
# 1. code and raw
# --------------------------------------------------------------------------


class TestCodeAndRaw:
    @pytest.mark.parametrize(
        ("code", "msg", "cls"),
        [
            (10020, "amount: invalid", RequestParameterError),
            (10001, "insufficient balance", InsufficientFundsError),
            (10001, "something", GenericError),
            (10000, "unknown", GenericError),
            (10010, "ERR_RATE_LIMIT", RateLimitError),
            (10100, "apikey: invalid", InvalidCredentialError),
            (10305, "offer: invalid", UnexpectedApiError),
            (10114, "nonce: small", NonceError),
            (11010, "ratelimit: error", ApiRateLimitError),
        ],
    )
    def test_every_mapped_error_carries_code_and_raw(self, code, msg, cls):
        err = _raised(_get, _error_body(code, msg))

        assert type(err) is cls
        assert err.code == code
        assert err.raw == ["error", code, msg]
        assert isinstance(err, BfxBaseException)

    def test_rest_errors_share_a_base(self):
        for cls in (
            RequestParameterError,
            GenericError,
            RateLimitError,
            InsufficientFundsError,
            NetworkError,
            UnexpectedApiError,
        ):
            assert issubclass(cls, BfxApiError)
            assert issubclass(cls, BfxBaseException)

    def test_defaults_are_none(self):
        assert GenericError("x").code is None
        assert GenericError("x").raw is None
        assert BfxBaseException("x").code is None
        assert InvalidCredentialError("x").raw is None

    def test_old_constructors_still_work(self):
        assert str(RequestParameterError("p")) == "p"
        assert RateLimitError("r", 5000).retry_after_ms == 5000
        assert NetworkError("n", False).retryable is False
        assert NetworkError("n").retryable is True
        assert NetworkError("n").outcome_unknown is False
        assert GenericError().args == ()

    def test_http_429_records_status(self):
        err = _raised(_get, "", status=429)
        assert isinstance(err, RateLimitError)
        assert err.status_code == 429
        assert str(err) == "Rate limit exceeded (HTTP 429)"


# --------------------------------------------------------------------------
# 2. Case-insensitive headers and Retry-After
# --------------------------------------------------------------------------


class TestHeaders:
    def test_mixed_case_headers_are_read(self):
        info = RateLimitInfo.from_headers(
            {
                "X-RateLimit-Remaining": "7",
                "X-RATELIMIT-LIMIT": "90",
                "x-ratelimit-reset": "123",
            }
        )
        assert (info.remaining, info.limit, info.reset) == (7, 90, 123)

    def test_a_real_response_updates_last_rate_limit(self):
        with patch(GET) as g:
            g.return_value = _response(
                200,
                "[1]",
                {"X-RateLimit-Remaining": "3", "X-RateLimit-Limit": "90"},
            )
            m = _public()
            m.get("platform/status")
        assert m.last_rate_limit.remaining == 3
        assert m.last_rate_limit.limit == 90

    def test_garbage_values_are_ignored(self):
        info = RateLimitInfo.from_headers(
            {"x-ratelimit-remaining": "lots", "retry-after": "soon"}
        )
        assert info.remaining is None
        assert info.retry_after_ms is None

    def test_a_non_mapping_reads_as_empty(self):
        assert RateLimitInfo.from_headers(None) == RateLimitInfo()  # type: ignore[arg-type]

    def test_retry_after_seconds(self):
        info = RateLimitInfo.from_headers({"Retry-After": "42"})
        assert info.retry_after_ms == 42_000

    def test_retry_after_http_date(self):
        when = datetime.now(UTC) + timedelta(seconds=120)
        info = RateLimitInfo.from_headers(
            {"Retry-After": format_datetime(when, usegmt=True)}
        )
        assert info.retry_after_ms is not None
        assert 100_000 <= info.retry_after_ms <= 121_000

    def test_retry_after_in_the_past_is_zero(self):
        info = RateLimitInfo.from_headers(
            {"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}
        )
        assert info.retry_after_ms == 0

    def test_retry_after_date_without_a_zone_reads_as_utc(self):
        info = RateLimitInfo.from_headers(
            {"Retry-After": "Wed, 21 Oct 2015 07:28:00 -0000"}
        )
        assert info.retry_after_ms == 0

    def test_http_429_honors_retry_after(self):
        err = _raised(_get, "", status=429, headers={"Retry-After": "17"})
        assert isinstance(err, RateLimitError)
        assert err.retry_after_ms == 17_000

    def test_retry_after_beats_the_reset_header(self):
        reset = int(datetime.now().timestamp()) + 500
        err = _raised(
            _get,
            "",
            status=429,
            headers={"retry-after": "5", "X-RateLimit-Reset": str(reset)},
        )
        assert err.retry_after_ms == 5_000

    def test_reset_header_still_works_without_retry_after(self):
        reset = int(datetime.now().timestamp()) + 30
        err = _raised(
            _get, "", status=429, headers={"X-RateLimit-Reset": str(reset)}
        )
        assert 28_000 <= err.retry_after_ms <= 31_000

    def test_retry_after_floor_is_one_second(self):
        err = _raised(_get, "", status=429, headers={"Retry-After": "0"})
        assert err.retry_after_ms == 1_000

    def test_10010_honors_retry_after(self):
        err = _raised(
            _get,
            _error_body(10010, "ERR_RATE_LIMIT"),
            headers={"Retry-After": "9"},
        )
        assert type(err) is RateLimitError
        assert err.retry_after_ms == 9_000

    def test_a_5xx_carries_retry_after(self):
        err = _raised(
            _get, "<html>503</html>", status=503, headers={"Retry-After": "4"}
        )
        assert isinstance(err, NetworkError)
        assert err.retry_after_ms == 4_000


# --------------------------------------------------------------------------
# 3. UnexpectedApiError, NonceError, 11010
# --------------------------------------------------------------------------


class TestUnexpectedApiError:
    def test_keeps_todays_message_and_is_a_runtime_error(self):
        err = _raised(_get, _error_body(10305, "offer: invalid"))

        assert type(err) is UnexpectedApiError
        assert isinstance(err, RuntimeError)
        assert str(err) == (
            "The request was rejected with an unexpected error: "
            "<['error', 10305, 'offer: invalid']>."
        )

    def test_nonce_is_a_retryable_unexpected_api_error(self):
        err = _raised(_post, _error_body(10114, "nonce: small"))

        assert type(err) is NonceError
        assert isinstance(err, UnexpectedApiError)
        assert isinstance(err, RuntimeError)
        assert err.retryable is True
        assert "unexpected error" in str(err)
        assert is_retryable(err)

    def test_11010_is_a_rate_limit_and_a_runtime_error(self):
        err = _raised(_get, _error_body(11010, "ratelimit: error"))

        assert isinstance(err, RateLimitError)
        assert isinstance(err, RuntimeError)
        assert isinstance(err, UnexpectedApiError)
        assert str(err) == (
            "The request was rejected with an unexpected error: "
            "<['error', 11010, 'ratelimit: error']>."
        )
        assert err.retry_after_ms == 60_000
        assert is_retryable(err)

    def test_11010_honors_retry_after(self):
        err = _raised(
            _get,
            _error_body(11010, "ratelimit: error"),
            headers={"Retry-After": "12"},
        )
        assert err.retry_after_ms == 12_000

    def test_unexpected_api_error_is_not_retryable(self):
        err = _raised(_get, _error_body(10305, "timeout connection"))
        assert not is_retryable(err)
        assert not is_retryable(err, idempotent=True)


# --------------------------------------------------------------------------
# 4. Non-JSON bodies
# --------------------------------------------------------------------------


class TestNonJsonBody:
    def test_is_a_network_error_and_a_requests_json_decode_error(self):
        html = "<html><body>502 Bad Gateway</body></html>"
        err = _raised(_get, html, status=502)

        assert isinstance(err, InvalidResponseError)
        assert isinstance(err, NetworkError)
        assert isinstance(err, requests.exceptions.JSONDecodeError)
        assert isinstance(err, json.JSONDecodeError)
        assert isinstance(err, ValueError)
        assert isinstance(err, requests.RequestException)
        assert str(err) == "Expecting value: line 1 column 1 (char 0)"
        assert err.raw == html
        assert err.doc == html
        assert err.status_code == 502
        assert err.code is None

    def test_message_matches_what_requests_raised_before(self):
        r = _response(502, "<html>")
        with pytest.raises(requests.exceptions.JSONDecodeError) as before:
            r.json()
        err = _raised(_get, "<html>", status=502)
        assert str(err) == str(before.value)
        assert err.response is not None

    def test_empty_body(self):
        err = _raised(_get, "", status=502)
        assert isinstance(err, InvalidResponseError)

    def test_a_read_is_retryable(self):
        err = _raised(_get, "<html>", status=520)
        assert err.retryable is True
        assert err.outcome_unknown is False
        assert is_retryable(err)

    def test_a_write_with_a_5xx_is_ambiguous(self):
        err = _raised(_post, "<html>", status=502)
        assert err.retryable is False
        assert err.outcome_unknown is True
        assert not is_retryable(err)

    def test_a_write_with_a_2xx_is_ambiguous(self):
        err = _raised(_post, "<html>", status=200)
        assert err.outcome_unknown is True

    def test_a_write_rejected_with_a_4xx_stays_retryable(self):
        err = _raised(_post, "<html>403</html>", status=403)
        assert err.retryable is True
        assert err.outcome_unknown is False

    def test_a_financial_decode_error_is_not_wrapped(self):
        from bfxapi._utils.financial_json import FinancialDecodeError

        with patch(GET) as g:
            g.return_value = _response(200, "[NaN]")
            m = Middleware(
                "https://api.example.com", lossless_financial_decode=True
            )
            with pytest.raises(FinancialDecodeError) as info:
                m.get("platform/status")
        assert not isinstance(info.value, NetworkError)


# --------------------------------------------------------------------------
# 5. Other requests exceptions
# --------------------------------------------------------------------------


class TestOtherRequestExceptions:
    def test_chunked_encoding_is_wrapped_but_still_caught_as_before(self):
        orig = requests.exceptions.ChunkedEncodingError(
            "Connection broken: IncompleteRead"
        )
        err = _raised(_get, orig)

        assert isinstance(err, NetworkError)
        assert isinstance(err, requests.exceptions.ChunkedEncodingError)
        assert isinstance(err, requests.RequestException)
        assert str(err) == str(orig)
        assert err.__cause__ is orig
        assert err.retryable is True

    def test_the_wrapper_class_is_reused(self):
        a = _raised(_get, requests.exceptions.ChunkedEncodingError("a"))
        b = _raised(_get, requests.exceptions.ChunkedEncodingError("b"))
        assert type(a) is type(b)
        assert type(a).__name__ == "BfxChunkedEncodingError"

    def test_a_write_broken_mid_body_is_ambiguous(self):
        err = _raised(_post, requests.exceptions.ChunkedEncodingError("x"))
        assert err.outcome_unknown is True
        assert err.retryable is False

    @pytest.mark.parametrize(
        "exc",
        [
            requests.exceptions.InvalidURL("bad"),
            requests.exceptions.MissingSchema("bad"),
            requests.exceptions.TooManyRedirects("loop"),
            requests.exceptions.InvalidHeader("bad"),
        ],
    )
    def test_request_errors_that_never_succeed_are_not_retryable(self, exc):
        for call in (_get, _post):
            err = _raised(call, exc)
            assert isinstance(err, type(exc))
            assert isinstance(err, NetworkError)
            assert err.retryable is False
            assert err.outcome_unknown is False

    def test_request_and_response_are_kept(self):
        orig = requests.exceptions.ContentDecodingError(
            "bad gzip", request="REQ", response="RESP"
        )
        err = _raised(_get, orig)
        assert err.request == "REQ"
        assert err.response == "RESP"


# --------------------------------------------------------------------------
# 6. Write ambiguity
# --------------------------------------------------------------------------


def _new_connection_failure() -> requests.ConnectionError:
    reason = NewConnectionError(None, "Failed to establish a new connection")  # type: ignore[arg-type]
    return requests.ConnectionError(MaxRetryError(None, "/v2/x", reason))  # type: ignore[arg-type]


class TestWriteAmbiguity:
    def test_read_timeout_on_a_write_is_ambiguous(self):
        err = _raised(_post, requests.exceptions.ReadTimeout("read timed out"))

        assert type(err) is NetworkError
        assert str(err) == "Request timeout: read timed out"
        assert err.retryable is False
        assert err.outcome_unknown is True

    def test_mid_request_connection_error_on_a_write_is_ambiguous(self):
        orig = requests.ConnectionError(
            ProtocolError("Connection aborted.", ConnectionResetError(104))
        )
        err = _raised(_post, orig)

        assert str(err).startswith("Connection error: ")
        assert err.outcome_unknown is True
        assert err.retryable is False

    def test_a_bare_connection_error_on_a_write_is_ambiguous(self):
        err = _raised(_post, requests.ConnectionError("reset"))
        assert err.outcome_unknown is True

    def test_connect_timeout_on_a_write_stays_retryable(self):
        err = _raised(_post, requests.exceptions.ConnectTimeout("connect"))
        assert str(err) == "Connection error: connect"
        assert err.retryable is True
        assert err.outcome_unknown is False

    def test_failed_new_connection_on_a_write_stays_retryable(self):
        err = _raised(_post, _new_connection_failure())
        assert err.retryable is True
        assert err.outcome_unknown is False

    def test_a_refused_socket_in_the_chain_stays_retryable(self):
        orig = requests.ConnectionError("refused")
        orig.__cause__ = ConnectionRefusedError(111, "refused")
        err = _raised(_post, orig)
        assert err.outcome_unknown is False

    def test_a_cyclic_cause_chain_terminates(self):
        orig = requests.ConnectionError("reset")
        inner = OSError("inner")
        orig.__cause__ = inner
        inner.__context__ = orig
        err = _raised(_post, orig)
        assert err.outcome_unknown is True

    def test_5xx_without_an_error_body_on_a_write_is_ambiguous(self):
        err = _raised(_post, json.dumps({"status": "oops"}), status=500)

        assert type(err) is NetworkError
        assert err.outcome_unknown is True
        assert err.status_code == 500
        assert err.raw == {"status": "oops"}
        assert str(err) == "Server error (HTTP 500)"

    def test_5xx_without_an_error_body_on_a_read_is_retryable(self):
        err = _raised(_get, "[]", status=503)
        assert type(err) is NetworkError
        assert err.retryable is True
        assert err.outcome_unknown is False

    def test_5xx_with_an_error_body_on_a_write_is_the_mapped_error(self):
        err = _raised(_post, _error_body(10020, "amount: invalid"))
        assert type(err) is RequestParameterError

    @pytest.mark.parametrize(
        "exc",
        [
            requests.exceptions.ReadTimeout("t"),
            requests.ConnectionError("reset"),
        ],
    )
    def test_reads_stay_retryable(self, exc):
        assert _raised(_get, exc).retryable is True
        # a POST to auth/r/* is a read, not a write
        assert _raised(_post, exc, endpoint=READ).retryable is True
        assert _raised(_post, exc, endpoint="calc/trade/avg").retryable

    def test_a_v2_prefixed_write_path_is_still_a_write(self):
        err = _raised(
            _post, requests.exceptions.ReadTimeout("t"), endpoint="/v2/" + WRITE
        )
        assert err.outcome_unknown is True

    def test_a_2xx_write_returns_data(self):
        assert _post("[1, 2]", status=200) == [1, 2]


# --------------------------------------------------------------------------
# 7. Compatibility: the old except clause still catches the new error
# --------------------------------------------------------------------------


COMPAT = [
    # (scenario, call, payload, status, the except clause that caught it)
    ("10305 unmapped", _get, _error_body(10305, "x"), 500, RuntimeError),
    (
        "10114 nonce",
        _post,
        _error_body(10114, "nonce: small"),
        500,
        RuntimeError,
    ),
    ("11010 ratelimit", _get, _error_body(11010, "r"), 500, RuntimeError),
    ("non-JSON body", _get, "<html>", 502, requests.exceptions.JSONDecodeError),
    ("non-JSON body", _get, "<html>", 502, ValueError),
    ("non-JSON body", _get, "<html>", 502, requests.RequestException),
    ("non-JSON body", _get, "<html>", 502, OSError),
    ("non-JSON write", _post, "<html>", 502, json.JSONDecodeError),
    ("10020 params", _get, _error_body(10020, "x"), 500, RequestParameterError),
    ("10001 generic", _get, _error_body(10001, "x"), 500, GenericError),
    ("10010 ratelimit", _get, _error_body(10010, "x"), 500, RateLimitError),
    ("10100 auth", _get, _error_body(10100, "x"), 500, InvalidCredentialError),
    ("HTTP 429", _get, "", 429, RateLimitError),
    (
        "chunked",
        _get,
        requests.exceptions.ChunkedEncodingError("x"),
        0,
        requests.exceptions.ChunkedEncodingError,
    ),
    (
        "read timeout write",
        _post,
        requests.exceptions.ReadTimeout("x"),
        0,
        NetworkError,
    ),
    ("conn error", _get, requests.ConnectionError("x"), 0, NetworkError),
]


@pytest.mark.parametrize(
    ("scenario", "call", "payload", "status", "clause"),
    COMPAT,
    ids=[f"{c[0]}->{c[4].__name__}" for c in COMPAT],
)
def test_the_old_except_clause_still_catches_it(
    scenario, call, payload, status, clause
):
    try:
        call(payload, status=status)
    except clause as e:
        assert isinstance(e, BfxBaseException)
    else:  # pragma: no cover - the call must raise
        pytest.fail(f"{scenario} did not raise")
