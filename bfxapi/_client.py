from typing import TYPE_CHECKING

from bfxapi._utils.logging import ColorLogger
from bfxapi.exceptions import IncompleteCredentialError
from bfxapi.rest import BfxRestInterface
from bfxapi.types.labeler import set_decimal_mode
from bfxapi.websocket import BfxWebSocketClient

if TYPE_CHECKING:
    from bfxapi.websocket._client.bfx_websocket_client import _Credentials

REST_HOST = "https://api.bitfinex.com/v2"
WSS_HOST = "wss://api.bitfinex.com/ws/2"

PUB_REST_HOST = "https://api-pub.bitfinex.com/v2"
PUB_WSS_HOST = "wss://api-pub.bitfinex.com/ws/2"


class Client:
    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        *,
        rest_host: str = REST_HOST,
        wss_host: str = WSS_HOST,
        filters: list[str] | None = None,
        timeout: int | None = 60 * 15,
        log_filename: str | None = None,
        decimal_mode: bool = False,
    ) -> None:
        set_decimal_mode(decimal_mode)
        credentials: _Credentials | None = None

        if api_key and api_secret:
            credentials = {
                "api_key": api_key,
                "api_secret": api_secret,
                "filters": filters,
            }
        elif api_key:
            raise IncompleteCredentialError(
                "You must provide both API-KEY and API-SECRET (missing API-KEY)."
            )
        elif api_secret:
            raise IncompleteCredentialError(
                "You must provide both API-KEY and API-SECRET (missing API-SECRET)."
            )

        self.rest = BfxRestInterface(rest_host, api_key, api_secret)

        logger = ColorLogger("bfxapi", level="INFO")

        if log_filename:
            logger.register(filename=log_filename)

        self.wss = BfxWebSocketClient(
            wss_host, credentials=credentials, timeout=timeout, logger=logger
        )
