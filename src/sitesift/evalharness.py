"""Offline-fixture eval harness for the LLM classifier.

Runs extraction over a golden set of frozen HTML fixtures and classifies each
through the ladder (the LLM is the decision engine), reporting site_type accuracy
and a per-method breakdown. Topic accuracy is reported only for golden entries
that carry an ``expected_topic_tier1`` label.

The golden set here is a small, unambiguous *synthetic* starter set — a real
hand-labeled set of live URLs (double-labeled, with Cohen's κ) is the next step
before trusting numbers on real traffic.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .classify.ladder import Ladder
from .extract.bundle import build_evidence

_FETCHED = datetime(2026, 1, 1, tzinfo=UTC)
_HEADERS = {"content-type": "text/html; charset=utf-8"}


@dataclass
class LadderReport:
    """Accuracy of the LLM ladder over the golden fixtures."""

    total: int = 0
    correct: int = 0
    topic_total: int = 0  # golden entries carrying an expected_topic_tier1 label
    topic_correct: int = 0
    by_method: Counter[str] = field(default_factory=Counter)
    correct_by_method: Counter[str] = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)

    @property
    def site_type_accuracy(self) -> float:
        return round(self.correct / self.total, 4) if self.total else 0.0

    @property
    def topic_accuracy(self) -> float | None:
        return round(self.topic_correct / self.topic_total, 4) if self.topic_total else None


def run_ladder_eval(
    *,
    ladder: Ladder,
    golden_path: str | Path = "eval/golden.jsonl",
    fixtures_dir: str | Path = "eval/fixtures",
) -> LadderReport:
    """Classify every golden fixture through the ladder and score it."""
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

        expected_topic = entry.get("expected_topic_tier1")
        if expected_topic:
            report.topic_total += 1
            if expected_topic in {t.tier1_id for t in outcome.verdict.topics}:
                report.topic_correct += 1

    return report


def format_ladder_report(report: LadderReport) -> str:
    lines = [
        "sitesift ladder eval (LLM)",
        f"  total:              {report.total}",
        f"  site_type_accuracy: {report.site_type_accuracy:.2f}",
    ]
    if report.topic_accuracy is not None:
        lines.append(
            f"  topic_accuracy:     {report.topic_accuracy:.2f}  "
            f"({report.topic_correct}/{report.topic_total} labeled)"
        )
    else:
        lines.append("  topic_accuracy:     n/a (no expected_topic_tier1 labels in golden set)")
    lines.append("  by method (correct/total):")
    for method, total in sorted(report.by_method.items()):
        lines.append(f"    {method:<14} {report.correct_by_method[method]}/{total}")
    if report.errors:
        lines.append("  misses:")
        lines.extend(f"    - {e}" for e in report.errors)
    return "\n".join(lines)
