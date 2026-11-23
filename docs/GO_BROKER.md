# Go Broker Backend

The **primary backend** for the trade-intel AI broker assistant. Written in Go
for low RAM and cheap-VPS efficiency; the existing Python parser / matcher / DB
stays the **source of truth**.

## Architecture

```
trade-intel chat   (Python Typer CLI — thin HTTP/SSE client)
        │  POST {query} → streams tokens via SSE   BROKER_URL=http://localhost:8088
        ▼
bin/broker serve  (Go, static, stdlib-only)    ← PRIMARY BACKEND
        │  orchestration · chat memory · concurrency · HTTP/SSE · worker pool
        │
        ├── facts bridge ──► .venv/bin/python scripts/broker_facts.py
        │                      └─ matcher = SOURCE OF TRUTH (verified JSON facts)
        │
        └── Ollama client ──► http://127.0.0.1:11434  (llama3.2:3b default)
                               └─ reasoning · ranking · WhatsApp message only
```

`trade-intel chat` resolves a backend in this order: (1) a running broker
server at `BROKER_URL`; (2) the local `bin/broker` binary; (3) an in-process
Python REPL. The model is chosen via `OLLAMA_MODEL` (forwarded per-request so
the client can override the server default).

**No paid APIs.** All inference is local via Ollama. Database facts always win;
the LLM only reasons over the verified candidate rows it is handed and may not
invent suppliers, prices, or quantities. If the LLM is down, the backend falls
back to a deterministic, fully-grounded template — it never depends on the
model being up.

## Build & run

```bash
make broker            # build → bin/broker (CGO-free static, ~8.7 MB)
make chat              # build + launch the chat REPL
make broker-serve      # build + start HTTP API + SSE streaming
make broker-health     # one-shot Ollama reachability probe

trade-intel chat       # Python entry — delegates to bin/broker when built
```

Binary modes:

| Command | Purpose |
|---|---|
| `broker chat` | interactive REPL, multi-turn memory (`/reset` clears, `exit` quits) |
| `broker ask "<request>"` | one-shot turn (`--json` for machine output) |
| `broker serve` | HTTP `POST /chat`, `POST /chat/stream` (SSE), `GET /healthz` |
| `broker --healthcheck` | exit 0 if Ollama reachable, 1 otherwise |

## Configuration (env — model-agnostic)

Shared with the Python engine via `.env` / real env vars. See `.env.example`.

| Var | Default | Notes |
|---|---|---|
| `OLLAMA_MODEL` | `llama3.2:3b` | primary (alias: `LLM_MODEL`); fast + small for CPU laptops |
| `OLLAMA_FALLBACK_MODEL` | `qwen3:4b` | auto-used if primary isn't pulled (alias: `LLM_FALLBACK_MODEL`) |
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | |
| `LLM_NUM_CTX` | `2048` | small = low RAM + faster first token |
| `LLM_NUM_PREDICT` | `512` | generation cap |
| `LLM_NUM_THREAD` | `0` | 0 = Ollama picks (core count) |
| `LLM_KEEP_ALIVE` | `5m` | unloads model when idle to free RAM; `-1` pins |
| `LLM_TIMEOUT` | `120` | per-request seconds — **raise on slow CPU boxes** |
| `LLM_ENABLED` | `true` | `false` → deterministic template only (instant) |
| `BROKER_ADDR` | `127.0.0.1:8088` | `serve` listen address |
| `BROKER_URL` | `http://localhost:8088` | where `trade-intel chat` connects |
| `BROKER_MAX_HISTORY` | `8` | chat turns kept per session |
| `TRADE_INTEL_TOPK` | `5` | top-K suppliers per query |

## Performance note (CPU inference)

CPU-only inference is slow. On a 4-core / 8 GB box, `qwen3:4b` runs at
~1.5–2 tok/s, so a full broker reply takes 1–2 minutes. The CLI **streams**
tokens so output appears progressively; on weak boxes raise `LLM_TIMEOUT`
(e.g. `600`).

`qwen3` is a *reasoning* model and may emit chain-of-thought; the backend
post-processes the final answer to start at the `Found:` marker (model-agnostic
preamble trim). For terse, format-strict output on small boxes, a non-reasoning
model (`hermes3`, `llama3:8b`) or the deterministic template (`LLM_ENABLED=false`)
is cleaner.

## Redis cache (Phase 1)

Repeated/similar broker queries are served from Redis instead of re-running the
matcher + LLM. Measured on this box: a cold reply (~165 s with LLM) drops to
**~2 ms on a cache hit**.

Start Redis in pure-cache mode (bounded RAM, LRU eviction, no persistence —
ideal for a cheap VPS):

```bash
redis-server --port 6379 --maxmemory 128mb --maxmemory-policy allkeys-lru --save ""
```

- **Stdlib RESP client** — no Go module dependency; the binary stays static.
- **Exact + fuzzy matching** — queries are reduced to a deterministic signature
  (lowercase, punctuation/word-order/filler-insensitive) that preserves product
  + quantity + urgency. "Need iPhone 16 Pro Max urgent" and "iphone 16 pro max,
  need it URGENT!!" share one entry; a different qty makes a distinct one.
- **TTL by urgency** — urgent 5 min, normal 15 min, insights 30 min (env-tunable).
- **Invalidation on inventory update** — `POST /cache/invalidate` bumps a
  namespace version (O(1), no SCAN); `trade-intel parse` pings it best-effort
  after an ingest.
- **Graceful fallback** — if Redis is down, reads miss and writes no-op; the
  broker keeps serving uncached (`/healthz` shows `cache_healthy: false`).

Caching is keyed on query-signature + model (not session), so the
history-independent ranking + WhatsApp draft are shared across callers.

## Module layout (`worker/`)

```
cmd/broker/main.go         CLI: chat | ask | serve | --healthcheck
internal/config/           env config (mirrors Python settings)
internal/ollama/           stdlib Ollama client (generate + stream + fallback)
internal/facts/            Python ML bridge (matcher = source of truth)
internal/broker/           prompts + orchestration + grounded template fallback
internal/session/          in-memory, bounded, concurrency-safe chat memory
internal/cache/            stdlib Redis (RESP) reply cache + query signature
internal/server/           HTTP API + SSE streaming + worker-pool concurrency
```

HTTP endpoints: `GET /healthz`, `POST /chat`, `POST /chat/stream` (SSE),
`POST /cache/invalidate`.
