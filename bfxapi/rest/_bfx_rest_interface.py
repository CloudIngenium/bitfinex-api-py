from collections.abc import Callable

from bfxapi.rest._interface.middleware import RateLimitInfo
from bfxapi.rest._interfaces import RestAuthEndpoints, RestPublicEndpoints


class BfxRestInterface:
    def __init__(
        self,
        host: str,
        api_key: str | None = None,
        api_secret: str | None = None,
        *,
        lossless_financial_decode: bool = False,
        decimal_mode: bool | None = None,
        nonce_provider: Callable[[], int] | None = None,
    ):
        """
        Args:
            nonce_provider: Where the signed (auth) endpoints take their
                nonces; None for the process-wide monotonic generator.
                See `bfxapi.Client`.
        """
        self.auth = RestAuthEndpoints(
            host=host,
            api_key=api_key,
            api_secret=api_secret,
            lossless_financial_decode=lossless_financial_decode,
            decimal_mode=decimal_mode,
            nonce_provider=nonce_provider,
        )

        self.public = RestPublicEndpoints(
            host=host,
            lossless_financial_decode=lossless_financial_decode,
            decimal_mode=decimal_mode,
        )

    @property
    def last_rate_limit(self) -> RateLimitInfo:
        """Rate-limit headers of the most recent REST response, auth or public.

        Bitfinex does not send ``x-ratelimit-*`` headers on the public
        endpoints (probed 2026-10-28), so the fields are often None: treat
        None as "unknown", never as "budget left".
        """
        auth, public = self.auth._m, self.public._m
        if public.last_response_at > auth.last_response_at:
            return public.last_rate_limit
        return auth.last_rate_limit
