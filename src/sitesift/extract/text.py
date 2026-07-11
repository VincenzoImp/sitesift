"""Main-text extraction (trafilatura) and boilerplate ratio.

The one place we lean on trafilatura: denoised main text is what the LLM reads,
and the boilerplate ratio (how much of the page is chrome) is itself a signal.
"""

from __future__ import annotations

import trafilatura


def extract_main_text(html: str) -> str:
    """Return trafilatura's precision-favoring main text (empty string if none)."""
    if not html:
        return ""
    text = trafilatura.extract(
        html,
        favor_precision=True,
        include_comments=False,
        include_tables=False,
    )
    return text or ""


def boilerplate_ratio(main_len: int, all_len: int) -> float:
    """Fraction of visible text that is *not* main content (0.0–1.0)."""
    if all_len <= 0:
        return 0.0
    return round(max(0.0, 1.0 - (main_len / all_len)), 4)
