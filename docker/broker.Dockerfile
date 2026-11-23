# Go chat broker + the Python facts bridge it shells out to (DB = source of truth).
#   docker build -f docker/broker.Dockerfile -t trade-intel/broker .
ARG PYTHON_VERSION=3.12
ARG GO_VERSION=1.22

FROM golang:${GO_VERSION}-alpine AS gobuild
ENV CGO_ENABLED=0 GOOS=linux GOFLAGS="-trimpath"
WORKDIR /src
COPY worker/go.mod ./
COPY worker/go.sum* ./
RUN go mod download
COPY worker/ ./
RUN go build -ldflags="-s -w" -o /out/broker ./cmd/broker

FROM python:${PYTHON_VERSION}-slim AS pybuild
ENV PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends build-essential gcc \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip wheel \
    && /opt/venv/bin/pip install ".[postgres]"

FROM python:${PYTHON_VERSION}-slim AS runtime
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    TRADE_INTEL_PYTHON=/opt/venv/bin/python \
    TRADE_INTEL_FACTS_SCRIPT=/app/scripts/broker_facts.py \
    BROKER_ADDR=0.0.0.0:8088
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --user-group trade
WORKDIR /app
COPY --from=pybuild /opt/venv /opt/venv
COPY --from=gobuild /out/broker /usr/local/bin/broker
COPY scripts/broker_facts.py ./scripts/broker_facts.py
RUN chown -R trade:trade /app
USER trade
EXPOSE 8088
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8088/healthz', timeout=4).status == 200 else 1)"
ENTRYPOINT ["broker"]
CMD ["serve"]
