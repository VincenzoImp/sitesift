"""Structured-data extraction: JSON-LD, microdata, RDFa.

Structured data (especially JSON-LD ``@type``) is the single strongest signal
for ``site_type``, so it gets its own module. Real-world JSON-LD is frequently
invalid, so every ``json.loads`` is wrapped — a parse failure yields no types,
never an exception.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from selectolax.parser import HTMLParser


@dataclass
class StructuredData:
    jsonld_types: list[str] = field(default_factory=list)
    jsonld_publisher: str | None = None
    jsonld_date_published: str | None = None
    microdata_types: list[str] = field(default_factory=list)
    rdfa_types: list[str] = field(default_factory=list)
    is_accessible_for_free: bool | None = None


def extract_structured(tree: HTMLParser) -> StructuredData:
    out = StructuredData()

    for node in tree.css('script[type="application/ld+json"]'):
        raw = node.text() or ""
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue  # ~30% of real JSON-LD is invalid; ignore, never raise
        _walk_jsonld(data, out)

    # Microdata: itemtype URLs -> last path segment as the type name.
    for node in tree.css("[itemtype]"):
        itemtype = node.attributes.get("itemtype") or ""
        name = _schema_name(itemtype)
        if name and name not in out.microdata_types:
            out.microdata_types.append(name)

    # RDFa: typeof attribute.
    for node in tree.css("[typeof]"):
        for token in (node.attributes.get("typeof") or "").split():
            name = _schema_name(token)
            if name and name not in out.rdfa_types:
                out.rdfa_types.append(name)

    return out


def _walk_jsonld(data: Any, out: StructuredData) -> None:
    if isinstance(data, list):
        for item in data:
            _walk_jsonld(item, out)
        return
    if not isinstance(data, dict):
        return

    # @graph containers hold the real nodes.
    if "@graph" in data:
        _walk_jsonld(data["@graph"], out)

    for type_name in _as_types(data.get("@type")):
        if type_name not in out.jsonld_types:
            out.jsonld_types.append(type_name)

    if out.jsonld_publisher is None:
        publisher = data.get("publisher")
        if isinstance(publisher, dict):
            name = publisher.get("name")
            if isinstance(name, str):
                out.jsonld_publisher = name
        elif isinstance(publisher, str):
            out.jsonld_publisher = publisher

    if out.jsonld_date_published is None:
        date = data.get("datePublished")
        if isinstance(date, str):
            out.jsonld_date_published = date

    free = data.get("isAccessibleForFree")
    if free is not None and out.is_accessible_for_free is None:
        out.is_accessible_for_free = _as_bool(free)


def _as_types(value: Any) -> list[str]:
    if isinstance(value, str):
        return [_schema_name(value)]
    if isinstance(value, list):
        return [_schema_name(v) for v in value if isinstance(v, str)]
    return []


def _schema_name(value: str) -> str:
    # "https://schema.org/NewsArticle" -> "NewsArticle"; "schema:Thing" -> "Thing".
    value = value.strip().rstrip("/")
    for sep in ("/", "#", ":"):
        if sep in value:
            value = value.rsplit(sep, 1)[-1]
    return value


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "yes", "1"):
            return True
        if low in ("false", "no", "0"):
            return False
    return None
