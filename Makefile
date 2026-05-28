# trade-intel — developer Makefile
# Run `make help` to see available targets.

SHELL       := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help
MAKEFLAGS   += --no-print-directory

# ── Paths & binaries ────────────────────────────────────────────────
VENV     ?= .venv
PYTHON   ?= $(VENV)/bin/python
PIP      := $(VENV)/bin/pip
PYTEST   := $(VENV)/bin/pytest
ALEMBIC  := $(VENV)/bin/alembic
RUFF     := $(VENV)/bin/ruff
MYPY     := $(VENV)/bin/mypy
UVICORN  := $(VENV)/bin/uvicorn

SRC_DIR  := src
PKG      := trade_intel
TESTS    := tests
DEV_DB   := data/dev.db

# ── Colors (opt out with NO_COLOR=1) ────────────────────────────────
ifndef NO_COLOR
GREEN  := \033[32m
YELLOW := \033[33m
CYAN   := \033[36m
RED    := \033[31m
BOLD   := \033[1m
RESET  := \033[0m
endif

# ── Help ────────────────────────────────────────────────────────────
.PHONY: help
help: ## Show this help
	@printf "$(BOLD)trade-intel$(RESET) — make targets\n"
	@awk 'BEGIN {FS = ":.*?## "} \
	     /^## / { printf "\n$(YELLOW)%s$(RESET)\n", substr($$0, 4); next } \
	     /^[a-zA-Z][a-zA-Z0-9_-]*:.*?## / { printf "  $(CYAN)%-18s$(RESET) %s\n", $$1, $$2 }' \
	     $(MAKEFILE_LIST)
	@printf "\n"

## Environment
.PHONY: install sync upgrade-pip
install: $(VENV)/.installed ## Create venv and install package with dev extras
$(VENV)/.installed: pyproject.toml
	@printf "$(CYAN)==> bootstrapping venv at $(VENV)$(RESET)\n"
	@if [ ! -d "$(VENV)" ]; then python3 -m venv "$(VENV)"; fi
	@$(PIP) install --upgrade pip wheel setuptools >/dev/null
	@$(PIP) install -e ".[dev]"
	@touch $@
	@printf "$(GREEN)==> install complete$(RESET)\n"

sync: ## Re-sync dependencies after pyproject.toml change
	@printf "$(CYAN)==> syncing deps$(RESET)\n"
	@$(PIP) install --upgrade -e ".[dev]"
	@touch $(VENV)/.installed

upgrade-pip: ## Upgrade pip / wheel / setuptools inside the venv
	@printf "$(CYAN)==> upgrading build tooling$(RESET)\n"
	@$(PIP) install --upgrade pip wheel setuptools

## Database
.PHONY: db-upgrade db-downgrade db-reset db-history db-current
db-upgrade: ## Apply all pending Alembic migrations (alembic upgrade head)
	@printf "$(CYAN)==> alembic upgrade head$(RESET)\n"
	@$(ALEMBIC) upgrade head

db-downgrade: ## Roll back one Alembic revision (alembic downgrade -1)
	@printf "$(CYAN)==> alembic downgrade -1$(RESET)\n"
	@$(ALEMBIC) downgrade -1

db-history: ## Show Alembic revision history
	@$(ALEMBIC) history --verbose

db-current: ## Show current Alembic revision
	@$(ALEMBIC) current

db-reset: ## Delete local SQLite DB and re-run migrations (FORCE=1 to skip prompt)
	@printf "$(YELLOW)==> resetting local dev DB ($(DEV_DB))$(RESET)\n"
	@if [ "$${FORCE:-0}" != "1" ]; then \
		printf "$(YELLOW)This will delete $(DEV_DB). Type 'yes' to continue: $(RESET)"; \
		read CONFIRM; \
		if [ "$$CONFIRM" != "yes" ]; then echo "aborted"; exit 1; fi; \
	fi
	@rm -f $(DEV_DB)
	@$(ALEMBIC) upgrade head
	@printf "$(GREEN)==> DB reset complete$(RESET)\n"

## Testing
.PHONY: test test-unit test-integration coverage
test: ## Run the full pytest suite
	@printf "$(CYAN)==> pytest$(RESET)\n"
	@$(PYTEST)

test-unit: ## Run only unit tests
	@$(PYTEST) $(TESTS)/unit

test-integration: ## Run only integration tests
	@$(PYTEST) $(TESTS)/integration

coverage: ## Run tests with coverage (term-missing + html)
	@printf "$(CYAN)==> pytest --cov$(RESET)\n"
	@$(PYTEST) --cov=$(PKG) --cov-report=term-missing --cov-report=html

## Linting & types
.PHONY: lint format typecheck
lint: ## Run ruff in check mode
	@printf "$(CYAN)==> ruff check$(RESET)\n"
	@$(RUFF) check $(SRC_DIR) $(TESTS)

format: ## Auto-format with ruff (format + import fix)
	@printf "$(CYAN)==> ruff format + fix$(RESET)\n"
	@$(RUFF) format $(SRC_DIR) $(TESTS)
	@$(RUFF) check --fix --exit-zero $(SRC_DIR) $(TESTS)

typecheck: ## Run mypy against the package
	@printf "$(CYAN)==> mypy$(RESET)\n"
	@$(MYPY) $(SRC_DIR)/$(PKG)

## Application
.PHONY: run-api run-cli shell
run-api: ## Start the FastAPI server (Phase 6)
	@if [ ! -f "$(SRC_DIR)/$(PKG)/api/main.py" ]; then \
		printf "$(YELLOW)run-api: $(SRC_DIR)/$(PKG)/api/main.py not yet present (Phase 6).$(RESET)\n"; \
		exit 1; \
	fi
	@$(UVICORN) $(PKG).api.main:app \
		--host $${API_HOST:-127.0.0.1} \
		--port $${API_PORT:-8000} \
		--reload

run-cli: ## Run the trader CLI (Phase 5)
	@if [ ! -f "$(SRC_DIR)/$(PKG)/cli/main.py" ]; then \
		printf "$(YELLOW)run-cli: $(SRC_DIR)/$(PKG)/cli/main.py not yet present (Phase 5).$(RESET)\n"; \
		exit 1; \
	fi
	@$(PYTHON) -m $(PKG).cli.main

shell: ## Open a Python REPL with the package importable
	@$(PYTHON)

## Quality gates
.PHONY: smoke-test verify preflight
smoke-test: ## Quick boot smoke: settings + engine + SELECT 1
	@printf "$(CYAN)==> smoke-test$(RESET)\n"
	@$(PYTHON) -c "from sqlalchemy import text; \
from trade_intel.config import get_settings; \
from trade_intel.db.session import get_engine; \
s = get_settings(); e = get_engine(); \
c = e.connect(); v = c.execute(text('SELECT 1')).scalar_one(); c.close(); \
assert v == 1; \
print(f'OK env={s.environment.value} dialect={e.dialect.name} url={s.database_url}')"

verify: lint typecheck test ## Quality gate: lint + typecheck + tests
	@printf "$(GREEN)==> verify passed$(RESET)\n"

preflight: verify db-upgrade smoke-test ## Pre-deploy gate: verify + db-upgrade + smoke-test + alembic drift check
	@printf "$(CYAN)==> alembic check (schema parity)$(RESET)\n"
	@$(ALEMBIC) check
	@printf "$(GREEN)==> preflight passed$(RESET)\n"
