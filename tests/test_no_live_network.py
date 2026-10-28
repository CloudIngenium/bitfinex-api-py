"""The guard's own address classification.

A guard is a test that tests other tests, so nothing else covers it. This matters more than the
usual "test the helper" argument: the JS sibling's equivalent function shipped with a bug that
reported tsx's own IPC pipe, ``/tmp/tsx-1000/561052.pipe``, as "a live network connection". A
guard whose diagnostic is wrong is worse than no guard -- it sends the reader hunting for a
network call that never happened.
"""

import socket
from typing import Any

import pytest

from tests.conftest import _ALLOW_LIVE, _describe, _resolved


@pytest.fixture
def clean_resolver() -> Any:
    saved = dict(_resolved)
    _resolved.clear()
    yield _resolved
    _resolved.clear()
    _resolved.update(saved)


class TestAddressesThatAreNotTheNetwork:
    """``None`` means "do not block this" -- a false positive here breaks unrelated tests."""

    @pytest.mark.parametrize(
        "address",
        [
            pytest.param(
                "/tmp/some.sock", id="AF_UNIX path is a filesystem socket"
            ),
            pytest.param("", id="empty AF_UNIX path"),
            pytest.param((), id="empty tuple"),
            pytest.param(("127.0.0.1", 80), id="IPv4 loopback"),
            pytest.param(("::1", 80), id="IPv6 loopback"),
            pytest.param(("localhost", 8080), id="localhost by name"),
            pytest.param(("0.0.0.0", 80), id="wildcard bind"),
            pytest.param(("", 80), id="empty host"),
            pytest.param((42, 80), id="non-string host"),
        ],
    )
    def test_is_not_blocked(self, address: Any, clean_resolver: Any) -> None:
        assert _describe(address) is None


class TestAddressesThatAreTheNetwork:
    def test_unresolved_ip_reports_the_ip(self, clean_resolver: Any) -> None:
        assert _describe(("104.16.164.90", 443)) == "104.16.164.90"

    def test_resolved_ip_reports_the_hostname(
        self, clean_resolver: Any
    ) -> None:
        clean_resolver["104.16.164.90"] = "api-pub.bitfinex.com"
        assert (
            _describe(("104.16.164.90", 443))
            == "api-pub.bitfinex.com (104.16.164.90)"
        )

    def test_hostname_is_not_repeated_when_it_resolved_to_itself(
        self, clean_resolver: Any
    ) -> None:
        """Otherwise the message reads ``api.bitfinex.com (api.bitfinex.com)``."""
        clean_resolver["api.bitfinex.com"] = "api.bitfinex.com"
        assert _describe(("api.bitfinex.com", 443)) == "api.bitfinex.com"

    def test_ipv6_sockaddr_has_four_elements(self, clean_resolver: Any) -> None:
        """AF_INET6 addresses carry flowinfo and scopeid; only element 0 may be read."""
        assert _describe(("2606:4700::1", 443, 0, 0)) == "2606:4700::1"


class TestTheGuardIsActuallyInstalled:
    """A guard that silently failed to install is the exact failure mode it exists to prevent."""

    def test_socket_functions_are_hooked(self) -> None:
        assert _ALLOW_LIVE is False, "this suite runs without LIVE_WIRE"
        assert socket.getaddrinfo.__name__ == "_spy_getaddrinfo"
        assert socket.socket.connect.__name__ == "_spy_connect"
        assert socket.socket.connect_ex.__name__ == "_spy_connect_ex"

    def test_loopback_still_works(self) -> None:
        """The guard must not break legitimate local sockets."""
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        try:
            client = socket.create_connection(server.getsockname(), timeout=2)
            client.close()
        finally:
            server.close()
