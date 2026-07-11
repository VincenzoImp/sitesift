"""LLM provider contract + the structured output schema the model must return.

The classifier has **no tools** — its only output is this constrained schema, so
the worst a hostile page can do is push a wrong category, never execution. The
model returns taxonomy *ids*; ``classify/validate.py`` resolves them to names and
enforces the tier hierarchy (the model will occasionally get that wrong).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, Field

from ...models import SiteType


class LLMTopic(BaseModel):
    """One topic path as the model emits it (ids only; names resolved later)."""

    tier1_id: str
    tier2_id: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)


class LLMVerdict(BaseModel):
    """The constrained structured output the model is forced to produce."""

    site_type: SiteType | None = None  # null == honest "unknown"
    # Required (no default): as a schema-required field the grammar forces the
    # model to emit a real value instead of silently defaulting to 0.
    site_type_confidence: float = Field(ge=0.0, le=1.0)
    topics: list[LLMTopic] = Field(default_factory=list, max_length=3)
    audience_geo: str | None = None  # ISO 3166-1 alpha-2 hint
    evidence: str = Field(default="", max_length=300)
    uncertain_because: str | None = None


@dataclass
class LLMUsage:
    model_id: str
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read: int = 0


@dataclass
class LLMResponse:
    verdict: LLMVerdict
    usage: LLMUsage


class LLMClient(Protocol):
    """A provider that turns (system, evidence) into a validated :class:`LLMVerdict`.

    Implementations must force structured output (native schema mode where
    available) and raise :class:`sitesift.errors.ClassifyError` on unrecoverable
    parse/API failures.
    """

    def complete(
        self, *, system: str, user: str, model: str, cache: bool = True
    ) -> LLMResponse: ...
