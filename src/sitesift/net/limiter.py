"""Politeness limiter: at most 1 concurrent request per host + a min delay.

The hard rule (``1 concurrent request per host``) is enforced with a per-host
lock; a min-delay-since-last-request is enforced on top. A global semaphore caps
total concurrency. Crawl-delay from robots.txt is clamped to a sane range.
"""

from __future__ import annotations

import time
from collections import defaultdict

import anyio


class HostRateLimiter:
    """Async, fair-enough politeness gate.

    Usage::

        async with limiter.host(host, delay):
            ... one request to `host` ...
    """

    def __init__(self, *, global_concurrency: int = 200, min_host_delay: float = 1.0) -> None:
        self._global = anyio.Semaphore(global_concurrency)
        self._min_host_delay = min_host_delay
        self._host_locks: dict[str, anyio.Lock] = defaultdict(anyio.Lock)
        self._last_request: dict[str, float] = {}

    def host(self, host: str, delay: float | None = None) -> _HostSlot:
        return _HostSlot(self, host, delay if delay is not None else self._min_host_delay)


class _HostSlot:
    def __init__(self, limiter: HostRateLimiter, host: str, delay: float) -> None:
        self._limiter = limiter
        self._host = host
        self._delay = delay

    async def __aenter__(self) -> None:
        await self._limiter._global.acquire()
        await self._limiter._host_locks[self._host].acquire()
        # Enforce the min delay since this host's previous request completed.
        last = self._limiter._last_request.get(self._host)
        if last is not None:
            elapsed = time.monotonic() - last
            if elapsed < self._delay:
                await anyio.sleep(self._delay - elapsed)

    async def __aexit__(self, *exc: object) -> None:
        self._limiter._last_request[self._host] = time.monotonic()
        self._limiter._host_locks[self._host].release()
        self._limiter._global.release()
