"""Core data models — the contract every other module depends on.

Three independent classification axes (see docs/taxonomy.md):

* ``Flags``        — status/quality booleans; *precede* the other two axes. If a
                     blocking flag (``dead``, ``parked``, ``soft_404``, ``non_html``)
                     is set, the other axes are unknown and no LLM token is spent.
* ``SiteType``     — closed 18-value enum: what the site *does* (primary function
                     of the homepage). ``unknown`` is the *absence* of a verdict,
                     represented by ``Verdict.site_type is None`` plus explanatory
                     flags — it is deliberately not an enum member.
* ``TopicPath``    — hierarchical: what the site is *about*.

Language is produced deterministically in phase 3 (never by the LLM).

Note on structured-output validation: the numeric/length bounds below
(``ge``/``le``/``max_length``) are **not** enforceable by the provider's JSON-schema
mode; the SDK strips them and validates client-side. We rely on Pydantic (via
``messages.parse()``) to enforce them, and add an explicit taxonomy-hierarchy
check in ``classify/validate.py`` on top.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# --- Caps for the prompt-facing evidence serialization ----------------------
# The model is the decision engine, so it receives *every* canonical fact. The
# only caps here are upper bounds that keep a pathological page from blowing up
# the token bill — generous enough that normal pages pass through whole.
PROMPT_TEXT_MAX_CHARS = 12000  # full de-boilerplated main text, hard upper bound
PROMPT_MAX_HEADINGS = 60
PROMPT_MAX_LIST_ITEMS = 40
PROMPT_META_DESCRIPTION_CHARS = 600


class Scope(StrEnum):
    """Which artifacts a URL should produce."""

    AUTO = "auto"
    SITE = "site"
    PAGE = "page"
    BOTH = "both"


class ClassifyMethod(StrEnum):
    """How a verdict was reached (recorded for provenance and metrics).

    Judgment is always the LLM's. ``blocked`` marks the one deterministic
    short-circuit: a non-content page (dead/parked/soft_404/non-HTML) or a run
    with classification disabled, where no model is worth spending.
    """

    BLOCKED = "blocked"
    LLM_SMALL = "llm_small"
    LLM_LARGE = "llm_large"
    FAILED = "failed_classify"


class UrlStatus(StrEnum):
    """URL state machine (see docs/architecture.md §state-machine)."""

    PENDING = "pending"
    FETCHING = "fetching"
    FETCHED = "fetched"
    FAILED_FETCH = "failed_fetch"
    DEAD = "dead"
    BLOCKED_ROBOTS = "blocked_robots"
    BLOCKED_ROBOTS_UNAVAILABLE = "blocked_robots_unavailable"
    SKIPPED_FILTER = "skipped_filter"
    SKIPPED_NONHTML = "skipped_nonhtml"
    SKIPPED_TOO_LARGE = "skipped_too_large"
    EXTRACTED = "extracted"
    CLASSIFIED = "classified"
    NEEDS_HUMAN = "needs_human"
    DONE = "done"


class DomainStatus(StrEnum):
    PENDING = "pending"
    PROFILED = "profiled"
    DEAD = "dead"
    BLOCKED_ROBOTS = "blocked_robots"


class SiteType(StrEnum):
    """Closed enum — the primary *function* of the homepage.

    Operational definitions live in docs/site_types.md; each must let two
    independent annotators agree. ``unknown`` is intentionally absent.
    """

    NEWS_OUTLET = "news_outlet"
    MAGAZINE = "magazine"
    BLOG_PERSONAL = "blog_personal"
    CORPORATE = "corporate"
    ECOMMERCE = "ecommerce"
    MARKETPLACE = "marketplace"
    FORUM_COMMUNITY = "forum_community"
    SOCIAL_PLATFORM = "social_platform"
    GOVERNMENT = "government"
    EDUCATION = "education"
    ACADEMIC_RESEARCH = "academic_research"
    REFERENCE_WIKI = "reference_wiki"
    SAAS_PRODUCT = "saas_product"
    PORTFOLIO = "portfolio"
    DIRECTORY_AGGREGATOR = "directory_aggregator"
    MEDIA_STREAMING = "media_streaming"
    GAMBLING_ADULT = "gambling_adult"
    OTHER = "other"


class Flags(BaseModel):
    """Axis 3 — independent status/quality booleans (always present)."""

    model_config = ConfigDict(extra="forbid")

    parked: bool = False
    dead: bool = False
    soft_404: bool = False
    login_wall: bool = False
    paywall: bool = False
    adult: bool = False
    gambling: bool = False
    spam_mfa: bool = False
    js_required: bool = False
    non_html: bool = False
    blocked_robots: bool = False
    injection_attempt: bool = False

    @property
    def is_blocking(self) -> bool:
        """True when the other axes cannot be trusted and no LLM should run."""
        return self.dead or self.parked or self.soft_404 or self.non_html


class LanguageInfo(BaseModel):
    """Deterministic language result (phase 3, never the LLM)."""

    model_config = ConfigDict(extra="forbid")

    value: str | None = None  # ISO 639-1 (or 639-3 fallback)
    region: str | None = None  # ISO 3166-1 alpha-2, if derivable
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    method: str = "unknown"  # detected | html_lang | unknown
    html_lang: str | None = None
    agreement: bool | None = None  # detected == html_lang
    multilingual: bool = False


class TopicPath(BaseModel):
    """Axis 2 — one hierarchical topic path with confidence."""

    model_config = ConfigDict(extra="forbid")

    tier1_id: str
    tier1_name: str
    tier2_id: str | None = None
    tier2_name: str | None = None
    tier3_id: str | None = None
    tier3_name: str | None = None
    tier4_id: str | None = None
    tier4_name: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)


class Verdict(BaseModel):
    """The structured classification result for a domain or a page."""

    model_config = ConfigDict(extra="forbid")

    flags: Flags = Field(default_factory=Flags)
    site_type: SiteType | None = None  # None == honest "unknown"
    site_type_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    topics: list[TopicPath] = Field(default_factory=list, max_length=3)
    language: LanguageInfo | None = None
    audience_geo: str | None = None  # ISO 3166-1 alpha-2 hint
    evidence: str = Field(default="", max_length=300)  # why it decided this
    uncertain_because: str | None = None

    @classmethod
    def unknown(cls, flags: Flags, *, reason: str = "") -> Verdict:
        """A short-circuit verdict (blocking flag or failed classification)."""
        return cls(flags=flags, site_type=None, uncertain_because=reason or None)


class Evidence(BaseModel):
    """The complete, deterministic input to the classifier (phase 3 output).

    This is the *only* thing the classifier sees. If a signal is not here, the
    model never sees it. Use :meth:`to_prompt_json` for the (truncated) LLM view
    and :meth:`model_dump` for the (complete) DB view.
    """

    model_config = ConfigDict(extra="forbid")

    # --- Identity ----------------------------------------------------------
    url_raw: str
    url_final: str
    domain: str  # eTLD+1
    host: str
    path_depth: int = 0
    redirect_chain: list[str] = Field(default_factory=list)
    cross_domain_redirect: bool = False

    # --- HTTP --------------------------------------------------------------
    status: int
    content_type: str = ""
    server: str | None = None
    fetched_at: datetime

    # --- HTML head ---------------------------------------------------------
    title: str | None = None
    meta_description: str | None = None
    meta_keywords: list[str] = Field(default_factory=list)
    meta_generator: str | None = None
    canonical: str | None = None
    og: dict[str, str] = Field(default_factory=dict)
    twitter_card: str | None = None

    # --- Structured data (strongest signal) --------------------------------
    jsonld_types: list[str] = Field(default_factory=list)
    jsonld_publisher: str | None = None
    jsonld_date_published: str | None = None
    microdata_types: list[str] = Field(default_factory=list)
    rdfa_types: list[str] = Field(default_factory=list)

    # --- Language ----------------------------------------------------------
    html_lang: str | None = None
    og_locale: str | None = None
    hreflang: list[str] = Field(default_factory=list)
    detected_lang: str | None = None
    detected_lang_conf: float = 0.0
    charset: str = "utf-8"
    charset_source: str = "unknown"

    # --- Page structure ----------------------------------------------------
    text_main: str = ""
    text_len_chars: int = 0
    n_links_internal: int = 0
    n_links_external: int = 0
    n_images: int = 0
    n_forms: int = 0
    n_scripts: int = 0
    has_search_form: bool = False
    headings: list[str] = Field(default_factory=list)

    # --- Type signals ------------------------------------------------------
    feeds: list[str] = Field(default_factory=list)
    cms: str | None = None
    ecommerce_platform: str | None = None
    price_patterns: int = 0
    has_cart_link: bool = False
    has_login_form: bool = False
    article_link_density: float = 0.0
    paywall_markers: list[str] = Field(default_factory=list)
    ad_networks: list[str] = Field(default_factory=list)
    analytics: list[str] = Field(default_factory=list)
    js_only: bool = False
    boilerplate_ratio: float = 0.0

    # --- Provenance --------------------------------------------------------
    extractor_version: str = "0"
    content_sha256: str = ""

    def to_prompt_json(
        self,
        *,
        text_max: int = PROMPT_TEXT_MAX_CHARS,
        max_headings: int = PROMPT_MAX_HEADINGS,
        max_list: int = PROMPT_MAX_LIST_ITEMS,
    ) -> dict[str, Any]:
        """The full canonical-fact view for the LLM.

        The model is the decision engine, so it gets *every* extracted fact —
        identity (incl. host and TLD), HTTP, all head/structured/language
        signals, page-structure counts, type signals, and the full
        de-boilerplated main text. Caps are upper bounds only, to keep a
        pathological page from exploding the token bill; normal pages pass
        through whole.
        """
        return {
            "url": self.url_final,
            "url_requested": self.url_raw,
            "domain": self.domain,
            "host": self.host,
            "tld": self.domain.split(".", 1)[1] if "." in self.domain else self.domain,
            "path_depth": self.path_depth,
            "redirects": self.redirect_chain[:max_list],
            "cross_domain_redirect": self.cross_domain_redirect,
            "http": {
                "status": self.status,
                "content_type": self.content_type,
                "server": self.server,
            },
            "title": self.title,
            "meta_description": _clip(self.meta_description, PROMPT_META_DESCRIPTION_CHARS),
            "meta_keywords": self.meta_keywords[:max_list],
            "meta_generator": self.meta_generator,
            "canonical": self.canonical,
            "og": self.og,
            "twitter_card": self.twitter_card,
            "jsonld_types": self.jsonld_types[:max_list],
            "jsonld_publisher": self.jsonld_publisher,
            "jsonld_date_published": self.jsonld_date_published,
            "microdata_types": self.microdata_types[:max_list],
            "rdfa_types": self.rdfa_types[:max_list],
            "html_lang": self.html_lang,
            "og_locale": self.og_locale,
            "hreflang": self.hreflang[:max_list],
            "detected_lang": self.detected_lang,
            "detected_lang_conf": round(self.detected_lang_conf, 3),
            "headings": [_clip(h, 120) for h in self.headings[:max_headings]],
            "text_main": _clip(self.text_main, text_max),
            "text_len_chars": self.text_len_chars,
            "links": {"internal": self.n_links_internal, "external": self.n_links_external},
            "n_forms": self.n_forms,
            "n_images": self.n_images,
            "n_scripts": self.n_scripts,
            "has_search_form": self.has_search_form,
            "has_login_form": self.has_login_form,
            "has_cart_link": self.has_cart_link,
            "price_patterns": self.price_patterns,
            "article_link_density": round(self.article_link_density, 3),
            "feeds": self.feeds[:max_list],
            "cms": self.cms,
            "ecommerce_platform": self.ecommerce_platform,
            "ad_networks": self.ad_networks[:max_list],
            "analytics": self.analytics[:max_list],
            "paywall_markers": self.paywall_markers[:max_list],
            "js_only": self.js_only,
            "boilerplate_ratio": round(self.boilerplate_ratio, 3),
        }


def _clip(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    return text if len(text) <= limit else text[:limit]
