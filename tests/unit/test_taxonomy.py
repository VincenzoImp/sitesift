"""Taxonomy loader tests."""

from __future__ import annotations

import pytest

from sitesift.taxonomy.loader import TaxonomyError, _parse, load_taxonomy


def test_default_taxonomy_loads() -> None:
    tax = load_taxonomy()
    assert tax.id == "sitesift-custom-1"
    assert len(tax.tier1()) == 26
    assert tax.get("sports.soccer") is not None
    assert tax.is_descendant("sports.soccer", "sports") is True
    assert tax.is_descendant("sports.soccer", "tech") is False
    assert tax.get("does.not.exist") is None
    assert {c.id for c in tax.children_of("sports")} >= {"sports.soccer", "sports.tennis"}


def test_prompt_lines_include_hints() -> None:
    tax = load_taxonomy()
    lines = tax.prompt_lines(max_tier=2)
    soccer = next(line for line in lines if line.startswith("sports.soccer "))
    assert "Sports > Soccer" in soccer
    assert "hints:" in soccer


def test_bad_parent_rejected() -> None:
    bad = """
id: t
version: "1"
nodes:
  - {id: "a", name: "A", tier: 1, parent: null}
  - {id: "b", name: "B", tier: 2, parent: "missing"}
"""
    with pytest.raises(TaxonomyError):
        _parse(bad)


def test_wrong_tier_rejected() -> None:
    bad = """
id: t
version: "1"
nodes:
  - {id: "a", name: "A", tier: 1, parent: null}
  - {id: "b", name: "B", tier: 3, parent: "a"}
"""
    with pytest.raises(TaxonomyError):
        _parse(bad)
