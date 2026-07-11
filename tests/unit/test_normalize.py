"""Parametrized tests for url_norm — the frontier primary key.

Covers scheme/host casing, default ports, fragments, tracking-param stripping,
query sorting, dot-segment resolution, percent-encoding canonicalization, IDN,
IPv4/IPv6 literals, userinfo, control-char stripping, and idempotency.
"""

from __future__ import annotations

import pytest

from sitesift.frontier.normalize import NormalizationError, normalize_url

CASES: list[tuple[str, str]] = [
    # scheme / host casing, trailing slash, default ports
    ("https://example.com", "https://example.com"),
    ("https://example.com/", "https://example.com"),
    ("HTTPS://Example.COM/Path", "https://example.com/Path"),
    ("https://example.com:443/", "https://example.com"),
    ("http://example.com:80/x", "http://example.com/x"),
    ("https://example.com:8443/x", "https://example.com:8443/x"),
    ("HTTP://E.COM", "http://e.com"),
    # scheme inference
    ("example.com", "https://example.com"),
    ("example.com/path", "https://example.com/path"),
    ("e.com/p?b=2&a=1", "https://e.com/p?a=1&b=2"),
    # fragments always dropped
    ("https://example.com/a#frag", "https://example.com/a"),
    ("https://e.com/p?a=1#top", "https://e.com/p?a=1"),
    # tracking params + query sorting
    ("https://example.com/?utm_source=x&a=1&utm_medium=y", "https://example.com?a=1"),
    ("https://example.com/p?b=2&a=1", "https://example.com/p?a=1&b=2"),
    ("https://example.com/p?a=2&a=1", "https://example.com/p?a=1&a=2"),
    ("https://e.com/p?fbclid=xyz&q=1", "https://e.com/p?q=1"),
    ("https://e.com/p?utm_source=a", "https://e.com/p"),
    ("https://e.com/p?gclid=1&ref=2&source=3", "https://e.com/p"),
    # dot-segment resolution / duplicate slashes preserved
    ("https://e.com/a/b/../c", "https://e.com/a/c"),
    ("https://e.com/a/./b", "https://e.com/a/b"),
    ("https://e.com/a/../../b", "https://e.com/b"),
    ("https://e.com/a//b", "https://e.com/a//b"),
    # percent-encoding canonicalization
    ("https://e.com/%2f", "https://e.com/%2F"),
    ("https://e.com/%7Euser", "https://e.com/~user"),
    ("https://e.com/a b", "https://e.com/a%20b"),
    ("https://e.com/café", "https://e.com/caf%C3%A9"),
    # IDN -> punycode
    ("https://münchen.de", "https://xn--mnchen-3ya.de"),
    ("https://MÜNCHEN.de/A", "https://xn--mnchen-3ya.de/A"),
    # host trailing dot, userinfo, IP literals
    ("https://example.com./", "https://example.com"),
    ("https://user:pass@e.com/x", "https://user:pass@e.com/x"),
    ("http://192.168.0.1:80/x", "http://192.168.0.1/x"),
    ("http://[::1]:8080/x", "http://[::1]:8080/x"),
    # control chars + whitespace
    ("https://e.com/a\x00b", "https://e.com/ab"),
    ("  https://e.com/x  ", "https://e.com/x"),
    # a bit of everything
    (
        "https://Example.com/Path/../Other/?utm_source=x&Z=1&a=2#frag",
        "https://example.com/Other/?Z=1&a=2",
    ),
]


@pytest.mark.parametrize(("raw", "expected"), CASES)
def test_normalize(raw: str, expected: str) -> None:
    assert normalize_url(raw) == expected


@pytest.mark.parametrize(("raw", "expected"), CASES)
def test_idempotent(raw: str, expected: str) -> None:
    once = normalize_url(raw)
    assert normalize_url(once) == once


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "mailto:foo@bar.com",
        "javascript:alert(1)",
        "ftp://host/file",
        "data:text/plain,hi",
    ],
)
def test_rejects_bad_input(raw: str) -> None:
    with pytest.raises(NormalizationError):
        normalize_url(raw)
