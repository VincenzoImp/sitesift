"""Ollama provider — local models via the native ``/api/chat`` endpoint.

Used for cost-free development and CI, and for anyone who does not want to pay for
a hosted model. Structured output is requested via Ollama's ``format`` field (a
JSON schema); on a validation failure we make one repair call in plain-JSON mode.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import ValidationError

from ...errors import ClassifyError, ErrorCode
from .base import LLMResponse, LLMUsage, LLMVerdict

_SCHEMA = LLMVerdict.model_json_schema()


class OllamaClient:
    def __init__(self, base_url: str = "http://localhost:11434", *, timeout: float = 120.0) -> None:
        self._base = base_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def complete(self, *, system: str, user: str, model: str, cache: bool = True) -> LLMResponse:
        # Ollama keeps the model + context warm on its own; `cache` is a no-op.
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        data = self._chat(model, messages, fmt=_SCHEMA)
        content = data.get("message", {}).get("content", "")
        try:
            verdict = LLMVerdict.model_validate_json(content)
        except ValidationError as exc:
            verdict = self._repair(model, messages, content, exc)
        return LLMResponse(verdict=verdict, usage=_usage(model, data))

    def _repair(
        self, model: str, messages: list[dict[str, str]], bad: str, exc: ValidationError
    ) -> LLMVerdict:
        repair_messages = [
            *messages,
            {"role": "assistant", "content": bad},
            {
                "role": "user",
                "content": (
                    "That did not validate against the schema: "
                    f"{exc.errors()!r}. Return ONLY a corrected JSON object."
                ),
            },
        ]
        data = self._chat(model, repair_messages, fmt="json")
        content = data.get("message", {}).get("content", "")
        try:
            return LLMVerdict.model_validate_json(content)
        except ValidationError as exc2:
            raise ClassifyError(ErrorCode.E_LLM_INVALID, str(exc2)) from exc2

    def _chat(self, model: str, messages: list[dict[str, str]], *, fmt: Any) -> dict[str, Any]:
        payload = {
            "model": model,
            "stream": False,
            "format": fmt,
            "messages": messages,
            "options": {"temperature": 0, "num_ctx": 8192},
        }
        try:
            resp = self._client.post(f"{self._base}/api/chat", json=payload)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ClassifyError(ErrorCode.E_LLM_RATE, f"ollama {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise ClassifyError(ErrorCode.E_LLM_AUTH, f"ollama unreachable: {exc}") from exc
        result: dict[str, Any] = resp.json()
        return result


def _usage(model: str, data: dict[str, Any]) -> LLMUsage:
    return LLMUsage(
        model_id=model,
        tokens_in=int(data.get("prompt_eval_count", 0) or 0),
        tokens_out=int(data.get("eval_count", 0) or 0),
    )
