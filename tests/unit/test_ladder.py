"""Ladder escalation tests with a mocked LLM client (offline, deterministic).

The LLM is the decision engine: every non-blocking page goes to the model, the
small rung first and the large rung only when the small one is not confident
enough. A blocking flag short-circuits to ``blocked`` with no model call.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sitesift.classify.ladder import Ladder
from sitesift.classify.llm.base import LLMResponse, LLMTopic, LLMUsage, LLMVerdict
from sitesift.classify.llm.engine import LLMClassifier
from sitesift.config import Settings
from sitesift.models import ClassifyMethod, Evidence, Flags, SiteType
from sitesift.taxonomy.loader import load_taxonomy

FETCHED = datetime(2026, 7, 10, tzinfo=UTC)
TAX = load_taxonomy()


class FakeClient:
    def __init__(self, *responses: object) -> None:
        self._responses = list(responses)
        self.calls: list[str] = []

    def complete(self, *, system: str, user: str, model: str, cache: bool = True) -> LLMResponse:
        self.calls.append(model)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        assert isinstance(item, LLMResponse)
        item.usage.model_id = model  # reflect the model actually called
        return item


def _resp(
    site_type: SiteType | None, conf: float, topics: list[LLMTopic] | None = None
) -> LLMResponse:
    return LLMResponse(
        verdict=LLMVerdict(site_type=site_type, site_type_confidence=conf, topics=topics or []),
        usage=LLMUsage(model_id="?", tokens_in=100, tokens_out=20),
    )


def _ev(**over: object) -> Evidence:
    base: dict[str, object] = {
        "url_raw": "https://x.test/",
        "url_final": "https://x.test/",
        "domain": "x.test",
        "host": "x.test",
        "status": 200,
        "fetched_at": FETCHED,
        "detected_lang": "en",
        "detected_lang_conf": 0.9,
    }
    base.update(over)
    return Evidence(**base)  # type: ignore[arg-type]


def _ladder(client: FakeClient) -> Ladder:
    settings = Settings(classify={"mode": "sync"})
    return Ladder(settings, LLMClassifier(client, TAX))


def test_blocking_flag_spends_nothing() -> None:
    client = FakeClient()  # no responses queued -> must not be called
    outcome = _ladder(client).classify(_ev(), Flags(dead=True))
    assert outcome.method is ClassifyMethod.BLOCKED
    assert outcome.verdict.site_type is None
    assert client.calls == []


def test_small_accepts() -> None:
    client = FakeClient(_resp(SiteType.BLOG_PERSONAL, 0.80))
    outcome = _ladder(client).classify(_ev(), Flags())
    assert outcome.method is ClassifyMethod.LLM_SMALL
    assert outcome.verdict.site_type is SiteType.BLOG_PERSONAL
    assert outcome.model_id == "claude-haiku-4-5"
    assert outcome.tokens_in == 100
    assert client.calls == ["claude-haiku-4-5"]


def test_escalates_to_large() -> None:
    client = FakeClient(_resp(SiteType.CORPORATE, 0.50), _resp(SiteType.CORPORATE, 0.70))
    outcome = _ladder(client).classify(_ev(), Flags())
    assert outcome.method is ClassifyMethod.LLM_LARGE
    assert outcome.needs_human is False
    assert client.calls == ["claude-haiku-4-5", "claude-sonnet-5"]


def test_both_low_needs_human() -> None:
    client = FakeClient(_resp(SiteType.OTHER, 0.40), _resp(SiteType.OTHER, 0.50))
    outcome = _ladder(client).classify(_ev(), Flags())
    assert outcome.method is ClassifyMethod.LLM_LARGE
    assert outcome.needs_human is True


def test_llm_error_needs_human() -> None:
    from sitesift.errors import ClassifyError, ErrorCode

    client = FakeClient(ClassifyError(ErrorCode.E_LLM_INVALID, "boom"))
    outcome = _ladder(client).classify(_ev(), Flags())
    assert outcome.method is ClassifyMethod.FAILED
    assert outcome.needs_human is True
    assert outcome.verdict.site_type is None


def test_no_model_blocks_and_defers() -> None:
    # mode=off: facts are extracted but nothing is classified.
    ladder = Ladder(Settings(classify={"mode": "off"}), None)
    outcome = ladder.classify(_ev(), Flags())
    assert outcome.method is ClassifyMethod.BLOCKED
    assert outcome.needs_human is True
    assert outcome.verdict.site_type is None


def test_topic_hierarchy_validated() -> None:
    # Valid Tier1 (sports) with a mismatched Tier2 (tech.ai) -> Tier2 dropped;
    # an unknown Tier1 -> whole topic dropped.
    topics = [
        LLMTopic(tier1_id="sports", tier2_id="tech.ai", confidence=0.9),
        LLMTopic(tier1_id="not_a_real_id", confidence=0.8),
    ]
    client = FakeClient(_resp(SiteType.NEWS_OUTLET, 0.90, topics))
    outcome = _ladder(client).classify(_ev(), Flags())
    assert len(outcome.verdict.topics) == 1
    topic = outcome.verdict.topics[0]
    assert topic.tier1_id == "sports"
    assert topic.tier2_id is None  # mismatched child dropped, Tier-1 kept
