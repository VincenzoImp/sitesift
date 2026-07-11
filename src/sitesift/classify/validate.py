"""Validate + resolve the model's structured output into a final ``Verdict``.

The model returns taxonomy ids and will sometimes pair a Tier-2 with the wrong
Tier-1. We resolve ids to names, drop any Tier-1 the taxonomy doesn't know, and
drop a Tier-2 that isn't actually a descendant of its stated Tier-1 (keeping the
Tier-1). Never fabricate a "most likely" category — an invalid topic is dropped.
"""

from __future__ import annotations

from ..models import Flags, LanguageInfo, TopicPath, Verdict
from ..taxonomy.loader import Taxonomy
from .llm.base import LLMVerdict


def to_verdict(
    llm: LLMVerdict,
    taxonomy: Taxonomy,
    flags: Flags,
    language: LanguageInfo,
) -> Verdict:
    topics: list[TopicPath] = []
    seen: set[tuple[str, str | None]] = set()
    for topic in llm.topics:
        node1 = taxonomy.get(topic.tier1_id)
        if node1 is None or node1.tier != 1:
            continue  # unknown / non-Tier-1 id -> drop
        path = TopicPath(
            tier1_id=node1.id,
            tier1_name=node1.name,
            confidence=_clamp(topic.confidence),
        )
        if topic.tier2_id:
            node2 = taxonomy.get(topic.tier2_id)
            if node2 is not None and taxonomy.is_descendant(node2.id, node1.id):
                path.tier2_id = node2.id
                path.tier2_name = node2.name
            # else: the model mismatched the hierarchy -> keep Tier-1 only
        key = (path.tier1_id, path.tier2_id)
        if key in seen:
            continue  # drop duplicate topic paths
        seen.add(key)
        topics.append(path)

    return Verdict(
        flags=flags,
        site_type=llm.site_type,
        site_type_confidence=_clamp(llm.site_type_confidence),
        topics=topics[:3],
        language=language,
        audience_geo=llm.audience_geo,
        evidence=llm.evidence[:300],
        uncertain_because=llm.uncertain_because,
    )


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
