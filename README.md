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

## Install (once implemented)

```bash
uvx sitesift --help          # or: pipx install sitesift
```

## Quick start

```bash
sitesift doctor                     # verify environment
sitesift init                       # write a starter sitesift.toml
export SITESIFT_IDENTITY__CONTACT="you@example.com"   # required to fetch

# Classify every URL with the LLM (local Ollama — free):
sitesift run urls.txt --llm sync --provider ollama \
  --base-url http://localhost:11434 --model-small gemma4:12b --model-large gemma4:12b
# …or with Anthropic:
sitesift run urls.txt --llm sync --provider anthropic   # needs ANTHROPIC_API_KEY + [anthropic] extra

# Extract the facts only, no classification (no LLM, no cost):
sitesift run urls.txt --llm off     # → out/results.jsonl

# Re-classify from stored evidence after changing prompt/model/taxonomy (no re-fetch):
sitesift reclassify --llm sync --provider ollama --base-url http://localhost:11434

sitesift status                     # frontier counts
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

## Status

Pre-alpha, under active development. See `CHANGELOG.md` and `docs/roadmap.md`.

## License

Apache-2.0. The bundled default taxonomy is original work under Apache-2.0; the
taxonomy loader is pluggable (see `NOTICE`).
