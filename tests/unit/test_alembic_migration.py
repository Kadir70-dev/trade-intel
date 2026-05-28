"""Alembic baseline migration: upgrade → downgrade → upgrade roundtrip
plus parity check against `Base.metadata`."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

import trade_intel.db.models  # noqa: F401 — populate Base.metadata
from trade_intel.config import get_settings
from trade_intel.db.base import Base
from trade_intel.db.session import get_engine, get_sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"

EXPECTED_TABLES = {
    "groups",
    "sellers",
    "buyers",
    "messages",
    "inventory",
    "demand",
    "matches",
    "embeddings",
    "lead_pipeline",
}


@pytest.fixture
def tmp_sqlite_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Point settings + alembic at an isolated tmp SQLite file."""
    db_path = tmp_path / "alembic_roundtrip.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("SQLITE_URL", url)
    monkeypatch.setenv("ENVIRONMENT", "test")
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    yield url
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()


def _alembic_config() -> Config:
    return Config(str(ALEMBIC_INI))


def _table_names(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_upgrade_creates_all_nine_tables(tmp_sqlite_url: str) -> None:
    command.upgrade(_alembic_config(), "head")
    tables = _table_names(tmp_sqlite_url)
    assert EXPECTED_TABLES <= tables
    assert "alembic_version" in tables


def test_downgrade_drops_every_entity_table(tmp_sqlite_url: str) -> None:
    cfg = _alembic_config()
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    tables = _table_names(tmp_sqlite_url)
    assert not (EXPECTED_TABLES & tables)
    assert tables == {"alembic_version"}


def test_upgrade_downgrade_upgrade_roundtrip(tmp_sqlite_url: str) -> None:
    cfg = _alembic_config()
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    tables = _table_names(tmp_sqlite_url)
    assert EXPECTED_TABLES <= tables


def test_migrated_indexes_match_metadata(tmp_sqlite_url: str) -> None:
    """Every named Index defined on a model should exist after upgrade."""
    command.upgrade(_alembic_config(), "head")
    engine = create_engine(tmp_sqlite_url)
    inspector = inspect(engine)

    expected_index_names: dict[str, set[str]] = {}
    for table_name, table in Base.metadata.tables.items():
        expected_index_names[table_name] = {idx.name for idx in table.indexes}

    for table_name, expected in expected_index_names.items():
        if not expected:
            continue
        actual = {idx["name"] for idx in inspector.get_indexes(table_name)}
        missing = expected - actual
        assert not missing, f"{table_name}: missing indexes {missing}"
    engine.dispose()


def test_migrated_foreign_keys_match_metadata(tmp_sqlite_url: str) -> None:
    command.upgrade(_alembic_config(), "head")
    engine = create_engine(tmp_sqlite_url)
    inspector = inspect(engine)

    fk_counts_expected = {
        "messages": 3,       # buyer, group, seller
        "inventory": 2,      # message, seller
        "demand": 2,         # message, buyer
        "matches": 2,        # demand, inventory
        "lead_pipeline": 1,  # match
        "embeddings": 0,     # polymorphic: NO FK
    }
    for table, expected in fk_counts_expected.items():
        actual = len(inspector.get_foreign_keys(table))
        assert actual == expected, f"{table}: expected {expected} FKs, got {actual}"
    engine.dispose()
