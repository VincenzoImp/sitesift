"""The classification ladder — the LLM is the decision engine on every URL.

The deterministic layer produces the facts; this decides. A blocking flag
(dead/parked/soft_404/non-HTML) short-circuits to ``blocked`` because there is no
content to judge — the one place no model runs. Otherwise the small model tries
first and the large model takes over only when the small one is not confident
enough (a deterministic cost gate, not a classification rule). An LLM failure
degrades gracefully. When ``llm`` is ``None`` (``mode = off``) the run extracts
facts but records ``blocked`` + ``needs_human`` instead of classifying.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Settings
from ..errors import ClassifyError
from ..extract.language import language_from_evidence
from ..models import ClassifyMethod, Evidence, Flags, LanguageInfo, Verdict
from .llm.engine import LLMClassifier, LLMOutcome


@dataclass
class ClassifyOutcome:
    verdict: Verdict
    method: ClassifyMethod
    confidence: float
    needs_human: bool = False
    model_id: str | None = None
    prompt_sha256: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None


class Ladder:
    def __init__(self, settings: Settings, llm: LLMClassifier | None = None) -> None:
        self._llm = llm
        c = settings.classify
        self._accept_small = c.accept_threshold_small
        self._accept_large = c.accept_threshold_large
        self._model_small = c.model_small
        self._model_large = c.model_large

    def classify(self, ev: Evidence, flags: Flags) -> ClassifyOutcome:
        language = language_from_evidence(ev)

        # Blocking flags: a non-content page. Nothing to classify, spend nothing.
        if flags.is_blocking:
            return _blocked(flags, language, _blocking_reason(flags), needs_human=False)

        # No model in the loop (mode=off): extract-only. Defer the judgment.
        if self._llm is None:
            return _blocked(flags, language, "classification disabled (mode=off)", needs_human=True)

        # Rung 1: small model.
        try:
            small = self._llm.classify(ev, flags, language, model=self._model_small)
        except ClassifyError:
            return _failed(flags, language)
        if _accepts(small.verdict, self._accept_small):
            return _llm_outcome(small, ClassifyMethod.LLM_SMALL)

        # Rung 2: large model with the same evidence.
        try:
            large = self._llm.classify(ev, flags, language, model=self._model_large)
        except ClassifyError:
            return _llm_outcome(small, ClassifyMethod.LLM_SMALL, needs_human=True)
        if _accepts(large.verdict, self._accept_large):
            return _llm_outcome(large, ClassifyMethod.LLM_LARGE)

        # Neither rung cleared its threshold — hand the (more capable) verdict to a human.
        return _llm_outcome(large, ClassifyMethod.LLM_LARGE, needs_human=True)


def _accepts(verdict: Verdict, threshold: float) -> bool:
    return verdict.site_type is not None and verdict.site_type_confidence >= threshold


def _blocked(
    flags: Flags, language: LanguageInfo, reason: str, *, needs_human: bool
) -> ClassifyOutcome:
    verdict = Verdict.unknown(flags, reason=reason)
    verdict.language = language
    return ClassifyOutcome(
        verdict, ClassifyMethod.BLOCKED, 0.0 if needs_human else 1.0, needs_human=needs_human
    )


def _failed(flags: Flags, language: LanguageInfo) -> ClassifyOutcome:
    verdict = Verdict.unknown(flags, reason="LLM classification failed")
    verdict.language = language
    return ClassifyOutcome(verdict, ClassifyMethod.FAILED, 0.0, needs_human=True)


def _llm_outcome(
    outcome: LLMOutcome, method: ClassifyMethod, *, needs_human: bool = False
) -> ClassifyOutcome:
    return ClassifyOutcome(
        verdict=outcome.verdict,
        method=method,
        confidence=outcome.verdict.site_type_confidence,
        needs_human=needs_human,
        model_id=outcome.usage.model_id,
        prompt_sha256=outcome.prompt_sha256,
        tokens_in=outcome.usage.tokens_in,
        tokens_out=outcome.usage.tokens_out,
    )


def _blocking_reason(flags: Flags) -> str:
    for name in ("dead", "parked", "soft_404", "non_html"):
        if getattr(flags, name):
            return f"blocking flag: {name}"
    return "blocking flag"
