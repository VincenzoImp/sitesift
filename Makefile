.PHONY: sync test lint fmt typecheck eval check clean

sync:
	uv sync --extra dev

test:
	uv run pytest -q

lint:
	uv run ruff check src/ tests/ eval/

fmt:
	uv run ruff format src/ tests/ eval/

typecheck:
	uv run mypy

# LLM accuracy eval needs a provider (free against a local Ollama). Override with:
#   make eval OLLAMA_URL=http://host:11434 OLLAMA_MODEL=gemma4:12b
OLLAMA_URL ?= http://localhost:11434
OLLAMA_MODEL ?= gemma4:12b
eval:
	uv run sitesift eval --provider ollama --base-url $(OLLAMA_URL) --model $(OLLAMA_MODEL)

# The offline gate (no network, no provider). Run `make eval` separately for accuracy.
check: lint typecheck test

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache out .sitesift
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
