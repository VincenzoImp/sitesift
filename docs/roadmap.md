# Roadmap

Status of the MVP build. The architecture is designed so deferred items bolt on
without reworking earlier phases.

## Done

- **M0 — Scaffold**: packaging (uv + hatchling), Apache-2.0 + NOTICE, core models
  (`Evidence`/`Verdict`/`Flags`/`SiteType`/…), error taxonomy, config, `doctor`.
- **M1 — Safe & polite fetch**: URL normalization, SSRF guard (IP pinning,
  metadata/tunnel blocking, rebinding-tested), robots (protego + Google
  semantics), per-host rate limiting (≤1 concurrent/host), streaming fetch with
  hard body/decompression limits, content-addressed blob cache, SQLite frontier.
- **M2 — Extraction**: full `Evidence` bundle (selectolax + JSON-LD + trafilatura
  main text + deterministic language via py3langid + CMS/e-commerce/ad
  fingerprints + anti-injection sanitization) and deterministic flags.
- **M3 — Rules + output (`v0.1.0`)**: 26-node default taxonomy, high-precision
  `site_type` rule engine, JSONL + SQLite output, end-to-end `sitesift run`
  pipeline, and an offline rules eval (`sitesift eval`) that gates
  `rules_coverage ≥ 0.30` and `rules_precision ≥ 0.95` in CI. **Useful with no
  LLM.**
- **M4 — LLM ladder (`v0.2.0`)**: rules → LLM small → LLM large → needs_human,
  with **Anthropic** (Haiku 4.5 → Sonnet 5, `messages.parse`, prompt caching) and
  **Ollama** (local, JSON-schema output) providers, taxonomy-hierarchy-validated
  structured output, and full provenance. Verified live against local models;
  the types rules can't cover (`corporate`, `blog_personal`, …) are now handled.
- **Hardening pass**: fetch retries with exponential backoff (honouring
  `Retry-After`), crash-recovery requeue of interrupted URLs on `--resume`,
  `max_llm_concurrency` gate, `reclassify` (re-classify from stored evidence with
  no re-fetch), SQL column allow-list in the store, and removal of all dead code
  / unused config / unused dependencies.

## Deferred (post-MVP)

- **Real golden set**: the eval currently uses a small *synthetic* fixture set.
  Replace with ~200 hand-labeled live URLs, double-labeled with Cohen's κ, before
  trusting metrics on real traffic. Add LLM-path accuracy (`site_type_accuracy`,
  `tier1_accuracy`, calibration/ECE) once the golden set exists.
- **Domain-level classification + `--scope` behavior**: today every URL is
  classified as a page; `--scope` is recorded but not acted upon (no
  site-vs-page fetch, no per-domain homepage dedup / `DomainProfile`).
- **Topic Tier-3/4 cascade (stage B)**: a second small LLM call over the chosen
  Tier-2 subtree (currently Tier-1/2 only).
- **IAB taxonomy**: vendor IAB Content 3.1 (CC BY 3.0) as an opt-in taxonomy.
- **On-disk blob cache**: persist fetched HTML so the extractor can re-run
  without re-fetching (evidence is already re-usable via `reclassify`).
- **Phase-separated subcommands**: `sitesift fetch` / `extract` / `classify`
  running a single phase over the frontier.
- **Scale**: Anthropic Batch API mode, sharding per domain, `--budget-usd`,
  richer metrics/logging.
- **Rendering**: opt-in Playwright fallback for `js_required` pages.
- **Agent**: optional `--investigate` step as a bespoke Anthropic tool-use loop.
