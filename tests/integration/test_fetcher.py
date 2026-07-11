"""Fetcher integration tests against a local HTTP server.

Uses the ``allow_private_ips`` escape hatch to hit 127.0.0.1. Verifies basic
fetch, manual redirect handling, robots blocking, and the hard politeness rule
(<= 1 concurrent connection per host) with a counting handler.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from sitesift.config import Settings
from sitesift.errors import ErrorCode
from sitesift.net.fetcher import Fetcher

_PAGE = b"<!doctype html><html lang='en'><head><title>Home</title></head><body><h1>Hi</h1></body></html>"

# Shared concurrency tracker for the /conc endpoint.
_conc_lock = threading.Lock()
_conc_now = 0
_conc_max = 0


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_a: object) -> None:  # silence
        pass

    def _html(self, body: bytes, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        global _conc_now, _conc_max
        path = self.path.split("?", 1)[0]
        if path == "/robots.txt":
            body = b"User-agent: *\nAllow: /\nDisallow: /private\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/target")
            self.end_headers()
        elif path == "/target":
            self._html(b"<html lang='en'><head><title>Target</title></head><body>ok</body></html>")
        elif path == "/conc":
            with _conc_lock:
                _conc_now += 1
                _conc_max = max(_conc_max, _conc_now)
            time.sleep(0.05)
            with _conc_lock:
                _conc_now -= 1
            self._html(_PAGE)
        else:
            self._html(_PAGE)


@pytest.fixture()
def server() -> Iterator[int]:
    global _conc_now, _conc_max
    _conc_now = _conc_max = 0
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        httpd.shutdown()


def _settings(port: int) -> Settings:
    return Settings(
        identity={"contact": "test@example.com"},
        security={"allow_private_ips": True},
        fetch={
            "allow_ports": [port, 80, 443],
            "min_host_delay": 0.01,
            "crawl_delay_clamp": [0.0, 5.0],
            "timeout_connect": 2.0,
            "timeout_read": 2.0,
            "timeout_total": 5.0,
        },
    )


async def test_basic_fetch(server: int) -> None:
    fetcher = Fetcher(_settings(server))
    try:
        out = await fetcher.fetch(f"http://127.0.0.1:{server}/")
        assert out.ok
        assert out.status == 200
        assert b"<h1>Hi</h1>" in (out.content or b"")
        assert out.charset == "utf-8"
    finally:
        await fetcher.aclose()


async def test_redirect_chain(server: int) -> None:
    fetcher = Fetcher(_settings(server))
    try:
        out = await fetcher.fetch(f"http://127.0.0.1:{server}/redirect")
        assert out.ok
        assert out.url_final.endswith("/target")
        assert any(u.endswith("/redirect") for u in out.redirect_chain)
    finally:
        await fetcher.aclose()


async def test_robots_block(server: int) -> None:
    fetcher = Fetcher(_settings(server))
    try:
        out = await fetcher.fetch(f"http://127.0.0.1:{server}/private")
        assert out.robots_blocked is True
        assert out.error_code == ErrorCode.E_ROBOTS_BLOCK
    finally:
        await fetcher.aclose()


async def test_one_connection_per_host(server: int) -> None:
    fetcher = Fetcher(_settings(server))
    try:
        urls = [f"http://127.0.0.1:{server}/conc?i={i}" for i in range(6)]
        results = await asyncio.gather(*(fetcher.fetch(u) for u in urls))
        assert all(r.ok for r in results)
        assert _conc_max == 1  # never more than one concurrent request to the host
    finally:
        await fetcher.aclose()
