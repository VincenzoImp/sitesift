"""Shared test fixtures.

The headline fixture is ``fake_classifier``: a factory that builds a real
:class:`LLMClassifier` (so the prompt-build + structured-output validation path is
exercised) backed by an in-memory, deterministic ``FakeLLMClient``. It lets
offline tests drive the whole LLM-driven pipeline without a network or a key.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from sitesift.classify.llm.base import LLMResponse, LLMUsage, LLMVerdict
from sitesift.classify.llm.engine import LLMClassifier
from sitesift.taxonomy.loader import load_taxonomy

FakeRule = tuple[str, LLMVerdict]


class FakeLLMClient:
    """In-memory LLMClient: returns a canned LLMVerdict by matching needles against
    the evidence user message (the first match wins). Deterministic, no network."""

    def __init__(self, rules: list[FakeRule], default: LLMVerdict) -> None:
        self._rules = rules
        self._default = default
        self.models: list[str] = []  # models actually called, for assertions

    def complete(self, *, system: str, user: str, model: str, cache: bool = True) -> LLMResponse:
        self.models.append(model)
        verdict = self._default
        for needle, canned in self._rules:
            if needle in user:
                verdict = canned
                break
        return LLMResponse(
            verdict=verdict, usage=LLMUsage(model_id=model, tokens_in=1, tokens_out=1)
        )

    def close(self) -> None:  # the engine calls close() if present
        pass


@pytest.fixture()
def fake_classifier() -> Callable[..., LLMClassifier]:
    """Factory: build an LLMClassifier backed by a FakeLLMClient for given rules."""
    taxonomy = load_taxonomy()

    def _build(rules: list[FakeRule], default: LLMVerdict | None = None) -> LLMClassifier:
        fallback = default or LLMVerdict(site_type=None, site_type_confidence=0.0)
        return LLMClassifier(FakeLLMClient(rules, fallback), taxonomy)

    return _build
