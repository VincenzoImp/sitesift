"""Full pipeline end-to-end WITH the LLM ladder, against a local server + Ollama.

Serves a corporate page and checks the LLM classifies it end to end. Skipped when
Ollama is unreachable.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from sitesift.config import Settings
from sitesift.models import Scope
from sitesift.pipeline import run_pipeline

_URL = os.environ.get("SITESIFT_TEST_OLLAMA_URL", "http://localhost:11434")
_MODEL = os.environ.get("SITESIFT_TEST_OLLAMA_MODEL", "gemma4:12b")


def _reachable() -> bool:
    try:
        httpx.get(f"{_URL}/api/tags", timeout=3.0).raise_for_status()
        return True
    except (httpx.HTTPError, OSError):
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason=f"Ollama not reachable at {_URL}")

_CORP = (
    "<!doctype html><html lang='en'><head><title>Acme Industrial Solutions</title></head>"
    "<body><h1>About Acme</h1><p>"
    + (
        "Acme provides industrial automation solutions for manufacturing companies. "
        "Contact our team to learn about our products and services. " * 12
    )
    + "</p></body></html>"
).encode()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_a: object) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] == "/robots.txt":
            body, ctype = b"User-agent: *\nAllow: /\n", "text/plain"
        else:
            body, ctype = _CORP, "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture()
def server() -> Iterator[int]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield port
    finally:
        httpd.shutdown()


async def test_pipeline_with_llm(server: int, tmp_path: Path) -> None:
    out = tmp_path / "results.jsonl"
    settings = Settings(
        identity={"contact": "test@example.com"},
        security={"allow_private_ips": True},
        fetch={
            "allow_ports": [server, 80, 443],
            "min_host_delay": 0.0,
            "crawl_delay_clamp": [0.0, 5.0],
            "timeout_read": 5.0,
            "timeout_total": 8.0,
        },
        classify={
            "mode": "sync",
            "provider": "ollama",
            "base_url": _URL,
            "model_small": _MODEL,
            "model_large": _MODEL,
        },
    )
    stats = await run_pipeline(
        settings,
        [f"http://127.0.0.1:{server}/"],
        out_path=str(out),
        db_path=str(tmp_path / "state.db"),
        default_scope=Scope.AUTO,
    )
    assert stats.classified == 1

    record = json.loads(out.read_text().splitlines()[0])
    # Every content URL is decided by the LLM.
    assert record["site"]["method"] in ("llm_small", "llm_large")
    assert record["site"]["site_type"] == "corporate"
    assert record["provenance"]["model_id"] == _MODEL
    assert record["provenance"]["tokens_in"]
