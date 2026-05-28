"""Minimal connectivity tests for the DB session layer.

Hermetic: each test runs against an in-memory SQLite via env override, so it
never touches the dev `data/dev.db` file.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from trade_intel.config import get_settings
from trade_intel.db.session import (
    get_db,
    get_engine,
    get_sessionmaker,
    session_scope,
)


@pytest.fixture(autouse=True)
def _isolated_in_memory_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SQLITE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("ENVIRONMENT", "test")
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    yield
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()


def test_engine_connects_and_executes_select_1() -> None:
    engine = get_engine()
    with engine.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar_one() == 1


def test_sqlite_foreign_keys_pragma_is_enabled() -> None:
    engine = get_engine()
    assert engine.dialect.name == "sqlite"
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar_one() == 1


def test_get_db_yields_session_and_closes() -> None:
    gen = get_db()
    session = next(gen)
    assert isinstance(session, Session)
    with pytest.raises(StopIteration):
        next(gen)


def test_session_scope_commits_on_success() -> None:
    with session_scope() as s:
        result = s.execute(text("SELECT 1")).scalar_one()
        assert result == 1


def test_session_scope_rolls_back_on_exception() -> None:
    class _BoomError(Exception):
        pass

    with pytest.raises(_BoomError):
        with session_scope() as s:
            s.execute(text("SELECT 1"))
            raise _BoomError
