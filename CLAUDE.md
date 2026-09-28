# CLAUDE.md — bitfinex-api-py

## Project Purpose

CloudIngenium fork of the official Bitfinex Python API client (v2). Published as `bitfinex-api-py`. This is the core library used by BfxLendingBot for all Bitfinex REST and WebSocket communication.

## Stack

- **Language:** Python 3.12+ (targets 3.13)
- **Build system:** Hatchling
- **Lint/Format:** Ruff (line-length 80, pycodestyle + pyflakes + bugbear + pyupgrade + isort)
- **Type checking:** mypy (strict mode)
- **Test:** pytest + pytest-asyncio + pytest-cov (fail-under set in `pyproject.toml` `addopts`)
- **Pre-commit:** Ruff linting and formatting hooks

## Build & Dev

```bash
pip install -e ".[dev]"           # Install in editable mode with dev deps
pytest                            # Run tests with coverage
mypy bfxapi/                      # Type checking (strict)
ruff check bfxapi/                # Lint
ruff format bfxapi/               # Format
pre-commit run --all-files        # Run all pre-commit hooks
```

## Structure

```
bfxapi/
  __init__.py           # Package entry — exports Client, host constants
  _client.py            # Main Client class (REST + WebSocket)
  _version.py           # Version string
  _utils/               # Internal utilities
  exceptions.py         # Package-level exceptions
  rest/
    __init__.py
    _bfx_rest_interface.py     # REST interface base
    _interface/
      interface.py             # Low-level HTTP interface
      middleware.py            # Request middleware (auth, etc.)
    _interfaces/
      rest_auth_endpoints.py   # Authenticated REST endpoints
      rest_public_endpoints.py # Public REST endpoints
    exceptions.py              # REST-specific exceptions
  types/
    __init__.py
    dataclasses.py      # All Bitfinex data types (Order, Trade, Candle, etc.)
    labeler.py          # Field labeling utilities
    notification.py     # Notification type
    serializers.py      # Type serialization
  websocket/
    __init__.py
    _connection.py      # WebSocket connection management
    subscriptions.py    # Subscription types and management
    exceptions.py       # WebSocket-specific exceptions
    _client/
      bfx_websocket_client.py   # Main WebSocket client
      bfx_websocket_bucket.py   # Connection bucketing
      bfx_websocket_inputs.py   # WebSocket input operations
    _event_emitter/
      bfx_event_emitter.py      # Event system
    _handlers/
      auth_events_handler.py    # Authenticated event handling
      public_channels_handler.py # Public channel handling
tests/                  # pytest test suites
examples/               # Usage examples (REST + WebSocket)
```

## Conventions

- Import types from `bfxapi.types.dataclasses` (never `bfxapi.models` — removed in v4)
- Use `Decimal` for monetary values, never `float`
- WebSocket preferred over REST for real-time data (it does not draw on the REST request budget; each connection holds at most 25 subscriptions — see `bfx_websocket_bucket.py`)
- REST rate limit: 90 req/5min on private endpoints
- Never hardcode API keys — use environment variables
- asyncio-based WebSocket client; use `bfx.wss.run()` or `await bfx.wss.start()`
