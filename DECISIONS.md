# DECISIONS — trade-intel

Each entry: **Decision · Date · Context · Alternatives · Rationale · Reversibility**

---

## D-001 · FastAPI for the API layer

- **Date:** 2026-05-27
- **Context:** Need an HTTP API for ingestion, queries, and dashboards.
- **Alternatives:** Flask, Django REST, Litestar, Go (chi/echo).
- **Rationale:** Async-native, first-class Pydantic typing, automatic OpenAPI, fastest path for a Python-heavy ML stack. Background tasks built in. Familiar to the team.
- **Reversibility:** High — handlers are thin; the `core/` layer is framework-agnostic.

## D-002 · SQLite first, PostgreSQL migration-ready

- **Date:** 2026-05-27
- **Context:** Iterating quickly with realistic-volume data on a laptop; later need to scale to multi-user SaaS.
- **Alternatives:** Postgres from day 1 (overhead), DuckDB (poor write concurrency), Mongo (loses relational guarantees).
- **Rationale:** SQLite removes dev infra friction. Using SQLAlchemy + Alembic from day 1 keeps the door open. No SQLite-only SQL allowed.
- **Reversibility:** High if discipline is held. ORM-only writes enforced.

## D-003 · FAISS first, Qdrant later

- **Date:** 2026-05-27
- **Context:** Vector search needed for semantic buyer↔seller matching.
- **Alternatives:** Qdrant/Weaviate/Milvus (heavier infra), pgvector (couples search to DB).
- **Rationale:** FAISS flat-L2 is dependency-free, fast to embed, and adequate up to ~100k vectors. Wrap behind a `VectorIndex` interface so the Qdrant switch is a single implementation change.
- **Reversibility:** High (interface boundary).

## D-004 · `sentence-transformers/all-MiniLM-L6-v2` baseline

- **Date:** 2026-05-27
- **Context:** Need to embed short, noisy WhatsApp messages.
- **Alternatives:** OpenAI/Cohere embeddings (API cost + latency), `bge-small-en`, fine-tuned model.
- **Rationale:** Small (~80MB), CPU-friendly, strong on short English text, no API dependency. Good enough for v1; upgrade once we have labeled match data.
- **Reversibility:** High — encapsulated in `ml/embeddings.py`.

## D-005 · Golang worker for ingestion

- **Date:** 2026-05-27
- **Context:** WhatsApp exports can be huge (10MB+ txt). Concurrent file scanning + line-level processing benefits from Go's concurrency model. Keep the Python API thin.
- **Alternatives:** Celery (Python), arq, asyncio workers in FastAPI.
- **Rationale:** Go is built for I/O-heavy concurrent file work, deploys as a single binary, and decouples ingestion CPU from API responsiveness.
- **Reversibility:** Medium — porting back to Python is straightforward but loses the perf budget.

## D-006 · Evidence-first confidence scoring (product invariant)

- **Date:** 2026-05-27
- **Context:** Earlier prototypes hallucinated trust scores and trends from tiny datasets, eroding trader trust.
- **Decision:** No score is emitted unless its data threshold is met (see CLAUDE.md → Confidence Thresholds).
- **Rationale:** A blank field is more valuable than a fabricated one. Trader credibility is the product.
- **Reversibility:** N/A — this is a product invariant, not a tech choice.

## D-007 · Hash-based message deduplication

- **Date:** 2026-05-27
- **Context:** Same chat may be re-exported; same seller cross-posts the same SKU in multiple groups.
- **Decision:** `hash = sha256(sender_name || sender_phone || message_ts || raw_text)` as the unique key on `messages`.
- **Rationale:** Idempotent ingestion. Re-imports never duplicate. Cross-group duplicates collapse naturally.
- **Reversibility:** High (column-level).

## D-008 · Domain-language schema

- **Date:** 2026-05-27
- **Context:** Tables modeled after the trader's vocabulary, not generic AI terms.
- **Decision:** Tables = `messages`, `buyers`, `sellers`, `inventory`, `demand`, `matches`, `embeddings`, `groups`, `lead_pipeline`.
- **Rationale:** Self-documenting for both the trader (who reads dashboards) and future engineers. Mirrors the chat-language used by users.
- **Reversibility:** Medium — column renames are cheap; table renames need migrations.

## D-009 · Identity-merge via phone-first, name-root fallback

- **Date:** 2026-05-27
- **Context:** UAE traders post under several handles (e.g., "Ali Mobiles" / "Ali DXB"). Phone is the only deterministic key.
- **Decision:** When phone present → merge by phone. When absent → merge candidate when name-root + SKU consistency + time proximity score ≥ 75 (weighted formula in ARCHITECTURE.md).
- **Rationale:** Avoids false merges in early data; surfaces "candidate merges" for human review.
- **Reversibility:** High — merges produce a parent-child link, never destructive deletes.

## D-010 · Freshness computed on read, not stored

- **Date:** 2026-05-27
- **Context:** Freshness tier (HOT/ACTIVE/…/STALE) is a function of `now() - posted_at`. Storing it would create a perpetual staleness bug.
- **Decision:** `posted_at` is stored; tier and recency multiplier are computed at query time.
- **Rationale:** Truth must auto-update. Eliminates a whole class of cron-update bugs.
- **Reversibility:** N/A — a derived value should never be denormalized prematurely.
