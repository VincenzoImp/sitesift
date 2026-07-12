"""SQLite frontier + results store — the durable state that makes resume free.

One file, WAL mode, transactional. Every URL state transition is persisted
before the next action runs, so a ``kill -9`` at any point leaves a consistent
frontier that ``--resume`` continues from.

The schema is intentionally close to the spec (§10.2), trimmed to what the MVP
uses; new columns/tables (batches, runs, sharding) bolt on without migration.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import zstandard

from ..models import Scope, UrlStatus

_SCHEMA_VERSION = "1"

# Columns set_status is allowed to update (guards the dynamic UPDATE against an
# unvetted column name reaching the SQL string).
_MUTABLE_COLUMNS = frozenset(
    {
        "last_error_code",
        "last_error_msg",
        "http_status",
        "content_sha256",
        "next_attempt_at",
        "fetched_at",
        "extracted_at",
        "classified_at",
        "skip_reason",
        "attempts",
    }
)

_CCTX = zstandard.ZstdCompressor(level=10)
_DCTX = zstandard.ZstdDecompressor()

_SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS domains (
  domain        TEXT PRIMARY KEY,
  status        TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  profiled_at   TEXT
);

CREATE TABLE IF NOT EXISTS urls (
  url_norm        TEXT PRIMARY KEY,
  url_raw         TEXT NOT NULL,
  host            TEXT NOT NULL,
  domain          TEXT NOT NULL,
  scope           TEXT NOT NULL,
  tags            TEXT,
  status          TEXT NOT NULL,
  attempts        INTEGER NOT NULL DEFAULT 0,
  next_attempt_at TEXT,
  last_error_code TEXT,
  last_error_msg  TEXT,
  http_status     INTEGER,
  content_sha256  TEXT,
  skip_reason     TEXT,
  fetched_at      TEXT,
  extracted_at    TEXT,
  classified_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_urls_status ON urls(status);
CREATE INDEX IF NOT EXISTS idx_urls_host   ON urls(host);

CREATE TABLE IF NOT EXISTS evidence (
  url_norm          TEXT PRIMARY KEY REFERENCES urls(url_norm),
  payload           BLOB NOT NULL,
  extractor_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS page_records (
  url_norm         TEXT PRIMARY KEY REFERENCES urls(url_norm),
  domain           TEXT NOT NULL,
  verdict          TEXT NOT NULL,
  method           TEXT NOT NULL,
  confidence       REAL NOT NULL,
  model_id         TEXT,
  prompt_sha256    TEXT,
  taxonomy_version TEXT NOT NULL,
  rules_version    TEXT NOT NULL,
  tokens_in        INTEGER,
  tokens_out       INTEGER,
  classified_at    TEXT NOT NULL
);
"""


@dataclass
class UrlRow:
    url_norm: str
    url_raw: str
    host: str
    domain: str
    scope: str
    status: str
    attempts: int


def _now() -> str:
    return datetime.now(UTC).isoformat()


class FrontierStore:
    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
            ("schema_version", _SCHEMA_VERSION),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> FrontierStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- frontier population ----------------------------------------------

    def add_url(
        self,
        *,
        url_norm: str,
        url_raw: str,
        host: str,
        domain: str,
        scope: Scope,
        tags: list[str] | None = None,
        skip_reason: str | None = None,
    ) -> bool:
        """Insert a URL if new. Returns True if inserted, False if already present."""
        status = UrlStatus.SKIPPED_FILTER if skip_reason else UrlStatus.PENDING
        now = _now()
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO domains(domain, status, first_seen_at) VALUES (?, ?, ?)",
                (domain, "pending", now),
            )
            cur = self._conn.execute(
                """INSERT OR IGNORE INTO urls
                   (url_norm, url_raw, host, domain, scope, tags, status, skip_reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    url_norm,
                    url_raw,
                    host,
                    domain,
                    str(scope),
                    json.dumps(tags or []),
                    str(status),
                    skip_reason,
                ),
            )
            return cur.rowcount > 0

    # --- state transitions -------------------------------------------------

    def set_status(self, url_norm: str, status: UrlStatus, **fields: object) -> None:
        cols = ["status = ?"]
        vals: list[object] = [str(status)]
        for key, value in fields.items():
            if key not in _MUTABLE_COLUMNS:  # never interpolate an unvetted column name
                raise ValueError(f"not an updatable column: {key!r}")
            cols.append(f"{key} = ?")
            vals.append(value)
        vals.append(url_norm)
        with self._conn:
            self._conn.execute(f"UPDATE urls SET {', '.join(cols)} WHERE url_norm = ?", vals)

    def bump_attempts(self, url_norm: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE urls SET attempts = attempts + 1 WHERE url_norm = ?", (url_norm,)
            )

    def requeue_stuck(self) -> int:
        """Reset URLs left in 'fetching' by a crash back to 'pending'. Returns count."""
        with self._conn:
            cur = self._conn.execute(
                "UPDATE urls SET status = ? WHERE status = ?",
                (str(UrlStatus.PENDING), str(UrlStatus.FETCHING)),
            )
        return cur.rowcount

    # --- evidence ----------------------------------------------------------

    def save_evidence(
        self,
        url_norm: str,
        evidence: dict[str, object],
        flags: dict[str, object],
        version: str,
    ) -> None:
        payload = {"evidence": evidence, "flags": flags}
        blob = _CCTX.compress(json.dumps(payload, default=str).encode("utf-8"))
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO evidence(url_norm, payload, extractor_version) "
                "VALUES (?, ?, ?)",
                (url_norm, blob, version),
            )
            self._conn.execute(
                "UPDATE urls SET status = ?, extracted_at = ? WHERE url_norm = ?",
                (str(UrlStatus.EXTRACTED), _now(), url_norm),
            )

    def load_evidence(self, url_norm: str) -> tuple[dict[str, object], dict[str, object]] | None:
        """Return the stored (evidence, flags) for a URL, or None."""
        row = self._conn.execute(
            "SELECT payload FROM evidence WHERE url_norm = ?", (url_norm,)
        ).fetchone()
        if row is None:
            return None
        data = json.loads(_DCTX.decompress(row["payload"]).decode("utf-8"))
        if not isinstance(data, dict) or "evidence" not in data:
            return None
        return data["evidence"], data.get("flags", {})

    # --- results -----------------------------------------------------------

    def save_page_record(
        self,
        *,
        url_norm: str,
        domain: str,
        verdict: dict[str, object],
        method: str,
        confidence: float,
        taxonomy_version: str,
        rules_version: str,
        model_id: str | None = None,
        prompt_sha256: str | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        needs_human: bool = False,
    ) -> None:
        now = _now()
        final_status = UrlStatus.NEEDS_HUMAN if needs_human else UrlStatus.CLASSIFIED
        with self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO page_records
                   (url_norm, domain, verdict, method, confidence, model_id, prompt_sha256,
                    taxonomy_version, rules_version, tokens_in, tokens_out, classified_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    url_norm,
                    domain,
                    json.dumps(verdict, default=str),
                    method,
                    confidence,
                    model_id,
                    prompt_sha256,
                    taxonomy_version,
                    rules_version,
                    tokens_in,
                    tokens_out,
                    now,
                ),
            )
            self._conn.execute(
                "UPDATE urls SET status = ?, classified_at = ? WHERE url_norm = ?",
                (str(final_status), now, url_norm),
            )

    # --- queries -----------------------------------------------------------

    def pending_urls(self, limit: int | None = None) -> list[UrlRow]:
        sql = "SELECT * FROM urls WHERE status = ? ORDER BY host"
        params: list[object] = [str(UrlStatus.PENDING)]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        return [self._to_row(r) for r in rows]

    def reclassifiable(self) -> list[UrlRow]:
        """URLs that have stored evidence (for re-classification without refetch)."""
        rows = self._conn.execute(
            "SELECT u.* FROM urls u JOIN evidence e ON e.url_norm = u.url_norm ORDER BY u.host"
        ).fetchall()
        return [self._to_row(r) for r in rows]

    def counts_by_status(self) -> Counter[str]:
        rows = self._conn.execute("SELECT status, COUNT(*) AS n FROM urls GROUP BY status")
        return Counter({r["status"]: r["n"] for r in rows})

    @staticmethod
    def _to_row(r: sqlite3.Row) -> UrlRow:
        return UrlRow(
            url_norm=r["url_norm"],
            url_raw=r["url_raw"],
            host=r["host"],
            domain=r["domain"],
            scope=r["scope"],
            status=r["status"],
            attempts=r["attempts"],
        )
