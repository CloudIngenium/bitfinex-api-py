import hashlib
import hmac
import json
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
import time
from typing import (
    Any,
    Concatenate,
    ParamSpec,
    TypeVar,
    cast,
)

from websockets.asyncio.client import ClientConnection

from bfxapi.websocket.exceptions import (
    ActionRequiresAuthentication,
    ConnectionNotOpen,
)

_S = TypeVar("_S", bound="Connection")

_R = TypeVar("_R")

_P = ParamSpec("_P")


class Connection(ABC):
    _HEARTBEAT = "hb"

    def __init__(self, host: str) -> None:
        self._host = host

        self._authentication: bool = False

        self.__protocol: ClientConnection | None = None

    @property
    def open(self) -> bool:
        return (
            self.__protocol is not None and self.__protocol.state.name == "OPEN"
        )

    @property
    def authentication(self) -> bool:
        return self._authentication

    @property
    def _websocket(self) -> ClientConnection:
        return cast(ClientConnection, self.__protocol)

    @_websocket.setter
    def _websocket(self, protocol: ClientConnection) -> None:
        self.__protocol = protocol

    @abstractmethod
    async def start(self) -> None: ...

    @staticmethod
    def _require_websocket_connection(
        function: Callable[Concatenate[_S, _P], Awaitable[_R]],
    ) -> Callable[Concatenate[_S, _P], Awaitable[_R]]:
        async def wrapper(
            self: _S, /, *args: _P.args, **kwargs: _P.kwargs
        ) -> _R:
            if self.open:
                return await function(self, *args, **kwargs)

            raise ConnectionNotOpen("No open connection with the server.")

        return wrapper

    @staticmethod
    def _require_websocket_authentication(
        function: Callable[Concatenate[_S, _P], Awaitable[_R]],
    ) -> Callable[Concatenate[_S, _P], Awaitable[_R]]:
        async def wrapper(
            self: _S, /, *args: _P.args, **kwargs: _P.kwargs
        ) -> _R:
            if not self.authentication:
                raise ActionRequiresAuthentication(
                    "To perform this action you need to "
                    "authenticate using your API_KEY and API_SECRET."
                )

            internal = Connection._require_websocket_connection(function)

            return await internal(self, *args, **kwargs)

        return wrapper

    @staticmethod
    def _get_authentication_message(
        api_key: str, api_secret: str, filters: list[str] | None = None
    ) -> str:
        message: dict[str, Any] = {
            "event": "auth",
            "filter": filters,
            "apiKey": api_key,
        }

        message["authNonce"] = time.time_ns() // 1_000

        message["authPayload"] = f"AUTH{message['authNonce']}"

        auth_sig = hmac.new(
            key=api_secret.encode("utf8"),
            msg=message["authPayload"].encode("utf8"),
            digestmod=hashlib.sha384,
        )

        message["authSig"] = auth_sig.hexdigest()

        return json.dumps(message)
