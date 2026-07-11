"""Gate the M3 acceptance targets in CI: rules coverage/precision on the golden set."""

from __future__ import annotations

from pathlib import Path

from sitesift.classify.ladder import Ladder
from sitesift.classify.rules import RuleEngine
from sitesift.config import Settings
from sitesift.evalharness import run_ladder_eval, run_rules_eval

_ROOT = Path(__file__).resolve().parents[2]
_GOLDEN = _ROOT / "eval" / "golden.jsonl"
_FIXTURES = _ROOT / "eval" / "fixtures"


def test_rules_meet_m3_targets() -> None:
    report = run_rules_eval(golden_path=_GOLDEN, fixtures_dir=_FIXTURES, threshold=0.90)
    assert report.total == 10
    assert report.rules_coverage >= 0.30, report.errors
    assert report.rules_precision >= 0.95, report.errors


def test_ladder_eval_rules_only() -> None:
    # Exercises the full-ladder eval machinery offline (no LLM): everything goes
    # through the rules rung, the two rule-uncovered types stay unknown.
    ladder = Ladder(RuleEngine.load(), Settings(classify={"mode": "off"}), None)
    report = run_ladder_eval(ladder=ladder, golden_path=_GOLDEN, fixtures_dir=_FIXTURES)
    assert report.total == 10
    assert report.by_method["rules"] == 10
    assert report.correct == 8  # corporate + blog have no rule -> unknown
    assert report.site_type_accuracy == 0.8
