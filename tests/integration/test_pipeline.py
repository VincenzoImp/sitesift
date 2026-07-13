"""End-to-end pipeline test with a deterministic, offline LLM (via fake_classifier).

Verifies fetch -> extract -> LLM classify -> JSONL, that robots blocks are
recorded, that a second run resumes (classifies nothing new), and that
reclassify re-runs judgment from stored evidence without touching the network.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from sitesift.classify.llm.base import LLMVerdict
from sitesift.classify.llm.engine import LLMClassifier
from sitesift.config import Settings
from sitesift.models import Scope, SiteType
from sitesift.pipeline import reclassify, run_pipeline

_SHOP = (
    b"<!doctype html><html lang='en'><head><title>Shop</title>"
    b"<script src='https://cdn.shopify.com/s/files/x.js'></script></head>"
    b"<body><h1>Sneakers</h1><a href='/cart'>Cart</a>"
    b"<div>Buy quality running shoes online with fast free shipping today.</div>"
    b"</body></html>"
)
_NEWS = (
    b"<!doctype html><html lang='en'><head><title>Daily News</title>"
    b"<link rel='alternate' type='application/rss+xml' href='/feed'>"
    b'<script type="application/ld+json">{"@type":"NewsArticle"}</script>'
    b"</head><body><h1>Headlines</h1><p>Today the world saw many important events unfold.</p>"
    b"</body></html>"
)

# Deterministic verdicts keyed on the page title present in the evidence JSON.
_RULES = [
    ("Daily News", LLMVerdict(site_type=SiteType.NEWS_OUTLET, site_type_confidence=0.95)),
    ("Shop", LLMVerdict(site_type=SiteType.ECOMMERCE, site_type_confidence=0.95)),
]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_a: object) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/robots.txt":
            self._send(b"User-agent: *\nDisallow: /blocked\n", "text/plain")
        elif path == "/news":
            self._send(_NEWS, "text/html; charset=utf-8")
        else:
            self._send(_SHOP, "text/html; charset=utf-8")

    def _send(self, body: bytes, ctype: str) -> None:
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


def _settings(port: int) -> Settings:
    return Settings(
        identity={"contact": "test@example.com"},
        security={"allow_private_ips": True},
        fetch={
            "allow_ports": [port, 80, 443],
            "min_host_delay": 0.0,
            "crawl_delay_clamp": [0.0, 5.0],
            "timeout_read": 3.0,
            "timeout_total": 5.0,
        },
    )


def _records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


async def test_pipeline_classifies_via_llm(
    server: int, tmp_path: Path, fake_classifier: Callable[..., LLMClassifier]
) -> None:
    out = tmp_path / "results.jsonl"
    db = tmp_path / "state.db"
    base = f"http://127.0.0.1:{server}"
    lines = [f"{base}/", f"{base}/news", f"{base}/blocked"]

    stats = await run_pipeline(
        _settings(server),
        lines,
        out_path=str(out),
        db_path=str(db),
        default_scope=Scope.AUTO,
        classifier=fake_classifier(_RULES),
    )
    assert stats.added == 3
    assert stats.classified == 2
    assert stats.errors == 1  # /blocked is disallowed by robots

    records = _records(out)
    by_type = {}
    for rec in records:
        by_type[rec["url"].rsplit("/", 1)[-1] or "root"] = rec

    assert by_type["root"]["site"]["site_type"] == "ecommerce"
    assert by_type["root"]["site"]["method"] == "llm_small"  # the LLM decided, not a rule
    assert by_type["news"]["site"]["site_type"] == "news_outlet"
    assert by_type["blocked"]["flags"]["blocked_robots"] is True

    # provenance records the model and content hash, and no longer any rules_version
    prov = by_type["root"]["provenance"]
    assert prov["model_id"] == "claude-haiku-4-5"
    assert prov["content_sha256"]
    assert "rules_version" not in prov


async def test_pipeline_resumes(
    server: int, tmp_path: Path, fake_classifier: Callable[..., LLMClassifier]
) -> None:
    out = tmp_path / "results.jsonl"
    db = tmp_path / "state.db"
    base = f"http://127.0.0.1:{server}"
    lines = [f"{base}/", f"{base}/news"]

    first = await run_pipeline(
        _settings(server),
        lines,
        out_path=str(out),
        db_path=str(db),
        default_scope=Scope.AUTO,
        classifier=fake_classifier(_RULES),
    )
    assert first.classified == 2

    # Second run: same DB, nothing pending -> no new work.
    second = await run_pipeline(
        _settings(server),
        lines,
        out_path=str(out),
        db_path=str(db),
        default_scope=Scope.AUTO,
        classifier=fake_classifier(_RULES),
    )
    assert second.added == 0
    assert second.classified == 0


async def test_reclassify_from_stored_evidence(
    server: int, tmp_path: Path, fake_classifier: Callable[..., LLMClassifier]
) -> None:
    db = tmp_path / "state.db"
    base = f"http://127.0.0.1:{server}"

    # First: fetch + classify, which stores evidence in the frontier.
    await run_pipeline(
        _settings(server),
        [f"{base}/", f"{base}/news"],
        out_path=str(tmp_path / "first.jsonl"),
        db_path=str(db),
        default_scope=Scope.AUTO,
        classifier=fake_classifier(_RULES),
    )

    # Then: re-classify from stored evidence with NO server access at all.
    out2 = tmp_path / "reclass.jsonl"
    stats = await reclassify(
        _settings(server),
        out_path=str(out2),
        db_path=str(db),
        classifier=fake_classifier(_RULES),
    )
    assert stats.classified == 2
    types = {r["site"]["site_type"] for r in _records(out2)}
    assert types == {"ecommerce", "news_outlet"}
