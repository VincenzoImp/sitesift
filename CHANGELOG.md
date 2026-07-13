# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Project scaffold: packaging (`pyproject.toml`, hatchling, `src/` layout),
  Apache-2.0 license, `NOTICE`.
- Core data models (`Evidence`, `Verdict`, `Flags`, `SiteTypeEnum`, `TopicPath`,
  `LanguageInfo`) and the stable error-code taxonomy.
- Configuration loader with precedence CLI > env > file > default.
- `sitesift doctor` command (M0 acceptance: exits 0 on a clean machine).
- `frontier/normalize.py`: deterministic `url_norm` (scheme/host casing, default
  ports, fragment + tracking-param stripping, query sorting, RFC 3986 dot-segment
  resolution and percent-encoding canonicalization, IDN→punycode). 33 cases,
  tested for correctness and idempotency.
- `net/guard.py`: SSRF guard — resolve, validate every IP, pin. Blocks private /
  loopback / link-local / CGNAT / multicast / reserved plus public-looking
  metadata endpoints (Azure WireServer, Alibaba) and embedded-IPv4 tunnels
  (IPv4-mapped, 6to4, NAT64, Teredo). Port allow-list, fail-closed multi-address
  resolution, DNS-rebinding test with a mocked resolver.
- Fetch layer (M1): `net/robots.py` (protego + Google error semantics),
  `net/limiter.py` (≤1 concurrent request/host + min delay), `net/fetcher.py`
  (async, IP-pinned via `sni_hostname`, manual per-hop-revalidated redirects,
  hard body/decompression limits, charset detection), `net/cache.py`
  (content-addressed zstd blob store), `frontier/filters.py`, and
  `frontier/store.py` (WAL SQLite frontier + evidence + page_records).
  Integration-tested against a local server (basic fetch, redirects, robots
  block, ≤1 connection/host).
- Extraction (M2): `extract/{html,structured,text,language,fingerprint,sanitize,
  bundle}.py` — full `Evidence` bundle (JSON-LD, deterministic language via
  py3langid, CMS/e-commerce/ad fingerprints, anti-injection sanitization) plus
  deterministic flags.
- Classification + output: `taxonomy/loader.py` + default 26-node
  `taxonomy_custom.yaml`, `classify/ladder.py` (the LLM decision ladder),
  `output/jsonl.py`, and `pipeline.py` orchestrating the full run. `sitesift run`
  produces JSONL end-to-end; `--resume` skips classified URLs (integration-tested).
- Eval harness (`sitesift eval`) over a synthetic golden set — classifies each
  fixture through the LLM ladder and reports `site_type_accuracy` (plus topic
  accuracy when labeled). A deterministic fake-LLM path is CI-tested offline.
- Release scaffolding: GitHub Actions CI (lint + format + typecheck + test, all
  offline), `Makefile`, and docs (`roadmap`, `politeness`, `site_types`).
- 145 tests; `ruff` + `ruff format` clean; `mypy --strict` clean on `models.py`
  and `net/guard.py`.

- LLM ladder: `classify/llm/` — provider contract + structured
  output schema (`base.py`), stable hashed system prompt with injection-safe
  evidence delimiting (`prompt.py`), output validation with taxonomy-hierarchy
  enforcement and topic dedup (`validate.py`), the `LLMClassifier` engine, and
  two providers: **Anthropic** (Haiku 4.5 → Sonnet 5 via `messages.parse`,
  prompt caching, per-model param handling) and **Ollama** (local models via
  `/api/chat` with JSON-schema-constrained output + one repair pass).
- `classify/ladder.py` runs LLM small → LLM large → needs_human: the LLM decides
  every content URL, degrading gracefully on failure. Blocking flags
  (dead/parked/soft-404/non-HTML) short-circuit to `blocked` before any model
  call. Provenance (`model_id`, `prompt_sha256`, `tokens_in/out`) is persisted
  per record.
- Pipeline runs classification off the event loop; `sitesift run --llm sync
  --provider {anthropic,ollama} [--base-url ...] [--model-small/-large ...]`.
- Tests: mocked ladder escalation/fallback/hierarchy (offline), plus **live**
  Ollama tests (classifier + full pipeline) that auto-skip when no endpoint is
  reachable. Verified live against local models (gemma4:12b): pages like
  `corporate` and `blog_personal` classify correctly.
- `sitesift eval` scores end-to-end `site_type_accuracy` over the golden set
  through the LLM ladder, with a per-method breakdown. The offline eval machinery
  is CI-tested with a deterministic fake LLM.

### Added (hardening pass)
- `sitesift reclassify`: re-run classification from stored evidence without
  re-fetching (after a prompt/model/taxonomy change). Evidence storage now also
  persists the deterministic flags so re-classification is exact.
- Fetch retries with exponential backoff + jitter, honouring `Retry-After` on
  429; interrupted-URL requeue on `--resume` (crash recovery).
- `max_llm_concurrency` gate around the LLM classification step.

### Changed
- **Classification is now LLM-driven.** The deterministic layer's only job is to
  extract *all* canonical facts; the LLM is the decision engine for `site_type`
  **and** topic on every content URL. `Evidence.to_prompt_json` now hands the
  model the full fact set (host + TLD, all head/structured/language signals, the
  full de-boilerplated main text) instead of a trimmed slice, and the prompt tells
  it to weigh every signal (a `.gov` TLD or Shopify marker is evidence, not proof).
- `set_status` validates column names against an allow-list.
- Honest docs: `NOTICE`/`README` no longer claim bundled IAB data; `--scope` is
  documented as recorded metadata (no site/page behaviour yet).

### Fixed
- Fetcher: an HTTP 4xx/5xx (incl. 429) with a non-HTML body now reports its real
  error code instead of a misleading `E_NONHTML`.

### Removed
- The hand-written `site_type` rule engine (`classify/rules.py` + `data/rules.yaml`)
  and everything tied to it: the `rules` classification method, the
  `rules_version` provenance field/column, the `accept_threshold_rules` setting,
  and the `rules_coverage`/`rules_precision` eval metrics. Those 8 rules were our
  own opinable thresholds that pre-empted the model; judgment is now the LLM's.
  Also dropped three declared-but-never-populated `Evidence` fields (`bylines`,
  `dates_in_listing`, `has_sitemap`).
- Dead code / cruft: unused `net/cache.py` blob store, unused store query
  helpers, unused config fields (`topic_depth`, `budget_usd`, `injection_canary`,
  `max_decompress_ratio`, `max_pages_per_domain`, and the unused Cache/Extract/
  Output config sections), and unused dependencies (`courlan`, `xxhash`,
  `structlog`).
