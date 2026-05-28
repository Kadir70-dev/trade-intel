# Multi-stage Dockerfile for the trade-intel Go ingestion worker (P7.2).
#
# Per DECISIONS.md D-005, ingestion is decoupled into a Go binary so
# concurrent file scanning + batched API POSTs don't compete with the
# Python API for CPU. v1 ships only the daemon scaffold (see
# worker/main.go) — the file watcher + upload pipeline lands next.
#
# Build:
#   docker build -f docker/worker.Dockerfile -t trade-intel/worker:latest .

ARG GO_VERSION=1.22

# ---------- stage 1: builder ----------
FROM golang:${GO_VERSION}-alpine AS builder

ENV CGO_ENABLED=0 \
    GOOS=linux \
    GOFLAGS="-trimpath"

WORKDIR /src

# Module file first so `go mod download` caches when source changes.
COPY worker/go.mod ./
# Optional go.sum (none yet — pure stdlib).
COPY worker/go.sum* ./
RUN go mod download

COPY worker/ ./
RUN go build -ldflags="-s -w" -o /out/worker .

# ---------- stage 2: runtime ----------
FROM alpine:3.20 AS runtime

RUN apk add --no-cache ca-certificates \
    && addgroup -S trade \
    && adduser -S -G trade -u 10001 trade

COPY --from=builder /out/worker /usr/local/bin/worker

USER trade

ENV TRADE_INTEL_API_URL=http://api:8000 \
    WORKER_POLL_SECONDS=30

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["/usr/local/bin/worker", "--healthcheck"]

ENTRYPOINT ["/usr/local/bin/worker"]
