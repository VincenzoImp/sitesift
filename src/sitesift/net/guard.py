"""SSRF guard — resolve, validate, pin.

This module is written **before** the fetcher so the fetcher cannot be born
insecure. The threat: a line in ``urls.txt`` that points at (or DNS-rebinds to)
``169.254.169.254`` (cloud metadata), ``127.0.0.1:6379`` (Redis), or a public-
looking metadata endpoint.

Defenses (all here):

1. Resolve the name explicitly (``getaddrinfo`` — every A *and* AAAA record).
2. Reject if **any** resolved address is private / loopback / link-local /
   CGNAT / multicast / reserved (via ``not ip.is_global``) — fail closed.
3. Normalize embedded-IPv4 forms (IPv4-mapped, 6to4) before the check — the
   single most-missed bypass class.
4. Block public-*looking* metadata addresses the range checks miss:
   Azure WireServer ``168.63.129.16``, Alibaba ``100.100.100.200``, NAT64
   ``64:ff9b::/96``, Teredo ``2001::/32``, and CGNAT ``100.64.0.0/10``
   explicitly (its ``is_global`` classification has varied across Python).
5. The fetcher connects to the **pinned IP** (not the name) and re-runs this
   guard on **every redirect** — defeating check-then-connect rebinding.

Only ``validate_ip`` / ``validate_port`` / ``resolve_and_validate`` live here;
connection pinning is applied by the fetcher using the returned IP plus the
``sni_hostname`` request extension (preserves TLS cert verification).
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterable

import anyio

from ..errors import SSRFBlocked

_IPNet = ipaddress.IPv4Network | ipaddress.IPv6Network
_IPAddr = ipaddress.IPv4Address | ipaddress.IPv6Address

# Ranges the standard ``is_global`` check misses or classifies inconsistently.
_EXTRA_BLOCKED: tuple[_IPNet, ...] = (
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT (RFC6598) — #1 missed range
    ipaddress.ip_network("168.63.129.16/32"),  # Azure WireServer (public-looking!)
    ipaddress.ip_network("100.100.100.200/32"),  # Alibaba Cloud metadata
    ipaddress.ip_network("64:ff9b::/96"),  # NAT64 -> reaches IMDS via 64:ff9b::a9fe:a9fe
    ipaddress.ip_network("2001::/32"),  # Teredo (embeds a possibly-private v4)
    ipaddress.ip_network("2002::/16"),  # 6to4 (embeds a possibly-private v4)
)

DEFAULT_ALLOWED_PORTS: frozenset[int] = frozenset({80, 443})

# Indirection so tests can monkeypatch the resolver (rebinding simulation).
_getaddrinfo = socket.getaddrinfo


def validate_ip(ip_str: str, *, allow_private: bool = False) -> None:
    """Raise :class:`SSRFBlocked` if ``ip_str`` is not a safe public destination.

    ``allow_private=True`` disables the check entirely (test-only escape hatch;
    the caller accepts the risk and the CLI warns).
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError as exc:
        raise SSRFBlocked(f"not an IP address: {ip_str!r}") from exc

    if allow_private:
        return

    # Explicit ranges first (catches public-looking metadata + CGNAT + tunnels
    # that embed private v4 before normalization can strip the wrapper).
    # ``ip in net`` returns False on a version mismatch, so no version guard.
    for net in _EXTRA_BLOCKED:
        if ip in net:
            raise SSRFBlocked(f"blocked range {net} for {ip}")

    # Normalize embedded-IPv4 tunnels so the global check sees the real target.
    checkable: _IPAddr = ip
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = ip.ipv4_mapped or ip.sixtofour
        if embedded is not None:
            checkable = embedded
            # re-run the explicit checks against the embedded address
            for net in _EXTRA_BLOCKED:
                if checkable in net:
                    raise SSRFBlocked(f"blocked embedded range {net} for {ip}")

    if not checkable.is_global:
        raise SSRFBlocked(f"non-global address: {ip}")
    if checkable.is_multicast:
        raise SSRFBlocked(f"multicast address: {ip}")


def validate_port(port: int | None, allowed: Iterable[int] = DEFAULT_ALLOWED_PORTS) -> None:
    """Raise :class:`SSRFBlocked` for a non-allowed port (``None`` = default)."""
    if port is None:
        return
    if port not in set(allowed):
        raise SSRFBlocked(f"port not allowed: {port}")


def resolve_and_validate(
    host: str,
    port: int | None,
    *,
    allow_private: bool = False,
    allowed_ports: Iterable[int] = DEFAULT_ALLOWED_PORTS,
) -> list[str]:
    """Resolve ``host`` and validate the port and every resolved address.

    Returns the list of validated IP strings (fail closed: if *any* resolved
    address is unsafe, the whole host is rejected). The caller pins one of these.
    """
    validate_port(port, allowed_ports)
    try:
        infos = _getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise SSRFBlocked(f"DNS resolution failed for {host!r}: {exc}") from exc

    ips = _dedupe([str(info[4][0]) for info in infos])
    if not ips:
        raise SSRFBlocked(f"no addresses for {host!r}")
    for ip in ips:
        validate_ip(ip, allow_private=allow_private)
    return ips


async def aresolve_and_validate(
    host: str,
    port: int | None,
    *,
    allow_private: bool = False,
    allowed_ports: Iterable[int] = DEFAULT_ALLOWED_PORTS,
) -> list[str]:
    """Async wrapper: run the (blocking) resolver + validation in a worker thread."""

    def _run() -> list[str]:
        return resolve_and_validate(
            host, port, allow_private=allow_private, allowed_ports=allowed_ports
        )

    return await anyio.to_thread.run_sync(_run)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        # getaddrinfo can return scoped IPv6 like "fe80::1%en0"; keep the address.
        addr = item.split("%", 1)[0]
        if addr not in seen:
            seen.add(addr)
            out.append(addr)
    return out
