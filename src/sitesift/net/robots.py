"""robots.txt policy.

This module is pure: it stores parsed policies keyed by host and answers
allow/delay questions. The *fetching* of ``/robots.txt`` is the fetcher's job
(so the SSRF guard applies); the fetcher then calls :meth:`RobotsCache.set`.

Error semantics — **fail open**: a robots.txt that cannot be read cleanly must
not block a fetchable page (a single homepage per host, in a research crawl,
should never be lost to a hiccuping or redirecting robots endpoint).

* ``2xx``                                → apply the parsed rules
* everything else (``4xx``, ``5xx``,
  ``3xx`` redirect, timeout/synthetic 0) → allow all

Only an explicit ``2xx`` robots with a matching ``Disallow`` blocks. Set
``fetch.respect_robots = false`` to ignore robots.txt entirely.
"""

from __future__ import annotations

from dataclasses import dataclass

from protego import Protego


@dataclass
class RobotsPolicy:
    """The decision surface for one host."""

    kind: str  # "rules" | "allow_all" | "disallow_all"
    _parser: Protego | None = None
    crawl_delay_value: float | None = None

    def can_fetch(self, url: str, user_agent: str) -> bool:
        if self.kind == "allow_all":
            return True
        if self.kind == "disallow_all":
            return False
        assert self._parser is not None
        return bool(self._parser.can_fetch(url, user_agent))

    @classmethod
    def allow_all(cls) -> RobotsPolicy:
        return cls(kind="allow_all")

    @classmethod
    def disallow_all(cls) -> RobotsPolicy:
        return cls(kind="disallow_all")

    @classmethod
    def from_body(cls, body: str, user_agent: str) -> RobotsPolicy:
        parser = Protego.parse(body)
        delay = parser.crawl_delay(user_agent)
        return cls(kind="rules", _parser=parser, crawl_delay_value=float(delay) if delay else None)


def policy_for_status(status: int, body: str, user_agent: str) -> RobotsPolicy:
    """Build a policy from a robots.txt fetch outcome (fail open on anything
    that is not a clean ``2xx`` with rules)."""
    if 200 <= status < 300:
        return RobotsPolicy.from_body(body, user_agent)
    # Anything else — 4xx, 5xx, a 3xx redirect we do not follow, or a synthetic
    # 0 for a timeout/connection error — fails OPEN: an unretrievable robots.txt
    # must never block a fetchable page.
    return RobotsPolicy.allow_all()


class RobotsCache:
    """In-memory host→policy cache (persistent caching is added with the store)."""

    def __init__(self, user_agent: str) -> None:
        self._user_agent = user_agent
        self._policies: dict[str, RobotsPolicy] = {}

    def get(self, host: str) -> RobotsPolicy | None:
        return self._policies.get(host)

    def set_from_fetch(self, host: str, status: int, body: str) -> RobotsPolicy:
        policy = policy_for_status(status, body, self._user_agent)
        self._policies[host] = policy
        return policy

    def set(self, host: str, policy: RobotsPolicy) -> None:
        self._policies[host] = policy
