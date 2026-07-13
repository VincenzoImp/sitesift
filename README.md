# sitesift

Given a set of URLs, `sitesift` produces for each a **structured, validated, reproducible** record describing:

- **what kind of site it is** (`site_type`: news, e-commerce, blog, forum, gov, …)
- **what it is about** (`topics`: hierarchical, e.g. `Sports > Soccer`)
- **what language** it is in (deterministic, not guessed by an LLM)
- **technical metadata** (CMS, feeds, paywall, schema.org, ad networks, …)
- **quality flags** (`parked`, `dead`, `spam`, `adult`, `login_wall`, …)
- **how confident** it is and **by which method** each decision was made (`llm_small`, `llm_large`, or `blocked` for non-content pages)

It is **not** a recursive crawler (one page per URL), does not render JavaScript, and produces indicative flags — not certified brand-safety.

## Why it exists

Extracting a page's text and metadata is a solved problem (`sitesift` reuses [`trafilatura`](https://github.com/adbar/trafilatura)). The gap is turning that evidence into a **structured judgment** — site type, topic, and quality flags — cheaply, reproducibly, and at scale. `sitesift` fills that gap with a strict split:

- a **deterministic layer** (normalize → fetch → extract) that never calls an LLM — its only job is to produce *all* the canonical facts of a page, and
- a **judgment layer** where the **LLM is the decision engine**: it reads the full evidence bundle and decides `site_type` and topic for **every** URL — a cheap model first, escalating to a stronger one only when it is not confident.

The only decision the deterministic layer makes is to *skip* a page with no content (dead / parked / soft-404 / non-HTML → `blocked`), so no model call is wasted. That split is what makes resume-after-crash, offline tests, re-classification without re-fetching, and prompt caching all work.

## Architecture

```
1 NORMALIZE ──> 2 FETCH ──> 3 EXTRACT ──────> 4 CLASSIFY (LLM decides)
  dedup          robots       trafilatura        LLM small (Haiku 4.5)
  eTLD+1         SSRF guard    JSON-LD            ↳ escalate if unsure
  filters        rate limit    langid + facts    LLM large (Sonnet 5)
     │              │              │                     │
 frontier.db     (polite)     evidence.db      results.jsonl + results.db
```

The deterministic layer hands the LLM the whole fact bundle (host/TLD, JSON-LD, platform markers, feeds, prices, the full main text, …); the model weighs it all. `sitesift run` orchestrates every phase.

## Install

Not on PyPI yet — install from source (the `[anthropic]` extra is optional, only for the Anthropic provider):

```bash
git clone https://github.com/VincenzoImp/sitesift && cd sitesift
uv sync                       # or: pipx install '.[anthropic]'
uv run sitesift --help
```

## Quick start

There is a ready-made config and URL list under [`examples/`](examples/):

```bash
sitesift doctor                     # verify environment
sitesift init                       # write a starter sitesift.toml
export SITESIFT_IDENTITY__CONTACT="you@example.com"   # required to fetch

# Classify every URL with the LLM (local Ollama — free):
sitesift run examples/urls.txt --llm sync --provider ollama \
  --base-url http://localhost:11434 --model-small gemma4:12b --model-large gemma4:12b
# …or with Anthropic:
sitesift run examples/urls.txt --llm sync --provider anthropic   # needs ANTHROPIC_API_KEY + [anthropic] extra

# Two-phase: collect the facts fast now, run inference later (no re-fetch) — see Performance.
sitesift run examples/urls.txt --llm off --db state.db          # phase 1: extract only
sitesift reclassify --llm sync --provider ollama --db state.db \
  --base-url http://localhost:11434 --model-small gemma4:12b    # phase 2: classify stored evidence

sitesift status --db state.db       # frontier counts
sitesift eval --provider ollama --base-url http://localhost:11434 --model gemma4:12b   # LLM accuracy on the golden set
sitesift taxonomy show sports       # inspect the topic tree
```

## Safety

`sitesift` accepts arbitrary third-party URLs, so it treats every input as hostile:

- **SSRF defense**: DNS is resolved and every IP validated against private / link-local /
  CGNAT / metadata ranges; the request connects to the pinned IP (defeating DNS rebinding)
  and re-validates on every redirect.
- **Prompt-injection defense**: page content is passed to the model as delimited, untrusted
  *data*; the classifier has no tools, so the worst case is a wrong category, never execution.
- **Bomb protection**: hard limits on body size, decompression size, and compression ratio.

Read `docs/politeness.md` before running at scale.

## Performance

The two phases have independent, opposite bottlenecks, so tune them separately:

- **Collection** (`fetch`) is I/O-bound and parallel across hosts (≥1 concurrent request *per host* + `min_host_delay`). Throughput scales with **how many distinct hosts** your list spans; `fetch.max_concurrency` (default 200) caps total in-flight fetches.
- **Inference** (`classify`) is bound by your model/provider throughput. `classify.max_llm_concurrency` only helps if the provider actually serves requests in parallel.

Because inference is usually **far** slower than collection, total time ≈ `URLs × time_per_inference ÷ provider_parallelism`; collection runs in its shadow. So put optimisation effort on the model side, and — when the model is slow — prefer the **two-phase** flow (`run --llm off` then `reclassify`) so a fast, resumable collection isn't blocked on the model.

Measured on a local Ollama (`gemma4:12b`, RTX 4090):

| | throughput |
|---|---|
| collection (`--llm off`) | ~0.85 s/URL (network-bound) |
| inference, warm single call | ~11.8 s/URL |
| inference, current server default | **~315 URL/hour** (Ollama serves 1 request at a time) |

To go faster with the same model: set `OLLAMA_NUM_PARALLEL=N` on the Ollama server so the GPU batches requests (the 4090 has VRAM headroom for a few), then match `classify.max_llm_concurrency = N`. For bulk, a hosted provider (Anthropic, with prompt caching already enabled) parallelises far more than a single local GPU. Picking the smallest model that is still accurate enough (measure with `sitesift eval`) is the single biggest lever.

## Status

Alpha (`v0.1.0`). The pipeline is complete and tested end-to-end; the eval still uses a synthetic golden set (see `docs/roadmap.md`). See `CHANGELOG.md`.

## License

Apache-2.0. The bundled default taxonomy is original work under Apache-2.0; the
taxonomy loader is pluggable (see `NOTICE`).
