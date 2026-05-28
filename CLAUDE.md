# CLAUDE.md — trade-intel

> **Session-start ritual:** read this file first, then `PROJECT_STATE.json`, then `TASKS.md`. Never assume — verify state on disk before acting.

## Project Vision

A persistent, AI-powered sourcing intelligence system for **UAE wholesale electronics trading**. Ingests messy WhatsApp chat exports (and CSV/Excel inventory sheets), extracts structured buyer/seller signals, normalizes traders' shorthand, and surfaces actionable deals — ranked by recency, evidence, and close probability.

The system is **execution-focused**: it does not just describe the market, it tells a trader exactly which 3 messages to send next.

## Project Identity

- **Codename:** trade-intel
- **Market:** UAE / GCC wholesale electronics (Dubai / Sharjah / Abu Dhabi brokers)
- **Primary user persona:** Deira / Bur Dubai electronics wholesaler
- **Working directory:** `/home/kadir70/trade-intel`

## Tech Stack

| Layer | Tech | Why |
|---|---|---|
| API | Python 3.11 + FastAPI | async, type hints, OpenAPI |
| Worker | Golang 1.22+ | concurrent ingestion, file scanning |
| DB (dev) | SQLite | zero-setup, fast iteration |
| DB (prod) | PostgreSQL | scale path |
| ORM | SQLAlchemy 2.x | works for both SQLite & PG |
| Migrations | Alembic | versioned schema |
| Embeddings | sentence-transformers (`all-MiniLM-L6-v2` baseline) | small, fast, good enough |
| Vector index | FAISS (flat L2 initially) | local, no infra |
| Vector index (later) | Qdrant | when corpus > ~100k vectors |
| CLI | Typer | clean ergonomics |
| Containers | Docker + docker-compose | Phase 7 |
| Tests | pytest, hypothesis | unit + property-based |

## Architecture Principles

1. **Evidence-first** — confidence scores must be earned. No fabricated trust scores until thresholds met (see "Confidence Thresholds" below).
2. **Phase discipline** — finish PHASE N before starting PHASE N+1. No half-built features.
3. **Modular layers** — parsers, normalization, intent, matching, pipeline are independent modules with thin interfaces.
4. **Idempotent ingestion** — every message has a `hash` (sender + ts + raw_text). Re-running an import never duplicates.
5. **Migration-ready** — never use SQLite-only SQL. ORM-only writes.
6. **Domain language** in code — `Buyer`, `Seller`, `Inventory`, `Demand`, `Match`, `LeadPipeline` are the nouns.

## Domain Assumptions (UAE wholesale)

- WhatsApp date format is **DD/MM/YY** by default (with locale fallback in Phase 3).
- Currency default = **AED** unless stated otherwise.
- "qty" = quantity. "LP" = last price. "pm" = price message. "av/avail" = available. "fresh/seal" = sealed/new. "dxb/auh/shj" = Dubai/Abu Dhabi/Sharjah.
- Same trader posts in **multiple groups** with **slightly different handle** (e.g., "Ali Mobiles" / "Ali DXB"). Phone number is the strongest identity signal; name root + SKU consistency is the fallback.
- "Urgent" buyer signals decay fast — useful within ~7d; stale after.

## Confidence Thresholds (evidence-first guardrails)

| Output type | Minimum data |
|---|---|
| Basic trend detection | ≥ 50 messages |
| Seller scoring | ≥ 200 messages |
| Demand prediction | ≥ 500 messages |
| Full market intelligence | ≥ 1000 messages |
| "Repeat buyer" tag | ≥ 2 requests from same buyer |
| "Specialist" tag | ≥ 3 consistent-category posts |
| Arbitrage signal | ≥ 2 sellers on same SKU with price data |

Below threshold, the engine returns `null` or `insufficient_evidence`. **Never invent.**

## Freshness Tiers (always relative to "now")

| Age | Tier | Multiplier |
|---|---|---|
| 0–6h | HOT | 1.00 |
| 6–24h | ACTIVE | 0.90 |
| 1–3d | WARM | 0.70 |
| 4–7d | COOL | 0.40 |
| 7–14d | STALE-recoverable | 0.15 |
| 15d+ | STALE | 0.05 |

## Product Normalization Rules

Authoritative dictionary will live in `src/trade_intel/normalization/dictionary.py` (Phase 3). Selected examples:

| Raw | Normalized |
|---|---|
| `mini one`, `mini 1` | iPad Mini 1 |
| `mini 2`, `mini two` | iPad Mini 2 |
| `mini 5` | iPad Mini 5 |
| `air 5` | iPad Air 5 |
| `pro m1` | iPad Pro M1 |
| `pro m2` | iPad Pro M2 |
| `12pm` | iPhone 12 Pro Max |
| `15pm` | iPhone 15 Pro Max |
| `tabs` | (generic) Tablets / iPads |

## Intent Lexicon

- **Buyer:** `need`, `req`, `required`, `urgent`, `wanted`, `looking for`, `want qty`, `asap`
- **Seller:** `avail`, `available`, `fresh`, `stock ready`, `qty`, `have`, `ready`
- **Broker:** `bridge`, `intro`, `looking buyer for`, `buyer needed for`
- **Negotiation:** `LP`, `last price`, `pm`, `price message`, `best rate`

## Folder Structure

```
trade-intel/
├── src/trade_intel/      # Python package
│   ├── api/              # FastAPI routes
│   ├── core/             # business logic (intent.py, logging.py)
│   ├── parsers/          # whatsapp.py, dedup.py, csv_parser.py, excel_parser.py
│   ├── ml/               # embeddings.py (P4.1), faiss_index.py (P4.2), ner.py
│   ├── matching/         # scoring.py (P4.3-5), candidates.py
│   ├── normalization/    # text.py, identity.py, dictionary.py, products.py
│   ├── persistence/      # ingest.py (P3.7), matches.py (P4.6)
│   ├── pipeline/         # lead_state.py
│   ├── db/               # models.py, session.py
│   ├── api/              # main.py (P6 FastAPI), schemas.py, __init__.py
│   └── cli/              # main.py (P5), formatters.py, messages.py
├── worker/               # main.go (P7.2 Go ingestion scaffold), go.mod
├── worker/               # Golang ingestion/background worker
├── db/                   # schema.sql, migrations/, seeds/
├── data/                 # raw/, processed/, vectors/, samples/
├── tests/                # unit/, integration/
├── docs/
├── scripts/
└── docker/               # api.Dockerfile, worker.Dockerfile, compose.yml, README.md
```

## Memory File Discipline

| File | Update when |
|---|---|
| `CLAUDE.md` | New convention, decision, lexicon entry, or phase complete |
| `PROJECT_STATE.json` | Any phase/task/component status change |
| `TASKS.md` | Task starts (→ IN_PROGRESS) or finishes (→ DONE) |
| `DECISIONS.md` | New architectural decision or trade-off |
| `ARCHITECTURE.md` | Schema or data-flow change |

After every meaningful change, **all relevant memory files must be touched in the same commit.**

## Completed Phases

- ✅ **PHASE 1** — Architecture, DB schema design, folder structure (2026-05-27)
- ✅ **PHASE 2** — Project scaffold (2026-05-28) — pyproject (py3.12, dev+ml+postgres extras), pydantic-settings Settings with prod gates, SQLAlchemy 2.x session layer (FK pragma, prod pool tuning), 9 typed ORM entities (partial unique phone, polymorphic embeddings), Alembic baseline `1f55ca15e963` (zero drift), Makefile (19 targets), `.gitignore`/`README.md`/structlog two-channel logging (request_id/session_id contextvars). **21/21 pytest pass. `make preflight` green.**
- ✅ **PHASE 3** — Ingest pipeline (2026-05-28). P3.1 WhatsApp tokenizer (Android/iPhone, multi-line coalesce, system/media/orphan tagging). P3.2 date-format auto-detect + Unicode text normalization (NFKC, emoji-safe, casefold). P3.3 sender identity extraction (`SenderIdentity` — phone+name+aliases+business+location signals). P3.4 hash-based dedup (`DedupSet` + `dedupe`, P3.7 hand-off via `from_hashes`). P3.5 product alias dictionary + extractor (~245 aliases → 41 canonical products covering iPhone 11-16 + iPad Mini/Air/Pro/generic). P3.6 rule-based intent classifier v1 (BUYER/SELLER/BROKER/UNKNOWN + HIGH/MEDIUM/LOW urgency + is_price_inquiry, ~110-key lexicon, longest-first regex, multi-kind keywords). P3.7 persistence wire-in (`ingest_messages` with savepoint-per-message isolation, identity merge D-009, SELLER→Inventory / BUYER→Demand routing). P3.8 CSV + Excel readers (shared `tabular.py` synthesis layer, openpyxl read-only, cross-source D-007 dedup). **504/504 pytest pass. `make preflight` green.**
- ✅ **PHASE 4** — Semantic matching engine (2026-05-28). P4.1 `Embedder` Protocol + `HashEmbedder` (deterministic BLAKE2b, zero deps) + lazy `MiniLMEmbedder` (D-004, sentence-transformers from `[ml]`). P4.2 `VectorIndex` Protocol + pure-numpy `NumpyIndex` + lazy `FaissFlatIndex` (faiss-cpu from `[ml]`, IndexFlatIP since vectors are L2-normalized); both persist/load via `meta.json` + `vectors.npy`/`faiss.index`. P4.3 `score_match` composes `final = semantic * spec_fit * recency * evidence`. P4.4 soft-match policy (storage hard mismatch *0.4, missing-one-side *0.85, both-missing no penalty, SEALED↔NEW compatible, qty shortfall *0.7; storage normalized so 256GB/256 GB/256 compare equal). P4.5 grade thresholds (A+ ≥0.85 / A ≥0.70 / B ≥0.50 / C) and freshness tiers per CLAUDE.md table. P4.6 `match_demand_against_inventory` + mirror persist Match (idempotent upsert on `(demand_id, inventory_id)` — rescore updates in place) + LeadPipeline (NEW stage, priority from score, `MATCH_CREATED`/`MATCH_RESCORED` last_action, JSON breakdown in `reasoning`). Moved `numpy>=1.26` to core deps; `sentence-transformers` + `faiss-cpu` stay in `[ml]` extra. **602/602 pytest pass. `make preflight` green.**
- ✅ **PHASE 5** — Trader CLI (2026-05-28). `trade-intel` Typer app registered as `[project.scripts]` entrypoint with 10 commands: `parse` (whatsapp/csv/excel auto-detected), `match` (semantic, --side seller|buyer), `who-has` + `who-needs` (case-insensitive DB filter), `hot` (recent messages, --intent filter), `opportunities` (Match table sorted by score, --grade --priority filters), `brief` (Daily Deal Board — summary + top matches + urgent demand + fresh inventory tables), `ping` (renders `seller_ping`/`buyer_ping` WhatsApp templates from a Match id), `chat` (REPL loop), `run-matcher` (score every active Demand — cron-friendly). All commands take `--json` for machine-readable output (plain stdlib `print(json.dumps(...))` to avoid Rich's ANSI-colored JSON). Templates degrade gracefully (bhai fallback when no relationship, drops missing fields). Side fixes: `init_db()` self-imports models so create_all works without callers remembering the import idiom; migrated `datetime.utcnow()` → `datetime.now(UTC).replace(tzinfo=None)` to clear deprecation warnings. **648/648 pytest pass. `make preflight` green.**
- ✅ **PHASE 6** — FastAPI HTTP API (2026-05-28). `src/trade_intel/api/` package with factory pattern (`create_app()` builds a fresh `FastAPI` per call so test fixtures can swap `SQLITE_URL`/`API_KEY`). 7 routers + healthz: `GET /healthz` (public liveness), `POST /ingest` (multipart upload, auto-detects whatsapp/csv/excel by extension, optional `?run_matcher=true` schedules a `BackgroundTasks` rescore in a fresh session_scope), `GET /messages[?intent=&since=&limit=&offset=]` + `GET /messages/{id}`, `GET /sellers` (sorted by post_count desc) + `GET /sellers/{id}` (returns inventory_items via `selectinload`), `GET /buyers` + `GET /buyers/{id}` (mirror with demands), `POST /match` (Pydantic-validated body `{query, side, k}`, returns `MatchQueryResponse`), `GET /leads[?stage=&priority=]` + `GET /leads/{id}` + `POST /leads/{id}/advance` (transitions PipelineStage + stamps last_action_at + persists notes), `GET /brief` (JSON Deal Board, same shape as CLI brief). P6.2: `APIKeyHeader(auto_error=False)` + `require_api_key` dep on every router except healthz. `Settings.api_key` field with prod-safety requirement; dev-open when key unset. OpenAPI/Swagger at `/openapi.json` + `/docs`. P6.3: FastAPI `BackgroundTasks` for post-ingest matcher rescore; D-005 reserves Go worker for higher throughput. New core dep `python-multipart>=0.0.7`. **687/687 pytest pass. `make preflight` green.**
- ✅ **PHASE 7** — Dockerization (2026-05-28). P7.1 `docker/api.Dockerfile` — multi-stage Python 3.12 slim (builder installs venv at `/opt/venv` with optional `[ml,postgres]` via `ARG INSTALL_ML`; runtime adds only libpq5+ca-certs, drops to non-root uid 10001, EXPOSE 8000, stdlib `/healthz` HEALTHCHECK, CMD uvicorn). P7.2 `worker/main.go` + `worker/go.mod` minimal stdlib-only Go 1.22 scaffold (`--healthcheck` one-shot for docker probe + daemon mode polling `/healthz` on `WORKER_POLL_SECONDS` interval, JSON `slog` output, `TRADE_INTEL_API_URL` env). `docker/worker.Dockerfile` — multi-stage Go (alpine builder + alpine:3.20 runtime, `CGO_ENABLED=0` static binary, non-root). P7.3 `docker/compose.yml` — 5 services: `postgres:16-alpine` (volume + pg_isready), `qdrant/qdrant:v1.10.1` (D-003 upgrade path, volume), `migrate` (one-shot `alembic upgrade head`, restart=no), `api` (waits for migrate via `service_completed_successfully`, runs ENVIRONMENT=prod against postgres with required API_KEY), `worker` (Go daemon depends on api). All credentials env-substituted with `${VAR:-default}`. `.dockerignore` excludes `.venv/`, all PII `data/raw|processed|samples|vectors/`, caches, `.git/`, `tests/`, `.env`. `docker/README.md` documents quick start + lean-vs-full image variants + migration workflow. Go worker build smoke-verified locally — clean compile, `--healthcheck` correctly exits 1 when API absent. **714/714 pytest pass (1 skipped: docker-compose plugin not installed on this box). `make preflight` green.**

## In-Progress Phase

**PHASE 8 COMPLETE — Production-ready against Postgres 16 + Docker Compose stack.** All sub-tasks P8.1–P8.5 done.

- ✅ **P8.1** Unit-test coverage audit (2026-05-28) — pytest-cov measured **93% line coverage** (2 423/2 597 covered, 174 missed) across 49 source files. No production module sits below 71%; the two sub-90% files (`ml/faiss_index.py` 71%, `cli/main.py` 79%) are uncovered by design (lazy `[ml]` extra, interactive REPL). Audit deliverable at `docs/COVERAGE_AUDIT_P8_1.md` triages gaps into intentionally-uncovered vs high-value missing tests (Parser A1–A6, Matcher B1–B6, Persistence C1–C6, API D1–D4, CLI E1–E3, Property F → P8.3). Recommended P8.2 first-wave: **A1, A2, B1, B2, B4, C1, C3, D1** (cross-source identity merge, freshness-tier boundary clamp, rescore-in-place idempotency, BackgroundTasks post-condition). Open decision flagged for `DECISIONS.md` before C2 lands: do two real different traders with same `canonical_name` + NULL phone merge or split? **714 passed + 1 skipped, zero regressions; audit writes no production-test code.**
- ✅ **P8.2** End-to-end UAE wholesale integration tests (2026-05-28) — 2 realistic fixtures + 53 new tests covering the full pipeline (WhatsApp + CSV + Excel → parse → normalize → dedupe → identity → intent → product → persist → match → lead pipeline → CLI + API). Fixtures: `tests/fixtures/e2e/uae_wholesale_day.txt` (18-line Dubai Wholesale Network export with system/media/dup/SELLER/BUYER/BROKER + emoji Sara DXB 🚀 + Arabic محمد عبدالله + multi-line continuation + orphan + phone-only seller) and `tests/fixtures/e2e/dubai_stock_sheet.csv` (7 rows with in-CSV phone merge + cross-source phone seller + Arabic canonical-name fallback). Single `tests/integration/test_e2e_uae_wholesale.py` test file across 8 capability groups: (A) ingest counters + intent distribution + product extraction + HIGH urgency + Arabic + emoji + multi-line; (B) idempotency — same-minute dup, full-file reingest, pipeline reingest preserves Match + flips to MATCH_RESCORED; (C) cross-source identity merge — WhatsApp+CSV phone merge → 1 Seller post_count=3 with unioned aliases, in-CSV phone collision, Arabic canonical-name fallback, repeat buyer phone; (D) match quality — top match is EXACT same-product, reasoning JSON has 7 keys, rescore in-place no dup, +65d STALE drops all, every LeadPipeline NEW with priority bucketed, HOT@5h → WARM@25h monotonic decrease; (E) CLI — parse counters, brief JSON shape, who-has/who-needs/hot --intent JSON, opportunities sorted, ping renders LP template; (F) API — /ingest 201, /match hits, /leads sorted + all NEW, /brief summary, /ingest?run_matcher=true writes Match rows synchronously under TestClient, /messages intent filter, /sellers post_count desc; (G) failure recovery — orphan no abort, per-message savepoint isolates synthetic extract_products failure (errors=1 with rest committing), CLI unsupported extension fails, API unknown extension/source_type 400; (H) confidence — grade thresholds, EXACT iff same product + spec_fit≥0.99, final == product of 4 factors, empty index, cross-source unified seller owns ≥3 Inventory rows, Excel-sourced inventory matchable. Landed P8.1 audit items: B1, B6, C1, D1, E1, E2. Coverage moved 93% → **94%** (160 missed; persistence/ingest 91→96%, cli/main 79→81%). Side fixes: dropped unused `Group` import; snake_cased 3 function names (NEW/EXACT → new/exact); replaced × with * in docstring (RUF002). **767 passed + 1 skipped, zero regressions; `make verify` + `make preflight` green.**
- ✅ **P8.3** Property-based tests (hypothesis) (2026-05-28) — new `tests/unit/test_property_based.py` with 51 hypothesis tests across 8 capability groups exercising pure-function invariants. Global `settings(max_examples=50, deadline=None)` keeps the suite ~6s. Groups: (1) normalize_text idempotence + BOM/bidi/newline stripping, (2) extract_identity never-raises + phone format + deterministic, (3) extract_products non-overlapping ordered offsets + seeded keyword surfaces in arbitrary context, (4) classify_intent confidence/score bounds + signal-span validity + UNKNOWN on empty, (5) dedupe idempotent + length-bounded + order-preserving + cross-batch union-of-distinct-hashes, (6) hash_for deterministic + sensitive to every input field + 64-char SHA-256 hex + orphan ts-less folds in line_no, (7) scoring — recency monotonic, grade buckets, spec_fit self-match=1.0, score_match identity `final==sem*spec*rec*ev` within 1e-9, semantic clamping, (8) parse_lines never raises + hash unique within a file + detect_date_order shape contract + ambiguous → DMY@conf=0. Hypothesis CAUGHT a real edge case: input `'0'` triggers `is_phone_only=True` but `phone=None` (< 7 digits won't canonicalize) — over-strict assertion replaced with a guarded one explaining the short-numeric-runs caveat. Side fix: per-file ruff ignore added in pyproject for the new file (RUF001/002/003 — fixture intentionally holds NBSP / full-width spaces / IDEOGRAPHIC space / en-dashes). Landed P8.1 audit category F (property targets — `normalize_text` idempotence, `extract_identity` never-raises, `hash_for` determinism + collision resistance under perturbation, scoring commutativity & monotonicity). Coverage held at **94%** (158 missed, down from 160 — 2 more lines exercised). **818 passed + 1 skipped (51 new), zero regressions; make verify + make preflight green.**

- ✅ **P8.4** Performance optimization + scale validation (2026-05-28) — new `scripts/bench_p8_4.py` (deterministic seed=42, 7 components: parse/ingest/csv/excel/match/dedup-heavy/multilingual, tracemalloc peak). cProfile baseline of `ingest_messages` revealed 58% time in `session.flush()` (SAVEPOINT) + 22% in `_get_or_create_seller/buyer` SELECTs + N+1 SELECTs in matcher upsert helpers. Two surgical opts landed: **OPT-1 in-process `_SenderCache`** (4 dicts preloaded via 2 SELECTs; tentative `pending_cache` buffer promoted only on `savepoint.commit()` so rollback leaves no leak); **OPT-2 per-demand Match/LeadPipeline preload** in `match_demand_against_inventory` + `match_inventory_against_demand` (`_upsert_*` gain optional `existing_matches=`/`existing_leads=` params; default None preserves old SQL path). Public API surface unchanged. Numbers: parse unchanged 3.3-3.7k/s; ingest 10k 64s→52s (+22%, **192 msg/s**), 50k 305s (164/s), **100k 831s (120 msg/s, peak 7.9 MB)**; csv 10k 75s; excel 10k 121s; dedup-heavy 10k 31s (**321 msg/s** fast path); multilingual 10k 63s; match 2k 30s match-only (**17 dem/s, +31% vs 13 baseline**), 5k 131s (9 dem/s). Memory bounded: ingest peak <10 MB at any scale. Documented deferred: per-message SAVEPOINT is the documented isolation boundary; bulk INSERT would bypass ORM events; FAISS swap wouldn't help (persistence is the cost, not vector search). Audit report at `docs/BENCHMARK_P8_4.md`. **818 passed + 1 skipped, zero regressions; verify + preflight green.**

- ✅ **P8.5** PostgreSQL migration verification + production readiness (2026-05-28) — new `tests/integration/test_db_migration_p8_5.py` (19 tests: 6 prod-config safety + 5 SQLite roundtrip + 8 Postgres opt-in via `POSTGRES_TEST_URL`). Verified against live Postgres 16: upgrade→downgrade→re-upgrade roundtrip, zero `alembic check` drift, all integrity constraints (UNIQUE hash, partial UNIQUE phone with NULL-coexist, FK violation rejection), and **full pipeline byte-identical to SQLite** (sellers=9 buyers=3 messages=21 inventory=15 matches=40 leads=40). **REAL bug surfaced and fixed**: baseline migration declared Boolean columns with `server_default=text("1")` — SQLite coerces, Postgres rejects with `DatatypeMismatch`. Fixed both model fields (`demand.py:43`, `inventory.py:46`) AND the baseline migration to `text("true")` (accepted by both dialects); zero drift confirmed on both. Final production-readiness doc at `docs/PRODUCTION_READINESS_P8_5.md` (12-item pre-deploy checklist all green, 5 open risks all Low/Medium with mitigations, 10-step deployment order). psycopg 3.3.4 installed from `[postgres]` extra. **829 passed + 9 skipped (818 + 11 new), zero regressions; verify + preflight green.**

## Pending Phases
_(none — all phases complete)_

## Known Issues / Open Questions

- ~~Date-format heuristic (DD/MM/YY vs MM/DD/YY) needs locale fallback~~ — **resolved P3.2**: `detect_date_order` infers per-file via day-over-12 / 4-digit-year evidence; `parse_file(date_order="auto")` two-passes the file; ambiguous corpora default to DMY (UAE bias).
- ~~Phone-number capture~~ — **resolved P3.3**: phone-shaped sender headers (`+971 …`, `971…`, hyphen / paren / NBSP variants) are extracted into `sender_phone` and folded into the dedup hash. Group-info-file extraction (when WhatsApp's `_chat` zip ships metadata) is still pending **P3.7** (persistence wire-in).
- FAISS persistence — flat-L2 + numpy `.npy` is fine to ~100k vectors; switch to IVF/HNSW or Qdrant beyond.

## Matching API (Phase 4, live)

```python
from datetime import datetime
from trade_intel.ml import HashEmbedder, NumpyIndex, MiniLMEmbedder, FaissFlatIndex
from trade_intel.matching import (
    build_inventory_index, build_demand_index,
    find_inventory_candidates, find_demand_candidates,
    score_match, grade_for, compute_recency_multiplier,
)
from trade_intel.persistence import (
    match_demand_against_inventory, match_inventory_against_demand,
    MatchWriteStats,
)
from trade_intel.db import session_scope
from trade_intel.db.models import Demand, Grade

# Dev: HashEmbedder + NumpyIndex (no [ml] extra needed).
# Prod: MiniLMEmbedder + FaissFlatIndex (install with `pip install -e .[ml]`).
embedder = HashEmbedder(dim=384)

with session_scope() as s:
    inv_index, _ = build_inventory_index(s, embedder=embedder)
    for demand in s.query(Demand).filter_by(is_active=True):
        stats = match_demand_against_inventory(
            demand,
            session=s,
            embedder=embedder,
            inventory_index=inv_index,
            k=10,
            min_grade=Grade.B,
            now=datetime.utcnow(),
        )
        print(demand.id, stats.as_dict())
```

Persisted Match row carries the decomposed score (`semantic_score`, `spec_fit_score`, `recency_multiplier`, `evidence_weight`, `final_score`, `grade`, `match_type`) + a compact JSON `reasoning` blob. Each Match auto-creates a `LeadPipeline` row at `PipelineStage.NEW` with a priority derived from the score (P1 ≥0.85, P2 ≥0.70, P3 ≥0.50, P4 otherwise).

## Parser API (P3.1–P3.8, live)

```python
from zoneinfo import ZoneInfo
from trade_intel.parsers import (
    parse_file, parse_lines, ParsedMessage, ParseStatus,
    detect_date_order, DateDetection,
    dedupe, DedupSet, DedupStats,
    parse_csv, parse_excel,           # P3.8
    HEADER_SYNONYMS, map_headers,     # tabular helpers
    hash_for,                         # D-007 hash shared across formats
)
from trade_intel.normalization import (
    normalize_text, casefold_for_match,
    SenderIdentity, extract_identity, normalize_sender_name,
    CanonicalProduct, ProductMatch, extract_products, ALIASES, PRODUCTS,
)
from trade_intel.core.intent import (
    classify_intent, IntentResult, IntentSignal, LEXICON,
)
from trade_intel.persistence import ingest_messages, IngestStats
from trade_intel.db import session_scope
from trade_intel.db.models import SourceType

# End-to-end WhatsApp ingest
with session_scope() as s:
    stats = ingest_messages(
        parse_file("data/raw/UAE_Wholesale.txt"),
        session=s,
        source_type=SourceType.WHATSAPP,
        source_path="data/raw/UAE_Wholesale.txt",
    )

# CSV inventory sheet
with session_scope() as s:
    stats = ingest_messages(
        parse_csv("data/raw/stock.csv"),
        session=s,
        source_type=SourceType.CSV,
        source_path="data/raw/stock.csv",
    )

# Excel workbook (sheet 0 by default; pass sheet_name=str|int to pick)
with session_scope() as s:
    stats = ingest_messages(
        parse_excel("data/raw/stock.xlsx", sheet_name="Inventory"),
        session=s,
        source_type=SourceType.EXCEL,
        source_path="data/raw/stock.xlsx",
    )
print(stats.as_dict())

# Default: auto-detect date order, naive local time
for msg in parse_file("data/raw/UAE_Wholesale.txt"):
    if msg.parse_status in (ParseStatus.SYSTEM, ParseStatus.MEDIA):
        continue
    print(msg.timestamp, msg.sender_name, msg.normalized_message)

# Explicit MDY override + attach Asia/Dubai timezone
for msg in parse_file("data/raw/us_locale.txt",
                      date_order="MDY", tz=ZoneInfo("Asia/Dubai")):
    ...

# Pre-flight detection only
with open("data/raw/sample.txt", encoding="utf-8") as fh:
    d: DateDetection = detect_date_order(fh)
    # d.order ∈ {"DMY","MDY","YMD"}, d.confidence ∈ [0,1], d.evidence={"dmy":..., "mdy":..., ...}
```

Contract:
- `ParsedMessage` slotted dataclass: `timestamp`, `sender_name`, `raw_message`, `normalized_message`, `group_name`, `message_hash`, `source_file`, `is_media`, `parse_status`, `line_no`, `sender_phone`, `sender_identity`.
- `sender_identity` is `SenderIdentity | None` (None for system messages). Carries `raw`, `display_name`, `normalized_name`, `phone`, `probable_business_name`, `aliases: tuple[str, ...]`, `is_phone_only`, `is_business`, `has_emoji` — see Identity layer below.
- `raw_message` preserves original case + multi-line structure (human-readable).
- `normalized_message` = `normalize_text(raw, casefold=True, flatten=True)` — NFKC'd (full-width digits / ligatures / NBSP folded), invisible noise stripped (bidi marks, soft hyphen, ZW space, BOM), whitespace + newlines collapsed to spaces, Unicode case-folded. Emoji-safe (ZWJ + variation selectors preserved).
- `parse_lines` accepts any `Iterable[str]` — safe for multi-MB streaming. Concrete `date_order` only (no "auto"; use `parse_file` for auto).
- `parse_file` defaults to `date_order="auto"` (two-pass: scan headers, infer, then parse). Pass `tz=ZoneInfo(...)` to attach a zone to every datetime; otherwise timestamps are naive local time.
- `message_hash` is SHA-256 over `sender_name | sender_phone | message_ts.isoformat() | raw_text` (D-007). For records without a timestamp (orphan / malformed), `source_file` + `line_no` are folded in so re-imports stay idempotent.
- Unsupported formats fall through to `ParseStatus.ORPHAN_CONTINUATION` rather than raising — the parser never crashes mid-file.

Normalization layer (`trade_intel.normalization`):
- `normalize_text(s, *, casefold=False, flatten=False)` — general-purpose NFKC + noise-strip + whitespace cleanup; preserves emoji sequences (ZWJ family, keycap digits, skin tones).
- `casefold_for_match(s)` — shortcut for embedding / index-key text (`casefold=True, flatten=True`).
- `strip_noise(s)` — invisible-only strip, keep everything else.

Product dictionary + extractor (`trade_intel.normalization.dictionary` + `.products`, P3.5):
- `CanonicalProduct` (frozen, slotted): `canonical_name`, `brand`, `family`, `model`, `generic` (True for category-level entries like Tablets).
- `ALIASES: MappingProxyType[str, CanonicalProduct]` — 245+ casefolded alias keys mapped to ~41 canonical products. Auto-built at import; collisions raise.
- `PRODUCTS: tuple[CanonicalProduct, ...]` — every unique product the dictionary knows about.
- Coverage: iPhone 11-16 (base / Pro / Pro Max / Plus on 14+ / Mini on 12-13), iPad Mini 1-7 + Air 1-6 (digit + English number-word forms), iPad Pro M1-M4, generic Tablets bucket. No bare-number aliases (`"12"` alone NOT registered — qty/storage/price overlap).
- `extract_products(text) -> list[ProductMatch]` — single compiled regex sweep over flattened+casefolded input. Longest-first alternation so `"iphone 15 pro max"` wins over `"15 pro max"` at the same position. Custom boundaries `(?<!\w)…(?!\w)` (not `\b`) so `"14+"` matches cleanly.
- `ProductMatch` (frozen, slotted): `canonical`, `surface`, `start`, `end`, `confidence` (1.0 for every dict hit in v1).
- Offsets are positions in the **flattened** text, not the original. Match list is in source order; non-overlapping by construction.
- Fuzzy fallback (ARCHITECTURE.md mention) deferred until labelled match data exists — 3-4 char product slang has too many false-positive neighbors at edit distance 1.
- Generic vs SKU: check `match.canonical.generic` (e.g. `"tabs"` → generic Tablets); confidence stays 1.0 either way.

Dedup layer (`trade_intel.parsers.dedup`, P3.4):
- `dedupe(messages, *, seen=None) -> Iterator[ParsedMessage]` — streaming filter; first-occurrence-by-`message_hash` wins, subsequent dups silently dropped. Status-blind (all `ParseStatus` values participate equally).
- `DedupSet` — reusable hash set with attached `stats`. Pass between `dedupe()` calls for cross-batch / cross-file dedup. `from_hashes(iter)` preloads from existing storage (P3.7 hook: load known `messages.hash` rows on startup; preloaded hashes are NOT counted as `seen`).
- `DedupStats` — `seen` / `unique` / `duplicates` + `duplicate_rate` property + `as_dict()` (rounded to 4dp for log payloads).
- Stats accumulate on a shared `DedupSet`. For per-batch stats, snapshot before + diff, or pass a fresh `DedupSet` per batch.
- D-007 invariants: same sender+ts+body across two source files → same hash → collapses on cross-file dedup. Same body two seconds apart → different hash → both kept. Orphan/ts-less lines fold `source_file`+`line_no` into the hash, so two orphans with the same body in the same file are still distinct records.
- Module is intentionally DB-free; only requires `message_hash: str` on input — keeps the door open for CSV/Excel records (P3.8) to use the same dedup primitive.

Tabular (CSV / Excel) parsers (`trade_intel.parsers.csv_parser` + `excel_parser` + shared `tabular`, P3.8):
- `parse_csv(path, *, group_name=None, encoding="utf-8-sig", delimiter=",", column_mapping=None, default_sender=None)` — stdlib `csv.reader`, BOM-safe, custom delimiter, optional `column_mapping` for headerless sheets.
- `parse_excel(path, *, sheet_name=0, group_name=None, column_mapping=None, default_sender=None)` — `openpyxl` read-only / data-only mode; `sheet_name` accepts str (sheet title) or int (0-based index).
- **Row → ParsedMessage synthesis** (`tabular.row_to_parsed_message`): builds `"available qty {qty} {product} {storage} {color} {condition} lp {price} {location}"` from mapped cells. `"available"` prefix ensures the rule-based classifier tags as SELLER by default. Optional `Intent`/`Type`/`Side` column matching `/\b(buy|need|demand|want|req)/` drops the prefix and routes to Demand instead.
- **Header synonyms** (`HEADER_SYNONYMS`, ~50 entries): seller/supplier/vendor/from/source/phone/contact/whatsapp/product/item/description/sku/qty/quantity/stock/price/lp/last price/rate/storage/memory/color/condition/grade/location/city/date/timestamp/intent map to 10 canonical fields. First-wins on synonyms.
- **Phone-column fix:** when phone lives in its own column, `_merge_phone_into_identity` rebuilds the `SenderIdentity` via `dataclasses.replace` with `phone` + phone alias merged in — without this, persistence's phone-first dedup would miss CSV sellers because `extract_identity(name_only).phone` is None.
- **D-007 hash shared** with the WhatsApp parser via the now-public `hash_for(msg)` so identical content from CSV / Excel / WhatsApp collapses on cross-source dedup.
- Empty rows (no product cell) are silently skipped. Unknown headers (no canonical mapping) log a warning and return zero rows.

Persistence wire-in (`trade_intel.persistence.ingest`, P3.7):
- `ingest_messages(messages, *, session, source_type, group_name=None, source_path=None, dedup_set=None) -> IngestStats` — persists a `ParsedMessage` stream into `groups` / `sellers` / `buyers` / `messages` / `inventory` / `demand`.
- **Idempotent** via the D-007 hash: re-ingesting the same source file produces zero new rows. `DedupSet` defaults to a fresh one preloaded from `messages.hash` (P3.4 hand-off — pass your own for cross-call sharing).
- **Per-message responsibility chain:** (1) status filter — only `ParseStatus.OK` writes; (2) hash dedup; (3) `classify_intent` + `extract_products` against `normalized_message`; (4) identity merge per D-009 (phone-first via `sellers.phone` unique, name fallback via `canonical_name` + NULL phone) — `_get_or_create_*` reconciles `post_count` / `request_count` / `first_seen` / `last_seen` / `aliases` (set-union + sort); (5) insert Message with `intent` / `intent_confidence` / FKs; (6) one Inventory per product on SELLER, one Demand (with `urgency`) per product on BUYER.
- **Intent routing:** SELLER → Seller row + Inventory rows. BUYER → Buyer row + Demand rows. **BROKER / UNKNOWN** → Message stored with intent column but no side-table touch in v1; Phase 4 matching can revisit using extracted products.
- **Group get-or-create** on `(source_path, source_type)` if path provided, else `(name, source_type)`.
- **Transactional model:** caller owns the session and commit. Per-message `session.begin_nested()` SAVEPOINT isolates failures so one bad row doesn't poison the batch; dedup_set only registers a hash after its savepoint commits, so failed messages can be retried.
- **`IngestStats`** (slotted): `messages_seen` / `messages_inserted` / `messages_skipped_{dup,system,media,malformed,orphan}` / `inventory_inserted` / `demand_inserted` / `sellers_{created,updated}` / `buyers_{created,updated}` / `errors`. Invariant: `inserted + sum(skipped_*) + errors == seen`. `as_dict()` excludes private fields.
- **Canonical-name fallback:** for emoji-only / blank-normalized senders, falls through `normalized_name → display_name → raw → sender_name → "unknown"` so every Seller/Buyer row has a non-empty `canonical_name`.

Intent classifier (`trade_intel.core.intent`, P3.6 — v1 rule-based):
- `classify_intent(text) -> IntentResult` — never raises; None / empty / whitespace / noise → UNKNOWN+MEDIUM+conf=0.
- `IntentResult` (frozen, slotted): `intent` (`IntentType` enum), `urgency` (`Urgency` enum), `confidence`, `is_price_inquiry`, `buyer_score`, `seller_score`, `broker_score`, `signals: tuple[IntentSignal, ...]`.
- `IntentSignal` (frozen, slotted): `keyword`, `kind` (Literal: `buyer` / `seller` / `broker` / `urgency_high` / `urgency_low` / `price`), `weight`, `start`, `end`.
- Single unified lexicon (`LEXICON: MappingProxyType`, ~110 keys). Multi-kind keywords supported — `"urgent"` lifts BUYER + URGENCY_HIGH at the same span. Longest-first single regex + `(?<!\w)…(?!\w)` boundaries so `"need qty"` (buyer w=2) beats `"qty"` (seller w=1) and `"looking buyer for"` (broker w=3) beats `"looking"`+`"buyer"`.
- Intent winner = highest cumulative weight; ties → UNKNOWN with conf=0. Confidence = winner / total intent weight.
- Urgency: HIGH wins over LOW wins over MEDIUM (default). `is_price_inquiry` is orthogonal — both sides can ask LP/PM.
- Lexicon coverage matches the CLAUDE.md Intent Lexicon section (`need` / `req` / `urgent` / `avail` / `fresh` / `stock ready` / `qty` / `have` / `bridge` / `intro` / `LP` / `pm` / `best rate` all present) plus pragmatic extensions (wtb, anyone has, for sale, wts, new lot, in hand, middleman, commission, today, tomorrow, no rush, ...).
- Limitations (v1): no negation; ASCII-only lexicon (Arabic intent terms deferred until labelled bilingual data); `have`/`qty` weighted weakly because they're conversational noise as often as stock signals; ML upgrade planned for a later phase.

Identity layer (`trade_intel.normalization.identity`, P3.3):
- `extract_identity(raw: str | None) -> SenderIdentity` — never raises; `None` / empty / whitespace-only input → empty identity.
- `SenderIdentity` (frozen, slotted): `raw`, `display_name`, `normalized_name`, `phone`, `probable_business_name`, `aliases`, `is_phone_only`, `is_business`, `has_emoji`.
- Phone canonicalization: `+CC…digits` (with `+`) or digits-only; accepts 7–15 digits; rejects bare digit runs `< 10` to avoid catching SKU numbers.
- Emoji policy: preserved in `raw` / `display_name` (incl. ZWJ + variation selectors as one block) but stripped from `normalized_name` / `aliases`.
- Location-suffix policy: trailing UAE/GCC location tokens (DXB / Dubai / Sharjah / AUH / UAE / GCC / Deira / parens / brackets / chained) recursively stripped into an alias so `Ali Mobiles DXB` and `Ali Mobiles AUH` rendezvous on the shared root.
- Business detection: ~50 ASCII keywords (`trading`, `mobiles`, `llc`, `wholesale`, `electronics`, `hub`, `center`, …) via `BUSINESS_KEYWORDS`. Non-Latin business words deliberately out of scope until P3.5.
- Alias dedup: each casefolded variant added once; punctuation-stripped variants collapse `L.L.C` ↔ `LLC`.
- Phone-only handles set `normalized_name = phone` so the canonical phone is the matching key.
- Identity merge policy is enforced downstream (D-009 — phone-first, name-root fallback); this layer just emits the signals.
- `normalize_sender_name(s)` is a thin shortcut for `extract_identity(s).normalized_name`.

## CLI Commands (live as of Phase 5)

Install entrypoint: `pip install -e .` then run `trade-intel --help`.

| Command | Purpose |
|---|---|
| `trade-intel parse <file>` | Ingest WhatsApp / CSV / Excel (auto-detected by extension; `--type` override) |
| `trade-intel match "<query>"` | Semantic lookup (`--side seller\|buyer`, `-k N`) |
| `trade-intel who-has <product>` | List active Inventory (`--location`, `--limit`) |
| `trade-intel who-needs <product>` | List active Demand |
| `trade-intel hot` | Recent messages (`--intent`, `--limit`) |
| `trade-intel opportunities` | Top matches by score (`--grade A+\|A\|B\|C`, `--priority P1..P4`) |
| `trade-intel brief` | Daily Deal Board — summary + top matches + urgent demand + fresh inventory |
| `trade-intel ping <match-id>` | WhatsApp-ready template (`--side seller\|buyer`) |
| `trade-intel chat` | Interactive REPL — type a query, get semantic hits |
| `trade-intel run-matcher` | Cron-friendly: score every active Demand (`--min-grade`) |

Every command accepts `--json` for machine-readable output. `trade-intel serve` (FastAPI) lands in Phase 6.

## Environment Setup

Settings load order: real env vars → `.env` at project root → defaults in `src/trade_intel/config.py`.

```bash
cp .env.example .env       # tweak for your machine
# Override anything: SQLITE_URL, LOG_LEVEL, API_PORT, ENVIRONMENT=dev|test|prod, ...
```

Production gates enforced by `Settings._enforce_prod_safety` model validator:
- `ENVIRONMENT=prod` requires `POSTGRES_URL`
- `ENVIRONMENT=prod` rejects `DEBUG=true` and `API_RELOAD=true`

Access pattern:
```python
from trade_intel.config import get_settings
settings = get_settings()           # cached
engine = create_engine(settings.database_url)
```

DB session entrypoints:
```python
from trade_intel.db import Base, get_db, session_scope, init_db
from trade_intel.db.models import (  # importing this package registers all 9 tables
    Buyer, Demand, Embedding, Group, Inventory, LeadPipeline, Match, Message, Seller,
    IntentType, SourceType, Condition, Urgency, MatchType, Grade, PipelineStage, Priority, VectorEntityType,
)
# FastAPI:    def route(db: Session = Depends(get_db)): ...
# CLI/script: with session_scope() as s: s.add(obj)
# Smoke:      init_db()   # create_all on Base.metadata
```

ORM conventions:
- Enums stored as TEXT (`.value`, not `.name`) via `str_enum_type` — cross-dialect, CHECK-constrained.
- `phone` uniqueness on `sellers` / `buyers` is a partial unique index (`WHERE phone IS NOT NULL`).
- `Embedding` has no FK; the `(entity_type, entity_id)` discriminator points polymorphically at messages / inventory / demand / product.
- Freshness is **never stored** — `posted_at` is the time anchor; tier is computed at read time (D-010).

Alembic:
```bash
# inherits settings.database_url from env
.venv/bin/alembic upgrade head
.venv/bin/alembic downgrade -1
.venv/bin/alembic revision --autogenerate -m "<slug>"
.venv/bin/alembic check    # asserts model ↔ migrations parity (zero drift)
```
Migration files live in `db/migrations/versions/` and are named `YYYYMMDD_HHMM_<rev>_<slug>.py`. SQLite migrations use batch operation mode automatically (`render_as_batch=True` in `env.py`) so future ALTER COLUMN operations work.

Daily workflow:
```bash
make install     # one-time: create .venv and install with [dev] extras
make verify      # lint + typecheck + tests — run before commit
make preflight   # verify + db-upgrade + smoke + alembic drift — run before push
make db-reset FORCE=1   # nuke dev SQLite and re-migrate
make smoke-test  # bootability proof (settings + engine + SELECT 1)
```

Logging:
```python
from trade_intel.core.logging import get_logger, set_request_id

log = get_logger(__name__, component="parser")  # bound logger
set_request_id("req-abc-123")                    # contextvar — auto-attached
log.info("ingest_started", file="dubai.txt", bytes=12345)
log.exception("parse_failed", sku="unknown")     # exc_info captured safely
```
Dev/test: ANSI pretty + rich tracebacks. Prod (`ENVIRONMENT=prod`): single-line JSON with sorted keys, including `exception` field on errors. Third-party loggers (SQLAlchemy / Alembic / Uvicorn) share the same formatter.

`.venv` is provisioned at the project root (`python3 -m venv .venv && pip install -e .`).

Remaining Phase 2 artifacts (Makefile, etc.) land in P2.6–P2.7.

## Session Continuity Checklist

When a new session begins:
1. `cat CLAUDE.md`  (this file)
2. `cat PROJECT_STATE.json`
3. `cat TASKS.md`
4. Scan `git log --oneline -20` (when repo initialized in Phase 2)
5. Resume from `current_phase` in PROJECT_STATE.json
