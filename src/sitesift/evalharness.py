"""Offline eval harness for the rule engine.

Runs extraction + rules over a golden set of frozen HTML fixtures (no network,
no LLM) and reports the metrics that gate M3: ``rules_coverage`` and
``rules_precision`` (plus overall ``site_type_accuracy``). The golden set here is
a small, unambiguous *synthetic* starter set — a real hand-labeled set of live
URLs (with double-labeling + Cohen's κ) is the next step before trusting numbers
on real traffic.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .classify.ladder import Ladder
from .classify.rules import RuleEngine
from .extract.bundle import build_evidence

_FETCHED = datetime(2026, 1, 1, tzinfo=UTC)
_HEADERS = {"content-type": "text/html; charset=utf-8"}


@dataclass
class EvalReport:
    total: int = 0
    accepted: int = 0
    correct_accepted: int = 0
    correct_overall: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def rules_coverage(self) -> float:
        return round(self.accepted / self.total, 4) if self.total else 0.0

    @property
    def rules_precision(self) -> float:
        return round(self.correct_accepted / self.accepted, 4) if self.accepted else 0.0

    @property
    def site_type_accuracy(self) -> float:
        return round(self.correct_overall / self.total, 4) if self.total else 0.0


def run_rules_eval(
    *,
    golden_path: str | Path = "eval/golden.jsonl",
    fixtures_dir: str | Path = "eval/fixtures",
    threshold: float = 0.90,
) -> EvalReport:
    engine = RuleEngine.load()
    golden = Path(golden_path)
    fixtures = Path(fixtures_dir)
    report = EvalReport()

    for line in golden.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        entry = json.loads(line)
        html = (fixtures / entry["fixture"]).read_bytes()
        ev, _flags = build_evidence(
            content=html,
            url_raw=entry["url"],
            url_final=entry["url"],
            redirect_chain=[],
            status=200,
            headers=_HEADERS,
            fetched_at=_FETCHED,
        )
        result = engine.evaluate(ev)
        predicted = result.site_type.value if result and result.confidence >= threshold else None
        expected = entry["expected_site_type"]

        report.total += 1
        if predicted is not None:
            report.accepted += 1
            if predicted == expected:
                report.correct_accepted += 1
        if predicted == expected:
            report.correct_overall += 1
        else:
            report.errors.append(
                f"{entry['name']}: expected {expected}, got {predicted or 'unknown'}"
            )

    return report


def format_report(report: EvalReport) -> str:
    lines = [
        "sitesift rules eval",
        f"  total:              {report.total}",
        f"  rules_coverage:     {report.rules_coverage:.2f}  (target >= 0.30)",
        f"  rules_precision:    {report.rules_precision:.2f}  (target >= 0.95)",
        f"  site_type_accuracy: {report.site_type_accuracy:.2f}",
    ]
    if report.errors:
        lines.append("  misses / non-covered:")
        lines.extend(f"    - {e}" for e in report.errors)
    return "\n".join(lines)


@dataclass
class LadderReport:
    """Full-ladder eval (rules + LLM): overall accuracy + per-method breakdown."""

    total: int = 0
    correct: int = 0
    by_method: Counter[str] = field(default_factory=Counter)
    correct_by_method: Counter[str] = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)

    @property
    def site_type_accuracy(self) -> float:
        return round(self.correct / self.total, 4) if self.total else 0.0


def run_ladder_eval(
    *,
    ladder: Ladder,
    golden_path: str | Path = "eval/golden.jsonl",
    fixtures_dir: str | Path = "eval/fixtures",
) -> LadderReport:
    """Classify every golden fixture through the full ladder and score site_type."""
    golden = Path(golden_path)
    fixtures = Path(fixtures_dir)
    report = LadderReport()

    for line in golden.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        entry = json.loads(line)
        html = (fixtures / entry["fixture"]).read_bytes()
        ev, flags = build_evidence(
            content=html,
            url_raw=entry["url"],
            url_final=entry["url"],
            redirect_chain=[],
            status=200,
            headers=_HEADERS,
            fetched_at=_FETCHED,
        )
        outcome = ladder.classify(ev, flags)
        predicted = outcome.verdict.site_type.value if outcome.verdict.site_type else None
        expected = entry["expected_site_type"]

        report.total += 1
        report.by_method[outcome.method.value] += 1
        if predicted == expected:
            report.correct += 1
            report.correct_by_method[outcome.method.value] += 1
        else:
            report.errors.append(
                f"{entry['name']}: expected {expected}, got {predicted or 'unknown'} "
                f"[{outcome.method.value}]"
            )

    return report


def format_ladder_report(report: LadderReport) -> str:
    lines = [
        "sitesift full-ladder eval",
        f"  total:              {report.total}",
        f"  site_type_accuracy: {report.site_type_accuracy:.2f}",
        "  by method (correct/total):",
    ]
    for method, total in sorted(report.by_method.items()):
        lines.append(f"    {method:<12} {report.correct_by_method[method]}/{total}")
    if report.errors:
        lines.append("  misses:")
        lines.extend(f"    - {e}" for e in report.errors)
    return "\n".join(lines)


def main() -> None:  # pragma: no cover - thin CLI entry
    report = run_rules_eval()
    print(format_report(report))


if __name__ == "__main__":  # pragma: no cover
    main()
