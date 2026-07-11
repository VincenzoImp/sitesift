"""Assemble the ``Evidence`` bundle — the sole input to the classifier.

Pure and deterministic: given the same bytes + headers it always produces the
same evidence. Also computes the deterministic ``Flags`` (axis 3), which the
classifier uses to short-circuit before spending any LLM token.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from datetime import datetime
from urllib.parse import urlsplit

import tldextract
from selectolax.parser import HTMLParser

from ..models import Evidence, Flags
from .fingerprint import fingerprint
from .html import parse_html
from .language import detect_language
from .sanitize import sanitize_text
from .structured import extract_structured
from .text import boilerplate_ratio, extract_main_text

EXTRACTOR_VERSION = "1"

# Offline eTLD+1 extraction (bundled public-suffix snapshot; no network).
_TLD = tldextract.TLDExtract(suffix_list_urls=())

_NOTFOUND = re.compile(
    r"\b(404|not found|page not found|pagina non trovata|non trovata|"
    r"introuvable|seite nicht gefunden)\b",
    re.IGNORECASE,
)
_PARKED = re.compile(
    r"for sale|buy this domain|domain is for sale|dominio in vendita|"
    r"parked (domain|free)|this domain (is|may be) for sale",
    re.IGNORECASE,
)
_ADULT_TLDS = {"xxx", "porn", "adult", "sex", "sexy", "cam", "tube"}
_GAMBLING_TLDS = {"casino", "poker", "bet", "bingo", "spielbank"}


def build_evidence(
    *,
    content: bytes,
    url_raw: str,
    url_final: str,
    redirect_chain: list[str],
    status: int,
    headers: Mapping[str, str],
    fetched_at: datetime,
    charset: str = "utf-8",
    charset_source: str = "header",
) -> tuple[Evidence, Flags]:
    """Return the ``Evidence`` and the deterministic ``Flags`` for a fetched page."""
    html = content.decode(charset, errors="replace")
    tree = HTMLParser(html)

    split = urlsplit(url_final)
    host = split.hostname or ""
    ext = _TLD(url_final)
    domain = ext.top_domain_under_public_suffix or host
    path_depth = len([seg for seg in split.path.split("/") if seg])

    page = parse_html(tree, host=host, domain=domain, base_url=url_final)
    structured = extract_structured(tree)
    main_text = extract_main_text(html)
    language = detect_language(
        main_text or page.body_text,
        html_lang=page.html_lang,
        hreflang=page.hreflang,
        og_locale=page.og_locale,
    )
    fp = fingerprint(html.lower(), page)

    content_type = (
        (headers.get("content-type") or headers.get("Content-Type") or "")
        .split(";", 1)[0]
        .strip()
        .lower()
    )
    server = headers.get("server") or headers.get("Server")

    evidence = Evidence(
        url_raw=url_raw,
        url_final=url_final,
        domain=domain,
        host=host,
        path_depth=path_depth,
        redirect_chain=redirect_chain,
        cross_domain_redirect=_domain_of(url_raw) != domain,
        status=status,
        content_type=content_type,
        server=server,
        fetched_at=fetched_at,
        title=page.title,
        meta_description=page.meta_description,
        meta_keywords=page.meta_keywords,
        meta_generator=page.meta_generator,
        canonical=page.canonical,
        og=page.og,
        twitter_card=page.twitter_card,
        jsonld_types=structured.jsonld_types,
        jsonld_publisher=structured.jsonld_publisher,
        jsonld_date_published=structured.jsonld_date_published,
        microdata_types=structured.microdata_types,
        rdfa_types=structured.rdfa_types,
        html_lang=page.html_lang,
        og_locale=page.og_locale,
        hreflang=page.hreflang,
        detected_lang=language.value,
        detected_lang_conf=language.confidence,
        charset=charset,
        charset_source=charset_source,
        text_main=sanitize_text(main_text),
        text_len_chars=len(main_text),
        n_links_internal=page.n_links_internal,
        n_links_external=page.n_links_external,
        n_images=page.n_images,
        n_forms=page.n_forms,
        n_scripts=page.n_scripts,
        has_search_form=page.has_search_form,
        headings=page.headings,
        feeds=page.feeds,
        cms=fp.cms,
        ecommerce_platform=fp.ecommerce_platform,
        price_patterns=page.price_patterns,
        has_cart_link=page.has_cart_link,
        has_login_form=page.has_login_form,
        article_link_density=page.article_link_density,
        paywall_markers=page.paywall_markers,
        ad_networks=fp.ad_networks,
        analytics=fp.analytics,
        js_only=page.js_only,
        boilerplate_ratio=boilerplate_ratio(len(main_text), page.body_text_len),
        extractor_version=EXTRACTOR_VERSION,
        content_sha256=hashlib.sha256(content).hexdigest(),
    )

    flags = deterministic_flags(evidence, is_accessible_for_free=structured.is_accessible_for_free)
    return evidence, flags


def deterministic_flags(ev: Evidence, *, is_accessible_for_free: bool | None) -> Flags:
    """Compute the flags derivable without an LLM (axis 3)."""
    flags = Flags()

    ct = ev.content_type
    flags.non_html = bool(ct) and not (
        ct.startswith("text/html") or ct.startswith("application/xhtml")
    )

    title_and_h1 = " ".join([ev.title or "", *ev.headings[:1]])
    if ev.status == 200 and _NOTFOUND.search(title_and_h1):
        flags.soft_404 = True

    flags.js_required = ev.js_only
    flags.login_wall = ev.has_login_form and ev.text_len_chars < 800
    flags.paywall = (is_accessible_for_free is False) or (len(ev.paywall_markers) >= 2)
    flags.parked = (
        ev.text_len_chars < 500
        and ev.n_links_internal < 5
        and _PARKED.search(ev.text_main.lower()) is not None
    )

    tld = ev.host.rsplit(".", 1)[-1] if "." in ev.host else ""
    if tld in _ADULT_TLDS:
        flags.adult = True
    if tld in _GAMBLING_TLDS:
        flags.gambling = True

    return flags


def _domain_of(url: str) -> str:
    host = urlsplit(url).hostname or ""
    return _TLD(url).top_domain_under_public_suffix or host
