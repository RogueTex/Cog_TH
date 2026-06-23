.DEFAULT_GOAL := help
.PHONY: help env install dev test lint fmt run up down logs build clean walkthrough

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

env: ## Create .env from .env.example if missing
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example")

install: ## Install runtime dependencies
	pip install -r requirements.txt

dev: ## Install dev + runtime dependencies
	pip install -r requirements-dev.txt

test: ## Run the test suite
	pytest

lint: ## Lint with ruff
	ruff check app tests

fmt: ## Auto-fix lint issues
	ruff check --fix app tests

run: ## Run the API locally with autoreload (http://localhost:8000)
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

build: ## Build the Docker image
	docker compose build

up: ## Start the API with docker compose (http://localhost:8000)
	docker compose up --build

down: ## Stop and remove containers
	docker compose down

logs: ## Tail container logs
	docker compose logs -f

walkthrough: ## Import issue #2 / PR #4 / Devin session, then print the status summary
	curl -fsS -X POST http://localhost:8000/runs/import \
		-H 'Content-Type: application/json' \
		-d '{"issue_number": 2, "devin_session_url": "https://app.devin.ai/sessions/edd1bd6ac10b4e899ba2a886a1b5f744", "pull_request_url": "https://github.com/RogueTex/superset/pull/4", "issue_title": "Remove dockerize init image from Helm startup waits"}'
	@echo "\n--- status summary ---"
	curl -fsS http://localhost:8000/summary

clean: ## Remove local data + caches
	rm -rf data .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} +
