"""Frontier store: evidence+flags roundtrip, status transitions, crash recovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from sitesift.frontier.store import FrontierStore
from sitesift.models import Scope, UrlStatus


def _store(tmp_path: Path) -> FrontierStore:
    return FrontierStore(tmp_path / "state.db")


def _add(store: FrontierStore, url: str = "https://x.it") -> None:
    store.add_url(url_norm=url, url_raw=url, host="x.it", domain="x.it", scope=Scope.AUTO, tags=[])


def test_add_url_is_idempotent(tmp_path: Path) -> None:
    with _store(tmp_path) as store:
        assert (
            store.add_url(
                url_norm="https://a.it",
                url_raw="https://a.it",
                host="a.it",
                domain="a.it",
                scope=Scope.AUTO,
            )
            is True
        )
        assert (
            store.add_url(
                url_norm="https://a.it",
                url_raw="https://a.it",
                host="a.it",
                domain="a.it",
                scope=Scope.AUTO,
            )
            is False
        )
        assert store.counts_by_status()["pending"] == 1


def test_evidence_and_flags_roundtrip(tmp_path: Path) -> None:
    with _store(tmp_path) as store:
        _add(store)
        store.save_evidence("https://x.it", {"domain": "x.it", "title": "Hi"}, {"dead": True}, "1")
        loaded = store.load_evidence("https://x.it")
        assert loaded is not None
        evidence, flags = loaded
        assert evidence["title"] == "Hi"
        assert flags["dead"] is True
        assert store.counts_by_status()["extracted"] == 1


def test_load_evidence_missing(tmp_path: Path) -> None:
    with _store(tmp_path) as store:
        assert store.load_evidence("https://nope.it") is None


def test_set_status_rejects_unknown_column(tmp_path: Path) -> None:
    with _store(tmp_path) as store:
        _add(store)
        # Valid column works…
        store.set_status("https://x.it", UrlStatus.FETCHED, http_status=200)
        # …an unvetted column name never reaches the SQL string.
        with pytest.raises(ValueError, match="not an updatable column"):
            store.set_status("https://x.it", UrlStatus.FETCHED, status_evil="x")


def test_requeue_stuck(tmp_path: Path) -> None:
    with _store(tmp_path) as store:
        _add(store)
        store.set_status("https://x.it", UrlStatus.FETCHING)
        assert store.counts_by_status()["fetching"] == 1
        assert store.requeue_stuck() == 1
        assert store.counts_by_status()["pending"] == 1
        assert "fetching" not in store.counts_by_status()


def test_requeue_status(tmp_path: Path) -> None:
    with _store(tmp_path) as store:
        _add(store)
        store.set_status(
            "https://x.it",
            UrlStatus.BLOCKED_ROBOTS_UNAVAILABLE,
            last_error_code="E_ROBOTS_UNAVAIL",
        )
        assert store.counts_by_status()["blocked_robots_unavailable"] == 1
        assert store.requeue_status(UrlStatus.BLOCKED_ROBOTS_UNAVAILABLE) == 1
        counts = store.counts_by_status()
        assert counts["pending"] == 1
        assert "blocked_robots_unavailable" not in counts


def test_reclassifiable_needs_evidence(tmp_path: Path) -> None:
    with _store(tmp_path) as store:
        _add(store, "https://withev.it")
        _add(store, "https://noev.it")
        store.save_evidence("https://withev.it", {"domain": "withev.it"}, {}, "1")
        rows = store.reclassifiable()
        assert [r.url_norm for r in rows] == ["https://withev.it"]
