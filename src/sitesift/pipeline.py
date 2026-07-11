"""End-to-end orchestrator: normalize → fetch → extract → classify → output.

For the MVP this runs in-process with bounded concurrency; the Fetcher enforces
per-host politeness so the run stays polite regardless of ordering. State is
persisted to the frontier so ``--resume`` skips already-classified URLs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlsplit

import anyio
import tldextract

from . import __version__
from .classify.ladder import Ladder
from .classify.llm import build_classifier
from .classify.rules import RuleEngine
from .config import Settings
from .errors import ErrorCode
from .extract.bundle import build_evidence
from .frontier.filters import prefilter
from .frontier.normalize import NormalizationError, normalize_url
from .frontier.store import FrontierStore, UrlRow
from .models import Flags, Scope, UrlStatus
from .net.fetcher import Fetcher, FetchOutcome
from .output.jsonl import JsonlWriter, build_error_record, build_record
from .taxonomy.loader import load_taxonomy

_TLD = tldextract.TLDExtract(suffix_list_urls=())

_ERROR_STATUS: dict[ErrorCode, UrlStatus] = {
    ErrorCode.E_ROBOTS_BLOCK: UrlStatus.BLOCKED_ROBOTS,
    ErrorCode.E_ROBOTS_UNAVAIL: UrlStatus.BLOCKED_ROBOTS_UNAVAILABLE,
    ErrorCode.E_NONHTML: UrlStatus.SKIPPED_NONHTML,
    ErrorCode.E_TOO_LARGE: UrlStatus.SKIPPED_TOO_LARGE,
    ErrorCode.E_BOMB: UrlStatus.SKIPPED_TOO_LARGE,
}


@dataclass
class PipelineStats:
    added: int = 0
    classified: int = 0
    needs_human: int = 0
    errors: int = 0
    skipped: int = 0
    by_error: dict[str, int] = field(default_factory=dict)


def _now() -> datetime:
    return datetime.now(UTC)


def parse_input(lines: list[str], default_scope: Scope) -> list[tuple[str, Scope, list[str]]]:
    """Parse plain-text or JSONL input lines into (url_raw, scope, tags)."""
    out: list[tuple[str, Scope, list[str]]] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("{"):
            try:
                obj = json.loads(stripped)
            except ValueError:
                continue
            url = obj.get("url")
            if not url:
                continue
            scope = Scope(obj["scope"]) if obj.get("scope") else default_scope
            tags = [str(t) for t in obj.get("tags", [])]
            out.append((str(url), scope, tags))
        else:
            out.append((stripped, default_scope, []))
    return out


async def run_pipeline(
    settings: Settings,
    lines: list[str],
    *,
    out_path: str,
    db_path: str,
    default_scope: Scope = Scope.AUTO,
) -> PipelineStats:
    store = FrontierStore(db_path)
    rules = RuleEngine.load()
    taxonomy = load_taxonomy(taxonomy_id=settings.taxonomy.id, path=settings.taxonomy.path)
    tax_version = taxonomy.id
    llm = build_classifier(settings, taxonomy)
    ladder = Ladder(rules, settings, llm)
    stats = PipelineStats()

    # --- populate frontier ---
    for url_raw, scope, tags in parse_input(lines, default_scope):
        try:
            url_norm = normalize_url(url_raw, default_scheme="https")
        except NormalizationError:
            stats.skipped += 1
            continue
        reason = prefilter(url_norm, allow_ip_hosts=settings.security.allow_private_ips)
        host = urlsplit(url_norm).hostname or ""
        domain = _TLD(url_norm).top_domain_under_public_suffix or host
        added = store.add_url(
            url_norm=url_norm,
            url_raw=url_raw,
            host=host,
            domain=domain,
            scope=scope,
            tags=tags,
            skip_reason=reason,
        )
        if added:
            stats.added += 1
            if reason:
                stats.skipped += 1

    # --- process pending ---
    fetcher = Fetcher(settings)
    writer = JsonlWriter(out_path)
    sem = anyio.Semaphore(max(1, settings.fetch.max_concurrency))
    pending = store.pending_urls()

    async def worker(row: UrlRow) -> None:
        async with sem:
            await _process(
                row, store, fetcher, ladder, writer, settings, tax_version, rules.version, stats
            )

    try:
        async with anyio.create_task_group() as tg:
            for row in pending:
                tg.start_soon(worker, row)
    finally:
        await fetcher.aclose()
        writer.close()
        store.close()
        if llm is not None:
            llm.close()

    return stats


async def _process(
    row: UrlRow,
    store: FrontierStore,
    fetcher: Fetcher,
    ladder: Ladder,
    writer: JsonlWriter,
    settings: Settings,
    tax_version: str,
    rules_version: str,
    stats: PipelineStats,
) -> None:
    store.set_status(row.url_norm, UrlStatus.FETCHING)
    store.bump_attempts(row.url_norm)
    out = await fetcher.fetch(row.url_norm)

    if out.ok and out.content is not None:
        ev, flags = build_evidence(
            content=out.content,
            url_raw=row.url_raw,
            url_final=out.url_final,
            redirect_chain=out.redirect_chain,
            status=out.status,
            headers=out.headers,
            fetched_at=_now(),
            charset=out.charset,
            charset_source=out.charset_source,
        )
        store.save_evidence(row.url_norm, ev.model_dump(), ev.extractor_version)
        # The LLM client is synchronous HTTP; run classify off the event loop.
        outcome = await anyio.to_thread.run_sync(ladder.classify, ev, flags)
        store.save_page_record(
            url_norm=row.url_norm,
            domain=ev.domain,
            verdict=outcome.verdict.model_dump(),
            method=outcome.method.value,
            confidence=outcome.confidence,
            taxonomy_version=tax_version,
            rules_version=rules_version,
            model_id=outcome.model_id,
            prompt_sha256=outcome.prompt_sha256,
            tokens_in=outcome.tokens_in,
            tokens_out=outcome.tokens_out,
            needs_human=outcome.needs_human,
        )
        writer.write(
            build_record(
                ev,
                outcome.verdict,
                outcome.method,
                scope=row.scope,
                taxonomy_version=tax_version,
                rules_version=rules_version,
                sitesift_version=__version__,
                model_id=outcome.model_id,
                tokens_in=outcome.tokens_in,
                tokens_out=outcome.tokens_out,
            )
        )
        stats.classified += 1
        if outcome.needs_human:
            stats.needs_human += 1
        return

    _handle_error(row, out, store, writer, stats)


def _handle_error(
    row: UrlRow, out: FetchOutcome, store: FrontierStore, writer: JsonlWriter, stats: PipelineStats
) -> None:
    code = out.error_code or ErrorCode.E_CONNECT
    status = _ERROR_STATUS.get(code, UrlStatus.FAILED_FETCH)
    flags = Flags()
    if out.robots_blocked:
        flags.blocked_robots = True
    if code == ErrorCode.E_NONHTML:
        flags.non_html = True
    store.set_status(
        row.url_norm,
        status,
        last_error_code=str(code),
        http_status=out.status or None,
    )
    writer.write(
        build_error_record(
            url_raw=row.url_raw,
            url_final=out.url_final,
            domain=row.domain,
            scope=row.scope,
            flags=flags,
            error_code=str(code),
            sitesift_version=__version__,
        )
    )
    stats.errors += 1
    stats.by_error[str(code)] = stats.by_error.get(str(code), 0) + 1
