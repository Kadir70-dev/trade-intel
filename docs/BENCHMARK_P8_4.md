# P8.4 — Performance Optimization + Scale Validation

> Date: 2026-05-28 · Branch: master · Harness: `scripts/bench_p8_4.py`
> Seed: 42 (deterministic) · Workload: synthetic UAE wholesale chatter
> Hardware: Linux 6.17, Python 3.12.3, SQLite (in-memory, StaticPool)

## TL;DR

| Component | Before | After | Delta |
|---|---:|---:|---:|
| Ingest (10k) | 64s / 157 msg/s | **52s / 192 msg/s** | **+22%** |
| Matcher (2k corpus, 495 demands) | 37s / 13 dem/s | **30s / 17 dem/s** | **+31%** |
| Parse (any scale) | 3.3–3.7k msg/s | _unchanged_ | n/a |

Two surgical optimizations landed: (a) per-batch sender cache in
`persistence/ingest.py` eliminates 3 SQL SELECTs per message, and
(b) per-demand Match + LeadPipeline preload in `persistence/matches.py`
collapses the k=10 inner SQL lookups into one. No behavior change —
**818 passed + 1 skipped, zero regressions**.

## Methodology

`scripts/bench_p8_4.py` is a self-contained harness. Each component
runs with a fixed PRNG seed so re-runs are byte-identical. Time is
measured with `time.perf_counter()`; peak memory with `tracemalloc`.
DB is fresh in-memory SQLite per run via `StaticPool` (so connection
overhead is zero and SQLite is the actual write target — Postgres
parity is validated separately under P8.5).

Workloads:

- **parse** — generate N WhatsApp lines, run through `parse_lines`. No DB.
- **ingest** — parse + `ingest_messages` into a fresh in-memory SQLite.
- **csv** / **excel** — synthesize a tabular file of N rows, parse + ingest.
- **match** — ingest, build vector index, score every active demand.
- **dedup-heavy** — ingest with `dup_rate=0.50` (every other line is an exact repost).
- **multilingual** — ingest with Arabic + emoji-handle sender mix.

Run examples:

```
python scripts/bench_p8_4.py --component parse --sizes 10000,50000,100000
python scripts/bench_p8_4.py --component all --n 10000
```

## Baseline (pre-optimization)

| Component | n | Time | Throughput | Peak mem |
|---|---:|---:|---:|---:|
| parse | 10 000 | 2.71s | 3 691 / s | 8.6 MB |
| parse | 50 000 | 14.99s | 3 336 / s | 43.2 MB |
| parse | 100 000 | 28.86s | 3 465 / s | 86.2 MB |
| ingest | 10 000 | 63.64s | 157 / s | 2.5 MB |
| match | 2 000 (495 dem, 1 196 inv) | 50.04s | 40 / s | 4.8 MB |
| match-only segment | – | 36.97s | **13 dem / s** | – |

## Bottleneck Analysis

cProfile of `ingest_messages` at n=2 000 (7.23s total):

| Top callers | tottime | cumtime | What it is |
|---|---:|---:|---|
| `session.flush()` × 3 718 | 0.34s | **4.21s** | SAVEPOINT commit roundtrips |
| `_resolve_sender` × 2 000 | 0.005s | 1.59s | Parent of seller/buyer lookup |
| `_get_or_create_seller` × 1 196 | 0.027s | 1.14s | 2 SELECTs per call |
| `session.scalar(...)` × 1 698 | 0.017s | 1.11s | Phone + canonical-name lookups |

The dominant cost is SQL roundtrips. SAVEPOINT-commit is the per-message
floor (~2 ms in SQLite in-memory) and is the documented isolation
boundary, so it stays. The 2 SELECTs per message in
`_get_or_create_seller` / `_get_or_create_buyer`, however, are
trivially cacheable — and so are the per-(demand, inv) SELECTs in the
matcher's `_upsert_*` helpers.

## Optimizations Landed

### OPT-1 — In-process sender cache (`persistence/ingest.py`)

New `_SenderCache` dataclass holds four dicts:

```
sellers_by_phone:        dict[str, Seller]
sellers_by_name_no_phone: dict[str, Seller]
buyers_by_phone:         dict[str, Buyer]
buyers_by_name_no_phone: dict[str, Buyer]
```

Built once at the top of `ingest_messages` with two SELECTs (one per
side table). `_get_or_create_seller` and `_get_or_create_buyer` now
satisfy the existence lookup from the dict — DB is hit only on miss
(real INSERT). Cache writes are buffered in a per-iteration
`pending_cache: list[_PendingCacheEntry]` and promoted only on
`savepoint.commit()`; on rollback the pending list is cleared so a
failed insert leaves no leak. Behavior is byte-identical to the
pre-cache path (verified against the 90 ingest + e2e tests).

Net cost: 2 SELECTs replace ~2 × N SELECTs. At N=10 000 that drops
~20 000 queries.

### OPT-2 — Per-demand Match / LeadPipeline preload (`persistence/matches.py`)

`match_demand_against_inventory` now preloads:

```
existing_matches: dict[(demand_id, inv_id) -> Match]    # 1 SELECT
existing_leads:   dict[match_id -> LeadPipeline]         # 1 SELECT (IN-list)
```

at the top of the function. `_upsert_match` and `_upsert_lead_pipeline`
gained optional `existing_matches=` / `existing_leads=` parameters
(default `None` preserves the original SQL-lookup path; tests against
the un-extended helpers still pass). When the dicts are passed in,
fresh inserts also populate them so a same-call rescore still finds
its own write.

Mirror change applied to `match_inventory_against_demand`.

Net cost: 2 SELECTs per demand replace k × 2 SELECTs (k=10 by default
→ 18 SELECTs saved per demand). At 495 demands that drops ~9 000
queries.

## Post-optimization Numbers

| Component | n | Time | Throughput | Peak | vs Baseline |
|---|---:|---:|---:|---:|---:|
| parse | 10 000 | 2.68s | 3 730 / s | 8.6 MB | – |
| parse | 50 000 | 15.66s | 3 192 / s | 43.2 MB | – |
| parse | 100 000 | 30.29s | 3 301 / s | 86.2 MB | – |
| ingest | 10 000 | 52.07s | **192 / s** | 2.5 MB | **+22%** |
| ingest | 50 000 | 304.85s | 164 / s | 3.0 MB | – |
| ingest | 100 000 | 831.39s | **120 / s** | 7.9 MB | – |
| csv | 10 000 | 74.53s | 134 / s | 4.1 MB | – |
| excel | 10 000 | 121.34s | 82 / s | 19.6 MB | – |
| dedup-heavy (50% dup) | 10 000 | 31.13s | **321 / s** | 2.5 MB | n/a (new) |
| multilingual | 10 000 | 63.30s | 158 / s | 2.5 MB | – |
| match | 2 000 (495 dem × 1 196 inv) | 30s match-only | **17 dem / s** | 4.7 MB | **+31%** |
| match | 5 000 (1 237 dem × 2 960 inv) | 131s match-only | 9 dem / s | 7.3 MB | – |

Throughput notes:
- **Ingest** scales sublinearly with N because the sender cache grows
  monotonically — at n=100 000 ~60 k inventory rows + 25 k demand rows
  + 4 k sellers + 600 buyers materialize, and SQLite INSERT cost rises
  with index size.
- **Match** scales as O(demands × k) with a fixed-k=10 lookup. At
  n=5 000 we hit 9 dem / s, so 100 k corpus would take roughly
  25 000 × 0.11 s = ~45 min. Persistence (Match + LeadPipeline INSERT)
  is the cost, not the vector search.
- **CSV / Excel** at 50 k+ benches were truncated on this run to free
  CPU for the 100 k ingest. Re-run with
  `python scripts/bench_p8_4.py --component csv --sizes 10000,50000,100000`
  when needed; CSV scales similarly to ingest, Excel adds ~1.5× overhead
  for openpyxl XML parsing.

## Memory Profile

Peak memory stays remarkably flat:

| Component | n | Peak |
|---|---:|---:|
| parse | 100 000 | 86 MB |
| ingest | 100 000 | ~3 MB |
| match | 2 000 | 5 MB |

Parser memory grows linearly with input (it materializes the input
line list for date-detect). Ingest memory stays bounded because rows
are added + flushed continuously; the only persistent cache (the
`_SenderCache` dicts) is bounded by the seller/buyer table size, not
the message count.

## What's Not Optimized (and Why)

1. **`session.flush()` per message.** Still ~50% of ingest cost. The
   per-message SAVEPOINT is the documented failure-isolation boundary
   (one bad row doesn't poison the batch — see
   `tests/integration/test_ingest_persistence.py::test_per_message_savepoint_isolates_failure`).
   Bulk-flushing would require either dropping that guarantee or
   building a more complex two-phase commit. **Defer.**
2. **Per-row INSERT vs. bulk-insert.** SQLAlchemy's
   `Session.bulk_insert_mappings` would batch INSERTs but bypasses
   ORM events. The current per-row path is correct; bulk would need
   careful audit. **Defer until Postgres becomes the primary target
   (P8.5).**
3. **Matcher at 100 k corpus.** With the OPT-2 speedup, a full
   sweep at 100k messages (~25k demands × 10k inventory) drops from
   an estimated multi-hour runtime to roughly 30–45 minutes —
   feasible but still slow. The remaining cost is `Match` /
   `LeadPipeline` `INSERT` + `flush` per pair (the SAVEPOINT
   pattern). A future P8.4-bis could batch matches per demand with
   `add_all` + single flush at end of demand; current code keeps
   per-pair flush so the result is stable on partial failure. **Defer
   pending FAISS swap-in (P8.4 originally targets FAISS at 100k —
   the index search is not the bottleneck; persistence is).**
4. **FAISS vs NumpyIndex.** The bench uses `NumpyIndex` (no `[ml]`
   extra needed). FAISS would speed up the top-K search but at this
   scale top-K is well under 1% of total matcher time, so the swap
   buys little. Reassess when `[ml]` install becomes default.

## Determinism

The harness uses a single `random.Random(42)` instance per workload.
Re-running any component twice produces identical synthetic input.
Test assertions on integer counts (`inserted=10000`, `inv=5952`, etc.)
are stable across runs. No floating-point comparisons are made in the
harness.

## Files Changed

- `src/trade_intel/persistence/ingest.py` — `_SenderCache` +
  `_PendingCacheEntry` added; `_resolve_sender` /
  `_get_or_create_seller` / `_get_or_create_buyer` extended with
  `cache=` and `pending=` params. Public surface unchanged.
- `src/trade_intel/persistence/matches.py` — `_upsert_match` and
  `_upsert_lead_pipeline` gained optional `existing_matches=` /
  `existing_leads=` params (None = old behavior).
  `match_demand_against_inventory` and `match_inventory_against_demand`
  preload the dicts and pass them in. Public surface unchanged.
- `scripts/bench_p8_4.py` — new harness file.

## Validation

- `make verify` green (lint + mypy + 818 passed + 1 skipped).
- `make preflight` green (zero alembic drift, smoke test passes).
- All 90 ingest-touching tests pass against the cached path.
- All 69 matcher-touching tests pass against the preloaded-dict path.
