"""Tests for the localhost-only binding policy (roadmap §P1-3.2).

The server/ and web/ entry points are thin argparse wrappers around
``server/bind.py``; the security-relevant logic lives there and is tested here:
loopback binds always allowed; non-loopback binds fail-closed without the
explicit ``--unsafe`` flag.
"""

from __future__ import annotations

import pytest

from data_analysis_agent.server.bind import is_loopback, resolve_bind_host


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "::1", "localhost", "127.0.0.2", "127.8.8.8"],
)
def test_is_loopback_true(host: str) -> None:
    assert is_loopback(host)


@pytest.mark.parametrize(
    "host",
    ["0.0.0.0", "*", "192.168.1.5", "10.0.0.1", "172.16.3.9", "my-laptop.local", "example.com"],
)
def test_is_loopback_false(host: str) -> None:
    assert not is_loopback(host)


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_resolve_loopback_allowed_without_unsafe(host: str) -> None:
    assert resolve_bind_host(host, unsafe=False) == host


def test_resolve_non_loopback_refuses_without_unsafe() -> None:
    # _refuse prints the message then SystemExit(1); assert the exit, not the text.
    with pytest.raises(SystemExit):
        resolve_bind_host("0.0.0.0", unsafe=False)
    with pytest.raises(SystemExit):
        resolve_bind_host("192.168.1.5", unsafe=False)


def test_resolve_non_loopback_allowed_with_unsafe() -> None:
    assert resolve_bind_host("0.0.0.0", unsafe=True) == "0.0.0.0"
    assert resolve_bind_host("192.168.1.5", unsafe=True) == "192.168.1.5"
