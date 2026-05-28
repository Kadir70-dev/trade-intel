"""P8.5 — Database migration verification + production-config safety.

Exercises the alembic baseline end-to-end on both SQLite (always) and
PostgreSQL (gated by ``POSTGRES_TEST_URL``). Catches dialect-specific
DDL drift that the SQLite-only test suite would silently miss.

Coverage:

* upgrade head → downgrade base → re-upgrade head roundtrip
* zero schema drift (``alembic check``)
* foreign-key integrity (``inventory.seller_id`` → ``sellers.id``)
* unique-constraint integrity (``messages.hash``)
* partial unique index on ``sellers.phone`` (two NULL-phone rows coexist)
* full ingest + matcher pipeline produces identical row counts to SQLite
* idempotent re-ingest yields zero new rows
* production-environment ``Settings`` safety gates fire correctly

To run the Postgres path::

    POSTGRES_TEST_URL=postgresql+psycopg://user@host:port/db \\
        pytest tests/integration/test_db_migration_p8_5.py -k postgres
"""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

import trade_intel.db.models  # noqa: F401 — populate Base.metadata
from trade_intel.config import Environment, Settings, get_settings
from trade_intel.db.base import Base
from trade_intel.db.models import (
    Buyer,
    Demand,
    Grade,
    Inventory,
    LeadPipeline,
    Match,
    Message,
    Seller,
    SourceType,
)
from trade_intel.db.session import get_engine, get_sessionmaker
from trade_intel.matching import build_inventory_index
from trade_intel.ml import HashEmbedder
from trade_intel.parsers import parse_csv, parse_file
from trade_intel.persistence import (
    ingest_messages,
    match_demand_against_inventory,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"
E2E = PROJECT_ROOT / "tests" / "fixtures" / "e2e"
WHATSAPP_FIXTURE = E2E / "uae_wholesale_day.txt"
CSV_FIXTURE = E2E / "dubai_stock_sheet.csv"

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

# ============================================================
# Helpers
# ============================================================


def _alembic_config_for(url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _table_set(url: str) -> set[str]:
    eng = create_engine(url)
    try:
        return set(inspect(eng).get_table_names())
    finally:
        eng.dispose()


def _seed_full_pipeline(session: Session) -> tuple[int, int, int, int, int, int]:
    """Seed WhatsApp + CSV + run matcher. Returns the 6 invariant counts."""
    ingest_messages(
        parse_file(WHATSAPP_FIXTURE),
        session=session,
        source_type=SourceType.WHATSAPP,
        source_path=str(WHATSAPP_FIXTURE),
    )
    ingest_messages(
        parse_csv(CSV_FIXTURE),
        session=session,
        source_type=SourceType.CSV,
        source_path=str(CSV_FIXTURE),
    )
    emb = HashEmbedder(dim=64)
    idx, _ = build_inventory_index(session, embedder=emb)
    when = datetime(2026, 5, 28, 9, 0)
    for dem in session.scalars(
        select(Demand).where(Demand.is_active.is_(True))
    ).all():
        match_demand_against_inventory(
            dem,
            session=session,
            embedder=emb,
            inventory_index=idx,
            k=10,
            min_grade=Grade.C,
            now=when,
        )
    return (
        session.scalar(select(func.count(Seller.id))) or 0,
        session.scalar(select(func.count(Buyer.id))) or 0,
        session.scalar(select(func.count(Message.id))) or 0,
        session.scalar(select(func.count(Inventory.id))) or 0,
        session.scalar(select(func.count(Match.id))) or 0,
        session.scalar(select(func.count(LeadPipeline.id))) or 0,
    )


# ============================================================
# Group A — Production config safety
# ============================================================


def test_prod_env_requires_postgres_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """ENVIRONMENT=prod without POSTGRES_URL must be rejected at boot."""
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)
    with pytest.raises(ValueError, match="postgres_url is required"):
        Settings()


def test_prod_env_rejects_debug_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv(
        "POSTGRES_URL", "postgresql+psycopg://u@h:5432/d"
    )
    monkeypatch.setenv("API_KEY", "x")
    monkeypatch.setenv("DEBUG", "true")
    with pytest.raises(ValueError, match="debug must be False in prod"):
        Settings()


def test_prod_env_rejects_api_reload_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv(
        "POSTGRES_URL", "postgresql+psycopg://u@h:5432/d"
    )
    monkeypatch.setenv("API_KEY", "x")
    monkeypatch.setenv("DEBUG", "false")
    monkeypatch.setenv("API_RELOAD", "true")
    with pytest.raises(ValueError, match="api_reload must be False"):
        Settings()


def test_prod_env_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv(
        "POSTGRES_URL", "postgresql+psycopg://u@h:5432/d"
    )
    monkeypatch.delenv("API_KEY", raising=False)
    with pytest.raises(ValueError, match="api_key is required"):
        Settings()


def test_prod_env_picks_postgres_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """When all prod gates pass, database_url is the Postgres URL."""
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv(
        "POSTGRES_URL", "postgresql+psycopg://u@h:5432/d"
    )
    monkeypatch.setenv("API_KEY", "secret")
    monkeypatch.setenv("DEBUG", "false")
    monkeypatch.setenv("API_RELOAD", "false")
    s = Settings()
    assert s.environment is Environment.PROD
    assert s.database_url.startswith("postgresql+psycopg://")
    assert s.api_key == "secret"


def test_dev_env_falls_back_to_sqlite_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even with POSTGRES_URL set, dev/test mode prefers SQLite."""
    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.setenv(
        "POSTGRES_URL", "postgresql+psycopg://u@h:5432/d"
    )
    monkeypatch.delenv("API_KEY", raising=False)
    s = Settings()
    assert s.database_url.startswith("sqlite:")


# ============================================================
# Group B — Migration roundtrip + schema parity (SQLite)
# ============================================================


@pytest.fixture
def tmp_sqlite_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[str, None, None]:
    """Isolated tmp SQLite + cleared Settings cache."""
    db_path = tmp_path / "p8_5_roundtrip.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("SQLITE_URL", url)
    monkeypatch.setenv("ENVIRONMENT", "test")
    for c in (get_settings, get_engine, get_sessionmaker):
        c.cache_clear()
    yield url
    for c in (get_settings, get_engine, get_sessionmaker):
        c.cache_clear()


def test_sqlite_fresh_boot_upgrade_head_creates_all_tables(
    tmp_sqlite_url: str,
) -> None:
    cfg = _alembic_config_for(tmp_sqlite_url)
    command.upgrade(cfg, "head")
    assert _table_set(tmp_sqlite_url) >= EXPECTED_TABLES | {"alembic_version"}


def test_sqlite_full_roundtrip_upgrade_downgrade_upgrade(
    tmp_sqlite_url: str,
) -> None:
    cfg = _alembic_config_for(tmp_sqlite_url)
    command.upgrade(cfg, "head")
    assert _table_set(tmp_sqlite_url) >= EXPECTED_TABLES
    command.downgrade(cfg, "base")
    after_down = _table_set(tmp_sqlite_url)
    assert after_down.isdisjoint(EXPECTED_TABLES)
    command.upgrade(cfg, "head")
    assert _table_set(tmp_sqlite_url) >= EXPECTED_TABLES


def test_sqlite_zero_schema_drift_after_upgrade(tmp_sqlite_url: str) -> None:
    """``alembic check`` must report zero drift between Base.metadata and
    the upgraded DB schema. Catches a forgotten autogenerate revision."""
    cfg = _alembic_config_for(tmp_sqlite_url)
    command.upgrade(cfg, "head")
    # alembic.command.check raises CommandError on drift.
    command.check(cfg)


def test_sqlite_partial_unique_phone_index_allows_multiple_nulls(
    tmp_sqlite_url: str,
) -> None:
    """``WHERE phone IS NOT NULL`` partial index lets two NULL-phone
    sellers coexist."""
    cfg = _alembic_config_for(tmp_sqlite_url)
    command.upgrade(cfg, "head")
    eng = create_engine(tmp_sqlite_url, connect_args={"check_same_thread": False})
    sm = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    try:
        with sm() as s:
            s.add(Seller(canonical_name="alpha", phone=None, post_count=1))
            s.add(Seller(canonical_name="beta", phone=None, post_count=1))
            s.commit()
            assert s.scalar(select(func.count(Seller.id))) == 2
    finally:
        eng.dispose()


def test_sqlite_messages_hash_unique_constraint_fires(
    tmp_sqlite_url: str,
) -> None:
    cfg = _alembic_config_for(tmp_sqlite_url)
    command.upgrade(cfg, "head")
    eng = create_engine(tmp_sqlite_url, connect_args={"check_same_thread": False})
    sm = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    try:
        with sm() as s:
            s.add(
                Message(
                    sender_name="A",
                    raw_text="x",
                    message_ts=datetime(2026, 5, 28),
                    hash="P8_5_DUP_HASH",
                )
            )
            s.commit()
        with pytest.raises(IntegrityError):
            with sm() as s:
                s.add(
                    Message(
                        sender_name="B",
                        raw_text="y",
                        message_ts=datetime(2026, 5, 28),
                        hash="P8_5_DUP_HASH",
                    )
                )
                s.commit()
    finally:
        eng.dispose()


# ============================================================
# Group C — Postgres roundtrip + integrity (opt-in)
# ============================================================

_PG_URL = os.environ.get("POSTGRES_TEST_URL")
_PG_REASON = (
    "Set POSTGRES_TEST_URL=postgresql+psycopg://user@host:port/db to run "
    "the live-Postgres half of P8.5."
)
postgres_only = pytest.mark.skipif(_PG_URL is None, reason=_PG_REASON)


def _reset_postgres(url: str) -> None:
    """Drop every public-schema table so each test starts clean."""
    eng = create_engine(url)
    try:
        with eng.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
    finally:
        eng.dispose()


@pytest.fixture
def pg_url(monkeypatch: pytest.MonkeyPatch) -> Generator[str, None, None]:
    """Cleaned Postgres URL + env wired so ``Settings`` (used by
    ``db/migrations/env.py``) picks the Postgres URL.

    ``env.py`` overrides any ``sqlalchemy.url`` we pass via ``Config``,
    so the only way to point alembic at Postgres is via the production
    Settings path (``ENVIRONMENT=prod`` + ``POSTGRES_URL`` + safety gates).
    """
    assert _PG_URL is not None  # guarded by ``postgres_only`` marker
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("POSTGRES_URL", _PG_URL)
    monkeypatch.setenv("API_KEY", "p8-5-test")
    monkeypatch.setenv("DEBUG", "false")
    monkeypatch.setenv("API_RELOAD", "false")
    for c in (get_settings, get_engine, get_sessionmaker):
        c.cache_clear()
    _reset_postgres(_PG_URL)
    yield _PG_URL
    _reset_postgres(_PG_URL)
    for c in (get_settings, get_engine, get_sessionmaker):
        c.cache_clear()


@postgres_only
def test_postgres_upgrade_head_creates_all_tables(pg_url: str) -> None:
    cfg = _alembic_config_for(pg_url)
    command.upgrade(cfg, "head")
    assert _table_set(pg_url) >= EXPECTED_TABLES


@postgres_only
def test_postgres_full_roundtrip_upgrade_downgrade_upgrade(
    pg_url: str,
) -> None:
    cfg = _alembic_config_for(pg_url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    after_down = _table_set(pg_url)
    assert after_down.isdisjoint(EXPECTED_TABLES)
    command.upgrade(cfg, "head")
    assert _table_set(pg_url) >= EXPECTED_TABLES


@postgres_only
def test_postgres_zero_schema_drift(pg_url: str) -> None:
    cfg = _alembic_config_for(pg_url)
    command.upgrade(cfg, "head")
    command.check(cfg)  # raises CommandError on drift


@postgres_only
def test_postgres_messages_hash_unique_constraint(pg_url: str) -> None:
    cfg = _alembic_config_for(pg_url)
    command.upgrade(cfg, "head")
    eng = create_engine(pg_url)
    sm = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    try:
        with sm() as s:
            s.add(
                Message(
                    sender_name="A",
                    raw_text="x",
                    message_ts=datetime(2026, 5, 28),
                    hash="P8_5_PG_DUP",
                )
            )
            s.commit()
        with pytest.raises(IntegrityError):
            with sm() as s:
                s.add(
                    Message(
                        sender_name="B",
                        raw_text="y",
                        message_ts=datetime(2026, 5, 28),
                        hash="P8_5_PG_DUP",
                    )
                )
                s.commit()
    finally:
        eng.dispose()


@postgres_only
def test_postgres_partial_unique_phone_allows_multiple_nulls(
    pg_url: str,
) -> None:
    cfg = _alembic_config_for(pg_url)
    command.upgrade(cfg, "head")
    eng = create_engine(pg_url)
    sm = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    try:
        with sm() as s:
            s.add(Seller(canonical_name="alpha", phone=None, post_count=1))
            s.add(Seller(canonical_name="beta", phone=None, post_count=1))
            s.commit()
            assert s.scalar(select(func.count(Seller.id))) == 2
        with pytest.raises(IntegrityError):
            with sm() as s:
                s.add(
                    Seller(
                        canonical_name="x",
                        phone="+971500000000",
                        post_count=1,
                    )
                )
                s.add(
                    Seller(
                        canonical_name="y",
                        phone="+971500000000",
                        post_count=1,
                    )
                )
                s.commit()
    finally:
        eng.dispose()


@postgres_only
def test_postgres_fk_integrity_inventory_seller(pg_url: str) -> None:
    cfg = _alembic_config_for(pg_url)
    command.upgrade(cfg, "head")
    eng = create_engine(pg_url)
    sm = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    try:
        with pytest.raises((IntegrityError, ProgrammingError)):
            with sm() as s:
                s.add(Inventory(normalized_product="ghost", seller_id=999_999_999))
                s.commit()
    finally:
        eng.dispose()


@postgres_only
def test_postgres_full_pipeline_yields_same_counts_as_sqlite(
    pg_url: str,
) -> None:
    """The whole ingest → match → leads pipeline must produce
    byte-identical row counts on Postgres and SQLite."""
    cfg = _alembic_config_for(pg_url)
    command.upgrade(cfg, "head")
    eng_pg = create_engine(pg_url)
    sm = sessionmaker(bind=eng_pg, autoflush=False, autocommit=False)
    try:
        with sm() as s:
            pg_counts = _seed_full_pipeline(s)
            s.commit()
    finally:
        eng_pg.dispose()
    # SQLite reference — fresh in-memory DB.
    from sqlalchemy.pool import StaticPool

    eng_sq = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng_sq)
    sm_sq = sessionmaker(bind=eng_sq, autoflush=False, autocommit=False)
    try:
        with sm_sq() as s:
            sqlite_counts = _seed_full_pipeline(s)
    finally:
        eng_sq.dispose()
    assert pg_counts == sqlite_counts, (
        f"Postgres counts {pg_counts} differ from SQLite {sqlite_counts}"
    )


@postgres_only
def test_postgres_reingest_is_idempotent(pg_url: str) -> None:
    cfg = _alembic_config_for(pg_url)
    command.upgrade(cfg, "head")
    eng = create_engine(pg_url)
    sm = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    try:
        with sm() as s:
            ingest_messages(
                parse_file(WHATSAPP_FIXTURE),
                session=s,
                source_type=SourceType.WHATSAPP,
                source_path=str(WHATSAPP_FIXTURE),
            )
            ingest_messages(
                parse_csv(CSV_FIXTURE),
                session=s,
                source_type=SourceType.CSV,
                source_path=str(CSV_FIXTURE),
            )
            s.commit()
            initial_msg = s.scalar(select(func.count(Message.id))) or 0
            initial_inv = s.scalar(select(func.count(Inventory.id))) or 0

        with sm() as s:
            r1 = ingest_messages(
                parse_file(WHATSAPP_FIXTURE),
                session=s,
                source_type=SourceType.WHATSAPP,
                source_path=str(WHATSAPP_FIXTURE),
            )
            r2 = ingest_messages(
                parse_csv(CSV_FIXTURE),
                session=s,
                source_type=SourceType.CSV,
                source_path=str(CSV_FIXTURE),
            )
            s.commit()
            assert r1.messages_inserted == 0
            assert r2.messages_inserted == 0
            assert s.scalar(select(func.count(Message.id))) == initial_msg
            assert s.scalar(select(func.count(Inventory.id))) == initial_inv
    finally:
        eng.dispose()
