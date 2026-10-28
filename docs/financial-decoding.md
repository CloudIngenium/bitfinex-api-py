# Per-client financial decoding

`Client(lossless_financial_decode=True)` opts that client's REST and WebSocket
transports into parsing JSON fractional/scientific numeric tokens directly into
`FinancialTokenDecimal`, a `Decimal` subclass. Its `source_token` preserves the
original token, including trailing zeros and exponent notation. Integer tokens
(IDs, and also amounts the exchange sends without a fraction, such as `0` or
`5`) remain exact `int` values, so financial fields hold `int | Decimal`.
Non-finite numeric constants (NaN, Infinity) are rejected with
`FinancialDecodeError`, a `BfxBaseException`.

This does not set the legacy global `decimal_mode`, and combining the two flags
on one client raises `ValueError`. SDK models and event names are unchanged.
Caveat while `decimal_mode` is still a process-wide setting: if any other client
in the process enables it, the labeler turns this client's integer-token money
fields into plain `Decimal` values without `source_token`. Arithmetic returns normal
Decimal values and does not retain a false claim that its result is an original
exchange token. Accounting callers must use operation-specific local contexts,
then serialize canonical decimal strings and explicit source precision.

The legacy `decimal_mode=True` converts already decoded floats; it does not
recover lost digits. Consumers must label those values `sdk-number`.

`client.rest.auth.get_user_info_raw()` reuses the authenticated middleware and
returns the original User Info array for registered account verification. Use
ID at 0, master account at 16 and paper indicator at 21 from the
[official User Info contract](https://docs.bitfinex.com/reference/rest-auth-info-user).
Do not infer identity from a username or auto-register a key's returned ID.

Validation uses real `requests.Response.json`, SDK serializers and authenticated
WS model handlers with simulated transport. It does not certify a production
interpreter migration, live account identity or exchange mutations.
