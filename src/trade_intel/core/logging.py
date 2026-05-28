"""Structured logging for trade-intel.

Two-channel design: structlog routes through stdlib `logging` so third-party
libraries (sqlalchemy, alembic, uvicorn, etc.) share the same handler and
formatter. JSON output in prod (machine-readable), ANSI-coloured in dev/test
(human-readable).

Contextual fields (`request_id`, `session_id`) are stored in `ContextVar`s
so FastAPI middleware (Phase 6) and CLI command wrappers (Phase 5) can attach
them without threading logger instances through every call site.

Usage:
    from trade_intel.core.logging import get_logger, configure_logging
    log = get_logger(__name__)
    log.info("ingest_started", file="dubai.txt", bytes=12345)
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import Any, cast

import structlog
from structlog.types import EventDict, Processor, WrappedLogger

from trade_intel.config import Environment, get_settings

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_session_id: ContextVar[str | None] = ContextVar("session_id", default=None)

_configured: bool = False


def set_request_id(value: str | None) -> None:
    """Bind a request id to the current context (e.g. from a FastAPI middleware)."""
    _request_id.set(value)


def set_session_id(value: str | None) -> None:
    """Bind a session id to the current context (e.g. for a CLI invocation)."""
    _session_id.set(value)


def _contextvar_processor(
    _logger: WrappedLogger, _name: str, event_dict: EventDict
) -> EventDict:
    """Attach request_id / session_id from contextvars when present."""
    rid = _request_id.get()
    sid = _session_id.get()
    if rid is not None:
        event_dict.setdefault("request_id", rid)
    if sid is not None:
        event_dict.setdefault("session_id", sid)
    return event_dict


def configure_logging(*, force: bool = False) -> None:
    """Configure stdlib logging + structlog. Idempotent unless `force=True`."""
    global _configured
    if _configured and not force:
        return

    settings = get_settings()
    log_level = logging.getLevelName(settings.log_level.value)
    pretty = settings.environment is not Environment.PROD

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _contextvar_processor,
        structlog.processors.StackInfoRenderer(),
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )

    # ConsoleRenderer formats exc_info itself; JSONRenderer wants it pre-formatted
    # into the event dict via format_exc_info.
    final_processors: list[Processor] = (
        [
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(colors=True),
        ]
        if pretty
        else [
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(sort_keys=True),
        ]
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=final_processors,
    )

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(log_level)

    _configured = True


def get_logger(
    name: str | None = None, **initial_values: Any
) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger, configuring on first call.

    Example:
        log = get_logger(__name__, component="parser")
        log.info("parsed", n=42)
        try:
            ...
        except Exception:
            log.exception("parse_failed")   # captures exc_info safely
    """
    configure_logging()
    logger = structlog.get_logger(name)
    if initial_values:
        logger = logger.bind(**initial_values)
    return cast("structlog.stdlib.BoundLogger", logger)
