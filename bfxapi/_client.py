from collections.abc import Callable
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
        nonce_provider: Callable[[], int] | None = None,
    ) -> None:
        """
        Args:
            decimal_mode: Parse monetary fields as Decimal. Applies to this
                client only; other Client instances keep their own mode.
            dms: Dead-man switch flag for the authenticated WebSocket
                (Bitfinex accepts 4: cancel all orders when the socket
                closes). Sent on every (re)authentication.
            nonce_provider: A callable returning the next nonce, a positive
                int, for every signed REST request and every WebSocket
                authentication of this client. Bitfinex keeps one nonce
                counter per API key and rejects a nonce that is not greater
                than the last one it accepted (error 10114, `NonceError`).
                With None (the default), REST and WebSocket share one
                process-wide monotonic microsecond generator, which is
                enough when this library is the only signer of the key.
                Use one API key per process, or pass the same shared,
                monotonic, thread-safe provider to every component that
                signs with the key (this client, your own REST or WebSocket
                code, other clients). If the provider raises or returns
                anything but a positive int, the call fails with
                `bfxapi.exceptions.NonceProviderError` before anything is
                signed or sent.
        """
        if nonce_provider is not None and not callable(nonce_provider):
            raise TypeError(
                "nonce_provider must be a callable returning an int, "
                f"got {type(nonce_provider).__name__}."
            )
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
            nonce_provider=nonce_provider,
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
            nonce_provider=nonce_provider,
        )
