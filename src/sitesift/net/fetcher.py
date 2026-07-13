"""Async fetcher — SSRF-pinned, polite, streaming, with hard limits.

Redirects are followed **manually** (one hop at a time) so every hop is
re-validated by the SSRF guard and the redirect chain is recorded. The URL is
rewritten to the validated IP (defeating DNS rebinding) while the original host
is preserved in the ``Host`` header and the ``sni_hostname`` extension (so TLS
certificate verification still targets the real hostname). ``Location`` is
resolved against the *tracked* original URL, never the pinned-IP URL.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import anyio
import httpx

from ..config import Settings
from ..errors import ErrorCode, SSRFBlocked
from .guard import aresolve_and_validate
from .limiter import HostRateLimiter
from .robots import RobotsCache, RobotsPolicy

_REDIRECT_CODES = {301, 302, 303, 307, 308}
_ROBOTS_MAX_BYTES = 1_048_576  # 1 MiB is plenty for robots.txt

# Transient outcomes worth retrying with backoff.
_RETRYABLE: frozenset[ErrorCode] = frozenset(
    {ErrorCode.E_TIMEOUT, ErrorCode.E_CONNECT, ErrorCode.E_HTTP_5XX, ErrorCode.E_RATE_LIMIT}
)
_RETRY_BASE = 0.5
_RETRY_CAP = 20.0
_RETRY_AFTER_CAP = 60.0


@dataclass
class FetchOutcome:
    url_raw: str
    url_final: str
    status: int = 0
    headers: dict[str, str] = field(default_factory=dict)
    content: bytes | None = None
    charset: str = "utf-8"
    charset_source: str = "default"
    redirect_chain: list[str] = field(default_factory=list)
    error_code: ErrorCode | None = None
    robots_blocked: bool = False

    @property
    def ok(self) -> bool:
        return self.error_code is None and self.content is not None


class Fetcher:
    def __init__(self, settings: Settings) -> None:
        self._s = settings
        f = settings.fetch
        self._ua = settings.user_agent()
        self._allow_private = settings.security.allow_private_ips
        self._allowed_ports = set(f.allow_ports)
        self._delay_clamp = f.crawl_delay_clamp
        self._limiter = HostRateLimiter(
            global_concurrency=f.max_concurrency, min_host_delay=f.min_host_delay
        )
        self._robots = RobotsCache(self._ua)
        self._respect_robots = f.respect_robots
        self._client = httpx.AsyncClient(
            follow_redirects=False,
            # HTTP/1.1 only: this crawler issues at most one concurrent request per
            # host, so HTTP/2 multiplexing buys nothing — and a misbehaving h2 peer
            # can raise a low-level h2 ProtocolError that httpx does not wrap.
            http2=False,
            headers={"User-Agent": self._ua, "Accept": "text/html,application/xhtml+xml"},
            timeout=httpx.Timeout(
                connect=f.timeout_connect,
                read=f.timeout_read,
                write=f.timeout_read,
                pool=f.timeout_total,
            ),
        )
        self._max_body = f.max_body_bytes
        self._max_decompressed = f.max_decompressed_bytes
        self._max_redirects = f.max_redirects
        self._retries = f.retries

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch(self, url_norm: str) -> FetchOutcome:
        """Fetch with bounded exponential-backoff retries on transient failures."""
        attempt = 0
        while True:
            outcome = await self._fetch_once(url_norm)
            if outcome.error_code not in _RETRYABLE or attempt >= self._retries:
                return outcome
            attempt += 1
            await anyio.sleep(_retry_delay(outcome, attempt))

    async def _fetch_once(self, url_norm: str) -> FetchOutcome:
        current = url_norm
        chain: list[str] = []
        for _hop in range(self._max_redirects + 1):
            split = urlsplit(current)
            host = split.hostname or ""
            port = split.port
            scheme = split.scheme

            try:
                pinned = await aresolve_and_validate(
                    host, port, allow_private=self._allow_private, allowed_ports=self._allowed_ports
                )
            except SSRFBlocked as exc:
                is_dns = "DNS resolution failed" in str(exc)
                code = ErrorCode.E_DNS if is_dns else ErrorCode.E_SSRF_BLOCKED
                return _err(url_norm, current, chain, code)

            blocked = await self._robots_block(scheme, host, port, current, pinned[0])
            if blocked is not None:
                return _err(
                    url_norm,
                    current,
                    chain,
                    blocked,
                    robots_blocked=blocked == ErrorCode.E_ROBOTS_BLOCK,
                )

            delay = self._host_delay(host)
            try:
                async with self._limiter.host(host, delay):
                    resp = await self._send(current, pinned[0], host, port)
                    try:
                        status = resp.status_code
                        if status in _REDIRECT_CODES and "location" in resp.headers:
                            chain.append(current)
                            current = _resolve_redirect(current, resp.headers["location"])
                            continue
                        return await self._finalize(url_norm, current, chain, resp)
                    finally:
                        await resp.aclose()
            except httpx.TimeoutException:
                return _err(url_norm, current, chain, ErrorCode.E_TIMEOUT)
            except httpx.HTTPError:
                # Any other transport-layer httpx error (connect/read/protocol);
                # never let it escape into the batch task group.
                return _err(url_norm, current, chain, ErrorCode.E_CONNECT)
            except _BodyLimitExceeded as exc:
                return _err(url_norm, current, chain, exc.code)

        return _err(url_norm, current, chain, ErrorCode.E_REDIRECT_LOOP)

    # --- internals --------------------------------------------------------

    async def _send(self, url: str, pinned_ip: str, host: str, port: int | None) -> httpx.Response:
        request = self._client.build_request("GET", url)
        request.url = request.url.copy_with(host=pinned_ip)
        host_header = f"[{host}]" if ":" in host else host
        if port is not None:
            host_header = f"{host_header}:{port}"
        request.headers["Host"] = host_header
        request.extensions = {**request.extensions, "sni_hostname": host}
        return await self._client.send(request, stream=True)

    async def _finalize(
        self, url_raw: str, url_final: str, chain: list[str], resp: httpx.Response
    ) -> FetchOutcome:
        headers = {k.lower(): v for k, v in resp.headers.items()}
        # HTTP-status errors take precedence over the content-type gate, so a 5xx
        # or 429 with a non-HTML body reports its real code (and can be retried),
        # not a misleading E_NONHTML.
        http_err = _http_error(resp.status_code)
        if http_err is not None:
            return FetchOutcome(
                url_raw,
                url_final,
                status=resp.status_code,
                headers=headers,
                redirect_chain=chain,
                error_code=http_err,
            )
        content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
        is_html = content_type.startswith("text/html") or content_type.startswith(
            "application/xhtml"
        )
        if not is_html:
            return FetchOutcome(
                url_raw,
                url_final,
                status=resp.status_code,
                headers=headers,
                redirect_chain=chain,
                error_code=ErrorCode.E_NONHTML,
            )
        if _too_large(headers.get("content-length"), self._max_body):
            return FetchOutcome(
                url_raw,
                url_final,
                status=resp.status_code,
                headers=headers,
                redirect_chain=chain,
                error_code=ErrorCode.E_TOO_LARGE,
            )
        if headers.get("content-encoding", "").count(",") >= 1:
            return FetchOutcome(
                url_raw,
                url_final,
                status=resp.status_code,
                headers=headers,
                redirect_chain=chain,
                error_code=ErrorCode.E_BOMB,
            )
        content = await self._read_limited(resp)
        charset, source = _pick_charset(headers, content)
        return FetchOutcome(
            url_raw,
            url_final,
            status=resp.status_code,
            headers=headers,
            content=content,
            charset=charset,
            charset_source=source,
            redirect_chain=chain,
            error_code=None,
        )

    async def _read_limited(self, resp: httpx.Response) -> bytes:
        chunks: list[bytes] = []
        total = 0
        async for chunk in resp.aiter_bytes():
            total += len(chunk)
            if total > self._max_decompressed:
                raise _BodyLimitExceeded(ErrorCode.E_BOMB)
            chunks.append(chunk)
        return b"".join(chunks)

    async def _robots_block(
        self, scheme: str, host: str, port: int | None, url: str, pinned_ip: str
    ) -> ErrorCode | None:
        if not self._respect_robots:
            return None
        policy = self._robots.get(host)
        if policy is None:
            policy = await self._fetch_robots(scheme, host, port)
        if policy.kind == "disallow_all":
            return ErrorCode.E_ROBOTS_UNAVAIL
        if not policy.can_fetch(url, self._ua):
            return ErrorCode.E_ROBOTS_BLOCK
        return None

    async def _fetch_robots(self, scheme: str, host: str, port: int | None) -> RobotsPolicy:
        netloc = f"[{host}]" if ":" in host else host
        if port is not None:
            netloc = f"{netloc}:{port}"
        robots_url = f"{scheme}://{netloc}/robots.txt"
        try:
            pinned = await aresolve_and_validate(
                host, port, allow_private=self._allow_private, allowed_ports=self._allowed_ports
            )
            delay = self._host_delay(host)
            async with self._limiter.host(host, delay):
                resp = await self._send(robots_url, pinned[0], host, port)
                try:
                    body = (await self._read_capped(resp, _ROBOTS_MAX_BYTES)).decode(
                        "utf-8", errors="replace"
                    )
                    return self._robots.set_from_fetch(host, resp.status_code, body)
                finally:
                    await resp.aclose()
        except (httpx.HTTPError, SSRFBlocked, _BodyLimitExceeded):
            policy = RobotsPolicy.disallow_all()  # unavailable -> disallow (Google semantics)
            self._robots.set(host, policy)
            return policy

    async def _read_capped(self, resp: httpx.Response, cap: int) -> bytes:
        chunks: list[bytes] = []
        total = 0
        async for chunk in resp.aiter_bytes():
            total += len(chunk)
            if total > cap:
                break
            chunks.append(chunk)
        return b"".join(chunks)

    def _host_delay(self, host: str) -> float:
        policy = self._robots.get(host)
        lo, hi = self._delay_clamp
        base = self._s.fetch.min_host_delay
        if policy is not None and policy.crawl_delay_value is not None:
            return max(lo, min(hi, policy.crawl_delay_value))
        return max(base, lo)


class _BodyLimitExceeded(Exception):
    def __init__(self, code: ErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


def _err(
    url_raw: str,
    url_final: str,
    chain: list[str],
    code: ErrorCode,
    *,
    robots_blocked: bool = False,
) -> FetchOutcome:
    return FetchOutcome(
        url_raw, url_final, redirect_chain=chain, error_code=code, robots_blocked=robots_blocked
    )


def _retry_delay(outcome: FetchOutcome, attempt: int) -> float:
    if outcome.error_code is ErrorCode.E_RATE_LIMIT:
        retry_after = _parse_retry_after(outcome.headers.get("retry-after"))
        if retry_after is not None:
            return min(retry_after, _RETRY_AFTER_CAP)
    backoff = min(_RETRY_CAP, _RETRY_BASE * (2 ** (attempt - 1)))
    return backoff + random.uniform(0, _RETRY_BASE)


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)  # delta-seconds form; HTTP-date form falls back to backoff
    except ValueError:
        return None


def _resolve_redirect(current: str, location: str) -> str:
    # Resolve against the tracked original URL (not the pinned-IP URL).
    return urljoin(current, location)


def _too_large(content_length: str | None, limit: int) -> bool:
    if not content_length:
        return False
    try:
        return int(content_length) > limit
    except ValueError:
        return False


def _http_error(status: int) -> ErrorCode | None:
    if 400 <= status < 500:
        return ErrorCode.E_RATE_LIMIT if status == 429 else ErrorCode.E_HTTP_4XX
    if status >= 500:
        return ErrorCode.E_HTTP_5XX
    return None


def _pick_charset(headers: dict[str, str], content: bytes) -> tuple[str, str]:
    ctype = headers.get("content-type", "")
    if "charset=" in ctype:
        charset = ctype.split("charset=", 1)[1].split(";", 1)[0].strip().strip('"').lower()
        if charset:
            return charset, "header"
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(content[:65536]).best()
        if best is not None and best.encoding:
            return best.encoding.lower(), "charset_normalizer"
    except Exception:  # noqa: BLE001 - detection is best-effort
        pass
    return "utf-8", "default"
