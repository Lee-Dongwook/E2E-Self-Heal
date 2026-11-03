.DEFAULT_GOAL := help

.PHONY: help install lint format format-check typecheck check test coverage run clean sync-pins check-pins

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Install deps (incl. dev extras) via uv + enable git hooks
	uv sync --extra dev
	git config core.hooksPath .githooks

lint: ## Lint with ruff
	uv run ruff check .

format: ## Format with ruff
	uv run ruff format .

format-check: ## Check formatting with ruff without modifying files
	uv run ruff format --check .

typecheck: ## Static type check with pyright
	uv run pyright

check: lint format-check typecheck ## Lint + format check + typecheck

test: ## Run tests with pytest
	uv run pytest

coverage: ## Run tests with coverage and enforce the configured floor
	uv run pytest --cov=app --cov-report=term-missing --cov-report=xml --cov-fail-under=90

# --frozen: a plain `uv run` re-locks uv.lock after a version bump before the script starts, so
# `check-pins` would no longer be read-only and the lockfile line would be uv's doing, not ours.
sync-pins: ## Update release pins and documented defaults from pyproject.toml and app/config.py
	uv run --frozen python scripts/sync_release_pins.py

check-pins: ## Fail if a release pin or documented default is out of date
	uv run --frozen python scripts/sync_release_pins.py --check

run: ## Run the healer, e.g. make run ARGS="tests/example.spec.ts --log playwright.log"
	uv run e2e-healer $(ARGS)

clean: ## Remove caches and build artifacts
	rm -rf .ruff_cache .pytest_cache .pyright build dist wheels *.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
