"""localhost-only binding policy (roadmap §P1-3.2).

Shared by both web entry points (server/ and web/): any non-loopback bind
requires an explicit ``--unsafe`` flag plus a prominent warning, else startup
is refused (fail-closed). Loopback binds (127.0.0.1 / ::1 / localhost) never
need it.
"""

from __future__ import annotations

import ipaddress
import sys

_LOOPBACK_LITERALS = ("127.0.0.1", "::1", "localhost")


def is_loopback(host: str) -> bool:
    """True for loopback literals and any parseable loopback IP."""
    if host in _LOOPBACK_LITERALS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        # Hostname / "*" / "0.0.0.0"… not a parseable IP — not provably loopback.
        return False


def resolve_bind_host(host: str, *, unsafe: bool) -> str:
    """Validate ``host`` against the localhost-only policy.

    Raises SystemExit (with a clear message) when a non-loopback bind is
    attempted without ``--unsafe``. Returns the host unchanged otherwise.
    """
    if is_loopback(host):
        return host
    if unsafe:
        return host
    _refuse(host)
    return host  # unreachable; _refuse exits


def _refuse(host: str) -> None:
    print(
        "ERROR: refusing to bind to non-loopback address "
        f"{host!r} without --unsafe (roadmap P1-3.2).\n"
        "  Public/LAN exposure must be explicit. Pass --unsafe to override — "
        "you are exposing the agent's workbench to the network; anyone who can "
        "reach this port can drive it."
    )
    sys.exit(1)


def unsafe_warning(host: str) -> None:
    """Print the prominent warning once an unsafe bind is allowed."""
    print(
        f"\n⚠️  UNSAFE: binding to {host} — this workbench is now reachable over "
        "the network. It serves agent output and mutating endpoints; only "
        "localhost usage is safe by design.\n"
    )
