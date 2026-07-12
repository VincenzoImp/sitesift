"""Fetcher error handling + retry behaviour against a local server."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from sitesift.config import Settings
from sitesift.errors import ErrorCode
from sitesift.net.fetcher import Fetcher, _parse_retry_after, _retry_delay

_HTML = b"<!doctype html><html lang='en'><head><title>Ok</title></head><body>ok</body></html>"

# Shared attempt counter for the /flaky endpoint.
_flaky_lock = threading.Lock()
_flaky_hits = 0


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_a: object) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        global _flaky_hits
        path = self.path.split("?", 1)[0]
        if path == "/robots.txt":
            self._send(200, b"User-agent: *\nAllow: /\n", "text/plain")
        elif path == "/err500":
            # A 5xx whose body is NOT html — must still be reported as E_HTTP_5XX.
            self._send(500, b'{"error":"boom"}', "application/json")
        elif path == "/flaky":
            with _flaky_lock:
                _flaky_hits += 1
                first = _flaky_hits == 1
            if first:
                self._send(503, b"try later", "text/plain")
            else:
                self._send(200, _HTML, "text/html; charset=utf-8")
        else:
            self._send(200, _HTML, "text/html; charset=utf-8")

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture()
def server() -> Iterator[int]:
    global _flaky_hits
    _flaky_hits = 0
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield port
    finally:
        httpd.shutdown()


def _settings(port: int, *, retries: int = 1) -> Settings:
    return Settings(
        identity={"contact": "test@example.com"},
        security={"allow_private_ips": True},
        fetch={
            "allow_ports": [port, 80, 443],
            "min_host_delay": 0.0,
            "crawl_delay_clamp": [0.0, 5.0],
            "timeout_read": 3.0,
            "timeout_total": 5.0,
            "retries": retries,
        },
    )


async def test_http_5xx_not_masked_by_content_type(server: int) -> None:
    fetcher = Fetcher(_settings(server, retries=0))
    try:
        out = await fetcher.fetch(f"http://127.0.0.1:{server}/err500")
        assert out.error_code is ErrorCode.E_HTTP_5XX  # not E_NONHTML
        assert out.status == 500
    finally:
        await fetcher.aclose()


async def test_retry_recovers_from_transient_error(server: int) -> None:
    fetcher = Fetcher(_settings(server, retries=2))
    try:
        out = await fetcher.fetch(f"http://127.0.0.1:{server}/flaky")
        assert out.ok  # first 503, then 200 on retry
        assert out.status == 200
    finally:
        await fetcher.aclose()


def test_parse_retry_after() -> None:
    assert _parse_retry_after("30") == 30.0
    assert _parse_retry_after(None) is None
    assert _parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT") is None  # HTTP-date -> backoff


def test_retry_delay_respects_retry_after() -> None:
    from sitesift.net.fetcher import FetchOutcome

    out = FetchOutcome(
        "u", "u", status=429, headers={"retry-after": "5"}, error_code=ErrorCode.E_RATE_LIMIT
    )
    assert _retry_delay(out, attempt=1) == 5.0
