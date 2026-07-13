"""robots.txt policy semantics.

An unretrievable or ambiguous robots.txt (5xx, redirect, timeout) must **fail
open** — a fetchable page is never skipped just because robots.txt could not be
read. Only an explicit ``2xx`` robots with a matching ``Disallow`` blocks.
"""

from __future__ import annotations

from sitesift.net.robots import policy_for_status

_UA = "sitesift-test"


def test_2xx_rules_are_honored() -> None:
    p = policy_for_status(200, "User-agent: *\nDisallow: /private\n", _UA)
    assert p.kind == "rules"
    assert p.can_fetch("https://x.com/", _UA) is True
    assert p.can_fetch("https://x.com/private", _UA) is False


def test_2xx_disallow_all_still_blocks() -> None:
    p = policy_for_status(200, "User-agent: *\nDisallow: /\n", _UA)
    assert p.can_fetch("https://x.com/", _UA) is False


def test_4xx_allows_all() -> None:
    assert policy_for_status(404, "", _UA).kind == "allow_all"


def test_5xx_fails_open() -> None:
    assert policy_for_status(503, "", _UA).kind == "allow_all"


def test_redirect_fails_open() -> None:
    # A 3xx on /robots.txt (very common: http->https, apex->www) is NOT followed
    # and must not block the host.
    assert policy_for_status(301, "", _UA).kind == "allow_all"


def test_synthetic_zero_timeout_fails_open() -> None:
    # The fetcher passes 0 for a timeout/connection error.
    assert policy_for_status(0, "", _UA).kind == "allow_all"
