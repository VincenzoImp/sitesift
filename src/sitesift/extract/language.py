"""Deterministic language detection (py3langid) — never the LLM.

Language is a solved problem that a library gets more right than a model and
for zero cost. The LLM must never guess it. We normalize to an ISO 639-1 code
and, when derivable from ``og:locale``/``hreflang``, an ISO 3166-1 region.
"""

from __future__ import annotations

import re
from typing import Any

from ..models import LanguageInfo

_MIN_TEXT = 200
_REGION = re.compile(r"[-_]([A-Za-z]{2})\b")

# Lazily-built normalized-probability identifier (one instance, thread-safe read).
_identifier: Any = None


def _get_identifier() -> Any:
    global _identifier
    if _identifier is None:
        from py3langid.langid import MODEL_FILE, LanguageIdentifier

        _identifier = LanguageIdentifier.from_pickled_model(MODEL_FILE, norm_probs=True)
    return _identifier


def detect_language(
    text: str,
    *,
    html_lang: str | None = None,
    hreflang: list[str] | None = None,
    og_locale: str | None = None,
) -> LanguageInfo:
    hreflang = hreflang or []
    base_langs = {_base(h) for h in hreflang if _base(h)}
    multilingual = len(base_langs) > 1
    region = _region_from(og_locale, hreflang)
    html_base = _base(html_lang)

    # Too little text to trust the classifier: fall back to the declared lang.
    if len(text) < _MIN_TEXT:
        return LanguageInfo(
            value=html_base,
            confidence=0.4 if html_base else 0.0,
            method="html_lang" if html_base else "unknown",
            html_lang=html_lang,
            multilingual=multilingual,
            region=region,
        )

    lang, prob = _get_identifier().classify(text[:3000])
    lang = str(lang)
    agreement = (lang == html_base) if html_base else None
    confidence = min(0.99, float(prob) + (0.1 if agreement else 0.0))
    return LanguageInfo(
        value=lang,
        confidence=round(confidence, 4),
        method="detected",
        html_lang=html_lang,
        agreement=agreement,
        multilingual=multilingual,
        region=region,
    )


def language_from_evidence(ev: Any) -> LanguageInfo:
    """Rebuild the language block from stored ``Evidence`` fields (for output)."""
    hreflang = list(ev.hreflang)
    base_langs = {_base(h) for h in hreflang if _base(h)}
    html_base = _base(ev.html_lang)
    if ev.detected_lang and ev.detected_lang_conf > 0:
        method = "detected"
        agreement = (ev.detected_lang == html_base) if html_base else None
    elif ev.detected_lang:
        method = "html_lang"
        agreement = None
    else:
        method = "unknown"
        agreement = None
    return LanguageInfo(
        value=ev.detected_lang,
        confidence=ev.detected_lang_conf,
        method=method,
        html_lang=ev.html_lang,
        agreement=agreement,
        multilingual=len(base_langs) > 1,
        region=_region_from(ev.og_locale, hreflang),
    )


def _base(code: str | None) -> str | None:
    if not code:
        return None
    return re.split(r"[-_]", code.strip(), maxsplit=1)[0].lower() or None


def _region_from(og_locale: str | None, hreflang: list[str]) -> str | None:
    for candidate in [og_locale, *hreflang]:
        if not candidate:
            continue
        m = _REGION.search(candidate)
        if m:
            return m.group(1).upper()
    return None
