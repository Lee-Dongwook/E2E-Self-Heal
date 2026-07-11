.DEFAULT_GOAL := help

.PHONY: help install lint format typecheck check test run clean

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

typecheck: ## Static type check with pyright
	uv run pyright

check: lint typecheck ## Lint + typecheck

test: ## Run tests with pytest
	uv run pytest

run: ## Run the healer, e.g. make run ARGS="tests/example.spec.ts --log playwright.log"
	uv run e2e-healer $(ARGS)

clean: ## Remove caches and build artifacts
	rm -rf .ruff_cache .pytest_cache .pyright build dist wheels *.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
