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

eval:
	uv run sitesift eval

check: lint typecheck test eval

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache out .sitesift
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
