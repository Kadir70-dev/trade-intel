"""Engine + session lifecycle.

One process-wide engine, lazily built and cached. Sync sessions only — async
support is not part of Phase 2 (see DECISIONS.md D-002).

Public surface:
  - get_engine()       cached `Engine`
  - get_sessionmaker() cached `sessionmaker[Session]`
  - get_db()           generator for FastAPI dependency injection
  - session_scope()    context manager for CLI / scripts (auto commit/rollback)
  - init_db()          create all tables registered on `Base.metadata`

Tests can swap the DB by setting env vars and calling `cache_clear()` on
`get_settings`, `get_engine`, and `get_sessionmaker`.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from trade_intel.config import Settings, get_settings
from trade_intel.db.base import Base


def _sqlite_file_path(url: str) -> Path | None:
    """Return the filesystem path for a file-backed SQLite URL, else None."""
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return None
    tail = url[len(prefix) :]
    if not tail or tail == ":memory:" or "mode=memory" in tail:
        return None
    return Path(tail)


def _is_memory_sqlite(url: str) -> bool:
    return url.endswith(":memory:") or "mode=memory" in url


def _install_sqlite_pragmas(engine: Engine) -> None:
    """Enable foreign keys on every new SQLite connection.

    SQLite ships with FK enforcement OFF by default; the schema relies on FKs.
    """

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection: DBAPIConnection, _record: object) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()


def _build_engine(settings: Settings) -> Engine:
    url = settings.database_url
    kwargs: dict[str, Any] = {
        "echo": settings.db_echo,
        "future": True,
    }

    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if _is_memory_sqlite(url):
            kwargs["poolclass"] = StaticPool
        else:
            db_path = _sqlite_file_path(url)
            if db_path is not None:
                db_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        kwargs["pool_pre_ping"] = True
        kwargs["pool_size"] = 5
        kwargs["max_overflow"] = 10
        kwargs["pool_recycle"] = 1800

    engine = create_engine(url, **kwargs)
    if engine.dialect.name == "sqlite":
        _install_sqlite_pragmas(engine)
    return engine


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Process-wide cached SQLAlchemy `Engine`."""
    return _build_engine(get_settings())


@lru_cache(maxsize=1)
def get_sessionmaker() -> sessionmaker[Session]:
    """Cached `sessionmaker` bound to the cached engine."""
    return sessionmaker(
        bind=get_engine(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )


def get_db() -> Iterator[Session]:
    """FastAPI dependency: yields a `Session`, always closes it.

    Example:
        @app.get("/sellers")
        def list_sellers(db: Session = Depends(get_db)) -> list[Seller]: ...
    """
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for scripts / CLI.

    Commits on clean exit, rolls back on exception, always closes.
    """
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """Create every table registered on `Base.metadata` against the active engine.

    Dev / smoke-test helper only — production schema changes flow through Alembic
    (see P2.5). Imports the models package first so all mappings are attached
    to `Base.metadata` before `create_all` runs.
    """
    # Side-effect import: registers every ORM model on Base.metadata.
    import trade_intel.db.models  # noqa: F401

    Base.metadata.create_all(get_engine())
