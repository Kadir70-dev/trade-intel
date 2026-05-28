# Multi-stage Dockerfile for the trade-intel FastAPI service (P7.1).
#
# Build (lean — no sentence-transformers / faiss):
#   docker build -f docker/api.Dockerfile -t trade-intel/api:latest .
#
# Build (with [ml] extras — pulls torch + sentence-transformers, ~1.5 GB image):
#   docker build -f docker/api.Dockerfile --build-arg INSTALL_ML=true \
#       -t trade-intel/api:ml .
#
# Run:
#   docker run --rm -p 8000:8000 \
#       -e ENVIRONMENT=dev \
#       trade-intel/api:latest
#
# Designed for compose-orchestrated postgres (set POSTGRES_URL +
# ENVIRONMENT=prod + API_KEY).

ARG PYTHON_VERSION=3.12

# ---------- stage 1: builder ----------
FROM python:${PYTHON_VERSION}-slim AS builder

ARG INSTALL_ML=false

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Build essentials for any wheels that need compilation (numpy etc.
# usually ship binary wheels but we keep this for resilience).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy metadata + source first so dependency resolution caches well.
COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip wheel \
    && if [ "$INSTALL_ML" = "true" ]; then \
          /opt/venv/bin/pip install -e ".[ml,postgres]"; \
       else \
          /opt/venv/bin/pip install -e ".[postgres]"; \
       fi

# ---------- stage 2: runtime ----------
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Minimal runtime libs: libpq for psycopg, ca-certificates for HTTPS.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --user-group trade

WORKDIR /app

# Bring across the populated venv + source + migration assets.
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app/src ./src
COPY pyproject.toml README.md ./
COPY alembic.ini ./
COPY db/ ./db/

# Drop privileges.
RUN chown -R trade:trade /app
USER trade

EXPOSE 8000

# Hit the public /healthz endpoint via stdlib (no curl needed in slim image).
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request, sys; \
sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz', timeout=3).status == 200 else 1)"

CMD ["uvicorn", "trade_intel.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
