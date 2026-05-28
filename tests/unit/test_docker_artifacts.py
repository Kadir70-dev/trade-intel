"""Sanity tests for the Phase 7 Docker artifacts.

We don't actually build images here (would require Docker, slow CI).
Instead we pin the key directives so silent regressions can't strip
healthchecks / non-root / multi-stage / port mappings without a test
failure. An optional `docker compose config` smoke test runs only when
the docker CLI is available.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DOCKER_DIR = PROJECT_ROOT / "docker"
WORKER_DIR = PROJECT_ROOT / "worker"
API_DOCKERFILE = DOCKER_DIR / "api.Dockerfile"
WORKER_DOCKERFILE = DOCKER_DIR / "worker.Dockerfile"
COMPOSE = DOCKER_DIR / "compose.yml"
DOCKERIGNORE = PROJECT_ROOT / ".dockerignore"


# ---------- file presence ----------


def test_api_dockerfile_exists() -> None:
    assert API_DOCKERFILE.is_file()


def test_worker_dockerfile_exists() -> None:
    assert WORKER_DOCKERFILE.is_file()


def test_compose_file_exists() -> None:
    assert COMPOSE.is_file()


def test_dockerignore_exists() -> None:
    assert DOCKERIGNORE.is_file()


def test_worker_go_files_exist() -> None:
    assert (WORKER_DIR / "main.go").is_file()
    assert (WORKER_DIR / "go.mod").is_file()


def test_docker_readme_exists() -> None:
    assert (DOCKER_DIR / "README.md").is_file()


# ---------- API Dockerfile shape ----------


def test_api_dockerfile_is_multistage() -> None:
    content = API_DOCKERFILE.read_text()
    assert "AS builder" in content
    assert "AS runtime" in content


def test_api_dockerfile_pins_python_version() -> None:
    content = API_DOCKERFILE.read_text()
    assert "PYTHON_VERSION=3.12" in content


def test_api_dockerfile_drops_to_non_root() -> None:
    content = API_DOCKERFILE.read_text()
    assert "useradd" in content
    assert "USER trade" in content


def test_api_dockerfile_exposes_8000_and_has_healthcheck() -> None:
    content = API_DOCKERFILE.read_text()
    assert "EXPOSE 8000" in content
    assert "HEALTHCHECK" in content
    assert "/healthz" in content


def test_api_dockerfile_default_cmd_runs_uvicorn() -> None:
    content = API_DOCKERFILE.read_text()
    assert "uvicorn" in content
    assert "trade_intel.api.main:app" in content


def test_api_dockerfile_install_ml_build_arg() -> None:
    content = API_DOCKERFILE.read_text()
    assert "ARG INSTALL_ML" in content
    assert 'INSTALL_ML' in content
    assert '[ml,postgres]' in content
    assert '[postgres]' in content


def test_api_dockerfile_copies_migration_assets() -> None:
    content = API_DOCKERFILE.read_text()
    assert "alembic.ini" in content
    assert "db/" in content


# ---------- worker Dockerfile shape ----------


def test_worker_dockerfile_is_multistage() -> None:
    content = WORKER_DOCKERFILE.read_text()
    assert "AS builder" in content
    assert "AS runtime" in content


def test_worker_dockerfile_drops_to_non_root() -> None:
    content = WORKER_DOCKERFILE.read_text()
    assert "adduser" in content
    assert "USER trade" in content


def test_worker_dockerfile_has_healthcheck_flag() -> None:
    content = WORKER_DOCKERFILE.read_text()
    assert "HEALTHCHECK" in content
    assert "--healthcheck" in content


def test_worker_dockerfile_cgo_disabled() -> None:
    """Static binary so the runtime stage can be alpine without glibc."""
    content = WORKER_DOCKERFILE.read_text()
    assert "CGO_ENABLED=0" in content


# ---------- compose.yml shape ----------


def test_compose_has_all_services() -> None:
    content = COMPOSE.read_text()
    for svc in ("postgres", "qdrant", "api", "worker", "migrate"):
        assert f"\n  {svc}:" in content, f"missing service {svc!r}"


def test_compose_defines_volumes() -> None:
    content = COMPOSE.read_text()
    assert "postgres-data" in content
    assert "qdrant-data" in content


def test_compose_api_depends_on_migrate() -> None:
    content = COMPOSE.read_text()
    assert "service_completed_successfully" in content


def test_compose_postgres_healthcheck() -> None:
    content = COMPOSE.read_text()
    assert "pg_isready" in content


def test_compose_api_runs_in_prod_mode() -> None:
    """Postgres-backed compose must set ENVIRONMENT=prod so Settings
    picks POSTGRES_URL over SQLITE_URL."""
    content = COMPOSE.read_text()
    assert "ENVIRONMENT: prod" in content
    assert "POSTGRES_URL:" in content
    assert "API_KEY:" in content


def test_compose_worker_targets_api_service() -> None:
    content = COMPOSE.read_text()
    assert "TRADE_INTEL_API_URL: http://api:8000" in content


def test_compose_uses_pinned_image_tags() -> None:
    """Avoid `latest` for base images — pinned versions are reproducible."""
    content = COMPOSE.read_text()
    assert "postgres:16-alpine" in content
    assert "qdrant/qdrant:v" in content  # any pinned v* tag


# ---------- .dockerignore shape ----------


def test_dockerignore_excludes_venv_and_pii() -> None:
    content = DOCKERIGNORE.read_text()
    for entry in (
        ".venv/",
        "data/raw/",
        "data/processed/",
        "data/samples/",
        "data/vectors/",
        "__pycache__/",
        ".git/",
        "tests/",
    ):
        assert entry in content, f"missing .dockerignore entry {entry!r}"


# ---------- worker Go source shape ----------


def test_worker_main_handles_healthcheck_flag() -> None:
    content = (WORKER_DIR / "main.go").read_text()
    assert "--healthcheck" in content or "healthcheck" in content
    assert "TRADE_INTEL_API_URL" in content
    assert "/healthz" in content


def test_worker_gomod_targets_go_1_22() -> None:
    content = (WORKER_DIR / "go.mod").read_text()
    assert "go 1.22" in content


# ---------- optional: docker compose config validates ----------


def _docker_compose_available() -> bool:
    """True iff `docker compose version` succeeds — the modern compose
    plugin is required (standalone `docker-compose` doesn't accept
    the same flag-positioning we use)."""
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        return False
    return result.returncode == 0


@pytest.mark.skipif(
    not _docker_compose_available(),
    reason="docker compose plugin not installed",
)
def test_docker_compose_config_validates() -> None:
    """If docker compose is available, prove compose syntax parses."""
    result = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), "config"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"`docker compose config` failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
