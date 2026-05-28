"""Smoke tests for the structlog wrapper."""

from __future__ import annotations

import json
import logging
import re

import pytest
import structlog

from trade_intel.config import get_settings

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


@pytest.fixture(autouse=True)
def _reset_logging_state(monkeypatch: pytest.MonkeyPatch) -> None:
    import trade_intel.core.logging as logmod

    logmod._configured = False
    structlog.reset_defaults()
    get_settings.cache_clear()
    logmod.set_request_id(None)
    logmod.set_session_id(None)
    # Reset stdlib root handlers between tests.
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    yield
    logmod._configured = False
    structlog.reset_defaults()
    get_settings.cache_clear()
    logmod.set_request_id(None)
    logmod.set_session_id(None)


def test_imports_only() -> None:
    from trade_intel.core.logging import (
        configure_logging,
        get_logger,
        set_request_id,
        set_session_id,
    )

    assert callable(configure_logging)
    assert callable(get_logger)
    assert callable(set_request_id)
    assert callable(set_session_id)


def test_smoke_log_renders_in_dev(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "dev")
    from trade_intel.core.logging import get_logger

    log = get_logger("test.smoke")
    log.info("hello", k=42)

    plain = _strip_ansi(capsys.readouterr().err)
    assert "hello" in plain
    assert "k=42" in plain
    assert "test.smoke" in plain


def test_log_emits_json_in_prod(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("POSTGRES_URL", "postgresql+psycopg://u:p@h/db")
    monkeypatch.setenv("API_KEY", "test-prod-key")
    monkeypatch.setenv("DEBUG", "false")
    monkeypatch.setenv("API_RELOAD", "false")
    from trade_intel.core.logging import get_logger

    log = get_logger("test.prod")
    log.info("event_x", widget="acme")

    line = capsys.readouterr().err.strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["event"] == "event_x"
    assert payload["widget"] == "acme"
    assert payload["logger"] == "test.prod"
    assert payload["level"] == "info"
    assert "timestamp" in payload


def test_request_id_contextvar_propagates(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("POSTGRES_URL", "postgresql+psycopg://u:p@h/db")
    monkeypatch.setenv("API_KEY", "test-prod-key")
    from trade_intel.core.logging import get_logger, set_request_id, set_session_id

    set_request_id("req-abc-123")
    set_session_id("sess-xyz-9")
    log = get_logger("test.ctx")
    log.info("traced")

    payload = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert payload["request_id"] == "req-abc-123"
    assert payload["session_id"] == "sess-xyz-9"


def test_exception_logging_captures_traceback(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("POSTGRES_URL", "postgresql+psycopg://u:p@h/db")
    monkeypatch.setenv("API_KEY", "test-prod-key")
    from trade_intel.core.logging import get_logger

    log = get_logger("test.exc")
    try:
        raise ValueError("boom")
    except ValueError:
        log.exception("operation_failed")

    payload = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert payload["event"] == "operation_failed"
    assert payload["level"] == "error"
    assert "ValueError" in payload["exception"]
    assert "boom" in payload["exception"]
