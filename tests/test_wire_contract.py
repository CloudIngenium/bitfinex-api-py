"""Guard against fixture rot.

A captured payload is a snapshot of somebody else's wire format, and it is only
ever compared against itself. When the real format moves, the fixture keeps
passing while describing a world that no longer exists -- green, loud about
nothing, and wrong. It is worse than having no fixture, because the test's name
still claims the coverage.

Bitfinex's 2026-09-23 FIRST_TRADE change (https://www.bitfinex.com/post/?id=1197)
produced three instances of it across the sibling TypeScript forks in one week,
every one of them found by a human reading an announcement. This repo handled
that change well -- ``PairInfo``/``TradingPairTicker``/``FundingCurrencyTicker``
all carry ``first_trade``, and ``test_rest_public_endpoints.py`` covers the null
case and asserts the request URL. What it does NOT have is any check that its
fixtures still match the live wire, so it carries exactly the same exposure the
others did.

Two layers:

L1 (always, offline)
    The serializers must fit the declared wire width, pinned from BOTH sides:
    at the declared arity the last mapped label is populated, and one element
    short it degrades to ``None``. Using only the public ``parse`` API, so the
    test does not depend on serializer internals. This catches a label list that
    grew past the wire, or an arity declared here that was never true.

L2 (opt-in, live)
    The declared arity must still match what api-pub.bitfinex.com returns. Opt
    IN via ``LIVE_WIRE=1``, so the default is safe and no ordinary test run
    reaches the network. Stdlib ``urllib`` only -- this adds no dependency.

    A red here is NOT necessarily a bug: it means the exchange moved and a human
    should look at the fixtures, the contract and possibly the serializer.

The WebSocket ``ticker`` channel pushes the same payload as ``/ticker/<symbol>``
and is checked live, weekly, by the sibling guard in ``bitfinex-api-node``. It is
deliberately not duplicated here: the wire is transport-independent, and a second
scheduled socket would watch the same thing twice while costing an async
dependency in a suite that otherwise needs none.
"""

from __future__ import annotations

import json
import os
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest

from bfxapi.types import serializers

_API = "https://api-pub.bitfinex.com/v2"

_LIVE = os.environ.get("LIVE_WIRE") == "1"
_needs_live = pytest.mark.skipif(
    not _LIVE, reason="live wire checks are opt-in: set LIVE_WIRE=1"
)


@dataclass(frozen=True)
class WireContract:
    """One public surface, and how wide its rows are on the wire."""

    name: str
    #: Path under the v2 API root.
    path: str
    #: Elements Bitfinex puts on the wire for one row of this surface.
    arity: int
    #: Parse a row of ``n`` sentinel values with the surface's serializer.
    parse: Callable[[int], Any]
    #: Field that occupies the LAST mapped slot, and the index it must land on.
    last_field: str
    last_index: int
    #: Pull one representative row out of a live response body.
    row: Callable[[Any], list[Any]]
    #: ISO date this arity was last confirmed against the live API.
    verified: str


def _ticker(serializer: Any) -> Callable[[int], Any]:
    return lambda n: serializer.parse(*list(range(n)))


def _pair_info(n: int) -> Any:
    # pub:info:pair rows are ["<PAIR>", [ ...details ]] and PairInfo splices
    # them, so the arity under test is the NESTED details array.
    return serializers.PairInfo.parse("tBTCUSD", list(range(n)))


CONTRACTS: list[WireContract] = [
    WireContract(
        name="ticker (trading)",
        path="/ticker/tBTCUSD",
        arity=11,
        parse=_ticker(serializers.TradingPairTicker),
        last_field="first_trade",
        last_index=10,
        row=lambda b: b,
        verified="2026-09-26",
    ),
    WireContract(
        # Indices 13-14 are null on the wire and deliberately unmapped -- an
        # undocumented reserved gap between `low` and `frr_amount_available`.
        # An off-by-one inside it does not raise; it returns a neighbouring
        # field's value, which is why the width is worth pinning.
        name="ticker (funding)",
        path="/ticker/fUSD",
        arity=17,
        parse=_ticker(serializers.FundingCurrencyTicker),
        last_field="first_trade",
        last_index=16,
        row=lambda b: b,
        verified="2026-09-26",
    ),
    WireContract(
        name="conf pub:info:pair (spot details)",
        path="/conf/pub:info:pair",
        arity=12,
        parse=_pair_info,
        last_field="min_margin",
        last_index=9,
        row=lambda b: b[0][0][1],
        verified="2026-09-26",
    ),
    WireContract(
        # Futures rows are two elements SHORTER than spot and `min_margin` sits
        # at the very last slot. There is no headroom: one more mapped label and
        # the futures half reads None while the spot half stays green.
        name="conf pub:info:pair:futures (details)",
        path="/conf/pub:info:pair:futures",
        arity=10,
        parse=_pair_info,
        last_field="min_margin",
        last_index=9,
        row=lambda b: b[0][0][1],
        verified="2026-09-26",
    ),
]


def test_contract_table_is_populated() -> None:
    """A gate that runs against nothing passes against nothing."""
    assert len(CONTRACTS) == 4, "wire contract table lost entries"
    for c in CONTRACTS:
        assert c.path.startswith("/"), f"{c.name}: path must be API-relative"
        assert c.arity > 0, f"{c.name}: arity must be positive"
        # An unaudited arity is a guess wearing a number's clothes.
        assert len(c.verified) == 10 and c.verified[4] == "-", (
            f"{c.name}: verified must be an ISO date"
        )


@pytest.mark.parametrize("c", CONTRACTS, ids=lambda c: c.name)
def test_serializer_fits_the_declared_wire(c: WireContract) -> None:
    """L1: the last mapped label lands inside the declared width."""
    parsed = c.parse(c.arity)
    assert getattr(parsed, c.last_field) == c.last_index, (
        f"{c.name}: at {c.arity} elements, {c.last_field} should come from "
        f"index {c.last_index}. Either the serializer's label list grew past "
        f"the wire, or this arity was never true."
    )


@pytest.mark.parametrize("c", CONTRACTS, ids=lambda c: c.name)
def test_no_label_reads_past_the_declared_wire(c: WireContract) -> None:
    """L1, the half that catches a label list outgrowing the wire.

    The previous assertion checked only that the last KNOWN field lands where
    expected, which a new label appended after it would sail straight through:
    add one to TradingPairTicker and ``first_trade`` is still at index 10, still
    green, while the new field is silently always None.

    So feed exactly ``arity`` sentinel integers and require every mapped field
    to come back populated. The sentinels are ints, never None, so a None can
    only mean the label ran off the end of the row. That is the failure -- and
    it is the same one ``optional_tail`` is designed to tolerate at runtime,
    which is precisely why it needs stating here instead.

    Note this is NOT the same as "arity is minimal". Spot ``pub:info:pair`` rows
    carry 12 details while the last mapped label sits at 9: two trailing
    placeholders the serializer deliberately ignores. Declaring 10 there would
    be wrong even though nothing would go None.
    """
    parsed = c.parse(c.arity)
    empty = [name for name, value in vars(parsed).items() if value is None]
    assert not empty, (
        f"{c.name}: {empty} came back None from a row of {c.arity} non-None "
        f"sentinels, so the serializer maps at least one label past the end of "
        f"the wire. Either the label list grew or this arity is stale."
    )


@_needs_live
@pytest.mark.parametrize("c", CONTRACTS, ids=lambda c: c.name)
def test_live_wire_still_matches(c: WireContract) -> None:
    """L2: the exchange still sends what this file says it sends."""
    # An explicit User-Agent is required, not cosmetic: api-pub.bitfinex.com
    # answers urllib's default `Python-urllib/3.x` with 403 Forbidden. Without
    # this the live layer would fail for a reason that has nothing to do with
    # the wire, and a guard that cries wolf gets muted.
    req = urllib.request.Request(
        f"{_API}{c.path}",
        headers={"User-Agent": "bitfinex-api-py wire-contract test"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        assert resp.status == 200, f"{c.name}: HTTP {resp.status}"
        body = json.loads(resp.read().decode())

    row = c.row(body)
    assert isinstance(row, list), f"{c.name}: expected an array row"
    assert len(row) == c.arity, (
        f"{c.name}: Bitfinex now returns {len(row)} elements, not the "
        f"{c.arity} last verified on {c.verified}. This is not necessarily a "
        f"bug -- it means the wire moved. Re-capture the fixtures that use "
        f"this shape, update this contract, and decide whether the new field "
        f"is worth a label."
    )


def test_live_checks_are_not_silently_green() -> None:
    """The live layer must be visibly skipped, never quietly absent.

    ``rest-1-public-unit.ts`` in the sibling fork exists because nine methods
    once showed full coverage on a laptop and none on the runner: the tests
    reported passing while covering nothing. A network check that is
    conditionally skipped is one editing mistake away from the same thing, so
    state the condition out loud rather than trusting the decorator.
    """
    assert _LIVE == (os.environ.get("LIVE_WIRE") == "1")
    if not _LIVE:
        pytest.skip(
            f"live wire checks skipped for {len(CONTRACTS)} contract(s); "
            f"set LIVE_WIRE=1 to run them against {_API}"
        )
