"""Exercise the LLM eval harness offline with a deterministic fake classifier.

No network, no key: a fake that echoes each golden URL's label drives the whole
extraction -> ladder -> scoring path, so the harness itself is tested in CI while
the real accuracy numbers come from `sitesift eval` against a live provider.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from sitesift.classify.ladder import Ladder
from sitesift.classify.llm.base import LLMVerdict
from sitesift.classify.llm.engine import LLMClassifier
from sitesift.config import Settings
from sitesift.evalharness import run_ladder_eval
from sitesift.models import SiteType

_ROOT = Path(__file__).resolve().parents[2]
_GOLDEN = _ROOT / "eval" / "golden.jsonl"
_FIXTURES = _ROOT / "eval" / "fixtures"


def _echo_rules() -> list[tuple[str, LLMVerdict]]:
    """One needle per golden entry: match the page's own `url` field, return its label."""
    rules: list[tuple[str, LLMVerdict]] = []
    for line in _GOLDEN.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        entry = json.loads(line)
        needle = f'"url": "{entry["url"]}"'
        verdict = LLMVerdict(
            site_type=SiteType(entry["expected_site_type"]), site_type_confidence=0.95
        )
        rules.append((needle, verdict))
    return rules


def test_ladder_eval_offline(fake_classifier: Callable[..., LLMClassifier]) -> None:
    ladder = Ladder(Settings(classify={"mode": "sync"}), fake_classifier(_echo_rules()))
    report = run_ladder_eval(ladder=ladder, golden_path=_GOLDEN, fixtures_dir=_FIXTURES)

    assert report.total == 10
    assert report.site_type_accuracy == 1.0
    assert report.by_method["llm_small"] == 10
    assert report.correct_by_method["llm_small"] == 10
    assert report.topic_accuracy is None  # no topic labels in the synthetic golden set
