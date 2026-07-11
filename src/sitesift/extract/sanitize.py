"""Anti-injection sanitization for text that ends up in the LLM prompt.

The page body is the only untrusted input to the whole system. Before it goes
into ``<evidence>…</evidence>`` we:

* drop Unicode control/format characters (bidi overrides, zero-width chars) that
  can hide instructions from a human reviewer;
* neutralize any literal ``</evidence>`` (or the opening tag) so page content
  cannot break out of the delimiter;
* collapse runs of whitespace so a wall of newlines can't push the real text out
  of the token budget.

``trafilatura`` already strips scripts/styles/comments from the *main text*; this
is the belt-and-suspenders pass on the final string.
"""

from __future__ import annotations

import re
import unicodedata

_WS = re.compile(r"[ \t\r\f\v]+")
_NL = re.compile(r"\n{3,}")
# Case-insensitive, tolerant of whitespace inside the tag.
_EVIDENCE_TAG = re.compile(r"</?\s*evidence\s*>", re.IGNORECASE)


def sanitize_text(text: str) -> str:
    """Return ``text`` safe to embed inside the evidence delimiter."""
    if not text:
        return ""
    cleaned = "".join(
        ch for ch in text if ch in ("\n", "\t") or not unicodedata.category(ch).startswith("C")
    )
    cleaned = _EVIDENCE_TAG.sub("[evidence-tag]", cleaned)
    cleaned = _WS.sub(" ", cleaned)
    cleaned = _NL.sub("\n\n", cleaned)
    return cleaned.strip()
