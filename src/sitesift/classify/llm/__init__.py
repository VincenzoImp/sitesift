"""LLM providers + factory.

``build_classifier(settings, taxonomy)`` returns an :class:`LLMClassifier` for the
configured provider, or ``None`` when ``classify.mode == "off"`` (extract-only:
facts are collected but no classification runs).
"""

from __future__ import annotations

from ...config import Settings
from ...errors import ConfigError
from ...taxonomy.loader import Taxonomy
from .base import LLMClient
from .engine import LLMClassifier

__all__ = ["LLMClassifier", "build_classifier", "build_client"]

_OLLAMA_DEFAULT = "http://localhost:11434"


def build_client(settings: Settings) -> LLMClient:
    provider = settings.classify.provider
    if provider == "ollama":
        from .ollama import OllamaClient

        return OllamaClient(settings.classify.base_url or _OLLAMA_DEFAULT)
    if provider == "anthropic":
        from .anthropic import AnthropicClient

        return AnthropicClient()
    raise ConfigError(f"unknown LLM provider: {provider!r} (use 'anthropic' or 'ollama')")


def build_classifier(settings: Settings, taxonomy: Taxonomy) -> LLMClassifier | None:
    if settings.classify.mode == "off":
        return None
    return LLMClassifier(build_client(settings), taxonomy)
