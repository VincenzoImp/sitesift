# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Project scaffold: packaging (`pyproject.toml`, hatchling, `src/` layout),
  Apache-2.0 license, `NOTICE` (CC BY 3.0 attribution for optional IAB data).
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
- Classification + output (M3 core): `taxonomy/loader.py` + default 26-node
  `taxonomy_custom.yaml`, `classify/rules.py` + `data/rules.yaml`
  (high-precision `site_type` rules), `classify/ladder.py` (rules-only for now,
  LLM rungs pluggable), `output/jsonl.py`, and `pipeline.py` orchestrating the
  full run. `sitesift run` produces JSONL end-to-end with no LLM; `--resume`
  skips classified URLs (integration-tested).
- Offline eval harness (`sitesift eval` / `eval/run_eval.py`) over a synthetic
  golden set — gates `rules_coverage ≥ 0.30` and `rules_precision ≥ 0.95` in CI
  (currently 0.80 / 1.00).
- Release scaffolding: GitHub Actions CI (lint + format + typecheck + test +
  eval, all offline), `Makefile`, and docs (`roadmap`, `politeness`, `site_types`).
- 145 tests; `ruff` + `ruff format` clean; `mypy --strict` clean on `models.py`
  and `net/guard.py`.

**Milestone reached: the tool is useful and publishable with no LLM (v0.1.0 scope).**

- LLM ladder (M4, v0.2.0 scope): `classify/llm/` — provider contract + structured
  output schema (`base.py`), stable hashed system prompt with injection-safe
  evidence delimiting (`prompt.py`), output validation with taxonomy-hierarchy
  enforcement and topic dedup (`validate.py`), the `LLMClassifier` engine, and
  two providers: **Anthropic** (Haiku 4.5 → Sonnet 5 via `messages.parse`,
  prompt caching, per-model param handling) and **Ollama** (local models via
  `/api/chat` with JSON-schema-constrained output + one repair pass).
- `classify/ladder.py` now escalates rules → LLM small → LLM large → needs_human,
  degrading gracefully to the best deterministic signal on LLM failure. Blocking
  flags still short-circuit before any rung. Provenance (`model_id`,
  `prompt_sha256`, `tokens_in/out`) is persisted per record.
- Pipeline runs classification off the event loop; `sitesift run --llm sync
  --provider {anthropic,ollama} [--base-url ...] [--model-small/-large ...]`.
- Tests: mocked ladder escalation/fallback/hierarchy (offline), plus **live**
  Ollama tests (classifier + full pipeline) that auto-skip when no endpoint is
  reachable. Verified live against local models (gemma4:12b): rules-uncovered
  types like `corporate` and `blog_personal` now classify correctly.
- Full-ladder eval: `sitesift eval --llm` scores end-to-end `site_type_accuracy`
  over the golden set through rules + LLM, with a per-method breakdown. Live run
  against gemma4:12b: **1.00 accuracy** (rules 8/8, llm_small 2/2). The offline
  ladder-eval machinery is CI-tested (rules-only path = 0.80).
- 156 tests; lint/format/typecheck clean.
