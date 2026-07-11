"""Structural HTML extraction with selectolax (head, links, forms, headings).

Pure and fast: no network, no LLM. Produces the raw structural signals the
evidence bundle and the rule engine consume.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from selectolax.parser import HTMLParser

_FEED_TYPES = {"application/rss+xml", "application/atom+xml"}
_SEARCH_NAMES = {"q", "query", "search", "s", "keyword"}
_CART_TOKENS = ("cart", "carrello", "basket", "checkout", "panier", "warenkorb", "cesta")
_PRICE = re.compile(r"(?:[€$£]|USD|EUR|GBP)\s?\d[\d.,]*|\d[\d.,]*\s?(?:[€$£]|USD|EUR|GBP)")
_ARTICLE_HREF = re.compile(
    r"/\d{4}/\d{2}/|/article/|/articolo/|/news/|/story/|/post/|/\d{4,}(?:/|$)"
)
_PAYWALL_TOKENS = (
    "subscribe",
    "subscription",
    "abbonati",
    "abbonamento",
    "paywall",
    "s'abonner",
    "premium content",
)
_HEADING_CLIP = 80
_MAX_HEADINGS = 15


@dataclass
class PageHtml:
    title: str | None = None
    meta_description: str | None = None
    meta_keywords: list[str] = field(default_factory=list)
    meta_generator: str | None = None
    canonical: str | None = None
    og: dict[str, str] = field(default_factory=dict)
    twitter_card: str | None = None
    html_lang: str | None = None
    og_locale: str | None = None
    hreflang: list[str] = field(default_factory=list)
    headings: list[str] = field(default_factory=list)
    feeds: list[str] = field(default_factory=list)
    n_links_internal: int = 0
    n_links_external: int = 0
    n_images: int = 0
    n_forms: int = 0
    n_scripts: int = 0
    has_search_form: bool = False
    has_login_form: bool = False
    has_cart_link: bool = False
    price_patterns: int = 0
    article_link_density: float = 0.0
    paywall_markers: list[str] = field(default_factory=list)
    body_text: str = ""
    body_text_len: int = 0
    js_only: bool = False


def parse_html(tree: HTMLParser, *, host: str, domain: str, base_url: str) -> PageHtml:
    out = PageHtml()

    html_node = tree.css_first("html")
    if html_node is not None:
        out.html_lang = html_node.attributes.get("lang")

    title = tree.css_first("title")
    if title is not None:
        out.title = _clean(title.text())

    for meta in tree.css("meta"):
        attrs = meta.attributes
        name = (attrs.get("name") or "").lower()
        prop = (attrs.get("property") or "").lower()
        content = attrs.get("content")
        if content is None:
            continue
        if name == "description":
            out.meta_description = _clean(content)
        elif name == "keywords":
            out.meta_keywords = [k.strip() for k in content.split(",") if k.strip()][:20]
        elif name == "generator":
            out.meta_generator = _clean(content)
        elif name == "twitter:card":
            out.twitter_card = _clean(content)
        elif prop.startswith("og:"):
            out.og[prop[3:]] = _clean(content) or ""
            if prop == "og:locale":
                out.og_locale = _clean(content)

    canonical = tree.css_first('link[rel="canonical"]')
    if canonical is not None:
        out.canonical = canonical.attributes.get("href")

    for link in tree.css("link[rel]"):
        rel = (link.attributes.get("rel") or "").lower()
        href = link.attributes.get("href")
        if "alternate" not in rel:
            continue
        ltype = (link.attributes.get("type") or "").lower()
        hreflang = link.attributes.get("hreflang")
        if href and ltype in _FEED_TYPES and href not in out.feeds:
            out.feeds.append(href)
        if hreflang and hreflang not in out.hreflang and len(out.hreflang) < 30:
            out.hreflang.append(hreflang)

    out.headings = [_clip(_clean(h.text()) or "", _HEADING_CLIP) for h in tree.css("h1, h2")][
        :_MAX_HEADINGS
    ]

    out.n_images = len(tree.css("img"))
    out.n_scripts = len(tree.css("script"))

    _count_links(tree, out, host=host, domain=domain, base_url=base_url)
    _analyze_forms(tree, out)

    body = tree.body
    out.body_text = body.text(separator=" ", strip=True) if body is not None else ""
    out.body_text_len = len(out.body_text)

    out.price_patterns = min(len(_PRICE.findall(out.body_text)), 999)
    low = out.body_text.lower()
    out.paywall_markers = [tok for tok in _PAYWALL_TOKENS if tok in low]

    out.js_only = (
        out.body_text_len < 200
        and out.n_scripts > 5
        and any(tree.css_first(sel) is not None for sel in ("#root", "#app", "#__next"))
    )

    return out


def _count_links(tree: HTMLParser, out: PageHtml, *, host: str, domain: str, base_url: str) -> None:
    internal = external = article = 0
    for a in tree.css("a[href]"):
        href = a.attributes.get("href") or ""
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        link_host = urlsplit(urljoin(base_url, href)).hostname or ""
        is_internal = (
            not link_host or link_host in (host, domain) or link_host.endswith("." + domain)
        )
        if is_internal:
            internal += 1
            path = urlsplit(urljoin(base_url, href)).path
            if _ARTICLE_HREF.search(path):
                article += 1
        else:
            external += 1
    out.n_links_internal = internal
    out.n_links_external = external
    total = internal + external
    out.article_link_density = round(article / total, 4) if total else 0.0


def _analyze_forms(tree: HTMLParser, out: PageHtml) -> None:
    forms = tree.css("form")
    out.n_forms = len(forms)
    for form in forms:
        if form.css_first('input[type="password"]') is not None:
            out.has_login_form = True
        role = (form.attributes.get("role") or "").lower()
        if role == "search" or form.css_first('input[type="search"]') is not None:
            out.has_search_form = True
        else:
            for inp in form.css("input[name]"):
                if (inp.attributes.get("name") or "").lower() in _SEARCH_NAMES:
                    out.has_search_form = True
                    break
    # Cart link: any anchor whose href or text mentions a cart token.
    for a in tree.css("a[href]"):
        blob = ((a.attributes.get("href") or "") + " " + (a.text() or "")).lower()
        if any(tok in blob for tok in _CART_TOKENS):
            out.has_cart_link = True
            break


def _clean(text: str | None) -> str | None:
    if text is None:
        return None
    collapsed = " ".join(text.split())
    return collapsed or None


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit]
