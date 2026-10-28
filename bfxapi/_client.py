from typing import TYPE_CHECKING

from bfxapi._utils.logging import ColorLogger
from bfxapi.exceptions import IncompleteCredentialError
from bfxapi.rest import BfxRestInterface
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
        lossless_financial_decode: bool = False,
        dms: int | None = None,
    ) -> None:
        """
        Args:
            decimal_mode: Parse monetary fields as Decimal. Applies to this
                client only; other Client instances keep their own mode.
            dms: Dead-man switch flag for the authenticated WebSocket
                (Bitfinex accepts 4: cancel all orders when the socket
                closes). Sent on every (re)authentication.
        """
        if lossless_financial_decode and decimal_mode:
            # Two decoders for the same numbers: the lossless one would win
            # and `decimal_mode` would be dropped without a word.
            raise ValueError(
                "decimal_mode and lossless_financial_decode are mutually "
                "exclusive; lossless_financial_decode already yields Decimal."
            )

        # Per client, never process-wide: a lossless client already yields
        # Decimal from the decoder, so it opts out of the legacy conversion.
        scoped_decimal_mode = decimal_mode and not lossless_financial_decode

        credentials: _Credentials | None = None

        if api_key and api_secret:
            credentials = {
                "api_key": api_key,
                "api_secret": api_secret,
                "filters": filters,
            }
            if dms is not None:
                credentials["dms"] = dms
        elif api_key:
            raise IncompleteCredentialError(
                "You must provide both API-KEY and API-SECRET (missing API-KEY)."
            )
        elif api_secret:
            raise IncompleteCredentialError(
                "You must provide both API-KEY and API-SECRET (missing API-SECRET)."
            )

        self.rest = BfxRestInterface(
            rest_host,
            api_key,
            api_secret,
            lossless_financial_decode=lossless_financial_decode,
            decimal_mode=scoped_decimal_mode,
        )

        logger = ColorLogger("bfxapi", level="INFO")

        if log_filename:
            logger.register(filename=log_filename)

        self.wss = BfxWebSocketClient(
            wss_host,
            credentials=credentials,
            timeout=timeout,
            logger=logger,
            lossless_financial_decode=lossless_financial_decode,
            decimal_mode=scoped_decimal_mode,
        )
