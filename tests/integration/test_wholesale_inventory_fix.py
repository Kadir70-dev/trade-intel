"""UAE wholesale inventory-extraction fix — bug-fix regression tests.

Reproduces the failure mode that motivated the fix::

    "Supplier Hassan: iPhone cases all models 12-15 Pro Max, MOQ 20 pcs,
     price AED 5-7, WhatsApp +971501112233"

    "Dragon Mart Wholesale: Xiaomi Redmi covers all models, MOQ 20,
     AED 3.5 each, in stock"

Before the fix both produced ``inventory_inserted == 0`` because:

* the intent classifier didn't recognize ``MOQ`` / ``pcs`` / ``price AED``
  / ``wholesale`` as SELLER signals, so the first message classified as
  UNKNOWN and persistence skipped the Inventory write;
* the product dictionary had no entry for accessories (``cases`` /
  ``covers``) or Android brands (``Xiaomi Redmi``), so even when intent
  was SELLER, ``_write_product_rows`` early-returned on an empty
  product list.

After the fix both messages produce SELLER intent + at least one
extracted product → Inventory rows land → the matcher finds candidates.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from trade_intel.core.intent import classify_intent
from trade_intel.db.base import Base
from trade_intel.db.models import (
    Buyer,
    Demand,
    Grade,
    IntentType,
    Inventory,
    LeadPipeline,
    Match,
    Seller,
    SourceType,
)
from trade_intel.matching import build_inventory_index
from trade_intel.ml import HashEmbedder
from trade_intel.normalization import extract_products
from trade_intel.parsers import parse_file, parse_lines
from trade_intel.persistence import (
    ingest_messages,
    match_demand_against_inventory,
)


@pytest.fixture
def session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sm = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )
    with sm() as s:
        yield s
    engine.dispose()


# ============================================================
# A. Reproductions of the exact user-reported messages
# ============================================================


def test_supplier_hassan_message_classifies_as_seller() -> None:
    body = (
        "iPhone cases all models 12-15 Pro Max, MOQ 20 pcs, "
        "price AED 5-7, WhatsApp +971501112233"
    )
    result = classify_intent(body)
    assert result.intent is IntentType.SELLER
    triggered = {sig.keyword for sig in result.signals}
    # All three new wholesale signals must fire on this message.
    assert "moq" in triggered
    assert "pcs" in triggered
    assert "price aed" in triggered


def test_supplier_hassan_message_extracts_products() -> None:
    body = (
        "iPhone cases all models 12-15 Pro Max, MOQ 20 pcs, "
        "price AED 5-7, WhatsApp +971501112233"
    )
    surfaces = {p.canonical.canonical_name for p in extract_products(body)}
    assert "Phone Cases" in surfaces
    assert "iPhone 15 Pro Max" in surfaces


def test_dragon_mart_message_classifies_as_seller() -> None:
    body = "Xiaomi Redmi covers all models, MOQ 20, AED 3.5 each, in stock"
    result = classify_intent(body)
    assert result.intent is IntentType.SELLER
    triggered = {sig.keyword for sig in result.signals}
    assert "in stock" in triggered
    assert "moq" in triggered


def test_dragon_mart_message_extracts_brand_and_accessory() -> None:
    body = "Xiaomi Redmi covers all models, MOQ 20, AED 3.5 each, in stock"
    names = {p.canonical.canonical_name for p in extract_products(body)}
    assert "Xiaomi Redmi" in names
    assert "Phone Covers" in names


# ============================================================
# B. End-to-end pipeline reproduction
# ============================================================


def test_full_pipeline_writes_inventory_for_user_reported_messages(
    session: Session,
) -> None:
    """Mirrors the user's bug-report validation: feed the two failing
    messages plus two matching demands → ``inventory_inserted > 0`` and
    ``run-matcher`` writes at least one Match."""
    lines = [
        "28/05/26, 09:00 - Supplier Hassan: iPhone cases all models 12-15 "
        "Pro Max, MOQ 20 pcs, price AED 5-7, WhatsApp +971501112233",
        "28/05/26, 09:01 - Dragon Mart Wholesale: Xiaomi Redmi covers "
        "all models, MOQ 20, AED 3.5 each, in stock",
        "28/05/26, 09:02 - Ahmed: need urgent iPhone cases bulk quote MOQ 50",
        "28/05/26, 09:03 - Bilal: req 30 redmi covers urgent",
    ]
    msgs = list(parse_lines(lines, source_file="test.txt", group_name="g"))
    stats = ingest_messages(
        msgs,
        session=session,
        source_type=SourceType.WHATSAPP,
        source_path="test.txt",
    )
    session.commit()

    # Goal: inventory_inserted > 0 (was 0 before the fix).
    assert stats.inventory_inserted > 0
    assert stats.inventory_inserted >= 2  # Hassan + Dragon Mart
    assert stats.demand_inserted >= 1
    active_inventory = (
        session.scalar(
            select(func.count(Inventory.id)).where(Inventory.is_active.is_(True))
        )
        or 0
    )
    assert active_inventory > 0

    # Run matcher — at least one demand must match the new inventory.
    embedder = HashEmbedder(dim=64)
    idx, _ = build_inventory_index(session, embedder=embedder)
    assert idx.size > 0
    for dem in session.scalars(
        select(Demand).where(Demand.is_active.is_(True))
    ).all():
        match_demand_against_inventory(
            dem,
            session=session,
            embedder=embedder,
            inventory_index=idx,
            k=10,
            min_grade=Grade.C,
            now=datetime(2026, 5, 28, 9, 30),
        )
    session.commit()
    assert (
        session.scalar(select(func.count(Match.id))) or 0
    ) > 0, "matcher should produce at least one Match"
    assert (session.scalar(select(func.count(LeadPipeline.id))) or 0) > 0


def test_converted_whatsapp_file_yields_nonzero_inventory(
    session: Session, tmp_path: Path
) -> None:
    """The user-specified validation: a ``converted_whatsapp.txt`` style
    export (synthesized in-test) must yield ``inventory_inserted > 0``."""
    text = (
        "28/05/26, 10:00 - Supplier Hassan: iPhone cases MOQ 20 pcs "
        "price AED 5 each in stock\n"
        "28/05/26, 10:01 - Dragon Mart Wholesale: Xiaomi Redmi covers "
        "all models AED 3.5 each\n"
        "28/05/26, 10:02 - Ali Mobiles: avail air5 64gb wholesale price "
        "AED 2400 ready stock\n"
        "28/05/26, 10:03 - Khalid: stk 12pm 256gb MOQ 10 pcs each AED 3200\n"
    )
    converted = tmp_path / "converted_whatsapp.txt"
    converted.write_text(text, encoding="utf-8")

    stats = ingest_messages(
        parse_file(converted),
        session=session,
        source_type=SourceType.WHATSAPP,
        source_path=str(converted),
    )
    session.commit()
    assert stats.inventory_inserted > 0


# ============================================================
# C. Lexicon coverage — wholesale phrasing
# ============================================================


@pytest.mark.parametrize(
    "body",
    [
        "MOQ 20 pcs iPhone 15 pro max",
        "wholesale 12pm bulk available",
        "bulk available stock 15pm",
        "price AED 3200 each ready stock",
        "ready to dispatch iPad mini 6 64gb",
        "ready to ship 20 pcs iPad air 5",
        "min order 50 pcs iPad pro m2",
        "minimum order 100 12pm",
        "supplier of iPhone covers cases",
        "wholesale price 15pm 256gb",
    ],
)
def test_seller_phrases_classify_as_seller(body: str) -> None:
    result = classify_intent(body)
    assert result.intent is IntentType.SELLER, (
        f"expected SELLER, got {result.intent.value} for {body!r}"
    )


@pytest.mark.parametrize(
    "body",
    [
        "av 12pm 256gb fresh",
        "stk 15pm sealed",
        "stk ready iPad mini 6",
        "pcs available bulk",
    ],
)
def test_seller_shorthand_classifies_as_seller(body: str) -> None:
    result = classify_intent(body)
    assert result.intent is IntentType.SELLER, (
        f"shorthand {body!r} should classify as SELLER but got "
        f"{result.intent.value}"
    )


# ============================================================
# D. Product dictionary coverage — accessories + Android brands
# ============================================================


@pytest.mark.parametrize(
    "body,expected_name",
    [
        ("iphone case in stock", "Phone Cases"),
        ("phone cases MOQ 20", "Phone Cases"),
        ("mobile case wholesale", "Phone Cases"),
        ("phone cover bulk available", "Phone Covers"),
        ("back cover all models", "Phone Covers"),
        ("mobile covers ready stock", "Phone Covers"),
        ("phone accessories MOQ 50", "Accessories"),
        ("chargers in stock 20 pcs", "Chargers"),
        ("charging cable wholesale", "Chargers"),
    ],
)
def test_accessory_extraction(body: str, expected_name: str) -> None:
    names = {p.canonical.canonical_name for p in extract_products(body)}
    assert expected_name in names, (
        f"expected {expected_name!r} in {names} for body {body!r}"
    )


@pytest.mark.parametrize(
    "body,expected_name",
    [
        ("xiaomi redmi note covers", "Xiaomi Redmi"),
        ("redmi covers all models", "Xiaomi Redmi"),
        ("xiaomi phones wholesale", "Xiaomi Redmi"),
        ("samsung galaxy stock ready", "Samsung"),
        ("samsung phones MOQ 100", "Samsung"),
        ("huawei phones in stock", "Huawei"),
        ("oppo phones bulk available", "Oppo"),
    ],
)
def test_android_brand_extraction(body: str, expected_name: str) -> None:
    names = {p.canonical.canonical_name for p in extract_products(body)}
    assert expected_name in names


@pytest.mark.parametrize(
    "body,expected_name",
    [
        ("mini6 wifi 64gb", "iPad Mini 6"),
        ("air5 256gb sealed", "iPad Air 5"),
        ("mini5 in stock", "iPad Mini 5"),
        ("air4 wholesale", "iPad Air 4"),
    ],
)
def test_ipad_shorthand_extraction(body: str, expected_name: str) -> None:
    names = {p.canonical.canonical_name for p in extract_products(body)}
    assert expected_name in names


# ============================================================
# E. Regression guards — buyer + broker + non-wholesale paths
# ============================================================


def test_buyer_intent_still_classifies_correctly() -> None:
    """The new SELLER lexicon must not steal buyer messages."""
    result = classify_intent("need urgent 5 iPhone 15 Pro Max asap shj")
    assert result.intent is IntentType.BUYER


def test_buyer_with_wholesale_word_still_buyer() -> None:
    """`wholesale` is a SELLER signal, but if a buyer asks 'looking for
    wholesale supplier of X' the stronger BROKER+BUYER signals must
    still beat it. This guards against future lexicon drift."""
    result = classify_intent("need urgent 50 pcs iPhone cases wtb wholesale")
    # SELLER signals: pcs (1), wholesale (2) = 3
    # BUYER signals: need (2), wtb (2), urgent (1) = 5
    assert result.intent is IntentType.BUYER


def test_broker_intent_preserved() -> None:
    result = classify_intent(
        "looking buyer for 200 pcs xiaomi redmi covers commission ok"
    )
    assert result.intent is IntentType.BROKER


def test_unknown_message_remains_unknown() -> None:
    """A pure greeting must not fire SELLER signals."""
    result = classify_intent("salaam brothers good morning")
    assert result.intent is IntentType.UNKNOWN


def test_lexicon_does_not_double_count_overlapping_phrases() -> None:
    """``ready stock`` (strong w=2) must beat ``stock`` (weak w=1) +
    ``ready`` (weak w=1) at the same span (longest-match wins).
    Otherwise a single phrase would score 4 instead of 2 and skew
    the confidence ratio."""
    result = classify_intent("ready stock iPhone 15")
    # Should fire exactly one signal on the phrase, not two
    # overlapping ones. (Other signals from "iPhone" body don't count
    # toward intent.)
    intent_signals = [s for s in result.signals if s.kind == "seller"]
    keywords = {s.keyword for s in intent_signals}
    assert "ready stock" in keywords
    # And NOT split into the weaker components.
    assert "ready" not in keywords
    assert "stock" not in keywords


def test_inventory_persists_for_classic_seller_message() -> None:
    """Pre-fix seller messages must still produce Inventory."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    sm = sessionmaker(
        bind=eng, autoflush=False, autocommit=False, expire_on_commit=False
    )
    try:
        with sm() as s:
            msgs = list(
                parse_lines(
                    [
                        "28/05/26, 09:00 - Ali Mobiles: avail 12pm 256gb "
                        "fresh seal qty 15 LP 3200 AED dxb"
                    ],
                    source_file="t.txt",
                    group_name="g",
                )
            )
            stats = ingest_messages(
                msgs,
                session=s,
                source_type=SourceType.WHATSAPP,
                source_path="t.txt",
            )
            s.commit()
            assert stats.inventory_inserted == 1
    finally:
        eng.dispose()


def test_buyer_extraction_still_creates_demand() -> None:
    """The new lexicon must not poach buyer messages into seller-side
    tables. ``ingest_messages`` for a HIGH-urgency buyer should still
    write a Demand row, zero Inventory rows."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    sm = sessionmaker(
        bind=eng, autoflush=False, autocommit=False, expire_on_commit=False
    )
    try:
        with sm() as s:
            msgs = list(
                parse_lines(
                    [
                        "28/05/26, 09:00 - Bilal: need urgent 10 mini6 "
                        "64gb wifi today shj"
                    ],
                    source_file="t.txt",
                    group_name="g",
                )
            )
            stats = ingest_messages(
                msgs,
                session=s,
                source_type=SourceType.WHATSAPP,
                source_path="t.txt",
            )
            s.commit()
            assert stats.demand_inserted == 1
            assert stats.inventory_inserted == 0
            assert s.scalar(select(func.count(Buyer.id))) == 1
            assert s.scalar(select(func.count(Seller.id))) == 0
    finally:
        eng.dispose()
