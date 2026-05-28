"""Integration tests: CSV / Excel → ingest_messages → DB (P3.8)."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from openpyxl import Workbook
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from trade_intel.db.base import Base
from trade_intel.db.models import (
    Group,
    IntentType,
    Inventory,
    Message,
    Seller,
    SourceType,
)
from trade_intel.parsers import parse_csv, parse_excel
from trade_intel.persistence import ingest_messages

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "tabular"


@pytest.fixture
def session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sm = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    with sm() as s:
        yield s
    engine.dispose()


# ---------- CSV → ingest ----------


def test_csv_fixture_creates_sellers_messages_inventory(session: Session) -> None:
    stats = ingest_messages(
        parse_csv(FIXTURES / "inventory_sample.csv"),
        session=session,
        source_type=SourceType.CSV,
        source_path=str(FIXTURES / "inventory_sample.csv"),
    )
    assert stats.messages_seen == 6
    assert stats.messages_inserted == 6
    assert stats.inventory_inserted == 6
    assert stats.demand_inserted == 0

    sellers = {s.canonical_name for s in session.scalars(select(Seller)).all()}
    # 'Ali Mobiles' appears twice with same phone → merged into one Seller row.
    assert "ali mobiles" in sellers
    assert "khalid trading llc" in sellers

    ali = session.scalar(
        select(Seller).where(Seller.canonical_name == "ali mobiles")
    )
    assert ali is not None
    assert ali.phone == "+971501234567"
    assert ali.post_count == 2

    inv_products = {
        i.normalized_product for i in session.scalars(select(Inventory)).all()
    }
    assert {"iPhone 12 Pro Max", "iPad Mini 5", "iPad Air 5", "iPhone 15 Pro Max", "iPad Pro M2"} <= inv_products


def test_csv_group_source_type_csv(session: Session) -> None:
    ingest_messages(
        parse_csv(FIXTURES / "inventory_sample.csv"),
        session=session,
        source_type=SourceType.CSV,
        source_path=str(FIXTURES / "inventory_sample.csv"),
    )
    group = session.scalar(select(Group))
    assert group is not None
    assert group.source_type is SourceType.CSV


def test_csv_reingest_idempotent(session: Session) -> None:
    src = FIXTURES / "inventory_sample.csv"
    first = ingest_messages(
        parse_csv(src),
        session=session,
        source_type=SourceType.CSV,
        source_path=str(src),
    )
    second = ingest_messages(
        parse_csv(src),
        session=session,
        source_type=SourceType.CSV,
        source_path=str(src),
    )
    assert second.messages_inserted == 0
    assert second.messages_skipped_dup == first.messages_inserted


def test_csv_messages_classified_as_seller(session: Session) -> None:
    """The 'available' prefix in synthesized bodies should tag all rows
    as SELLER intent."""
    ingest_messages(
        parse_csv(FIXTURES / "inventory_sample.csv"),
        session=session,
        source_type=SourceType.CSV,
        source_path=str(FIXTURES / "inventory_sample.csv"),
    )
    intents = {m.intent for m in session.scalars(select(Message)).all()}
    assert intents == {IntentType.SELLER}


# ---------- Excel → ingest ----------


def test_excel_basic_flow(tmp_path: Path, session: Session) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Stock"
    ws.append(["Seller", "Phone", "Product", "Qty", "Price"])
    ws.append(["Ali Mobiles", "+971501234567", "iPhone 12 Pro Max", 5, 3200])
    ws.append(["Sara DXB", None, "iPad Mini 5", 3, 1800])
    p = tmp_path / "stock.xlsx"
    wb.save(p)

    stats = ingest_messages(
        parse_excel(p),
        session=session,
        source_type=SourceType.EXCEL,
        source_path=str(p),
    )
    assert stats.messages_inserted == 2
    assert stats.inventory_inserted == 2
    assert stats.sellers_created == 2

    group = session.scalar(select(Group))
    assert group is not None
    assert group.source_type is SourceType.EXCEL
    assert group.name == "Stock"


def test_excel_reingest_idempotent(tmp_path: Path, session: Session) -> None:
    wb = Workbook()
    wb.active.append(["Seller", "Product", "Date"])
    wb.active.append(["Ali", "iPhone 12 Pro Max", "2026-05-28"])
    p = tmp_path / "x.xlsx"
    wb.save(p)

    first = ingest_messages(
        parse_excel(p),
        session=session,
        source_type=SourceType.EXCEL,
        source_path=str(p),
    )
    second = ingest_messages(
        parse_excel(p),
        session=session,
        source_type=SourceType.EXCEL,
        source_path=str(p),
    )
    assert first.messages_inserted == 1
    assert second.messages_inserted == 0
    assert second.messages_skipped_dup == 1


# ---------- cross-source dedup ----------


def test_same_content_csv_and_excel_collapse(
    tmp_path: Path, session: Session
) -> None:
    """If a WhatsApp / CSV / Excel source emits the same sender + timestamp +
    body, the D-007 hash collapses them on a cross-source ingest."""
    csv_p = tmp_path / "x.csv"
    csv_p.write_text(
        "Seller,Phone,Product,Qty,Date\n"
        "Ali Mobiles,+971501234567,iPhone 12 Pro Max,5,2026-05-28\n",
        encoding="utf-8",
    )

    wb = Workbook()
    wb.active.append(["Seller", "Phone", "Product", "Qty", "Date"])
    wb.active.append(["Ali Mobiles", "+971501234567", "iPhone 12 Pro Max", 5, "2026-05-28"])
    xlsx_p = tmp_path / "x.xlsx"
    wb.save(xlsx_p)

    csv_stats = ingest_messages(
        parse_csv(csv_p),
        session=session,
        source_type=SourceType.CSV,
        source_path=str(csv_p),
    )
    xlsx_stats = ingest_messages(
        parse_excel(xlsx_p),
        session=session,
        source_type=SourceType.EXCEL,
        source_path=str(xlsx_p),
    )
    assert csv_stats.messages_inserted == 1
    assert xlsx_stats.messages_inserted == 0
    assert xlsx_stats.messages_skipped_dup == 1


# ---------- buyer intent hint ----------


def test_csv_with_buyer_intent_hint_routes_to_demand(
    tmp_path: Path, session: Session
) -> None:
    p = tmp_path / "buyers.csv"
    p.write_text(
        "Seller,Product,Qty,Intent\n"
        "Sara,iPhone 12 Pro Max,5,need urgent\n",
        encoding="utf-8",
    )
    stats = ingest_messages(
        parse_csv(p),
        session=session,
        source_type=SourceType.CSV,
        source_path=str(p),
    )
    assert stats.demand_inserted == 1
    assert stats.inventory_inserted == 0
    msg = session.scalar(select(Message))
    assert msg is not None
    assert msg.intent is IntentType.BUYER
