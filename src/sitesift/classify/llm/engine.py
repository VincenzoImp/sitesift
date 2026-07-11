"""LLMClassifier — binds a provider client to the prompt + taxonomy.

Builds the stable system prompt once (hashed for provenance), sends the evidence,
and resolves the model's structured output into a validated ``Verdict``.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...models import Evidence, Flags, LanguageInfo, Verdict
from ...taxonomy.loader import Taxonomy
from ..prompt import build_system_prompt, build_user_message, prompt_hash
from ..validate import to_verdict
from .base import LLMClient, LLMUsage


@dataclass
class LLMOutcome:
    verdict: Verdict
    usage: LLMUsage
    prompt_sha256: str


class LLMClassifier:
    def __init__(self, client: LLMClient, taxonomy: Taxonomy) -> None:
        self._client = client
        self._taxonomy = taxonomy
        self.system = build_system_prompt(taxonomy)
        self.prompt_sha256 = prompt_hash(self.system)

    def classify(
        self, ev: Evidence, flags: Flags, language: LanguageInfo, *, model: str, cache: bool = True
    ) -> LLMOutcome:
        user = build_user_message(ev.to_prompt_json())
        resp = self._client.complete(system=self.system, user=user, model=model, cache=cache)
        verdict = to_verdict(resp.verdict, self._taxonomy, flags, language)
        return LLMOutcome(verdict=verdict, usage=resp.usage, prompt_sha256=self.prompt_sha256)

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()
