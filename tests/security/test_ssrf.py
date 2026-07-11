"""SSRF guard tests — the security core.

Covers the IP blocklist (private / loopback / link-local / CGNAT / multicast /
reserved), the public-looking metadata addresses the range checks miss
(Azure WireServer, Alibaba, NAT64), embedded-IPv4 tunnels (IPv4-mapped, 6to4),
port allow-listing, fail-closed multi-address resolution, and a DNS-rebinding
simulation with a mocked resolver.
"""

from __future__ import annotations

import socket

import pytest

from sitesift.errors import SSRFBlocked
from sitesift.net import guard

BLOCKED_IPS = [
    # RFC1918 / loopback / link-local / unspecified / broadcast
    "127.0.0.1",
    "10.0.0.1",
    "172.16.5.4",
    "192.168.1.1",
    "169.254.169.254",  # AWS/GCP/Azure IMDS (link-local)
    "0.0.0.0",
    "255.255.255.255",
    # CGNAT
    "100.64.0.1",
    # multicast
    "224.0.0.1",
    # public-looking metadata endpoints (is_global == True)
    "168.63.129.16",  # Azure WireServer
    "100.100.100.200",  # Alibaba Cloud metadata
    # IPv6 private / loopback / link-local / ULA
    "::1",
    "::",
    "fe80::1",
    "fc00::1",
    "fd00:ec2::254",  # AWS IPv6 IMDS (ULA)
    # embedded-IPv4 tunnels
    "::ffff:127.0.0.1",  # IPv4-mapped loopback
    "::ffff:169.254.169.254",  # IPv4-mapped IMDS
    "::ffff:10.0.0.1",  # IPv4-mapped RFC1918
    "64:ff9b::a9fe:a9fe",  # NAT64 -> 169.254.169.254
    "2001::1",  # Teredo range
    "2002:0a00:0001::",  # 6to4 embedding 10.0.0.1
]

ALLOWED_IPS = [
    "8.8.8.8",
    "1.1.1.1",
    "93.184.216.34",
    "2606:4700:4700::1111",  # Cloudflare DNS (public IPv6)
]


@pytest.mark.parametrize("ip", BLOCKED_IPS)
def test_validate_ip_blocks(ip: str) -> None:
    with pytest.raises(SSRFBlocked):
        guard.validate_ip(ip)


@pytest.mark.parametrize("ip", ALLOWED_IPS)
def test_validate_ip_allows_public(ip: str) -> None:
    guard.validate_ip(ip)  # must not raise


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.1", "::1", "169.254.169.254"])
def test_allow_private_escape_hatch(ip: str) -> None:
    guard.validate_ip(ip, allow_private=True)  # must not raise


def test_validate_ip_rejects_garbage() -> None:
    with pytest.raises(SSRFBlocked):
        guard.validate_ip("not-an-ip")


@pytest.mark.parametrize("port", [22, 25, 6379, 8080, 3306, 0])
def test_validate_port_blocks(port: int) -> None:
    with pytest.raises(SSRFBlocked):
        guard.validate_port(port)


@pytest.mark.parametrize("port", [80, 443, None])
def test_validate_port_allows(port: int | None) -> None:
    guard.validate_port(port)


def _fake_resolver(*ips: str):
    """Build a getaddrinfo stand-in returning the given IPs, counting calls."""
    calls: list[int] = []

    def fake(host: str, port: int | None, *_a: object, **_k: object) -> list[tuple]:
        calls.append(1)
        out = []
        for ip in ips:
            fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
            sockaddr = (ip, port or 80) if fam == socket.AF_INET else (ip, port or 80, 0, 0)
            out.append((fam, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr))
        return out

    return fake, calls


def test_resolve_and_validate_public(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, _ = _fake_resolver("93.184.216.34")
    monkeypatch.setattr(guard, "_getaddrinfo", fake)
    assert guard.resolve_and_validate("example.com", 443) == ["93.184.216.34"]


def test_resolve_and_validate_blocks_private(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, _ = _fake_resolver("10.0.0.1")
    monkeypatch.setattr(guard, "_getaddrinfo", fake)
    with pytest.raises(SSRFBlocked):
        guard.resolve_and_validate("internal.evil", 443)


def test_resolve_and_validate_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    # A hostname that resolves to one public AND one private IP must be rejected.
    fake, _ = _fake_resolver("93.184.216.34", "169.254.169.254")
    monkeypatch.setattr(guard, "_getaddrinfo", fake)
    with pytest.raises(SSRFBlocked):
        guard.resolve_and_validate("rebind.evil", 443)


def test_port_checked_before_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, calls = _fake_resolver("93.184.216.34")
    monkeypatch.setattr(guard, "_getaddrinfo", fake)
    with pytest.raises(SSRFBlocked):
        guard.resolve_and_validate("example.com", 6379)
    assert calls == []  # never resolved a bad-port target


def test_rebinding_resolves_once(monkeypatch: pytest.MonkeyPatch) -> None:
    # The guard resolves exactly once and returns the pinned IP; the fetcher then
    # connects to that IP without re-resolving, closing the rebinding window.
    fake, calls = _fake_resolver("93.184.216.34")
    monkeypatch.setattr(guard, "_getaddrinfo", fake)
    pinned = guard.resolve_and_validate("example.com", 443)
    assert pinned == ["93.184.216.34"]
    assert len(calls) == 1


async def test_aresolve_and_validate(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, _ = _fake_resolver("8.8.8.8")
    monkeypatch.setattr(guard, "_getaddrinfo", fake)
    pinned = await guard.aresolve_and_validate("dns.google", 443)
    assert pinned == ["8.8.8.8"]
