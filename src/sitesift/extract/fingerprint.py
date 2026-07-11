"""Lightweight tech fingerprinting: CMS, e-commerce platform, ads, analytics.

Rules are our own (Wappalyzer is no longer open-source — we do not copy its
data). This is a deliberately small, high-precision starter set; it can move to
``data/fingerprints.yaml`` and grow without touching callers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .html import PageHtml

# (name, [substrings that, if any is present in the lowercased HTML, match])
_CMS: list[tuple[str, tuple[str, ...]]] = [
    ("WordPress", ("wp-content", "wp-includes", "/wp-json")),
    ("Drupal", ("/sites/default/files", "drupal-settings-json", "data-drupal")),
    ("Joomla", ("/media/jui/", "com_content", "joomla")),
    ("Ghost", ("ghost-", "content/images", 'name="generator" content="ghost')),
    ("Wix", ("wix.com", "wixstatic.com", "_wixcssimports")),
    ("Squarespace", ("squarespace.com", "static1.squarespace")),
    ("Webflow", ("webflow.js", "wf-active", "assets.website-files")),
]
_CMS_GENERATOR = {
    "wordpress": "WordPress",
    "drupal": "Drupal",
    "joomla": "Joomla",
    "ghost": "Ghost",
    "wix": "Wix",
    "squarespace": "Squarespace",
    "hugo": "Hugo",
    "jekyll": "Jekyll",
}

_ECOMMERCE: list[tuple[str, tuple[str, ...]]] = [
    ("Shopify", ("cdn.shopify.com", "shopify.theme", "myshopify.com")),
    ("WooCommerce", ("woocommerce", "wc-ajax", "wc_add_to_cart")),
    ("Magento", ("mage/", "/static/version", "magento")),
    ("PrestaShop", ("prestashop", "/modules/ps_")),
    ("BigCommerce", ("bigcommerce.com", "stencil-utils")),
]

_AD_NETWORKS: list[tuple[str, tuple[str, ...]]] = [
    ("adsense", ("adsbygoogle", "pagead2.googlesyndication")),
    ("gtag", ("googletagmanager.com", "gtag/js", "gtag(")),
    ("doubleclick", ("doubleclick.net", "googlesyndication")),
    ("taboola", ("taboola.com", "_taboola")),
    ("outbrain", ("outbrain.com", "ob-widget")),
    ("prebid", ("prebid.js", "pbjs")),
    ("criteo", ("criteo.com", "criteo_")),
]

_ANALYTICS: list[tuple[str, tuple[str, ...]]] = [
    ("google-analytics", ("google-analytics.com", "analytics.js", "ga(")),
    ("plausible", ("plausible.io",)),
    ("matomo", ("matomo.js", "piwik.js", "matomo.php")),
    ("hotjar", ("hotjar.com", "hj(")),
    ("segment", ("segment.com/analytics.js", "analytics.load(")),
]


@dataclass
class Fingerprint:
    cms: str | None = None
    ecommerce_platform: str | None = None
    ad_networks: list[str] = field(default_factory=list)
    analytics: list[str] = field(default_factory=list)


def fingerprint(html_lower: str, page: PageHtml) -> Fingerprint:
    out = Fingerprint()

    generator = (page.meta_generator or "").lower()
    for key, name in _CMS_GENERATOR.items():
        if key in generator:
            out.cms = name
            break
    if out.cms is None:
        out.cms = _first_match(html_lower, _CMS)

    out.ecommerce_platform = _first_match(html_lower, _ECOMMERCE)
    out.ad_networks = _all_matches(html_lower, _AD_NETWORKS)
    out.analytics = _all_matches(html_lower, _ANALYTICS)
    return out


def _first_match(blob: str, table: list[tuple[str, tuple[str, ...]]]) -> str | None:
    for name, needles in table:
        if any(n in blob for n in needles):
            return name
    return None


def _all_matches(blob: str, table: list[tuple[str, tuple[str, ...]]]) -> list[str]:
    return [name for name, needles in table if any(n in blob for n in needles)]
