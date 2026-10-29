import hashlib
import hmac
import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from enum import IntEnum
from typing import TYPE_CHECKING, Any, NoReturn

import requests
from urllib3.exceptions import MaxRetryError, NewConnectionError

from bfxapi._utils.financial_json import financial_decode_options
from bfxapi._utils.json_decoder import JSONDecoder, _RawKeysJSONDecoder
from bfxapi._utils.json_encoder import _FiniteJSONEncoder, _NonFiniteNumber
from bfxapi._utils.nonce import NonceProvider, next_nonce, resolve_nonce
from bfxapi.exceptions import InvalidCredentialError
from bfxapi.rest.exceptions import (
    ApiRateLimitError,
    GenericError,
    InsufficientFundsError,
    InvalidResponseError,
    NetworkError,
    NonceError,
    NonFiniteNumberError,
    RateLimitError,
    RequestParameterError,
    UnexpectedApiError,
)

if TYPE_CHECKING:
    from requests.sessions import _Params


def _encode_body(body: Any | None) -> str | None:
    """A REST JSON body, or None for an empty one.

    Raises NonFiniteNumberError (a RequestParameterError and a ValueError)
    for a NaN or infinity anywhere in it, before the request is signed.
    """
    if not body:
        return None
    try:
        return json.dumps(body, cls=_FiniteJSONEncoder)
    except _NonFiniteNumber as e:
        raise NonFiniteNumberError(str(e), value=e.value) from e


def _next_nonce() -> str:
    """Monotonic microsecond nonce. Guards against duplicates when two calls
    hit the same microsecond tick under concurrent use (H8 fix).

    The process-wide generator in `bfxapi._utils.nonce`, which the
    WebSocket auth message shares, so the two never issue the same nonce."""
    return str(next_nonce())


def _parse_int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _parse_retry_after_ms(value: str | None) -> int | None:
    """Retry-After is either delta-seconds or an HTTP-date (RFC 9110)."""
    if value is None:
        return None
    value = value.strip()
    if value.isdigit():
        return int(value) * 1000
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    delta = (when - datetime.now(UTC)).total_seconds()
    return max(0, int(delta * 1000))


@dataclass
class RateLimitInfo:
    """Rate limit information from the last API response."""

    remaining: int | None = None
    limit: int | None = None
    reset: int | None = None
    retry_after_ms: int | None = None

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> "RateLimitInfo":
        """Header names are matched case-insensitively, whatever Mapping
        holds them; a value that does not parse reads as None."""
        if not isinstance(headers, Mapping):
            return cls()
        lowered = {str(k).lower(): v for k, v in headers.items()}

        return cls(
            remaining=_parse_int(lowered.get("x-ratelimit-remaining")),
            limit=_parse_int(lowered.get("x-ratelimit-limit")),
            reset=_parse_int(lowered.get("x-ratelimit-reset")),
            retry_after_ms=_parse_retry_after_ms(lowered.get("retry-after")),
        )


class _Error(IntEnum):
    ERR_UNK = 10000
    ERR_GENERIC = 10001
    ERR_RATE_LIMIT = 10010
    ERR_PARAMS = 10020
    ERR_AUTH_FAIL = 10100
    ERR_NONCE_SMALL = 10114
    ERR_RATE_LIMIT_11010 = 11010


# A POST to one of these paths changes state on Bitfinex (orders, offers,
# transfers, withdrawals). Sending it twice can apply it twice.
_WRITE_PATH = re.compile(r"^(?:v2/)?auth/w/")

# Request errors raised before anything was sent, or that will fail the
# same way every time: resending cannot help, and nothing was applied.
_NEVER_SUCCEEDS = (
    requests.exceptions.InvalidURL,
    requests.exceptions.MissingSchema,
    requests.exceptions.InvalidSchema,
    requests.exceptions.URLRequired,
    requests.exceptions.InvalidHeader,
    requests.exceptions.InvalidJSONError,
    requests.exceptions.TooManyRedirects,
)

_WRAPPED_CLASSES: dict[type[requests.RequestException], type[NetworkError]] = {}


def _is_write(method: str, endpoint: str) -> bool:
    return method == "POST" and bool(_WRITE_PATH.match(endpoint.lstrip("/")))


def _never_reached_server(error: BaseException) -> bool:
    """True when the connection was never established, so nothing was sent:
    a connect timeout, a refused connection or a DNS failure."""
    if isinstance(error, requests.exceptions.ConnectTimeout):
        return True
    seen: set[int] = set()
    stack: list[BaseException] = [error]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, (NewConnectionError, ConnectionRefusedError)):
            return True
        if isinstance(current, MaxRetryError) and current.reason is not None:
            stack.append(current.reason)
        stack.extend(a for a in current.args if isinstance(a, BaseException))
        for link in (current.__cause__, current.__context__):
            if link is not None:
                stack.append(link)
    return False


def _transport_error(
    message: str, error: BaseException, write: bool
) -> NetworkError:
    unknown = write and not _never_reached_server(error)
    return NetworkError(message, retryable=not unknown, outcome_unknown=unknown)


def _wrap_request_exception(
    error: requests.RequestException, write: bool
) -> NetworkError:
    """A NetworkError that is still an instance of the original requests
    class, so `except requests.exceptions.ChunkedEncodingError` (or any
    parent) keeps catching it. str() is unchanged."""
    cls = type(error)
    wrapper = _WRAPPED_CLASSES.get(cls)
    if wrapper is None:
        wrapper = type(f"Bfx{cls.__name__}", (NetworkError, cls), {})
        _WRAPPED_CLASSES[cls] = wrapper

    if isinstance(error, _NEVER_SUCCEEDS):
        retryable, unknown = False, False
    else:
        retryable, unknown = not write, write

    wrapped = wrapper(str(error), retryable=retryable, outcome_unknown=unknown)
    wrapped.args = error.args
    wrapped.request = error.request  # type: ignore[attr-defined]
    wrapped.response = error.response  # type: ignore[attr-defined]
    return wrapped


class Middleware:
    __TIMEOUT = 30

    def __init__(
        self,
        host: str,
        api_key: str | None = None,
        api_secret: str | None = None,
        *,
        lossless_financial_decode: bool = False,
        nonce_provider: NonceProvider | None = None,
    ):
        self.__host = host
        # None: the process-wide generator the WebSocket auth shares.
        self.__nonce_provider = nonce_provider
        self.__decode_options = financial_decode_options(
            lossless_financial_decode
        )

        self.__api_key = api_key

        self.__api_secret = api_secret

        self.last_rate_limit: RateLimitInfo = RateLimitInfo()

        # time.monotonic() of the most recent response (0.0 = none yet);
        # lets BfxRestInterface pick the newer of the auth/public headers.
        self.last_response_at: float = 0.0

    def get(self, endpoint: str, params: "_Params | None" = None) -> Any:
        headers = {"Accept": "application/json"}

        if self.__api_key and self.__api_secret:
            headers = {**headers, **self.__get_authentication_headers(endpoint)}

        try:
            response = requests.get(
                url=f"{self.__host}/{endpoint}",
                params=params,
                headers=headers,
                timeout=Middleware.__TIMEOUT,
            )
        except requests.ConnectionError as e:
            raise _transport_error(f"Connection error: {e}", e, False) from e
        except requests.Timeout as e:
            raise _transport_error(f"Request timeout: {e}", e, False) from e
        except requests.RequestException as e:
            raise _wrap_request_exception(e, False) from e

        return self.__process_response(response, write=False)

    def post(
        self,
        endpoint: str,
        body: Any | None = None,
        params: "_Params | None" = None,
        *,
        raw_keys: bool = False,
    ) -> Any:
        """POST ``endpoint``; the decoded JSON reply.

        Object keys are snake-cased unless ``raw_keys`` is set, for replies
        keyed by data (currency codes) rather than by field name.
        """
        _body = _encode_body(body)
        write = _is_write("POST", endpoint)

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        if self.__api_key and self.__api_secret:
            headers = {
                **headers,
                **self.__get_authentication_headers(endpoint, _body),
            }

        try:
            response = requests.post(
                url=f"{self.__host}/{endpoint}",
                data=_body,
                params=params,
                headers=headers,
                timeout=Middleware.__TIMEOUT,
            )
        except requests.ConnectionError as e:
            raise _transport_error(f"Connection error: {e}", e, write) from e
        except requests.Timeout as e:
            raise _transport_error(f"Request timeout: {e}", e, write) from e
        except requests.RequestException as e:
            raise _wrap_request_exception(e, write) from e

        return self.__process_response(response, write=write, raw_keys=raw_keys)

    def __rate_limit_delay_ms(self) -> int:
        info = self.last_rate_limit
        if info.retry_after_ms is not None:
            retry_ms = info.retry_after_ms
        elif info.reset:
            retry_ms = (info.reset - int(datetime.now().timestamp())) * 1000
        else:
            retry_ms = 60_000
        return max(retry_ms, 1000)

    def __process_response(
        self, response: requests.Response, write: bool, raw_keys: bool = False
    ) -> Any:
        self.last_response_at = time.monotonic()
        self.last_rate_limit = RateLimitInfo.from_headers(response.headers)

        status = response.status_code
        if not isinstance(status, int):
            status = None

        if status == 429:
            raise RateLimitError(
                "Rate limit exceeded (HTTP 429)",
                retry_after_ms=self.__rate_limit_delay_ms(),
                status_code=429,
            )

        try:
            decoder = _RawKeysJSONDecoder if raw_keys else JSONDecoder
            data = response.json(cls=decoder, **self.__decode_options)
        except requests.exceptions.JSONDecodeError as e:
            # A 4xx without a JSON body was refused before it was applied;
            # anything else on a write may have been applied.
            refused = status is not None and 400 <= status < 500
            unknown = write and not refused
            raise InvalidResponseError(
                e.msg,
                e.doc,
                e.pos,
                retryable=not unknown,
                outcome_unknown=unknown,
                retry_after_ms=self.last_rate_limit.retry_after_ms,
                status_code=status,
                response=response,
            ) from e

        if isinstance(data, list) and len(data) > 0 and data[0] == "error":
            self.__handle_error(data, status)

        if status is not None and status >= 500:
            raise NetworkError(
                f"Server error (HTTP {status})",
                retryable=not write,
                outcome_unknown=write,
                retry_after_ms=self.last_rate_limit.retry_after_ms,
                raw=data,
                status_code=status,
            )

        return data

    def __handle_error(self, error: list[Any], status: int | None) -> NoReturn:
        code = error[1]
        message = error[2] if len(error) > 2 else str(error)
        ctx: dict[str, Any] = {
            "code": code if isinstance(code, int) else None,
            "raw": error,
            "status_code": status,
        }

        if code == _Error.ERR_RATE_LIMIT:
            raise RateLimitError(
                f"Rate limit exceeded: <{message}>",
                retry_after_ms=self.__rate_limit_delay_ms(),
                **ctx,
            )

        if code == _Error.ERR_PARAMS:
            raise RequestParameterError(
                "The request was rejected with the following parameter "
                f"error: <{message}>.",
                **ctx,
            )

        if code == _Error.ERR_AUTH_FAIL:
            credential_error = InvalidCredentialError(
                "Can't authenticate with given API-KEY and API-SECRET."
            )
            credential_error.code = ctx["code"]
            credential_error.raw = error
            raise credential_error

        # Insufficient funds — check both error code and message
        if code == _Error.ERR_GENERIC and isinstance(message, str):
            msg_lower = message.lower()
            if "insufficient" in msg_lower or "not enough" in msg_lower:
                raise InsufficientFundsError(
                    f"Insufficient funds: <{message}>", **ctx
                )

        if not code or code == _Error.ERR_UNK or code == _Error.ERR_GENERIC:
            raise GenericError(
                "The request was rejected with the following generic "
                f"error: <{message}>.",
                **ctx,
            )

        # The text is what the bare RuntimeError raised here used to say.
        unexpected = (
            f"The request was rejected with an unexpected error: <{error}>."
        )

        if code == _Error.ERR_NONCE_SMALL:
            raise NonceError(unexpected, **ctx)

        if code == _Error.ERR_RATE_LIMIT_11010:
            raise ApiRateLimitError(
                unexpected,
                retry_after_ms=self.__rate_limit_delay_ms(),
                **ctx,
            )

        raise UnexpectedApiError(unexpected, **ctx)

    def __get_authentication_headers(
        self, endpoint: str, data: str | None = None
    ) -> dict[str, str]:
        assert self.__api_key and self.__api_secret

        # NonceProviderError here leaves before anything is signed or sent.
        if self.__nonce_provider is None:
            nonce = _next_nonce()
        else:
            nonce = str(resolve_nonce(self.__nonce_provider))

        if not data:
            message = f"/api/v2/{endpoint}{nonce}"
        else:
            message = f"/api/v2/{endpoint}{nonce}{data}"

        signature = hmac.new(
            key=self.__api_secret.encode("utf8"),
            msg=message.encode("utf8"),
            digestmod=hashlib.sha384,
        )

        return {
            "bfx-nonce": nonce,
            "bfx-signature": signature.hexdigest(),
            "bfx-apikey": self.__api_key,
        }
