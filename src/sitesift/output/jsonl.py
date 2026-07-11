"""JSONL output — the streaming, append-only, resumable result sink.

One JSON object per line. The schema mirrors the spec (§10.3): ``flags`` /
``language`` / ``site`` / ``signals`` / ``provenance`` blocks.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..models import ClassifyMethod, Evidence, Flags, Verdict

SCHEMA_VERSION = "1.0"


def build_record(
    ev: Evidence,
    verdict: Verdict,
    method: ClassifyMethod,
    *,
    scope: str,
    taxonomy_version: str,
    rules_version: str,
    sitesift_version: str,
    model_id: str | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "url": ev.url_raw,
        "url_final": ev.url_final,
        "domain": ev.domain,
        "scope": scope,
        "fetched_at": ev.fetched_at.isoformat(),
        "http": {
            "status": ev.status,
            "redirects": len(ev.redirect_chain),
            "content_type": ev.content_type,
        },
        "flags": verdict.flags.model_dump(),
        "language": verdict.language.model_dump() if verdict.language else None,
        "site": {
            "site_type": verdict.site_type.value if verdict.site_type else "unknown",
            "site_type_confidence": verdict.site_type_confidence,
            "topics": [t.model_dump() for t in verdict.topics],
            "audience_geo": verdict.audience_geo,
            "method": method.value,
            "evidence": verdict.evidence,
            "uncertain_because": verdict.uncertain_because,
        },
        "signals": {
            "cms": ev.cms,
            "ecommerce_platform": ev.ecommerce_platform,
            "has_rss": bool(ev.feeds),
            "jsonld_types": ev.jsonld_types,
            "ad_networks": ev.ad_networks,
            "charset": ev.charset,
            "boilerplate_ratio": ev.boilerplate_ratio,
        },
        "provenance": {
            "sitesift_version": sitesift_version,
            "extractor_version": ev.extractor_version,
            "rules_version": rules_version,
            "taxonomy_version": taxonomy_version,
            "model_id": model_id,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "content_sha256": ev.content_sha256,
        },
    }


def build_error_record(
    *,
    url_raw: str,
    url_final: str,
    domain: str,
    scope: str,
    flags: Flags,
    error_code: str,
    sitesift_version: str,
) -> dict[str, Any]:
    """A record for a URL that never produced evidence (blocked/dead/non-HTML)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "url": url_raw,
        "url_final": url_final,
        "domain": domain,
        "scope": scope,
        "flags": flags.model_dump(),
        "language": None,
        "site": {"site_type": "unknown", "method": "rules", "evidence": error_code},
        "provenance": {"sitesift_version": sitesift_version, "error_code": error_code},
    }


class JsonlWriter:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8")

    def write(self, record: dict[str, Any]) -> None:
        self._fh.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> JsonlWriter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
