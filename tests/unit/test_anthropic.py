"""AnthropicClient request shape, verified against a mocked SDK (no network/key)."""

from __future__ import annotations

import sys
import types

import pytest

from sitesift.classify.llm.base import LLMVerdict
from sitesift.models import SiteType

_CAPTURED: dict[str, object] = {}


class _Usage:
    input_tokens = 2380
    output_tokens = 190
    cache_read_input_tokens = 5000


class _Response:
    parsed_output = LLMVerdict(site_type=SiteType.NEWS_OUTLET, site_type_confidence=0.9)
    usage = _Usage()


class _Messages:
    def parse(self, **kwargs: object) -> _Response:
        _CAPTURED.clear()
        _CAPTURED.update(kwargs)
        return _Response()


class _Anthropic:
    def __init__(self, api_key: str | None = None) -> None:
        self.messages = _Messages()


def _fake_anthropic_module() -> types.ModuleType:
    mod = types.ModuleType("anthropic")
    mod.Anthropic = _Anthropic  # type: ignore[attr-defined]
    mod.APIError = type("APIError", (Exception,), {})  # type: ignore[attr-defined]
    mod.APIStatusError = type(  # type: ignore[attr-defined]
        "APIStatusError",
        (mod.APIError,),
        {},  # type: ignore[attr-defined]
    )
    return mod


@pytest.fixture()
def anthropic_client(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setitem(sys.modules, "anthropic", _fake_anthropic_module())
    from sitesift.classify.llm.anthropic import AnthropicClient

    return AnthropicClient(api_key="test-key")


def test_request_shape_large_model(anthropic_client) -> None:  # type: ignore[no-untyped-def]
    resp = anthropic_client.complete(
        system="SYS", user="<evidence>...</evidence>", model="claude-sonnet-5", cache=True
    )
    assert _CAPTURED["model"] == "claude-sonnet-5"
    assert _CAPTURED["output_format"] is LLMVerdict
    assert _CAPTURED["thinking"] == {"type": "disabled"}  # disabled on non-haiku
    system = _CAPTURED["system"]
    assert isinstance(system, list)
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    assert _CAPTURED["messages"] == [{"role": "user", "content": "<evidence>...</evidence>"}]

    assert resp.verdict.site_type is SiteType.NEWS_OUTLET
    assert resp.usage.tokens_in == 2380
    assert resp.usage.tokens_out == 190
    assert resp.usage.cache_read == 5000


def test_haiku_omits_thinking_and_cache_off(anthropic_client) -> None:  # type: ignore[no-untyped-def]
    anthropic_client.complete(system="SYS", user="U", model="claude-haiku-4-5", cache=False)
    assert "thinking" not in _CAPTURED  # small model runs without thinking config
    assert "cache_control" not in _CAPTURED["system"][0]
