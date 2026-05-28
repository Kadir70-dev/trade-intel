# Docker stack (Phase 7)

Multi-service compose for the trade-intel API + ingestion worker, backed
by PostgreSQL and Qdrant.

## Quick start

```bash
docker compose -f docker/compose.yml up --build
```

Wait for the `migrate` service to exit cleanly, then hit:

- API → http://localhost:8000/healthz
- Swagger UI → http://localhost:8000/docs
- Qdrant → http://localhost:6333
- Postgres → `postgresql://trade:trade@localhost:5432/trade_intel`

## Services

| Service | Image | Purpose |
|---|---|---|
| `postgres` | `postgres:16-alpine` | Prod-mode DB (DECISIONS.md D-002) |
| `qdrant` | `qdrant/qdrant:v1.10.1` | Vector store upgrade path (D-003); not wired in v1 |
| `migrate` | built from `api.Dockerfile` | One-shot `alembic upgrade head` |
| `api` | built from `api.Dockerfile` | FastAPI service |
| `worker` | built from `worker.Dockerfile` | Go ingestion daemon (D-005 scaffold) |

## Configuration

Override defaults via env or a `.env` file next to `compose.yml`:

```bash
POSTGRES_USER=trade
POSTGRES_PASSWORD=trade
POSTGRES_DB=trade_intel
POSTGRES_PORT=5432
API_PORT=8000
API_KEY=changeme-dev-key   # set a real secret in prod
INSTALL_ML=false           # set true to bake sentence-transformers + faiss-cpu in
QDRANT_HTTP_PORT=6333
QDRANT_GRPC_PORT=6334
```

## Image variants

- **Lean (`INSTALL_ML=false`, default)** — ~250 MB. `MiniLMEmbedder` /
  `FaissFlatIndex` raise `ImportError` on use; `HashEmbedder` +
  `NumpyIndex` work out of the box.
- **Full (`INSTALL_ML=true`)** — ~1.5 GB. Includes `sentence-transformers`
  + `faiss-cpu` for real semantic matching.

Pick by passing the build arg:

```bash
docker build -f docker/api.Dockerfile --build-arg INSTALL_ML=true \
    -t trade-intel/api:ml .
```

## Migrations

The `migrate` service runs `alembic upgrade head` against postgres on
every `compose up`. Idempotent — re-running on a head DB is a no-op.

To run migrations manually:

```bash
docker compose -f docker/compose.yml run --rm migrate
```

## Worker (v1 scope)

The Go worker currently polls `GET /healthz` on a configurable interval
(see `worker/main.go`). Per D-005, the file-watcher + batched
`POST /ingest` path lands in a follow-up — the scaffold here proves the
build, the network wiring, and the healthcheck contract.

## Teardown

```bash
docker compose -f docker/compose.yml down          # stop containers
docker compose -f docker/compose.yml down -v       # also drop volumes
```
