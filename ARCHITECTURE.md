# ARCHITECTURE — trade-intel

## System Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                          DATA SOURCES                            │
│   WhatsApp .txt   |   CSV   |   Excel   |   Manual messages      │
└────────────────────────────────┬─────────────────────────────────┘
                                 │
                                 ▼
┌──────────────────────────────────────────────────────────────────┐
│                    INGESTION LAYER (Golang worker)               │
│   - file watcher / queue                                         │
│   - format detection                                             │
│   - byte-stream to line-stream                                   │
│   - POSTs structured rows to /ingest                             │
└────────────────────────────────┬─────────────────────────────────┘
                                 │
                                 ▼
┌──────────────────────────────────────────────────────────────────┐
│                       PARSING LAYER (Python)                     │
│   parsers/whatsapp.py    parsers/csv_parser.py                   │
│   - tokenize messages                                            │
│   - extract sender + timestamp                                   │
│   - emit raw message records                                     │
└────────────────────────────────┬─────────────────────────────────┘
                                 │
                                 ▼
┌──────────────────────────────────────────────────────────────────┐
│                   NORMALIZATION + EXTRACTION                     │
│   - product normalizer (dictionary + fuzzy)                      │
│   - intent classifier (rules → ML)                               │
│   - entity extractor (qty, storage, price, location, condition)  │
│   - urgency tagger                                               │
└────────────────────────────────┬─────────────────────────────────┘
                                 │
                                 ▼
┌──────────────────────────────────────────────────────────────────┐
│                       PERSISTENCE LAYER                          │
│   SQLite (dev) / PostgreSQL (prod)                               │
│   tables: messages, buyers, sellers, inventory, demand,          │
│           matches, embeddings, groups, lead_pipeline             │
│   dedup via message hash                                         │
│   identity merge via phone + name root + SKU consistency         │
└────────────────────────────────┬─────────────────────────────────┘
                                 │
                                 ▼
┌──────────────────────────────────────────────────────────────────┐
│                   EMBEDDING + VECTOR INDEX                       │
│   sentence-transformers → FAISS                                  │
│   index per entity type: messages, inventory, demand             │
└────────────────────────────────┬─────────────────────────────────┘
                                 │
                                 ▼
┌──────────────────────────────────────────────────────────────────┐
│                       MATCHING ENGINE                            │
│   exact → similar → probable                                     │
│   score = semantic × spec_fit × recency × evidence               │
│   writes `matches` + creates `lead_pipeline` row                 │
└────────────────────────────────┬─────────────────────────────────┘
                                 │
                                 ▼
┌──────────────────────────────────────────────────────────────────┐
│                     QUERY / OUTPUT LAYER                         │
│   FastAPI REST  |   CLI (Typer)  |   Dashboard renderer          │
│   "who has X" / "who needs X" / "opportunities" / "brief"        │
└──────────────────────────────────────────────────────────────────┘
```

## Data Flow

**Ingest:** Source file → worker queue → parser → normalizer → extractor → DB rows → embedding job → FAISS update → match candidate computation.

**Query:** API/CLI request → query planner → DB lookup + FAISS search → match scorer → response renderer (JSON / dashboard).

**Pipeline progression:** Trader action (sent ping, got reply) → API `/leads/{id}/advance` → state machine updates `lead_pipeline.stage`.

## Module Responsibilities

| Module | Owns |
|---|---|
| `parsers/` | Raw text → structured raw messages (no domain interpretation) |
| `normalization/` | Slang → canonical product names; dictionary + fuzzy fallback |
| `core/intent/` | Buyer / Seller / Broker / Unknown classification + urgency |
| `core/entity/` | Qty, storage, price, condition, location extraction |
| `ml/embeddings/` | Single source of truth for vector encoding |
| `ml/faiss_index/` | Index lifecycle: build, persist, reload, search |
| `matching/` | Candidate generation + scoring + grading |
| `pipeline/` | Lead state machine (NEW → … → CLOSED/LOST) |
| `core/freshness/` | Recency tier computation |
| `api/` | HTTP routes, request validation, response shaping (thin) |
| `cli/` | Typer commands, dashboard rendering |
| `db/` | Models, sessions, migrations |
| `worker/` (Go) | File watching, queueing, parallel ingestion bursts |

## Database Schema (v1)

Source of truth: `db/schema.sql`. Highlights:

- **9 tables:** `groups`, `sellers`, `buyers`, `messages`, `inventory`, `demand`, `matches`, `embeddings`, `lead_pipeline`.
- **Identity:** `sellers.phone` and `buyers.phone` are unique when present. Without phone, identity merge is heuristic (`canonical_name` + alias JSON array).
- **Dedup:** `messages.hash` is unique.
- **Indices:** intent, ts, normalized_product, match grade/score, pipeline stage.
- **No SQLite-only types** — only `INTEGER`, `TEXT`, `REAL`, `TIMESTAMP`, `BOOLEAN`. Migration to Postgres is mechanical.

### Entity-Relationship summary

```
groups   1──*   messages   *──1   sellers
                  │   │            │
                  │   └──1──*  buyers
                  │                │
                  └──1──*  inventory  ──┐
                  └──1──*  demand    ──┤
                                       ▼
                                    matches  ──*──1  lead_pipeline
                                       ▲
                                       │
                                  embeddings (entity_type/id polymorphic)
```

## Semantic Matching Logic

1. **Candidate generation (recall):**
   - Exact: same `normalized_product` + (storage match OR storage unspecified on one side)
   - Vector: FAISS top-K on demand→inventory and inventory→demand
2. **Scoring (precision):**
   ```
   semantic_score   ∈ [0,1]   # cosine similarity from FAISS
   spec_fit         ∈ [0,1]   # storage, condition, qty alignment
   recency_mult     ∈ [0.05,1.0]  # freshness tier (see CLAUDE.md)
   evidence_weight  ∈ [0.5,1.0]   # post completeness, repeat behavior

   final_score = semantic_score * spec_fit * recency_mult * evidence_weight
   ```
3. **Grading:**
   - A+ ≥ 0.85, A ≥ 0.70, B ≥ 0.50, C < 0.50
4. **Match types** recorded in `matches.match_type`: `exact`, `similar`, `probable`.

## Identity Merge (cross-group dedup)

Merge confidence scoring:

| Signal | Weight |
|---|---|
| Same phone number | +60 |
| Identical canonical name | +25 |
| Same name root (Ali / Ali Mobiles / Ali DXB) | +15 |
| Same SKU posted within 24h | +15 |
| Identical wording (≥80% similarity) | +10 |

Merge threshold: ≥ 75. Below that → keep separate, store as a *candidate merge* for human review.

## Freshness & Pipeline (cross-cutting)

- Recency tier is **computed on read** from `posted_at` — never stored. (See DECISIONS.md D-010.)
- `inventory.is_active` and `demand.is_active` flip to `false` only on explicit signal (seller says "sold out", buyer says "got it") — not on age alone.
- `lead_pipeline.stage` is the only mutable lead state; transitions logged via `last_action` + `last_action_at`.

## API Surface (live as of Phase 6)

Run: `uvicorn trade_intel.api.main:app --reload` → Swagger at `http://127.0.0.1:8000/docs`.

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/healthz` | public | Liveness probe |
| POST | `/ingest` | required | Multipart upload (`file`); auto-detects whatsapp/csv/excel by extension; `source_type` form field overrides; `?run_matcher=true` schedules background rescore |
| GET | `/messages` | required | Paged list; filters: `intent`, `since`, `limit`, `offset` |
| GET | `/messages/{id}` | required | Single message |
| GET | `/sellers` | required | Paged list, ordered by post_count desc |
| GET | `/sellers/{id}` | required | Seller + eagerly-loaded `inventory_items` |
| GET | `/buyers` | required | Paged list, ordered by request_count desc |
| GET | `/buyers/{id}` | required | Buyer + eagerly-loaded `demands` |
| POST | `/match` | required | Body `{query, side: seller\|buyer, k}` → ranked matches |
| GET | `/leads` | required | Pipeline list; filters: `stage`, `priority` |
| GET | `/leads/{id}` | required | Single lead |
| POST | `/leads/{id}/advance` | required | Body `{to_stage, action, notes}` — transitions stage |
| GET | `/brief` | required | Daily Deal Board JSON |
| GET | `/openapi.json`, `/docs`, `/redoc` | public | Auto-generated docs |

Auth: when `API_KEY` env var is set, every non-public endpoint requires `X-API-Key: <key>` header (401 on miss/wrong). When unset (dev), the API runs open. Prod environments are rejected at boot if `api_key` is None (see `Settings._enforce_prod_safety`).

## Configuration

All env-driven via `pydantic-settings` (Phase 2):
- `DATABASE_URL`
- `EMBED_MODEL_NAME`
- `FAISS_INDEX_DIR`
- `LOG_LEVEL`
- `WORKER_QUEUE_URL` (Phase 5+)
- `API_KEY` (Phase 6)

## Phases (status)

- ✅ PHASE 1: Architecture + DB + folder structure
- ✅ PHASE 2: Project scaffold (ORM + Alembic + Makefile + logging)
- ✅ PHASE 3: WhatsApp + CSV + Excel ingest pipeline
- ✅ PHASE 4: Semantic matching engine
- ✅ PHASE 5: Typer CLI (10 commands + JSON mode)
- ✅ PHASE 6: FastAPI HTTP API (12 endpoints + API-key auth + BackgroundTasks)
- ✅ PHASE 7: Dockerization (api + worker Dockerfiles + 5-service compose)
- ✅ PHASE 8: Optimization + testing — coverage audit (P8.1 → 93%), E2E UAE-wholesale integration suite (P8.2 → 94%), hypothesis property tests (P8.3, 51 tests), perf optimization + scale benchmark (P8.4: ingest +22%, matcher +31%, 100k ingest verified), Postgres migration verified + production readiness (P8.5, live Postgres 16 roundtrip + byte-identical pipeline parity vs SQLite + 12-item pre-deploy checklist green). Project is production-ready.
- See TASKS.md for sub-task detail.

## Production Readiness

The project ships green against Postgres 16 + the Docker Compose stack
described above. The final readiness summary, deployment order, and
open-risk register live in `docs/PRODUCTION_READINESS_P8_5.md`.
Benchmark methodology + numbers (parse 3.3 k msg/s, ingest 192 msg/s
at 10k / 120 msg/s at 100k, match 17 dem/s) live in
`docs/BENCHMARK_P8_4.md`. Coverage audit + recommended-test triage
live in `docs/COVERAGE_AUDIT_P8_1.md`.
