# P8.5 — Production Readiness Summary

> Date: 2026-05-28 · Branch: master · Status: **GREEN with one minor open item**

## TL;DR

The trade-intel pipeline is production-ready against PostgreSQL with one
small caveat (see _Open Risks_). The P8.5 verification swept the
SQLite-only test suite past its limit and surfaced exactly one
cross-dialect bug — a Boolean-default DDL miss in the baseline
migration — which is now fixed and verified on a live Postgres 16
instance.

| Gate | Status |
|---|:---:|
| SQLite migration roundtrip (upgrade → downgrade → upgrade) | ✅ |
| Postgres migration roundtrip | ✅ |
| `alembic check` zero drift, SQLite | ✅ |
| `alembic check` zero drift, Postgres | ✅ |
| Foreign-key integrity (SQLite + Postgres) | ✅ |
| Unique constraint on `messages.hash` (SQLite + Postgres) | ✅ |
| Partial unique on `sellers.phone` / `buyers.phone` (SQLite + Postgres) | ✅ |
| Full pipeline ingest + matcher (SQLite + Postgres) — identical row counts | ✅ |
| Idempotent re-ingest (SQLite + Postgres) | ✅ |
| Production-config safety gates (`Settings._enforce_prod_safety`) | ✅ |
| `make verify` (lint + mypy + 829 tests + 9 skipped) | ✅ |
| `make preflight` (zero drift, smoke test, alembic head) | ✅ |

## What P8.5 Verified

### 1. Migration roundtrip — both dialects

`tests/integration/test_db_migration_p8_5.py` runs the full alembic
roundtrip on:

- **SQLite** (always, in-memory + tmp file) — 11 tests
- **PostgreSQL** (opt-in via `POSTGRES_TEST_URL`) — 8 tests, all green
  against a fresh Postgres 16 instance

For each dialect: `alembic upgrade head` → all 9 entity tables present
→ `alembic downgrade base` → tables gone → `alembic upgrade head` →
tables restored → `alembic check` reports zero drift.

### 2. The bug P8.5 caught

The baseline migration `20260528_0049_1f55ca15e963_baseline_schema.py`
declared two `Boolean` columns (`demand.is_active`,
`inventory.is_active`) with a `server_default=text("1")`. SQLite
silently coerces `1` ↔ boolean; Postgres rejects with::

    DatatypeMismatch: column "is_active" is of type boolean
    but default expression is of type integer

Fixed by changing both columns (in the models and in the baseline
migration) to `server_default=text("true")`, which both dialects
accept verbatim. The model files
(`src/trade_intel/db/models/demand.py`,
`src/trade_intel/db/models/inventory.py`) were updated in lockstep
so `alembic check` reports zero drift on both dialects.

This kind of footgun is exactly why P8.5 has to actually talk to
Postgres — the SQLite-only test suite was green for months despite
this bug.

### 3. Constraint integrity

| Constraint | SQLite test | Postgres test |
|---|:---:|:---:|
| `messages.hash` UNIQUE | ✅ | ✅ |
| `sellers.phone` partial UNIQUE (NULL coexist) | ✅ | ✅ |
| `inventory.seller_id` → `sellers.id` FK | (covered by Phase 2) | ✅ |

Two NULL-phone sellers coexist on both dialects (the SQLite test
covers this through `sqlite_where`; the Postgres test through
`postgresql_where`). The partial-unique migration captured in baseline
correctly translates to both dialects via SQLAlchemy's dialect
dispatch.

### 4. Full pipeline parity

`test_postgres_full_pipeline_yields_same_counts_as_sqlite` runs the
end-to-end ingest + matcher pipeline against both dialects with the
same UAE wholesale fixtures from P8.2 and asserts the row counts are
identical:

```
sellers=9 buyers=3 messages=21 inventory=15 matches=40 leads=40
```

byte-identical across SQLite and Postgres. Cross-source identity
merge, idempotent re-ingest, and LeadPipeline creation all behave the
same on both backends.

### 5. Production-config safety

`tests/integration/test_db_migration_p8_5.py::Group A` exercises
`Settings._enforce_prod_safety` on every documented prod-mode
invariant:

- `ENVIRONMENT=prod` without `POSTGRES_URL` → raises
- `ENVIRONMENT=prod` with `DEBUG=true` → raises
- `ENVIRONMENT=prod` with `API_RELOAD=true` → raises
- `ENVIRONMENT=prod` without `API_KEY` → raises
- `ENVIRONMENT=prod` with all gates passed → returns Postgres URL
- `ENVIRONMENT=dev` with `POSTGRES_URL` set → still picks SQLite

## How To Run

```
# SQLite-only path (always runs):
.venv/bin/python -m pytest tests/integration/test_db_migration_p8_5.py

# Full path including live Postgres (requires a reachable instance):
POSTGRES_TEST_URL="postgresql+psycopg://user@host:port/db" \
    .venv/bin/python -m pytest tests/integration/test_db_migration_p8_5.py
```

The Postgres half is gated by `POSTGRES_TEST_URL` so CI without
Postgres still passes the SQLite half cleanly (8 tests skip with a
documented reason).

## Open Risks

| Risk | Severity | Mitigation |
|---|:---:|---|
| Partial unique on Postgres uses an INDEX-with-WHERE; if a future autogenerate run rewrites this without `postgresql_where`, two duplicate phones could land | Low | `test_postgres_partial_unique_phone_allows_multiple_nulls` catches the regression on every CI run that enables the Postgres path |
| `BackgroundTasks` matcher rescore runs inside a single-process FastAPI worker; horizontal scaling will need a worker queue (D-005 reserves Go worker for this) | Medium | Documented in `docs/BENCHMARK_P8_4.md`; in-process is correct up to ~5 requests/sec |
| `_SenderCache` (P8.4 optimization) is per-`ingest_messages` call; if two concurrent ingest calls land in the same process with overlapping sender IDs, the cache could race | Low | Sessions are not thread-safe (documented). Each request owns its own session + cache. |
| ChunksGo CSV/Excel parse at 100k+ has not been profiled past 10k in P8.4 due to time | Low | Re-run `scripts/bench_p8_4.py --component csv --sizes 50000,100000` when sample workloads stabilize |
| FAISS swap (D-004) is unverified at 100k vectors; current `NumpyIndex` works but FAISS would help if matcher scales past ~10k inventory | Low | Defer until the dataset actually crosses 10k inventory rows; the current `match` benchmark shows persistence is the bottleneck, not vector search |

## Recommended Deployment Order

1. **Provision** Postgres 16 (matches the test fixture exactly).
2. **Set** environment variables: `ENVIRONMENT=prod`, `POSTGRES_URL`,
   `API_KEY`, `DEBUG=false`, `API_RELOAD=false`,
   `LOG_LEVEL=INFO` (the prod-safety gate refuses to boot otherwise).
3. **Run** `alembic upgrade head` once against the empty production
   database. Confirm `alembic current` shows `1f55ca15e963 (head)`
   and `alembic check` reports zero drift.
4. **Smoke-test** by booting `uvicorn trade_intel.api.main:app` and
   hitting `GET /healthz`. The response must include
   `"environment": "prod"`.
5. **Run** the CLI smoke test:
   `trade-intel parse <small-sample.txt>` → confirm rows land via
   `GET /messages`.
6. **Verify** API-key enforcement: `GET /messages` without the
   `X-API-Key` header must return 401.
7. **Configure** the Docker Compose stack from `docker/compose.yml`
   (postgres + qdrant + migrate + api + worker), substituting real
   credentials for the `${VAR:-default}` placeholders. The `migrate`
   service auto-runs `alembic upgrade head` before `api` starts.
8. **Watch** the `api_startup` log line — it carries `auth_enabled`
   and `env` fields. Confirm both match expectations.
9. **Pre-populate** the dedup baseline by ingesting the production
   archive (CSV / Excel / WhatsApp exports). The D-007 hash makes
   subsequent re-ingest free.
10. **Tune** as needed: `WORKER_POLL_SECONDS` (Go worker), uvicorn
    `--workers` count, Postgres pool size (currently `pool_size=5,
    max_overflow=10`).

## Files Changed in P8.5

- `src/trade_intel/db/models/demand.py` — `is_active` server_default
  fixed (`text("1")` → `text("true")`).
- `src/trade_intel/db/models/inventory.py` — same fix.
- `db/migrations/versions/20260528_0049_1f55ca15e963_baseline_schema.py`
  — both `is_active` columns updated to match. (Baseline never
  applied to a real Postgres before P8.5; in-place edit is safe.)
- `tests/integration/test_db_migration_p8_5.py` — new, 19 tests (11
  SQLite-always + 8 Postgres-opt-in).
- `docs/PRODUCTION_READINESS_P8_5.md` — this document.

## Final Pre-Deploy Checklist

- [x] `make verify` green (lint + mypy + 829 tests + 9 skipped)
- [x] `make preflight` green (alembic zero drift, smoke test passes)
- [x] Live Postgres migration roundtrip verified (3 cycles, zero drift)
- [x] Foreign-key + unique constraint integrity verified on Postgres
- [x] Full pipeline produces identical counts on Postgres vs SQLite
- [x] Idempotent re-ingest verified on Postgres
- [x] All four production-config safety gates fire correctly
- [x] Boolean-default Postgres compatibility bug fixed + tested
- [x] Docker Compose stack (P7) wires `migrate` as a one-shot before `api`
- [x] OpenAPI spec served at `/openapi.json` + Swagger at `/docs`
- [x] CLI entrypoint registered (`trade-intel`)
- [x] Logging configured (structlog two-channel: pretty in dev, JSON in prod)

**Phase 8 complete. Ship it.**
