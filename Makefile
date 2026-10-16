.PHONY: install fmt lint type test test-all check notebook clean

install:          ## Install all deps (incl. dev + notebook) and git hooks
	uv sync
	uv run pre-commit install

fmt:              ## Auto-format and auto-fix lint
	uv run ruff format .
	uv run ruff check --fix .

lint:             ## Lint without fixing
	uv run ruff format --check .
	uv run ruff check .

type:             ## Static type check
	uv run mypy

test:             ## Fast tests (no network)
	uv run pytest -m "not network"

test-all:         ## All tests including live network calls
	uv run pytest

check: lint type test   ## Everything CI runs

notebook:         ## Launch JupyterLab
	uv run jupyter lab

clean:
	rm -rf .ruff_cache .mypy_cache .pytest_cache .coverage htmlcov
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
