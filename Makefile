.PHONY: help py-install py-lint py-test py-seed py-search web-install web-dev web-build rust-check ci

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-14s %s\n", $$1, $$2}'

py-install: ## Install the Python package + dev deps
	cd python && pip install -e ".[dev]"

py-lint: ## Ruff + mypy
	cd python && ruff check . ../scripts && mypy lexicon

py-test: ## Run Python tests
	cd python && pytest

py-seed: ## Seed the global corpus (needs AWS creds + provisioned vector index)
	cd python && python -m lexicon seed

py-search: ## Example filtered search
	cd python && python -m lexicon search "retrieval augmented generation"

web-install: ## Install web deps
	cd apps/web && npm install

web-dev: ## Run the web dev server
	cd apps/web && npm run dev

web-build: ## Type-check + build the web app
	cd apps/web && npm run build

rust-check: ## Format + lint + check the desktop shell
	cd apps/desktop/src-tauri && cargo fmt --check && cargo clippy -- -D warnings && cargo check

ci: py-lint py-test web-build rust-check ## Run everything CI runs

smoke: ## Read-only check of a deployed environment (needs AWS creds + python/.venv)
	python/.venv/bin/python scripts/smoke_test.py
