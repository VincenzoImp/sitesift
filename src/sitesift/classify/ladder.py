"""The classification ladder: rules → LLM small → LLM large → needs_human.

Blocking flags short-circuit before any rung. Rules decide ``site_type`` at zero
cost; if they don't clear the acceptance threshold, the LLM rungs take over
(small first, escalating to large). An LLM failure degrades gracefully to the
best deterministic signal available. When ``llm`` is ``None`` the ladder is
rules-only (v0.1.0 behavior).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Settings
from ..errors import ClassifyError
from ..extract.language import language_from_evidence
from ..models import ClassifyMethod, Evidence, Flags, LanguageInfo, Verdict
from .llm.engine import LLMClassifier, LLMOutcome
from .rules import RuleEngine, RuleResult


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
    def __init__(
        self, rules: RuleEngine, settings: Settings, llm: LLMClassifier | None = None
    ) -> None:
        self._rules = rules
        self._llm = llm
        c = settings.classify
        self._accept_rules = c.accept_threshold_rules
        self._accept_small = c.accept_threshold_small
        self._accept_large = c.accept_threshold_large
        self._model_small = c.model_small
        self._model_large = c.model_large

    def classify(self, ev: Evidence, flags: Flags) -> ClassifyOutcome:
        language = language_from_evidence(ev)

        # Blocking flags: the other axes are unknown; spend nothing.
        if flags.is_blocking:
            verdict = Verdict.unknown(flags, reason=_blocking_reason(flags))
            verdict.language = language
            return ClassifyOutcome(verdict, ClassifyMethod.RULES, 1.0)

        rule = self._rules.evaluate(ev)
        if rule is not None and rule.confidence >= self._accept_rules:
            return ClassifyOutcome(
                _verdict_from_rule(rule, flags, language), ClassifyMethod.RULES, rule.confidence
            )

        if self._llm is None:
            return self._rules_only(rule, flags, language)

        # Rung 1: small model.
        try:
            small = self._llm.classify(ev, flags, language, model=self._model_small)
        except ClassifyError:
            return self._rules_only(rule, flags, language)
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

    def _rules_only(
        self, rule: RuleResult | None, flags: Flags, language: LanguageInfo
    ) -> ClassifyOutcome:
        if rule is not None:
            return ClassifyOutcome(
                _verdict_from_rule(rule, flags, language), ClassifyMethod.RULES, rule.confidence
            )
        verdict = Verdict.unknown(flags, reason="no rule matched")
        verdict.language = language
        return ClassifyOutcome(verdict, ClassifyMethod.RULES, 0.0, needs_human=True)


def _accepts(verdict: Verdict, threshold: float) -> bool:
    return verdict.site_type is not None and verdict.site_type_confidence >= threshold


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


def _verdict_from_rule(rule: RuleResult, flags: Flags, language: LanguageInfo) -> Verdict:
    return Verdict(
        flags=flags,
        site_type=rule.site_type,
        site_type_confidence=rule.confidence,
        evidence=rule.evidence[:300],
        language=language,
    )


def _blocking_reason(flags: Flags) -> str:
    for name in ("dead", "parked", "soft_404", "non_html"):
        if getattr(flags, name):
            return f"blocking flag: {name}"
    return "blocking flag"
