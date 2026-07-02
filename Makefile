.DEFAULT_GOAL := help
.PHONY: help install dev-up dev-down smoke-test test test-unit lint typecheck format clean kind-up kind-down

PYTHON ?= python3
UV ?= uv

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n\nTargets:\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

install: ## Install all workspace dependencies (uv sync)
	$(UV) sync --all-packages

dev-up: ## Start local Postgres + Redis (docker-compose)
	docker compose -f docker-compose.yml up -d
	@echo "Waiting for Postgres to be ready..."
	@until docker compose exec -T postgres pg_isready -U kubepilot >/dev/null 2>&1; do sleep 1; done
	@echo "Postgres ready."

dev-down: ## Stop local dev services
	docker compose -f docker-compose.yml down

dev-reset: ## Wipe local dev volumes (destructive)
	docker compose -f docker-compose.yml down -v

smoke-test: ## Run smoke test (validates LLM provider + DB connectivity)
	$(UV) run --package kubepilot-orch python -m kubepilot_orch.smoke_test

kind-up: ## Create a local kind cluster with sample workloads (requires kind)
	bash scripts/dev-cluster.sh up

kind-down: ## Tear down the local kind cluster
	bash scripts/dev-cluster.sh down

test: test-unit ## Run unit tests across all packages

test-unit: ## Run unit tests (no integration markers)
	$(UV) run pytest -m "not integration and not live_llm"

test-integration: ## Run integration tests (requires dev-up)
	$(UV) run pytest -m integration

lint: ## Lint with ruff
	$(UV) run ruff check .
	$(UV) run ruff format --check .

format: ## Auto-format
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

typecheck: ## Type-check with mypy
	$(UV) run mypy services/

check: lint typecheck test ## Run all checks (lint + typecheck + tests)

clean: ## Clean caches
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name .pytest_cache -prune -exec rm -rf {} +
	find . -type d -name .ruff_cache -prune -exec rm -rf {} +
	find . -type d -name .mypy_cache -prune -exec rm -rf {} +
