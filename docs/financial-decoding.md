# Per-client financial decoding

`Client(lossless_financial_decode=True)` opts that client's REST and WebSocket
transports into parsing JSON fractional/scientific numeric tokens directly into
`FinancialTokenDecimal`, a `Decimal` subclass. Its `source_token` preserves the
original token, including trailing zeros and exponent notation. Python integer
IDs remain exact `int` values. Non-finite numeric constants are rejected.

This does not set the legacy global `decimal_mode`. Other client instances keep
their configured decoding. SDK models and event names are unchanged; financial
fields in the opted-in client contain Decimal values. Arithmetic returns normal
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
