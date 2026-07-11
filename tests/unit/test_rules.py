"""Rule engine tests — high-precision site_type decisions."""

from __future__ import annotations

from datetime import UTC, datetime

from sitesift.classify.rules import RuleEngine
from sitesift.models import Evidence, SiteType

ENGINE = RuleEngine.load()
FETCHED = datetime(2026, 7, 10, tzinfo=UTC)


def _ev(**over: object) -> Evidence:
    base: dict[str, object] = {
        "url_raw": "https://x.test/",
        "url_final": "https://x.test/",
        "domain": "x.test",
        "host": "x.test",
        "status": 200,
        "fetched_at": FETCHED,
    }
    base.update(over)
    return Evidence(**base)  # type: ignore[arg-type]


def test_news_via_jsonld() -> None:
    res = ENGINE.evaluate(_ev(jsonld_types=["NewsArticle"], feeds=["https://x.test/feed"]))
    assert res is not None
    assert res.site_type is SiteType.NEWS_OUTLET
    assert res.confidence >= 0.9


def test_news_needs_feed() -> None:
    # NewsArticle without a feed does not fire the news rule.
    res = ENGINE.evaluate(_ev(jsonld_types=["NewsArticle"], feeds=[]))
    assert res is None or res.site_type is not SiteType.NEWS_OUTLET


def test_ecommerce_via_platform() -> None:
    res = ENGINE.evaluate(_ev(ecommerce_platform="Shopify", has_cart_link=True))
    assert res is not None
    assert res.site_type is SiteType.ECOMMERCE


def test_ecommerce_via_prices() -> None:
    res = ENGINE.evaluate(_ev(ecommerce_platform="WooCommerce", price_patterns=7))
    assert res is not None
    assert res.site_type is SiteType.ECOMMERCE


def test_gov_via_tld() -> None:
    res = ENGINE.evaluate(_ev(host="agency.gov"))
    assert res is not None
    assert res.site_type is SiteType.GOVERNMENT
    assert res.confidence >= 0.95


def test_wiki_via_host() -> None:
    res = ENGINE.evaluate(_ev(host="it.wikipedia.org"))
    assert res is not None
    assert res.site_type is SiteType.REFERENCE_WIKI


def test_no_match_returns_none() -> None:
    res = ENGINE.evaluate(_ev(host="random.example", title="Hello"))
    assert res is None
