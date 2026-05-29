"""Integration tests: ingest → index → match → write Match + LeadPipeline (P4)."""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from trade_intel.db.base import Base
from trade_intel.db.models import (
    Condition,
    Demand,
    Grade,
    Inventory,
    LeadPipeline,
    Match,
    MatchType,
    PipelineStage,
    Priority,
    SourceType,
)
from trade_intel.matching import (
    build_demand_index,
    build_inventory_index,
    embedding_text_for_demand,
    embedding_text_for_inventory,
)
from trade_intel.ml import HashEmbedder
from trade_intel.parsers import parse_lines
from trade_intel.persistence import (
    ingest_messages,
    match_demand_against_inventory,
    match_inventory_against_demand,
)

# ---------- fixtures ----------


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


def _ingest(session: Session, lines: list[str]) -> None:
    msgs = list(parse_lines(lines, source_file="t.txt", group_name="g"))
    ingest_messages(
        msgs,
        session=session,
        source_type=SourceType.WHATSAPP,
        group_name="g",
        source_path="t.txt",
    )


# ---------- embedding text shape ----------


def test_embedding_text_for_inventory_includes_product_and_storage(
    session: Session,
) -> None:
    """P8.6: embedding text is product-centric — product + storage only.
    Condition / location are intentionally NOT in the text; they are
    handled by :func:`compute_spec_fit` instead.
    """
    inv = Inventory(
        normalized_product="iPhone 12 Pro Max",
        storage="256GB",
        condition=Condition.SEALED,
        location="dxb",
    )
    text = embedding_text_for_inventory(inv)
    assert text.startswith("iPhone 12 Pro Max")
    assert "storage=256GB" in text
    # Condition + location intentionally OMITTED — spec_fit handles them.
    assert "condition" not in text
    assert "location" not in text


def test_embedding_text_for_demand_includes_only_present_fields() -> None:
    dem = Demand(normalized_product="iPad Mini 5")
    text = embedding_text_for_demand(dem)
    assert text == "iPad Mini 5"  # no extras


# ---------- index build ----------


def test_build_inventory_index_from_db(session: Session) -> None:
    _ingest(
        session,
        [
            "28/05/26, 09:00 - Ali Mobiles: available 5 12pm 256gb fresh",
            "28/05/26, 09:01 - Sara: available 3 mini 5",
        ],
    )
    embedder = HashEmbedder(dim=32)
    index, id_map = build_inventory_index(session, embedder=embedder)
    assert index.size == 2
    # id_map is identity for NumpyIndex / FaissFlatIndex.
    assert set(id_map.values()) == set(id_map.keys())


def test_build_inventory_index_excludes_inactive(session: Session) -> None:
    _ingest(
        session,
        ["28/05/26, 09:00 - Ali Mobiles: available 5 12pm fresh"],
    )
    # Mark sole inventory row as inactive.
    inv = session.scalar(select(Inventory))
    assert inv is not None
    inv.is_active = False
    session.flush()
    index, _ = build_inventory_index(session, embedder=HashEmbedder(dim=16))
    assert index.size == 0


def test_build_demand_index_from_db(session: Session) -> None:
    _ingest(
        session,
        [
            "28/05/26, 09:00 - Sara: need urgent 5 12pm",
            "28/05/26, 09:01 - Bob: need 10 mini 5",
        ],
    )
    index, id_map = build_demand_index(session, embedder=HashEmbedder(dim=32))
    assert index.size == 2
    assert len(id_map) == 2


# ---------- match_demand_against_inventory ----------


def test_match_demand_finds_exact_same_product(session: Session) -> None:
    _ingest(
        session,
        [
            "28/05/26, 09:00 - Ali Mobiles: available 5 12pm 256gb fresh",
            "28/05/26, 09:01 - Sara: need urgent 5 12pm 256gb",
        ],
    )
    embedder = HashEmbedder(dim=64)
    inv_index, _ = build_inventory_index(session, embedder=embedder)
    demand = session.scalar(select(Demand))
    assert demand is not None

    stats = match_demand_against_inventory(
        demand,
        session=session,
        embedder=embedder,
        inventory_index=inv_index,
        k=5,
        min_grade=Grade.C,
        now=datetime(2026, 5, 28, 9, 5),
    )
    assert stats.candidates_seen >= 1
    assert stats.matches_created >= 1
    matches = session.scalars(select(Match).where(Match.demand_id == demand.id)).all()
    assert len(matches) >= 1
    top = max(matches, key=lambda m: (m.final_score or 0))
    assert top.match_type is MatchType.EXACT
    assert top.grade in (Grade.A, Grade.A_PLUS, Grade.B)


def test_match_demand_creates_lead_pipeline_with_priority(session: Session) -> None:
    _ingest(
        session,
        [
            "28/05/26, 09:00 - Ali Mobiles: available 5 12pm 256gb fresh",
            "28/05/26, 09:01 - Sara: need urgent 5 12pm 256gb",
        ],
    )
    embedder = HashEmbedder(dim=64)
    inv_index, _ = build_inventory_index(session, embedder=embedder)
    demand = session.scalar(select(Demand))
    match_demand_against_inventory(
        demand,
        session=session,
        embedder=embedder,
        inventory_index=inv_index,
        now=datetime(2026, 5, 28, 9, 5),
    )
    leads = session.scalars(select(LeadPipeline)).all()
    assert len(leads) >= 1
    for lead in leads:
        assert lead.stage is PipelineStage.NEW
        assert lead.priority in (Priority.P1, Priority.P2, Priority.P3, Priority.P4)
        assert lead.last_action == "MATCH_CREATED"
        assert lead.last_action_at is not None


def test_match_demand_min_grade_filter_drops_low_quality(session: Session) -> None:
    _ingest(
        session,
        [
            # Stale 30-day-old inventory will drop to grade C via recency.
            "28/04/26, 09:00 - Ali Mobiles: available 5 12pm 256gb fresh",
            "28/05/26, 09:01 - Sara: need urgent 5 12pm 256gb",
        ],
    )
    embedder = HashEmbedder(dim=64)
    inv_index, _ = build_inventory_index(session, embedder=embedder)
    demand = session.scalar(select(Demand))
    stats = match_demand_against_inventory(
        demand,
        session=session,
        embedder=embedder,
        inventory_index=inv_index,
        min_grade=Grade.B,
        now=datetime(2026, 5, 28, 12, 0),
    )
    # All candidates fail min_grade=B → no matches written.
    assert stats.matches_created == 0
    assert stats.matches_below_threshold == stats.candidates_seen
    assert session.scalar(select(Match)) is None


def test_match_demand_rescore_updates_existing_match_row(session: Session) -> None:
    """Calling matcher twice should UPDATE the existing Match, not duplicate."""
    _ingest(
        session,
        [
            "28/05/26, 09:00 - Ali Mobiles: available 5 12pm 256gb fresh",
            "28/05/26, 09:01 - Sara: need urgent 5 12pm 256gb",
        ],
    )
    embedder = HashEmbedder(dim=64)
    inv_index, _ = build_inventory_index(session, embedder=embedder)
    demand = session.scalar(select(Demand))
    when = datetime(2026, 5, 28, 9, 5)
    first = match_demand_against_inventory(
        demand,
        session=session,
        embedder=embedder,
        inventory_index=inv_index,
        now=when,
    )
    second = match_demand_against_inventory(
        demand,
        session=session,
        embedder=embedder,
        inventory_index=inv_index,
        now=when,
    )
    assert first.matches_created >= 1
    assert second.matches_created == 0
    assert second.matches_updated == first.matches_created
    # LeadPipeline row touched, not duplicated.
    leads = session.scalars(select(LeadPipeline)).all()
    assert len(leads) == first.matches_created
    for lead in leads:
        assert lead.last_action == "MATCH_RESCORED"


def test_match_demand_no_inventory_returns_empty_stats(session: Session) -> None:
    _ingest(session, ["28/05/26, 09:01 - Sara: need urgent 5 12pm"])
    embedder = HashEmbedder(dim=32)
    inv_index, _ = build_inventory_index(session, embedder=embedder)
    demand = session.scalar(select(Demand))
    stats = match_demand_against_inventory(
        demand,
        session=session,
        embedder=embedder,
        inventory_index=inv_index,
    )
    assert stats.candidates_seen == 0
    assert stats.matches_created == 0


# ---------- match_inventory_against_demand ----------


def test_match_inventory_writes_matches_and_leads(session: Session) -> None:
    _ingest(
        session,
        [
            "28/05/26, 09:00 - Sara: need urgent 5 12pm 256gb",
            "28/05/26, 09:01 - Ali Mobiles: available 5 12pm 256gb fresh",
        ],
    )
    embedder = HashEmbedder(dim=64)
    dem_index, _ = build_demand_index(session, embedder=embedder)
    inventory = session.scalar(select(Inventory))
    stats = match_inventory_against_demand(
        inventory,
        session=session,
        embedder=embedder,
        demand_index=dem_index,
        now=datetime(2026, 5, 28, 9, 5),
    )
    assert stats.matches_created >= 1
    assert session.scalar(select(Match)) is not None
    assert session.scalar(select(LeadPipeline)) is not None


# ---------- reasoning field carries the breakdown ----------


def test_match_reasoning_carries_score_breakdown(session: Session) -> None:
    import json as _json

    _ingest(
        session,
        [
            "28/05/26, 09:00 - Ali Mobiles: available 5 12pm 256gb fresh",
            "28/05/26, 09:01 - Sara: need urgent 5 12pm 256gb",
        ],
    )
    embedder = HashEmbedder(dim=64)
    inv_index, _ = build_inventory_index(session, embedder=embedder)
    demand = session.scalar(select(Demand))
    match_demand_against_inventory(
        demand,
        session=session,
        embedder=embedder,
        inventory_index=inv_index,
        now=datetime(2026, 5, 28, 9, 5),
    )
    match = session.scalar(select(Match))
    assert match is not None
    assert match.reasoning is not None
    payload = _json.loads(match.reasoning)
    for key in ("semantic", "spec_fit", "recency", "evidence", "final", "match_type", "grade"):
        assert key in payload


# ---------- priority assignment ----------


@pytest.mark.parametrize(
    "score,expected",
    [
        (0.90, Priority.P1),
        (0.75, Priority.P2),
        (0.55, Priority.P3),
        (0.30, Priority.P4),
    ],
)
def test_priority_mapping(score: float, expected: Priority) -> None:
    from trade_intel.persistence.matches import _priority_for

    assert _priority_for(score) is expected
