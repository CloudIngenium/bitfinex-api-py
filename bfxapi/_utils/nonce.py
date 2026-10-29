"""The nonce every signed request carries, REST and WebSocket alike.

Bitfinex keeps one nonce counter per API key and rejects a nonce that is not
greater than the last one it accepted (error 10114). So every component that
signs with the same key, in this process or another, must draw its nonces
from one monotonic source.

By default the REST middleware and the WebSocket auth message share the
process-wide generator below. A caller that also signs with the same key
elsewhere (its own REST or WebSocket code, another process) passes one shared
``nonce_provider`` to every component instead.

This module imports neither ``bfxapi.rest`` nor ``bfxapi.websocket``.
"""

import threading
import time
from collections.abc import Callable

from bfxapi.exceptions import NonceProviderError

NonceProvider = Callable[[], int]

_LOCK = threading.Lock()
_LAST = 0


def next_nonce() -> int:
    """The process-wide monotonic microsecond nonce.

    Strictly greater than every nonce it returned before, from any thread:
    two calls in the same microsecond tick get consecutive values.
    """
    global _LAST
    with _LOCK:
        candidate = time.time_ns() // 1_000
        if candidate <= _LAST:
            candidate = _LAST + 1
        _LAST = candidate
    return candidate


def resolve_nonce(provider: NonceProvider | None) -> int:
    """The nonce for the next signature: from ``provider`` when one is set,
    else from the process-wide generator.

    Raises NonceProviderError, before anything is signed, when the provider
    raises or returns anything but a positive int (a bool is not one). An
    int subclass is judged and returned by its plain int value: its own
    __int__, __le__ or __str__ never runs.
    """
    if provider is None:
        return next_nonce()

    try:
        value = provider()
    except Exception as e:
        raise NonceProviderError(
            f"The nonce provider raised {type(e).__name__}: {e}"
        ) from e

    if isinstance(value, bool) or not isinstance(value, int):
        raise NonceProviderError(
            "The nonce provider must return a positive int, "
            f"got {value!r} ({type(value).__name__})."
        )

    # A plain int first, through int's own slot: an int subclass's own
    # __int__, __le__ or __str__ can neither fake a positive value nor
    # change how the nonce is rendered.
    try:
        plain = int.__int__(value)
    except Exception as e:
        raise NonceProviderError(
            "The nonce provider returned an int that cannot be converted "
            f"({type(value).__name__}): {type(e).__name__}: {e}"
        ) from e

    if plain <= 0:
        raise NonceProviderError(
            "The nonce provider must return a positive int, "
            f"got {plain} ({type(value).__name__})."
        )

    return plain
