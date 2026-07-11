"""Live LLM ladder test against a real Ollama endpoint.

Skipped automatically when the endpoint is unreachable, so CI without a local
model still passes. Point it elsewhere with SITESIFT_TEST_OLLAMA_URL /
SITESIFT_TEST_OLLAMA_MODEL.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import httpx
import pytest

from sitesift.classify.llm.engine import LLMClassifier
from sitesift.classify.llm.ollama import OllamaClient
from sitesift.extract.bundle import build_evidence
from sitesift.extract.language import language_from_evidence
from sitesift.models import SiteType
from sitesift.taxonomy.loader import load_taxonomy

_URL = os.environ.get("SITESIFT_TEST_OLLAMA_URL", "http://100.86.142.99:11434")
_MODEL = os.environ.get("SITESIFT_TEST_OLLAMA_MODEL", "gemma4:12b")
_HEADERS = {"content-type": "text/html; charset=utf-8"}
_FETCHED = datetime(2026, 7, 10, tzinfo=UTC)


def _reachable() -> bool:
    try:
        httpx.get(f"{_URL}/api/tags", timeout=3.0).raise_for_status()
        return True
    except (httpx.HTTPError, OSError):
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason=f"Ollama not reachable at {_URL}")

_SHOP = (
    "<!doctype html><html lang='en'><head><title>RunFast Shoes</title></head>"
    "<body><h1>Running Shoes Store</h1><a href='/cart'>Cart</a><div>$89.99</div><p>"
    + ("Buy premium running shoes online. Add to cart and checkout securely. " * 20)
    + "</p></body></html>"
)
_BLOG = (
    "<!doctype html><html lang='en'><head><title>Jane's Journal</title></head>"
    "<body><h1>My thoughts on hiking</h1><p>"
    + ("Today I share my personal reflections on my weekend hiking trip in the mountains. " * 15)
    + "</p></body></html>"
)


def _classify(html: str) -> tuple[SiteType | None, float]:
    ev, flags = build_evidence(
        content=html.encode(),
        url_raw="https://x.test/",
        url_final="https://x.test/",
        redirect_chain=[],
        status=200,
        headers=_HEADERS,
        fetched_at=_FETCHED,
    )
    client = OllamaClient(_URL, timeout=120.0)
    try:
        clf = LLMClassifier(client, load_taxonomy())
        out = clf.classify(ev, flags, language_from_evidence(ev), model=_MODEL)
    finally:
        client.close()
    return out.verdict.site_type, out.verdict.site_type_confidence


def test_ecommerce_classified() -> None:
    site_type, conf = _classify(_SHOP)
    assert site_type is SiteType.ECOMMERCE
    assert conf > 0.0


def test_blog_classified_where_rules_cannot() -> None:
    # blog_personal has no rule — this exercises the LLM rung specifically.
    site_type, conf = _classify(_BLOG)
    assert site_type is SiteType.BLOG_PERSONAL
    assert conf > 0.0
