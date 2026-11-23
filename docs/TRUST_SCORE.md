# Supplier Trust Score (Phase 3)

A deterministic 0-100 reputation per seller, computed from real trader
behaviour already in the DB — no LLM, no extra services, cheap on a VPS. It is
persisted to `sellers.trust_score` and weighted **heavily** into broker ranking.

## Signals → score

| Signal | Source | Weight |
|---|---|---|
| inventory freshness | newest live listing age | 0.22 |
| active seller | `last_seen` recency (reply-speed proxy) | 0.20 |
| LP availability | share of listings quoting a price | 0.18 |
| quantity reliability | share of listings stating a real qty | 0.15 |
| repeat / established | posting volume | 0.15 |
| successful matches | A/A+ grade matches the stock produced | 0.10 |
| duplicate-spam penalty | many near-identical listings | −up to 25 |
| ghost penalty | no live inventory → capped at 40 | — |

**Grades:** A+ ≥85 · A ≥70 · B ≥50 · C ≥30 · Risky <30.

## Usage

```bash
trade-intel trust            # recompute + persist + show leaderboard
trade-intel trust --no-recompute   # show current stored scores
trade-intel trust --json -n 50
```

Cron-friendly (run alongside `run-matcher`). After a recompute the broker reads
`sellers.trust_score` for free during ranking.

## Ranking integration

Trader priority order (now enforced in `llm/ranking.py`):

```
product match (.32) > freshness (.22) > TRUST (.18) > qty fit (.12)
                    > LP/price (.08) > MOQ (.05) > location (.03)
```

Trust is the 3rd-strongest factor — above qty/LP/MOQ/location — so a trusted
seller outranks an unproven one for the same product, and duplicate-spam /
ghost sellers are pushed down. Each candidate's reason now includes
`trust <grade> (<score>)`.

## Validated (before/after, real dev data)

`trade-intel trust` recomputed 113 sellers, then the same queries were ranked
with trust off vs on (`rank_suppliers(..., apply_trust=False/True)`):

- **"Need 20 iPhone 16 Pro Max urgent"** — Mantse (trust C 47) and Waqas Mirza
  (C 31) rose into the top-5 above Risky-grade sellers.
- **"Who has Samsung S24 Ultra stock?"** — Mujitapha Musa (trust 49) overtook
  Muhmmad (37) for #1; the duplicate-spammer **Sunil was demoted out of the
  top-5** by the spam penalty.

Grades cluster at C/Risky on this corpus because the historical data is old and
rarely quotes LP — the engine rates that honestly; a fresh seller that shares
LP + states qty + has matches scores into A/A+.

## VPS notes

- Fully deterministic; one pass over sellers + a COUNT per seller for matches.
- Scores cached in the DB column → ranking adds zero per-query cost.
- No LLM, no new runtime services.
