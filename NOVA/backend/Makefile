# NOVA – Beyond the Mission | backend developer tasks.
#
#   make install     create .venv and install dev dependencies
#   make dev         uvicorn with autoreload on 0.0.0.0:8000
#   make check       everything CI runs (offline, in-process)
#   make probes      real uvicorn + real socket + live NASA (evidence, not CI)
#
# Override the interpreter with `make PY=python3.12 check`.

PY ?= python3
VENV ?= .venv
BIN := $(VENV)/bin
PORT ?= 8000

.DEFAULT_GOAL := help
.PHONY: help install dev run test cov lint format typecheck content audit contract text playthrough probes probes-offline check docs migrate revision docker clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Create the virtualenv and install dev dependencies
	$(PY) -m venv $(VENV)
	$(BIN)/pip install -r requirements-dev.txt

dev: ## Run uvicorn with autoreload
	$(BIN)/uvicorn app.main:app --reload --host 0.0.0.0 --port $(PORT)

run: ## Run uvicorn without autoreload
	$(BIN)/uvicorn app.main:app --host 0.0.0.0 --port $(PORT)

test: ## Run the test suite (offline)
	$(BIN)/pytest

cov: ## Test coverage report
	$(BIN)/pytest --cov=app --cov-report=term-missing --cov-report=html

lint: ## Ruff lint + format check
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .

format: ## Auto-fix lint and formatting
	$(BIN)/ruff check --fix .
	$(BIN)/ruff format .

typecheck: ## Mypy
	$(BIN)/mypy app scripts

content: ## Validate the mission content pack
	$(BIN)/python scripts/validate_content.py --strict

audit: ## Audit the HTTP contract (fails on unguarded writes / untyped responses)
	$(BIN)/python scripts/audit_routes.py --check

contract: ## Verify docs/openapi.json is not stale
	$(BIN)/python scripts/export_openapi.py --check

text: ## Audit every player-facing API string (English-only; also enforced by pytest)
	$(BIN)/python scripts/audit_player_text.py

playthrough: ## Play all 15 missions through the public API
	$(BIN)/python scripts/playthrough.py --in-process

probes: ## Probe a real uvicorn socket (WebSocket resume + live NASA)
	$(BIN)/python scripts/live_probes.py

probes-offline: ## Same probes without outbound NASA calls (deterministic)
	$(BIN)/python scripts/live_probes.py --no-live

check: lint typecheck content audit contract test playthrough ## Everything CI runs

docs: ## Regenerate docs/openapi.json, docs/routes.md and docs/routes.json
	$(BIN)/python scripts/export_openapi.py
	$(BIN)/python scripts/audit_routes.py

migrate: ## Apply database migrations
	$(BIN)/alembic upgrade head

revision: ## Autogenerate a migration (make revision m="add column")
	$(BIN)/alembic revision --autogenerate -m "$(m)"
	$(BIN)/ruff format migrations/versions

docker: ## Build the production image
	docker build -t nova-backend .

clean: ## Remove caches and build artefacts
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
