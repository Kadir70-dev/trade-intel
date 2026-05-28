# P8.1 — Unit-Test Coverage Audit (trade-intel)

> Date: 2026-05-28 · Branch: master · Tooling: pytest-cov 7.1.0 against `src/trade_intel`

## Headline

- **Suite size:** 715 collected · **714 passed · 1 skipped** (docker-compose plugin not installed)
- **Line coverage:** **93%** (2 423 of 2 597 statements covered; 174 missed)
- **No production module sits below 71%.** The two sub-90% modules (`ml/faiss_index.py` 71%, `cli/main.py` 79%) are uncovered by design (lazy `[ml]` extra, interactive REPL).
- **Verdict: high coverage, but two real testing blind spots** — (a) freshness / urgency decay over wall-clock time, (b) cross-source identity merge under concurrent ingestion. Everything else is either covered or correctly out-of-scope.

## Coverage by Module

| Module | Stmts | Miss | Cover | Status |
|---|---:|---:|---:|---|
| `api/main.py` | 217 | 14 | 94% | OK — gaps are 404 short-circuits |
| `api/schemas.py` | 149 | 0 | 100% | ✅ |
| `cli/formatters.py` | 56 | 2 | 96% | OK |
| `cli/main.py` | 228 | 47 | **79%** | ⚠ — `chat` REPL + JSON-shape branches |
| `cli/messages.py` | 41 | 0 | 100% | ✅ |
| `config.py` | 80 | 10 | 88% | ⚠ — prod-safety branches partial |
| `core/intent.py` | 110 | 1 | 99% | ✅ |
| `core/logging.py` | 47 | 0 | 100% | ✅ |
| `db/session.py` | 71 | 6 | 92% | OK — Postgres-pool branch, `init_db` |
| `db/models/*` | 286 | 0 | 100% | ✅ |
| `matching/candidates.py` | 58 | 4 | 93% | OK |
| `matching/scoring.py` | 117 | 4 | 97% | ✅ |
| `ml/embeddings.py` | 64 | 8 | 88% | by-design — `MiniLMEmbedder` lazy `[ml]` |
| `ml/faiss_index.py` | 125 | 36 | **71%** | by-design — `FaissFlatIndex` lazy `[ml]` |
| `normalization/dictionary.py` | 71 | 2 | 97% | ✅ |
| `normalization/identity.py` | 93 | 3 | 97% | ✅ |
| `normalization/products.py` | 26 | 0 | 100% | ✅ |
| `normalization/text.py` | 29 | 0 | 100% | ✅ |
| `parsers/csv_parser.py` | 40 | 0 | 100% | ✅ |
| `parsers/dedup.py` | 39 | 0 | 100% | ✅ |
| `parsers/excel_parser.py` | 52 | 5 | 90% | OK — missing-sheet branch |
| `parsers/tabular.py` | 90 | 1 | 99% | ✅ |
| `parsers/whatsapp.py` | 210 | 9 | 96% | OK — orphan + emoji edges |
| `persistence/ingest.py` | 169 | 16 | 91% | ⚠ — `_touch_seller/buyer` partial |
| `persistence/matches.py` | 107 | 6 | 94% | OK — mirror path partial |
| **TOTAL** | **2 597** | **174** | **93%** |  |

## Gap Triage — What's Already Fine

These uncovered lines are **intentional** and should NOT be tested:

1. **`ml/embeddings.py` 129–149, `ml/faiss_index.py` 161–234** — `MiniLMEmbedder` + `FaissFlatIndex` are lazy-import wrappers around `sentence-transformers` and `faiss-cpu`, both in the `[ml]` extra. The Protocol contract is already exercised end-to-end through `HashEmbedder` + `NumpyIndex`. Adding tests would either skip-when-extra-missing (no signal in CI) or force `[ml]` install in CI (expensive: ~1.5 GB and torch wheels). **Decision: leave uncovered, document as `[ml]`-gated.**
2. **`cli/main.py` 597–627 (`chat` REPL)** — interactive `typer.prompt` loop. Stubbing stdin via `CliRunner.invoke(input="...")` is brittle. **Decision: leave uncovered; the underlying scoring is already tested.**
3. **`config.py` prod-safety raises** (lines 113, 115, 117, 119) — each raise is one `pytest.raises(ValueError)` away. **Decision: add to P8.1 follow-up (cheap, high-signal).**
4. **`db/session.py` 38, 41, 80–83** — Postgres pool branch + the `Path` mkdir for file-backed SQLite. **Decision: Postgres branch covered structurally by P8.5; SQLite-file mkdir is one fixture-temp-dir test away.**

## Gap Triage — Tests We Recommend

Ranked **high → low impact**. Each item ships with a clear assertion target so a future agent can write it without re-discovering scope.

### A. Parser Edge-Case Gaps (high impact)

| ID | What's missing | Why it matters | Where to put it |
|---|---|---|---|
| **A1** | **Same-second, same-sender, near-identical body** (one trailing emoji difference) — does the D-007 hash split them? Currently we only test exact dup vs body-diff. | UAE traders blast slight typo'd reposts in the same minute. If we collapse them, we lose evidence count; if we split overzealously, we double-count. | `tests/unit/test_parser_whatsapp.py` |
| **A2** | **Multi-line message containing a real timestamp-shaped string** (e.g. quote of "12/05/24, 10:30" inside a body) — does the continuation collapser get fooled? | The Android header regex is `re.match` (anchored), but mid-body lines that happen to look header-like are a known footgun. | `tests/unit/test_parser_whatsapp.py` |
| **A3** | **CSV with mixed price units** ("AED 4 200", "4200/-", "4.2k") — does `parse_date_cell` and the inventory-row synthesizer survive? | Brokers freely mix conventions in the same sheet. We currently only test clean numerics. | `tests/unit/test_csv_parser.py` |
| **A4** | **Excel with hyperlinked / formatted cells** + `data_only=True` round-trip — confirm formula values surface as their cached result. | Wholesalers send Excel templates with `=A2*B2` price columns. | `tests/unit/test_excel_parser.py` |
| **A5** | **WhatsApp export with timezone shifted across DST** (rare but plausible for re-exports) — does `tz=ZoneInfo("Asia/Dubai")` stay consistent? | Asia/Dubai has no DST, but our parser is used by Indian re-shippers too. **Lower priority.** |
| **A6** | **`detect_date_order` on a file with 100 % ambiguous dates** (all days ≤ 12) — confirm `confidence=0.0` + DMY default behavior is preserved. | Documented behavior; not asserted today. | `tests/unit/test_whatsapp_date_detect.py` |

### B. Matcher Confidence Gaps (high impact)

| ID | What's missing | Why it matters | Where to put it |
|---|---|---|---|
| **B1** | **Freshness tier transitions** — score the same Match at the boundaries (5h59m → 6h01m, 23h59m → 24h01m, 7d-1m → 7d+1m). Today we test the table but not the boundary clamp. | Wholesale deals decay fast. A 1-minute drift at the boundary is the difference between A+ and A. | `tests/unit/test_matching_scoring.py` |
| **B2** | **Storage normalization corpus** — "256GB" / "256 GB" / "256gb" / "256" / "0.25TB" / "256 GB" (NBSP). Currently tested at single examples; no parametric matrix. | Sellers and buyers write storage 6 ways. If `compute_spec_fit` mis-normalizes one, every Match for that SKU is wrong. | `tests/unit/test_matching_scoring.py` |
| **B3** | **Soft-match policy: condition cross-product** — SEALED↔NEW (compat), USED↔OPEN_BOX, REFURB↔NEW, and the missing-one-side cases. Today we only assert SEALED↔NEW + new↔used. | Condition is the #2 driver of close probability after price; under-tested. | `tests/unit/test_matching_scoring.py` |
| **B4** | **Idempotency under rescoring**: ingest → match → mutate `Inventory.qty` → rescore → confirm Match row updated in place, LeadPipeline `last_action=MATCH_RESCORED`, **no** duplicate. Partially covered — the upsert is tested but not the *score change in place*. | This is the cron-job invariant. Drift here silently grows the matches table. | `tests/integration/test_matching_pipeline.py` |
| **B5** | **Empty inventory index + active demand** — confirm `match_demand_against_inventory` returns `MatchWriteStats()` defaults (all zeros), logs `match_demand_no_candidates`, and does not touch LeadPipeline. | Covered partially via integration "no-inventory" test; the log signal is not asserted. | `tests/integration/test_matching_pipeline.py` |
| **B6** | **`compute_match_type` boundary**: spec_fit exactly 0.99 → still EXACT (per docstring); 0.989 → SIMILAR. | Off-by-one in match_type can mis-route reporting downstream. | `tests/unit/test_matching_scoring.py` |

### C. Persistence / DB Edge Cases (medium-high impact)

| ID | What's missing | Why it matters | Where to put it |
|---|---|---|---|
| **C1** | **Cross-source identity merge** — same phone arrives via WhatsApp then via CSV → one Seller row with `post_count=2`, aliases union, both messages linked. We test cross-source *dedup* (hash) but not cross-source *merge*. | The whole point of the system is unifying a trader across surfaces. This is the load-bearing invariant. | `tests/integration/test_ingest_persistence.py` |
| **C2** | **D-009 fallback collision**: two real different people share the same `canonical_name` and both have NULL phone. Today they merge — confirm that's intentional and document, OR confirm they split. | The current code merges them. If that's wrong, every nameless-pair in the corpus is a silent bug. **Audit decision needed first** before adding the test. |
| **C3** | **`_touch_seller` / `_touch_buyer` out-of-order timestamps** — first_seen widening with a *back-dated* re-ingest. Lines 428–436 are uncovered. | Re-importing an older WhatsApp export should pull `first_seen` earlier, not later. | `tests/integration/test_ingest_persistence.py` |
| **C4** | **Group reuse under name collision but different source_type** — "UAE Wholesale" as WHATSAPP and as CSV → must be two Groups. | Today we test path-based reuse; not the `(name, source_type)` fallback collision. | `tests/integration/test_ingest_persistence.py` |
| **C5** | **DedupSet preload from an already-populated DB** end-to-end: pre-seed `messages.hash`, then ingest the same WhatsApp file → all rows skip as dup. | Covered narrowly in `test_ingest_persistence` for one row; not for a full file. | `tests/integration/test_ingest_persistence.py` |
| **C6** | **Partial unique index on `sellers.phone`**: insert two sellers, one with phone, one with NULL — must coexist. Today asserted in models test but **not** under the actual Alembic-upgraded schema. | The partial-where clause is dialect-specific. If a future migration drops the `sqlite_where`, this silently regresses. | `tests/unit/test_alembic_migration.py` |

### D. API Coverage Gaps (medium impact)

| ID | What's missing | Why it matters | Where to put it |
|---|---|---|---|
| **D1** | **`/ingest` with `?run_matcher=true` actually persists Matches** — today we assert the response field `matcher_scheduled=true` but not the *post-condition* (the BackgroundTask completion writes Match rows). TestClient executes BackgroundTasks synchronously after response — fully testable. | This is the documented end-to-end flow. Today's test only proves the flag was passed. | `tests/integration/test_api.py` |
| **D2** | **`/leads/{id}/advance` invariant**: stage transitions update `last_action_at` strictly later than before, and `notes` is appended (not overwritten?) — confirm the documented semantics. | Lead pipeline is the only mutable lead state. Drift here corrupts trader workflow. | `tests/integration/test_api.py` |
| **D3** | **`/match` against a corrupt index** (manual `index.vectors = None` simulation) → confirm `MatchQueryResponse(hits=[])` not 500. | Robustness; today we only test the happy path + empty index. **Lower priority.** |
| **D4** | **`/healthz` reports `environment` accurately under each `Environment` enum** — currently only DEV asserted. | Sanity, but cheap. |

### E. CLI Coverage Gaps (medium impact)

| ID | What's missing | Why it matters | Where to put it |
|---|---|---|---|
| **E1** | **`hot --json` and `who-needs --json` output-shape tests** (lines 287–303, 345–359 are the JSON branches). Today only `who-has --json` shape is asserted. | Trivial cost, locks the contract. | `tests/integration/test_cli_app.py` |
| **E2** | **`brief --json` schema** — assert key set on the JSON payload (active_inventory, active_demand, top_matches, urgent_demand, fresh_inventory). | The brief is the documented machine-readable Deal Board surface. | `tests/integration/test_cli_app.py` |
| **E3** | **`ping --json`** outputs the rendered template inside a structured envelope. | Required for downstream Slack/Telegram bot integration. **Lower priority — verify the JSON path actually exists first.** |

### F. Property-Based Test Targets (for P8.3, listed here so they aren't lost)

These belong in P8.3 (hypothesis), not P8.1. Captured so they survive the phase boundary:

- `normalize_text` idempotence: `normalize_text(normalize_text(s)) == normalize_text(s)` for any unicode string.
- `extract_identity` never raises on arbitrary bytes-decoded strings (already asserted on a 6-case soup; widen with hypothesis text strategy).
- `hash_for(msg)` collision rate < 1e-6 on randomized 10 k message corpus (smoke under property strategy).
- `compute_spec_fit(inv, dem) == compute_spec_fit(dem, inv)` when both have full specs (commutativity).

## Recommended Action

1. **Land items A1, A2, B1, B2, B4, C1, C3, D1 in P8.2** — these close the genuinely *load-bearing* gaps (cross-source merge, freshness boundaries, background-task post-condition).
2. **Land items A3, A4, B3, B5, B6, C4, C5, C6, D2, E1, E2 opportunistically** during P8.2 if cycles allow — they're cheap and lock contracts that will otherwise rot.
3. **Defer A5, D3, D4, E3, F** to P8.3 / P8.5 explicitly.
4. **Resolve C2 by decision** *before* writing the test — flag in DECISIONS.md.

## Out-of-Scope Decisions (documented, no test needed)

| Decision | Rationale |
|---|---|
| `MiniLMEmbedder` / `FaissFlatIndex` paths stay uncovered in the default suite | Requires `[ml]` extra (~1.5 GB). Smoke-test exists. |
| `chat` REPL stays uncovered | Interactive; underlying scoring is fully tested elsewhere. |
| `--healthcheck` exit-code path of `worker/main.go` is not unit-tested in Python | Lives in Go; smoke-verified during P7.2. |

---

**Audit status:** complete. Implementation of recommended tests is **P8.2 scope** — this audit deliberately writes no production-test code. 714 / 714 pass · zero regressions.
