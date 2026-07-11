"""Declarative rule engine (gradino 0) — decides ``site_type`` at zero cost.

Rules live in ``data/rules.yaml``, are evaluated in order, and the first match
wins. They set ``site_type`` (and a confidence + a human-readable reason), never
``topic`` — topic needs the LLM. Rules must be conservative: a rule whose
precision drops below ~0.95 on the eval set should have its confidence lowered
below the acceptance threshold or be removed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml

from ..models import Evidence, SiteType


@dataclass
class RuleResult:
    rule_id: str
    site_type: SiteType
    confidence: float
    evidence: str


class RuleEngine:
    def __init__(self, version: str, rules: list[dict[str, Any]]) -> None:
        self.version = version
        self._rules = rules

    @classmethod
    def load(cls, path: str = "") -> RuleEngine:
        if path:
            raw = Path(path).expanduser().read_text(encoding="utf-8")
        else:
            raw = (files("sitesift.data") / "rules.yaml").read_text(encoding="utf-8")
        data = yaml.safe_load(raw)
        return cls(version=str(data.get("version", "0")), rules=list(data.get("rules", [])))

    def evaluate(self, ev: Evidence) -> RuleResult | None:
        for rule in self._rules:
            if _eval_condition(rule["when"], ev):
                then = rule["then"]
                return RuleResult(
                    rule_id=str(rule["id"]),
                    site_type=SiteType(then["site_type"]),
                    confidence=float(then["confidence"]),
                    evidence=str(then.get("evidence", "")),
                )
        return None


def _eval_condition(cond: dict[str, Any], ev: Evidence) -> bool:
    if "all" in cond:
        return all(_eval_condition(c, ev) for c in cond["all"])
    if "any" in cond:
        return any(_eval_condition(c, ev) for c in cond["any"])
    if "not" in cond:
        return not _eval_condition(cond["not"], ev)

    field = cond["field"]
    value = getattr(ev, field, None)
    for op, arg in cond.items():
        if op == "field":
            continue
        if not _apply(op, value, arg):
            return False
    return True


def _apply(op: str, value: Any, arg: Any) -> bool:  # noqa: PLR0911 - flat dispatch is clearest
    if op == "eq":
        return bool(value == arg)
    if op == "ne":
        return bool(value != arg)
    if op == "gte":
        return _num(value) >= arg
    if op == "lte":
        return _num(value) <= arg
    if op == "gt":
        return _num(value) > arg
    if op == "lt":
        return _num(value) < arg
    if op == "is_not_null":
        return (value is not None) == bool(arg)
    if op == "is_null":
        return (value is None) == bool(arg)
    if op == "min_len":
        return len(value or []) >= arg
    if op == "max_len":
        return len(value or []) <= arg
    if op == "contains_any":
        haystack = set(value or [])
        return bool(haystack & set(arg))
    if op == "contains":
        return arg in (value or ("" if isinstance(value, str) else []))
    if op == "in_list":
        return value in arg
    if op == "ends_with":
        return isinstance(value, str) and any(value.endswith(s) for s in _as_list(arg))
    if op == "starts_with":
        return isinstance(value, str) and any(value.startswith(s) for s in _as_list(arg))
    if op == "regex":
        return isinstance(value, str) and re.search(arg, value) is not None
    raise ValueError(f"unknown rule operator: {op}")


def _num(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def _as_list(arg: Any) -> list[Any]:
    return arg if isinstance(arg, list) else [arg]
