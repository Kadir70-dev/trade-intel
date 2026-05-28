# trade-intel

> AI-powered sourcing intelligence for **UAE wholesale electronics trading**.
> Turns messy WhatsApp chat exports and inventory sheets into ranked, actionable buyer↔seller matches.

The system is execution-focused: it does not just describe the market, it tells a Deira / Bur Dubai trader which 3 messages to send next.

## Project status

| Phase | Status |
|---|---|
| 1 — Architecture, schema, folder structure | ✅ done |
| 2 — Project scaffold (config, ORM, migrations, Makefile, logging) | 🟡 in progress |
| 3 — WhatsApp parser | ⬜ planned |
| 4 — Semantic matching (embeddings + FAISS) | ⬜ planned |
| 5 — CLI trader assistant | ⬜ planned |
| 6 — FastAPI HTTP API | ⬜ planned |
| 7 — Dockerization | ⬜ planned |
| 8 — Optimization + testing | ⬜ planned |

Authoritative state lives in `PROJECT_STATE.json` and `TASKS.md`.

## Architecture (one-screen summary)

```
WhatsApp / CSV / Excel
        ▼
Ingestion (Go worker, Phase 7)
        ▼
Parsers  →  Normalization (dictionary + fuzzy)  →  Intent + entity extraction
        ▼
DB (SQLite dev, Postgres prod) + Embeddings → FAISS index
        ▼
Matching engine (semantic × spec_fit × recency × evidence) → matches + lead_pipeline
        ▼
CLI (Typer)  /  FastAPI  /  Dashboard
```

Full detail: see [`ARCHITECTURE.md`](ARCHITECTURE.md).
Design decisions and trade-offs: see [`DECISIONS.md`](DECISIONS.md).
Project conventions and confidence thresholds: see [`CLAUDE.md`](CLAUDE.md).

## Tech stack

Python 3.12 · FastAPI · SQLAlchemy 2.x · Alembic · pydantic-settings · structlog · Typer · sentence-transformers · FAISS · SQLite / PostgreSQL · pytest · ruff · mypy.

## Quick start

```bash
make install      # creates .venv and installs the package with [dev] extras
cp .env.example .env
make db-upgrade   # apply Alembic migrations to the local SQLite DB
make smoke-test   # boot proof: settings + engine + SELECT 1
make verify       # lint + typecheck + tests
```

`make help` lists every target.

## Environment

Settings load in this order: **real env vars → `.env` at project root → defaults**.

| Var | Default | Notes |
|---|---|---|
| `APP_NAME` | `trade-intel` | |
| `ENVIRONMENT` | `dev` | `dev` / `test` / `prod` |
| `DEBUG` | `false` | Must be `false` in prod |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL` |
| `SQLITE_URL` | `sqlite:///<repo>/data/dev.db` | Used in dev / test |
| `POSTGRES_URL` | unset | Required when `ENVIRONMENT=prod` |
| `DB_ECHO` | `false` | SA engine echo |
| `EMBEDDING_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | |
| `VECTOR_STORE_TYPE` | `faiss` | `faiss` / `qdrant` |
| `API_HOST` | `127.0.0.1` | |
| `API_PORT` | `8000` | |
| `API_RELOAD` | `false` | Must be `false` in prod |

Production gates (enforced by a `model_validator` in `src/trade_intel/config.py`): `ENVIRONMENT=prod` requires `POSTGRES_URL`; refuses `DEBUG=true` or `API_RELOAD=true`.

## Database

Schema source of truth: [`db/schema.sql`](db/schema.sql) (9 tables — `groups`, `sellers`, `buyers`, `messages`, `inventory`, `demand`, `matches`, `embeddings`, `lead_pipeline`).
ORM: [`src/trade_intel/db/models/`](src/trade_intel/db/models) — one file per entity.
Migrations: [`db/migrations/versions/`](db/migrations/versions) — Alembic, baseline `1f55ca15e963`.

```bash
make db-upgrade            # alembic upgrade head
make db-downgrade          # roll back one revision
make db-history            # show full revision history
make db-current            # show current revision
make db-reset FORCE=1      # drop dev SQLite and re-migrate (destructive)
```

When models change, generate a new migration:
```bash
.venv/bin/alembic revision --autogenerate -m "<short_slug>"
```

`make preflight` runs `alembic check` to guarantee zero drift between ORM models and migrations.

## Make commands

`make help` is the canonical list. Highlights:

| Target | Purpose |
|---|---|
| `install` / `sync` / `upgrade-pip` | Environment management |
| `db-upgrade` / `db-downgrade` / `db-reset` / `db-history` / `db-current` | Database lifecycle |
| `test` / `test-unit` / `test-integration` / `coverage` | Testing |
| `lint` / `format` / `typecheck` | Code quality |
| `run-api` / `run-cli` / `shell` | Run the app (Phases 5–6) |
| `smoke-test` / `verify` / `preflight` | Quality gates |

`make verify` is the pre-commit gate. `make preflight` is the pre-push / pre-deploy gate.

## Logging

Structured logging is configured at first use via `trade_intel.core.logging`:

```python
from trade_intel.core.logging import get_logger, set_request_id

log = get_logger(__name__, component="parser")
set_request_id("req-abc-123")        # propagated via contextvars
log.info("ingest_started", file="dubai.txt", bytes=12345)
```

- **dev / test:** colour console renderer.
- **prod:** JSON renderer, one event per line, machine-readable.
- `request_id` / `session_id` ride on `ContextVar`s — set in FastAPI middleware (Phase 6) or CLI command wrappers (Phase 5).
- Third-party loggers (SQLAlchemy, Alembic, Uvicorn) are routed through the same formatter.

## Development workflow

1. `make install` (one-time).
2. Edit code.
3. `make format` to auto-fix style.
4. `make verify` — must be green before commit.
5. `make preflight` before push / release.
6. New ORM column? `alembic revision --autogenerate -m "<slug>"` → review the generated migration → commit it alongside the model change.

## Project memory

| File | Purpose |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | Project conventions, lexicon, confidence thresholds, phase plan |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | System diagram, module responsibilities, schema overview |
| [`DECISIONS.md`](DECISIONS.md) | Architecture decision log (`D-001`…) |
| [`PROJECT_STATE.json`](PROJECT_STATE.json) | Current phase, task status, session log |
| [`TASKS.md`](TASKS.md) | Phased task checklist |

## Example future usage (planned, not yet implemented)

```bash
# Phase 3 — parse a WhatsApp export
trade-intel parse data/raw/dubai_brokers.txt

# Phase 4 — query for matches
trade-intel match "iPad mini 5 256GB sealed qty 20"

# Phase 5 — interactive trader CLI
trade-intel chat

# Phase 6 — run the API
make run-api
curl -sX POST localhost:8000/match -d '{"query":"15pm 256 sealed"}'
```

## Licence

Proprietary. © trade-intel.
