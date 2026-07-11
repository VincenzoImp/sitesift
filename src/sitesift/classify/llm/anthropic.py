"""Anthropic provider — Haiku 4.5 (small) and Sonnet 5 (large).

Uses native structured output (``messages.parse`` + the Pydantic schema) and puts
a cache breakpoint on the (large, stable) system prompt. Per-model quirks handled
here: ``temperature`` is omitted (rejected on Sonnet 5), and thinking is disabled
for the classifier (kept cheap/fast) on models that accept ``{"type":"disabled"}``.
"""

from __future__ import annotations

from typing import Any

from ...errors import ClassifyError, ErrorCode
from .base import LLMResponse, LLMUsage, LLMVerdict


class AnthropicClient:
    def __init__(self, api_key: str | None = None) -> None:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ClassifyError(
                ErrorCode.E_LLM_AUTH,
                "the 'anthropic' extra is not installed (pip install 'sitesift[anthropic]')",
            ) from exc
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    def complete(self, *, system: str, user: str, model: str, cache: bool = True) -> LLMResponse:
        system_blocks: list[dict[str, Any]] = [{"type": "text", "text": system}]
        if cache:
            system_blocks[0]["cache_control"] = {"type": "ephemeral"}

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": 1024,
            "system": system_blocks,
            "messages": [{"role": "user", "content": user}],
            "output_format": LLMVerdict,
        }
        # Disable thinking for the classifier where the model supports the flag
        # (newer models run adaptive thinking by default when omitted).
        if not model.startswith("claude-haiku"):
            kwargs["thinking"] = {"type": "disabled"}

        try:
            resp = self._client.messages.parse(**kwargs)
        except self._anthropic.APIStatusError as exc:  # pragma: no cover - needs network
            code = (
                ErrorCode.E_LLM_AUTH
                if exc.status_code in (401, 403)
                else ErrorCode.E_LLM_RATE
                if exc.status_code in (429, 529)
                else ErrorCode.E_LLM_INVALID
            )
            raise ClassifyError(code, f"anthropic {exc.status_code}") from exc
        except self._anthropic.APIError as exc:  # pragma: no cover - needs network
            raise ClassifyError(ErrorCode.E_LLM_AUTH, str(exc)) from exc

        verdict = resp.parsed_output
        if not isinstance(verdict, LLMVerdict):  # pragma: no cover - defensive
            raise ClassifyError(ErrorCode.E_LLM_INVALID, "no structured output returned")
        usage = LLMUsage(
            model_id=model,
            tokens_in=getattr(resp.usage, "input_tokens", 0) or 0,
            tokens_out=getattr(resp.usage, "output_tokens", 0) or 0,
            cache_read=getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
        )
        return LLMResponse(verdict=verdict, usage=usage)
