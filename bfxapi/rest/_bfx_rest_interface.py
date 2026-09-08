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
    ):
        self.auth = RestAuthEndpoints(
            host=host,
            api_key=api_key,
            api_secret=api_secret,
            lossless_financial_decode=lossless_financial_decode,
        )

        self.public = RestPublicEndpoints(
            host=host, lossless_financial_decode=lossless_financial_decode
        )

    @property
    def last_rate_limit(self) -> RateLimitInfo:
        """Rate limit info from the most recent REST call (auth or public)."""
        return self.auth._m.last_rate_limit
