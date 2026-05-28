"""Model-level integrity + create_all smoke."""

from __future__ import annotations

import pytest
from sqlalchemy import inspect

from trade_intel.config import get_settings
from trade_intel.db import Base, init_db
from trade_intel.db.models import (
    Buyer,
    Demand,
    Embedding,
    Grade,
    Group,
    IntentType,
    Inventory,
    LeadPipeline,
    Match,
    MatchType,
    Message,
    PipelineStage,
    Priority,
    Seller,
    SourceType,
)
from trade_intel.db.session import (
    get_engine,
    get_sessionmaker,
    session_scope,
)

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


def test_all_nine_tables_registered_on_metadata() -> None:
    assert EXPECTED_TABLES <= set(Base.metadata.tables.keys())


def test_init_db_creates_all_tables() -> None:
    init_db()
    inspector = inspect(get_engine())
    assert EXPECTED_TABLES <= set(inspector.get_table_names())


def test_phone_partial_unique_indexes_exist() -> None:
    init_db()
    inspector = inspect(get_engine())
    seller_idx = {i["name"] for i in inspector.get_indexes("sellers")}
    buyer_idx = {i["name"] for i in inspector.get_indexes("buyers")}
    assert "uq_sellers_phone" in seller_idx
    assert "uq_buyers_phone" in buyer_idx


def test_relationships_resolve_both_directions() -> None:
    init_db()
    with session_scope() as s:
        group = Group(name="Dubai Brokers", source_type=SourceType.WHATSAPP)
        seller = Seller(canonical_name="Ali Mobiles", phone="+971500000001")
        buyer = Buyer(canonical_name="Khalid Trading", phone="+971500000002")
        s.add_all([group, seller, buyer])
        s.flush()

        msg = Message(
            group_id=group.id,
            sender_name="Ali",
            raw_text="iPad mini 5 avail qty 50",
            hash="a" * 64,
            intent=IntentType.SELLER,
            seller_id=seller.id,
        )
        s.add(msg)
        s.flush()

        inv = Inventory(
            message_id=msg.id,
            seller_id=seller.id,
            normalized_product="iPad Mini 5",
            quantity=50,
        )
        dem = Demand(
            buyer_id=buyer.id,
            normalized_product="iPad Mini 5",
            quantity=30,
        )
        s.add_all([inv, dem])
        s.flush()

        match = Match(
            demand_id=dem.id,
            inventory_id=inv.id,
            match_type=MatchType.EXACT,
            final_score=0.92,
            grade=Grade.A_PLUS,
        )
        s.add(match)
        s.flush()

        lead = LeadPipeline(
            match_id=match.id,
            stage=PipelineStage.NEW,
            priority=Priority.P1,
        )
        s.add(lead)
        s.flush()

        # Forward navigation
        assert msg.group is not None and msg.group.id == group.id
        assert inv.seller is not None and inv.seller.id == seller.id
        assert dem.buyer is not None and dem.buyer.id == buyer.id
        assert match.demand is not None and match.demand.id == dem.id
        assert match.inventory is not None and match.inventory.id == inv.id
        assert lead.match is not None and lead.match.id == match.id

        # Reverse navigation
        assert msg in group.messages
        assert inv in seller.inventory_items
        assert dem in buyer.demands
        assert match in dem.matches
        assert match in inv.matches
        assert lead in match.pipeline_entries


def test_message_hash_unique_constraint() -> None:
    from sqlalchemy.exc import IntegrityError

    init_db()
    factory = get_sessionmaker()
    with factory() as s:
        s.add(Message(raw_text="x", hash="h" * 64))
        s.commit()
    with factory() as s, pytest.raises(IntegrityError):
        s.add(Message(raw_text="y", hash="h" * 64))
        s.commit()


def test_embedding_polymorphic_pointer_has_no_fk() -> None:
    init_db()
    inspector = inspect(get_engine())
    fks = inspector.get_foreign_keys("embeddings")
    assert fks == []
    with session_scope() as s:
        s.add(
            Embedding(
                entity_type="message",
                entity_id=12345,
                model_name="all-MiniLM-L6-v2",
                vector_dim=384,
                faiss_index_id=0,
            )
        )
