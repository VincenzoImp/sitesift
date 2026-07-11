"""robots.txt policy with the error semantics Google documents.

This module is pure: it stores parsed policies keyed by host and answers
allow/delay questions. The *fetching* of ``/robots.txt`` is the fetcher's job
(so the SSRF guard applies); the fetcher then calls :meth:`RobotsCache.set`.

Error semantics (aligned with Google):

* ``2xx``            → apply the parsed rules
* ``4xx`` (incl 404) → allow all
* ``5xx`` / timeout  → **disallow all** for this host (retry next run)
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
    """Build a policy from a robots.txt fetch outcome."""
    if 200 <= status < 300:
        return RobotsPolicy.from_body(body, user_agent)
    if 400 <= status < 500:
        return RobotsPolicy.allow_all()
    # 5xx, or any non-2xx/4xx (e.g. a synthetic 0 for timeout) -> disallow all.
    return RobotsPolicy.disallow_all()


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
