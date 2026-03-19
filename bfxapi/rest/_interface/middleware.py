import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum
from typing import TYPE_CHECKING, Any, NoReturn

import requests

from bfxapi._utils.json_decoder import JSONDecoder
from bfxapi._utils.json_encoder import JSONEncoder
from bfxapi.exceptions import InvalidCredentialError
from bfxapi.rest.exceptions import (
    GenericError,
    InsufficientFundsError,
    NetworkError,
    RateLimitError,
    RequestParameterError,
)

if TYPE_CHECKING:
    from requests.sessions import _Params


@dataclass
class RateLimitInfo:
    """Rate limit information from the last API response."""

    remaining: int | None = None
    limit: int | None = None
    reset: int | None = None

    @classmethod
    def from_headers(cls, headers: dict[str, str]) -> "RateLimitInfo":
        def _int(key: str) -> int | None:
            val = headers.get(key)
            return int(val) if val is not None else None

        return cls(
            remaining=_int("x-ratelimit-remaining"),
            limit=_int("x-ratelimit-limit"),
            reset=_int("x-ratelimit-reset"),
        )


class _Error(IntEnum):
    ERR_UNK = 10000
    ERR_GENERIC = 10001
    ERR_RATE_LIMIT = 10010
    ERR_PARAMS = 10020
    ERR_AUTH_FAIL = 10100


class Middleware:
    __TIMEOUT = 30

    def __init__(
        self,
        host: str,
        api_key: str | None = None,
        api_secret: str | None = None,
    ):
        self.__host = host

        self.__api_key = api_key

        self.__api_secret = api_secret

        self.last_rate_limit: RateLimitInfo = RateLimitInfo()

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
            raise NetworkError(f"Connection error: {e}") from e
        except requests.Timeout as e:
            raise NetworkError(f"Request timeout: {e}") from e

        self.last_rate_limit = RateLimitInfo.from_headers(
            dict(response.headers)
        )

        if response.status_code == 429:
            reset = self.last_rate_limit.reset
            retry_ms = (
                (reset - int(datetime.now().timestamp())) * 1000
                if reset
                else 60_000
            )
            raise RateLimitError(
                "Rate limit exceeded (HTTP 429)",
                retry_after_ms=max(retry_ms, 1000),
            )

        data = response.json(cls=JSONDecoder)

        if isinstance(data, list) and len(data) > 0 and data[0] == "error":
            self.__handle_error(data)

        return data

    def post(
        self,
        endpoint: str,
        body: Any | None = None,
        params: "_Params | None" = None,
    ) -> Any:
        _body = body and json.dumps(body, cls=JSONEncoder) or None

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
            raise NetworkError(f"Connection error: {e}") from e
        except requests.Timeout as e:
            raise NetworkError(f"Request timeout: {e}") from e

        self.last_rate_limit = RateLimitInfo.from_headers(
            dict(response.headers)
        )

        if response.status_code == 429:
            reset = self.last_rate_limit.reset
            retry_ms = (
                (reset - int(datetime.now().timestamp())) * 1000
                if reset
                else 60_000
            )
            raise RateLimitError(
                "Rate limit exceeded (HTTP 429)",
                retry_after_ms=max(retry_ms, 1000),
            )

        data = response.json(cls=JSONDecoder)

        if isinstance(data, list) and len(data) > 0 and data[0] == "error":
            self.__handle_error(data)

        return data

    def __handle_error(self, error: list[Any]) -> NoReturn:
        code = error[1]
        message = error[2] if len(error) > 2 else str(error)

        if code == _Error.ERR_RATE_LIMIT:
            reset = self.last_rate_limit.reset
            retry_ms = (
                (reset - int(datetime.now().timestamp())) * 1000
                if reset
                else 60_000
            )
            raise RateLimitError(
                f"Rate limit exceeded: <{message}>",
                retry_after_ms=max(retry_ms, 1000),
            )

        if code == _Error.ERR_PARAMS:
            raise RequestParameterError(
                "The request was rejected with the following parameter "
                f"error: <{message}>."
            )

        if code == _Error.ERR_AUTH_FAIL:
            raise InvalidCredentialError(
                "Can't authenticate with given API-KEY and API-SECRET."
            )

        # Insufficient funds — check both error code and message
        if code == _Error.ERR_GENERIC and isinstance(message, str):
            msg_lower = message.lower()
            if "insufficient" in msg_lower or "not enough" in msg_lower:
                raise InsufficientFundsError(f"Insufficient funds: <{message}>")

        if not code or code == _Error.ERR_UNK or code == _Error.ERR_GENERIC:
            raise GenericError(
                "The request was rejected with the following generic "
                f"error: <{message}>."
            )

        raise RuntimeError(
            f"The request was rejected with an unexpected error: <{error}>."
        )

    def __get_authentication_headers(
        self, endpoint: str, data: str | None = None
    ) -> dict[str, str]:
        assert self.__api_key and self.__api_secret

        nonce = str(round(datetime.now().timestamp() * 1_000_000))

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
