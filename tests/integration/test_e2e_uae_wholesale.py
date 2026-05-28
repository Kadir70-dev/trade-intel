"""End-to-end UAE wholesale integration tests (P8.2).

Exercises the full pipeline against realistic multi-language,
emoji-bearing, duplicate-rich UAE wholesale fixtures::

    WhatsApp .txt / CSV / Excel
        -> parse_file / parse_csv / parse_excel
        -> normalize (NFKC + identity + product + intent)
        -> dedupe (D-007 hash, shared across sources)
        -> persist (Group / Seller / Buyer / Message / Inventory / Demand)
        -> match_demand_against_inventory (HashEmbedder + NumpyIndex)
        -> upsert Match + LeadPipeline
        -> render via CLI + FastAPI surfaces

Tests are grouped by capability. See ``docs/COVERAGE_AUDIT_P8_1.md`` for
the gap analysis that drove this phase.
"""

from __future__ import annotations

import io
import json
from collections.abc import Generator
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from typer.testing import CliRunner

from trade_intel.api.main import create_app
from trade_intel.cli.main import app as cli_app
from trade_intel.db.base import Base
from trade_intel.db.models import (
    Buyer,
    Demand,
    Grade,
    IntentType,
    Inventory,
    LeadPipeline,
    Match,
    MatchType,
    Message,
    PipelineStage,
    Seller,
    SourceType,
    Urgency,
)
from trade_intel.matching import build_inventory_index
from trade_intel.ml import HashEmbedder
from trade_intel.parsers import parse_csv, parse_excel, parse_file
from trade_intel.persistence import (
    ingest_messages,
    match_demand_against_inventory,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
E2E = FIXTURES / "e2e"
WHATSAPP_FIXTURE = E2E / "uae_wholesale_day.txt"
CSV_FIXTURE = E2E / "dubai_stock_sheet.csv"


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def session() -> Generator[Session, None, None]:
    """Fresh in-memory SQLite per test (single connection via StaticPool)."""
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


@pytest.fixture
def excel_workbook(tmp_path: Path) -> Path:
    """Generate an Excel inventory sheet (Sharjah / Ajman side of the market)."""
    path = tmp_path / "sharjah_stock.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Inventory"
    ws.append(
        [
            "Seller",
            "Phone",
            "Product",
            "Qty",
            "Storage",
            "Condition",
            "Price",
            "Location",
            "Date",
        ]
    )
    ws.append(
        [
            "Sharjah Hub",
            "+971502221111",
            "iPad Mini 6",
            5,
            "256GB",
            "Sealed",
            2100,
            "Sharjah",
            "2026-05-28",
        ]
    )
    ws.append(
        [
            "Ajman Mobiles",
            "+971503334444",
            "iPhone 14 Pro",
            8,
            "256GB",
            "Fresh",
            3600,
            "Ajman",
            "2026-05-28",
        ]
    )
    ws.append(
        [
            "Sharjah Hub",
            "+971502221111",
            "iPad Air 5",
            12,
            "64GB",
            "New",
            2400,
            "Sharjah",
            "2026-05-28",
        ]
    )
    ws.append([None, None, None, None, None, None, None, None, None])
    wb.save(path)
    wb.close()
    return path


def _ingest_whatsapp(session: Session) -> None:
    ingest_messages(
        parse_file(WHATSAPP_FIXTURE),
        session=session,
        source_type=SourceType.WHATSAPP,
        source_path=str(WHATSAPP_FIXTURE),
    )


def _ingest_csv(session: Session) -> None:
    ingest_messages(
        parse_csv(CSV_FIXTURE),
        session=session,
        source_type=SourceType.CSV,
        source_path=str(CSV_FIXTURE),
    )


def _ingest_all(session: Session) -> None:
    """Both WhatsApp + CSV — the canonical seed for downstream tests."""
    _ingest_whatsapp(session)
    _ingest_csv(session)


def _run_matcher(session: Session, *, now: datetime | None = None) -> int:
    """Score every active Demand. Returns the number of demands scored."""
    when = now or datetime(2026, 5, 28, 9, 0)
    embedder = HashEmbedder(dim=64)
    idx, _ = build_inventory_index(session, embedder=embedder)
    scored = 0
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
            now=when,
        )
        scored += 1
    return scored


# --- CLI / API fixtures (file-backed SQLite + cleared Settings caches) ---


@pytest.fixture
def cli_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[Path, None, None]:
    """File-backed SQLite for CLI/API tests; init_db creates the schema."""
    db = tmp_path / "e2e.db"
    monkeypatch.setenv("SQLITE_URL", f"sqlite:///{db}")

    from trade_intel.config import get_settings
    from trade_intel.db.session import get_engine, get_sessionmaker

    for cache in (get_settings, get_engine, get_sessionmaker):
        cache.cache_clear()

    from trade_intel.db import init_db

    init_db()
    yield db

    for cache in (get_settings, get_engine, get_sessionmaker):
        cache.cache_clear()


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def api_client(cli_db: Path) -> TestClient:
    return TestClient(create_app())


def _seed_cli_db() -> None:
    """Ingest the two file fixtures against the active `cli_db` SQLite."""
    from trade_intel.db.session import session_scope

    with session_scope() as s:
        _ingest_all(s)


def _seed_and_match_cli_db() -> None:
    """Full pipeline seed for CLI/API tests."""
    from trade_intel.db.session import session_scope

    with session_scope() as s:
        _ingest_all(s)
        _run_matcher(s)


# ============================================================
# A. End-to-end ingest success
# ============================================================


def test_whatsapp_ingest_counters_match_fixture_shape(session: Session) -> None:
    """The WhatsApp fixture has 18 lines; 2 system + 1 media + 1 dup are
    skipped, 14 OK messages persist."""
    stats = ingest_messages(
        parse_file(WHATSAPP_FIXTURE),
        session=session,
        source_type=SourceType.WHATSAPP,
        source_path=str(WHATSAPP_FIXTURE),
    )
    assert stats.messages_seen == 18
    assert stats.messages_inserted == 14
    assert stats.messages_skipped_dup == 1
    assert stats.messages_skipped_system == 2
    assert stats.messages_skipped_media == 1
    assert stats.errors == 0
    # Invariant from the persistence contract.
    assert (
        stats.messages_inserted
        + stats.messages_skipped_dup
        + stats.messages_skipped_system
        + stats.messages_skipped_media
        + stats.messages_skipped_malformed
        + stats.messages_skipped_orphan
        + stats.errors
        == stats.messages_seen
    )


def test_csv_ingest_counters_match_fixture_shape(session: Session) -> None:
    stats = ingest_messages(
        parse_csv(CSV_FIXTURE),
        session=session,
        source_type=SourceType.CSV,
        source_path=str(CSV_FIXTURE),
    )
    # 7 data rows; empty trailing row has product cell so all 7 are seen.
    assert stats.messages_seen == 7
    assert stats.messages_inserted == 7
    assert stats.inventory_inserted == 7
    assert stats.demand_inserted == 0


def test_excel_ingest_yields_inventory_rows(
    session: Session, excel_workbook: Path
) -> None:
    stats = ingest_messages(
        parse_excel(excel_workbook),
        session=session,
        source_type=SourceType.EXCEL,
        source_path=str(excel_workbook),
    )
    assert stats.messages_seen == 3  # empty trailing row is silently skipped
    assert stats.messages_inserted == 3
    assert stats.inventory_inserted == 3


def test_whatsapp_intent_distribution(session: Session) -> None:
    """WhatsApp fixture mixes 8 SELLER, 4 BUYER, 2 BROKER OK messages."""
    _ingest_whatsapp(session)
    by_intent = {
        it: session.scalar(
            select(func.count(Message.id)).where(Message.intent == it)
        )
        for it in IntentType
    }
    assert by_intent[IntentType.SELLER] == 8
    assert by_intent[IntentType.BUYER] == 4
    assert by_intent[IntentType.BROKER] == 2
    # Every persisted message must classify (no UNKNOWN in this fixture).
    assert by_intent[IntentType.UNKNOWN] == 0


def test_whatsapp_high_urgency_demand_persisted(session: Session) -> None:
    """Every demand row in this fixture carries HIGH urgency
    ('need urgent', 'asap', 'today', 'wtb ... urgent')."""
    _ingest_whatsapp(session)
    demands = session.scalars(select(Demand)).all()
    assert len(demands) == 4
    assert all(d.urgency is Urgency.HIGH for d in demands)


def test_whatsapp_product_extraction_covers_iphones_and_ipads(
    session: Session,
) -> None:
    """Inventory + demand rows together cover every product the fixture
    explicitly names — and only those."""
    _ingest_whatsapp(session)
    inv_products = {
        i.normalized_product
        for i in session.scalars(select(Inventory)).all()
    }
    dem_products = {
        d.normalized_product for d in session.scalars(select(Demand)).all()
    }
    assert "iPhone 12 Pro Max" in inv_products
    assert "iPhone 15 Pro Max" in inv_products
    assert "iPad Air 5" in inv_products
    assert "iPhone 12 Pro Max" in dem_products
    assert "iPhone 15 Pro Max" in dem_products
    assert "iPad Mini 6" in dem_products
    assert "iPhone 14 Pro" in dem_products


def test_whatsapp_arabic_seller_classified_and_inserted(session: Session) -> None:
    """Multilingual seller — Arabic display name, ASCII signal ('fresh', 'stock').

    The rule-based classifier is ASCII-only, but the body still carries
    English signals; expect a SELLER message + Inventory row with the
    Arabic canonical_name preserved.
    """
    _ingest_whatsapp(session)
    arabic_seller = session.scalar(
        select(Seller).where(Seller.canonical_name == "محمد عبدالله")
    )
    assert arabic_seller is not None
    assert arabic_seller.post_count == 1
    # ...and the matching message tagged SELLER, with at least one
    # inventory row linked.
    inv = session.scalar(
        select(Inventory).where(Inventory.seller_id == arabic_seller.id)
    )
    assert inv is not None
    assert inv.normalized_product == "iPhone 15 Pro Max"


def test_whatsapp_emoji_handle_yields_clean_identity(session: Session) -> None:
    """Sara DXB 🚀 — emoji preserved on raw alias; canonical_name strips emoji."""
    _ingest_whatsapp(session)
    sara = session.scalar(
        select(Seller).where(Seller.canonical_name == "sara dxb")
    )
    assert sara is not None
    assert sara.aliases is not None
    assert "sara dxb 🚀" in sara.aliases  # raw form retained
    assert "sara" in sara.aliases  # location-stripped root alias


def test_whatsapp_continuation_lines_fold_into_prior_message(
    session: Session,
) -> None:
    """The fixture has '... also need 5 15pm 512gb same day urgent' followed
    by two continuation lines — they must collapse into one Message body."""
    _ingest_whatsapp(session)
    repeat_buyer = session.scalar(
        select(Buyer).where(Buyer.phone == "+971505551234")
    )
    assert repeat_buyer is not None
    assert repeat_buyer.request_count == 2  # two distinct buyer posts
    # Locate the long urgent message and verify the continuation body folded.
    msg = session.scalar(
        select(Message).where(Message.buyer_id == repeat_buyer.id).order_by(
            Message.message_ts.desc()
        )
    )
    assert msg is not None
    assert "continuation" in msg.raw_text  # second physical line attached


# ============================================================
# B. Duplicate-safe re-ingest (idempotency)
# ============================================================


def test_whatsapp_same_minute_repost_collapses_via_hash(
    session: Session,
) -> None:
    """Ali Mobiles posts the same body twice at the same minute — D-007 hash
    collapses one. (Confirmed via stats.messages_skipped_dup == 1.)"""
    _ingest_whatsapp(session)
    # Exactly one Inventory row for Ali Mobiles DXB iPhone 12 PM,
    # not two — the second-line repost would have added a duplicate.
    ali = session.scalar(
        select(Seller).where(Seller.canonical_name == "ali mobiles dxb")
    )
    assert ali is not None
    assert ali.post_count == 1  # one post survived dedup


def test_whatsapp_full_file_reingest_is_full_dup(session: Session) -> None:
    first = ingest_messages(
        parse_file(WHATSAPP_FIXTURE),
        session=session,
        source_type=SourceType.WHATSAPP,
        source_path=str(WHATSAPP_FIXTURE),
    )
    second = ingest_messages(
        parse_file(WHATSAPP_FIXTURE),
        session=session,
        source_type=SourceType.WHATSAPP,
        source_path=str(WHATSAPP_FIXTURE),
    )
    # Second pass: zero new rows, every OK message dedup'd.
    assert second.messages_inserted == 0
    assert second.inventory_inserted == 0
    assert second.demand_inserted == 0
    # Same OK count → same dup count second time around.
    assert (
        second.messages_skipped_dup
        == first.messages_inserted + first.messages_skipped_dup
    )


def test_csv_reingest_zero_new_rows(session: Session) -> None:
    _ingest_csv(session)
    inv_before = session.scalar(select(func.count(Inventory.id)))
    sellers_before = session.scalar(select(func.count(Seller.id)))
    second = ingest_messages(
        parse_csv(CSV_FIXTURE),
        session=session,
        source_type=SourceType.CSV,
        source_path=str(CSV_FIXTURE),
    )
    assert second.messages_inserted == 0
    assert second.inventory_inserted == 0
    assert session.scalar(select(func.count(Inventory.id))) == inv_before
    assert session.scalar(select(func.count(Seller.id))) == sellers_before


def test_excel_reingest_zero_new_rows(
    session: Session, excel_workbook: Path
) -> None:
    ingest_messages(
        parse_excel(excel_workbook),
        session=session,
        source_type=SourceType.EXCEL,
        source_path=str(excel_workbook),
    )
    second = ingest_messages(
        parse_excel(excel_workbook),
        session=session,
        source_type=SourceType.EXCEL,
        source_path=str(excel_workbook),
    )
    assert second.messages_inserted == 0
    assert second.inventory_inserted == 0


def test_full_pipeline_reingest_preserves_match_count(session: Session) -> None:
    """End-to-end idempotency: ingest → match → re-ingest → re-match yields
    the same Match + LeadPipeline counts (rescore in place, no duplicates)."""
    _ingest_all(session)
    _run_matcher(session)
    matches_before = session.scalar(select(func.count(Match.id)))
    leads_before = session.scalar(select(func.count(LeadPipeline.id)))
    assert matches_before is not None and matches_before > 0

    _ingest_all(session)
    _run_matcher(session)

    assert session.scalar(select(func.count(Match.id))) == matches_before
    assert session.scalar(select(func.count(LeadPipeline.id))) == leads_before
    # Every lead now stamped MATCH_RESCORED.
    for lp in session.scalars(select(LeadPipeline)).all():
        assert lp.last_action == "MATCH_RESCORED"


# ============================================================
# C. Cross-source identity merge
# ============================================================


def test_phone_match_across_whatsapp_and_csv_collapses_seller(
    session: Session,
) -> None:
    """WhatsApp has a phone-only seller +971501234567; CSV has two Ali
    Mobiles rows with the same phone. After both ingests, a single
    Seller row owns all three posts."""
    _ingest_all(session)
    sellers = session.scalars(
        select(Seller).where(Seller.phone == "+971501234567")
    ).all()
    assert len(sellers) == 1
    s = sellers[0]
    # 1 WhatsApp post + 2 CSV rows merged on phone.
    assert s.post_count == 3
    # Aliases unioned across sources.
    assert "ali mobiles dxb" in (s.aliases or [])
    assert "ali mobiles auh" in (s.aliases or [])


def test_in_csv_phone_collision_collapses_two_handles(session: Session) -> None:
    """Standalone CSV ingest: Ali Mobiles DXB + Ali Mobiles AUH share a
    phone → one Seller, post_count=2."""
    _ingest_csv(session)
    s = session.scalar(select(Seller).where(Seller.phone == "+971501234567"))
    assert s is not None
    assert s.post_count == 2


def test_canonical_name_fallback_merges_arabic_seller_across_sources(
    session: Session,
) -> None:
    """Arabic seller has NULL phone in WhatsApp but a phone in CSV; persistence
    falls through to canonical_name + NULL phone lookup → merges into the
    existing WhatsApp Seller. (Phone is NOT backfilled — see D-009.)"""
    _ingest_all(session)
    arabic = session.scalar(
        select(Seller).where(Seller.canonical_name == "محمد عبدالله")
    )
    assert arabic is not None
    assert arabic.post_count == 2  # WhatsApp + CSV


def test_repeat_buyer_phone_across_messages_increments_request_count(
    session: Session,
) -> None:
    """+971 50 555 1234 makes two distinct buyer posts in the WhatsApp
    fixture — both must merge into a single Buyer row."""
    _ingest_whatsapp(session)
    repeat = session.scalar(select(Buyer).where(Buyer.phone == "+971505551234"))
    assert repeat is not None
    assert repeat.request_count == 2


# ============================================================
# D. Match quality + lead creation
# ============================================================


def test_matcher_creates_match_for_every_active_demand(session: Session) -> None:
    _ingest_all(session)
    demand_count = session.scalar(
        select(func.count(Demand.id)).where(Demand.is_active.is_(True))
    )
    scored = _run_matcher(session)
    assert scored == demand_count
    # Every demand got at least one candidate against this corpus.
    matches_per_demand = session.execute(
        select(Match.demand_id, func.count(Match.id)).group_by(Match.demand_id)
    ).all()
    assert {row[0] for row in matches_per_demand} == {
        d.id
        for d in session.scalars(
            select(Demand).where(Demand.is_active.is_(True))
        ).all()
    }


def test_matcher_top_match_is_exact_when_inventory_exists(
    session: Session,
) -> None:
    """For every demand whose product also exists in active Inventory, the
    top-scored Match must be that same product (EXACT match_type).

    Demands without a same-product inventory row (e.g. iPhone 14 Pro in
    this fixture) are not asserted here — match_type fairness for those
    is covered separately by ``test_excel_fixture_participates_in_matching``.
    """
    _ingest_all(session)
    _run_matcher(session)
    inv_products = {
        i.normalized_product
        for i in session.scalars(
            select(Inventory).where(Inventory.is_active.is_(True))
        ).all()
    }
    asserted = 0
    for dem in session.scalars(
        select(Demand).where(Demand.is_active.is_(True))
    ).all():
        if dem.normalized_product not in inv_products:
            continue
        top = session.scalar(
            select(Match)
            .where(Match.demand_id == dem.id)
            .order_by(Match.final_score.desc())
        )
        assert top is not None
        inv = session.get(Inventory, top.inventory_id)
        assert inv is not None
        assert inv.normalized_product == dem.normalized_product
        assert top.match_type is MatchType.EXACT
        asserted += 1
    assert asserted >= 3  # 12 PM, 15 PM, Mini 6 all line up


def test_matcher_persists_full_score_breakdown(session: Session) -> None:
    """Match.reasoning JSON must include all 7 documented keys."""
    _ingest_all(session)
    _run_matcher(session)
    m = session.scalar(select(Match).order_by(Match.final_score.desc()))
    assert m is not None
    assert m.reasoning is not None
    payload = json.loads(m.reasoning)
    assert set(payload.keys()) == {
        "semantic",
        "spec_fit",
        "recency",
        "evidence",
        "final",
        "match_type",
        "grade",
    }
    # final = semantic * spec_fit * recency * evidence (within float epsilon).
    expected = (
        payload["semantic"]
        * payload["spec_fit"]
        * payload["recency"]
        * payload["evidence"]
    )
    assert abs(expected - payload["final"]) < 1e-3


def test_matcher_rescore_in_place_no_duplicate_rows(session: Session) -> None:
    """Calling the matcher twice must update existing Match rows, not
    duplicate them. LeadPipeline last_action flips to MATCH_RESCORED."""
    _ingest_all(session)
    _run_matcher(session)
    n_before = session.scalar(select(func.count(Match.id)))
    _run_matcher(session)
    n_after = session.scalar(select(func.count(Match.id)))
    assert n_after == n_before
    for lp in session.scalars(select(LeadPipeline)).all():
        assert lp.last_action == "MATCH_RESCORED"


def test_matcher_min_grade_filter_drops_stale_candidates(
    session: Session,
) -> None:
    """Scoring 60 days after the inventory was posted pushes every match
    below grade B (recency multiplier 0.05) → min_grade=B writes nothing."""
    _ingest_all(session)
    embedder = HashEmbedder(dim=64)
    idx, _ = build_inventory_index(session, embedder=embedder)
    stale_when = datetime(2026, 8, 1, 9, 0)  # ~65 days after posted_at
    for dem in session.scalars(
        select(Demand).where(Demand.is_active.is_(True))
    ).all():
        stats = match_demand_against_inventory(
            dem,
            session=session,
            embedder=embedder,
            inventory_index=idx,
            k=10,
            min_grade=Grade.B,
            now=stale_when,
        )
        assert stats.matches_created == 0
        if stats.candidates_seen:
            assert stats.matches_below_threshold == stats.candidates_seen
    assert session.scalar(select(func.count(Match.id))) == 0


def test_lead_pipeline_new_stage_and_priority_match_score(
    session: Session,
) -> None:
    """Every created Match spawns a NEW LeadPipeline row whose priority is
    derived from the same final_score (P1>=.85, P2>=.70, P3>=.50, else P4)."""
    _ingest_all(session)
    _run_matcher(session)
    leads = session.scalars(select(LeadPipeline)).all()
    assert len(leads) > 0
    for lp in leads:
        assert lp.stage is PipelineStage.NEW
        assert lp.last_action == "MATCH_CREATED"
        m = session.get(Match, lp.match_id)
        assert m is not None
        score = m.final_score or 0.0
        # Priority/score consistency.
        if score >= 0.85:
            assert lp.priority.value == "P1"
        elif score >= 0.70:
            assert lp.priority.value == "P2"
        elif score >= 0.50:
            assert lp.priority.value == "P3"
        else:
            assert lp.priority.value == "P4"


def test_match_freshness_multiplier_reflects_age(session: Session) -> None:
    """Rescoring the same Match later in time strictly reduces (or equals)
    the recency multiplier. Boundary: 5h is HOT (1.00), 25h is WARM (0.70)."""
    _ingest_all(session)
    embedder = HashEmbedder(dim=64)
    idx, _ = build_inventory_index(session, embedder=embedder)
    dem = session.scalar(
        select(Demand).where(Demand.normalized_product == "iPhone 12 Pro Max")
    )
    assert dem is not None
    # Posted at 28/05 08:30; score at 28/05 13:30 (5h after) → HOT 1.00.
    hot_when = datetime(2026, 5, 28, 13, 30)
    match_demand_against_inventory(
        dem,
        session=session,
        embedder=embedder,
        inventory_index=idx,
        now=hot_when,
    )
    m_row = session.scalar(
        select(Match)
        .where(Match.demand_id == dem.id)
        .order_by(Match.final_score.desc())
    )
    assert m_row is not None
    # Capture HOT-tier values BEFORE the in-place rescore mutates m_row.
    hot_recency = m_row.recency_multiplier
    hot_final = m_row.final_score
    assert abs((hot_recency or 0) - 1.00) < 1e-6

    # Rescore 25h after posting → WARM 0.70 (mutates m_row in place).
    warm_when = datetime(2026, 5, 29, 9, 35)
    match_demand_against_inventory(
        dem,
        session=session,
        embedder=embedder,
        inventory_index=idx,
        now=warm_when,
    )
    assert abs((m_row.recency_multiplier or 0) - 0.70) < 1e-6
    assert (m_row.final_score or 0) < (hot_final or 0)


# ============================================================
# E. CLI output correctness
# ============================================================


def test_cli_parse_whatsapp_fixture_inserts_expected_counters(
    cli_db: Path, runner: CliRunner
) -> None:
    result = runner.invoke(
        cli_app, ["parse", str(WHATSAPP_FIXTURE), "--json"]
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["messages_seen"] == 18
    assert payload["messages_inserted"] == 14
    assert payload["inventory_inserted"] == 8
    assert payload["demand_inserted"] == 4


def test_cli_parse_csv_fixture_inserts_expected_counters(
    cli_db: Path, runner: CliRunner
) -> None:
    result = runner.invoke(cli_app, ["parse", str(CSV_FIXTURE), "--json"])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["messages_inserted"] == 7
    assert payload["inventory_inserted"] == 7


def test_cli_brief_after_full_pipeline_reports_real_counts(
    cli_db: Path, runner: CliRunner
) -> None:
    _seed_and_match_cli_db()
    result = runner.invoke(cli_app, ["brief", "--json"])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    summary = payload["summary"]
    assert summary["active_inventory"] == 15
    assert summary["active_demand"] == 4
    assert summary["matches"] > 0
    assert summary["new_pipeline"] > 0
    # Brief surfaces lists are present and bounded at 5.
    assert len(payload["top_matches"]) <= 5
    assert len(payload["urgent_demand"]) <= 5
    assert len(payload["fresh_inventory"]) <= 5


def test_cli_brief_top_matches_include_grade_and_score(
    cli_db: Path, runner: CliRunner
) -> None:
    _seed_and_match_cli_db()
    result = runner.invoke(cli_app, ["brief", "--json"])
    payload = json.loads(result.stdout)
    assert payload["top_matches"], "expected at least one scored match"
    for row in payload["top_matches"]:
        # matches_to_rows shape: [id, product, grade, type, final, sem, spec, rec]
        assert len(row) == 8
        # Grade must be one of A+ / A / B / C (column 2).
        assert row[2] in {"A+", "A", "B", "C"}


def test_cli_who_has_finds_seller_inventory(
    cli_db: Path, runner: CliRunner
) -> None:
    _seed_cli_db()
    result = runner.invoke(
        cli_app, ["who-has", "iPhone 12 Pro Max", "--json"]
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    # At least three inventory rows for iPhone 12 PM (WhatsApp Ali +
    # phone-only seller + CSV Ali + WhatsApp Rashid).
    assert isinstance(payload, list)
    assert len(payload) >= 3


def test_cli_who_needs_finds_high_urgency_buyer_demand(
    cli_db: Path, runner: CliRunner
) -> None:
    _seed_cli_db()
    result = runner.invoke(
        cli_app, ["who-needs", "iPhone 12 Pro Max", "--json"]
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload
    # Every demand for 12pm came in as HIGH urgency.
    assert all(row["urgency"] == "high" for row in payload)


def test_cli_hot_with_buyer_intent_filter(
    cli_db: Path, runner: CliRunner
) -> None:
    _seed_cli_db()
    result = runner.invoke(
        cli_app, ["hot", "--intent", "buyer", "--json"]
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload, "expected buyer messages in the fixture"
    assert all(row["intent"] == "buyer" for row in payload)


def test_cli_opportunities_after_run_matcher_shows_top_matches(
    cli_db: Path, runner: CliRunner
) -> None:
    _seed_and_match_cli_db()
    result = runner.invoke(cli_app, ["opportunities", "--json", "-n", "3"])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert 1 <= len(payload) <= 3
    # Sorted descending by final score.
    finals = [row["final"] for row in payload]
    assert finals == sorted(finals, reverse=True)


def test_cli_ping_renders_seller_template_for_real_match(
    cli_db: Path, runner: CliRunner
) -> None:
    _seed_and_match_cli_db()
    # Find a match where both sides have a linked seller + buyer.
    from trade_intel.db.session import session_scope

    with session_scope() as s:
        m = s.scalar(
            select(Match)
            .join(Inventory, Inventory.id == Match.inventory_id)
            .join(Demand, Demand.id == Match.demand_id)
            .where(Inventory.seller_id.is_not(None))
            .where(Demand.buyer_id.is_not(None))
            .order_by(Match.final_score.desc())
        )
        assert m is not None
        match_id = m.id
    result = runner.invoke(cli_app, ["ping", str(match_id), "--side", "seller"])
    assert result.exit_code == 0, result.stdout
    assert "LP" in result.stdout  # template always asks for LP


# ============================================================
# F. API endpoint correctness
# ============================================================


def test_api_ingest_whatsapp_returns_201_and_counters(
    api_client: TestClient,
) -> None:
    with WHATSAPP_FIXTURE.open("rb") as fh:
        resp = api_client.post(
            "/ingest",
            files={
                "file": (
                    WHATSAPP_FIXTURE.name,
                    fh,
                    "text/plain",
                )
            },
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["stats"]["messages_inserted"] == 14
    assert body["stats"]["inventory_inserted"] == 8
    assert body["stats"]["demand_inserted"] == 4
    assert body["matcher_scheduled"] is False


def test_api_ingest_csv_returns_inventory_counters(
    api_client: TestClient,
) -> None:
    with CSV_FIXTURE.open("rb") as fh:
        resp = api_client.post(
            "/ingest",
            files={"file": (CSV_FIXTURE.name, fh, "text/csv")},
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["stats"]["messages_inserted"] == 7
    assert body["stats"]["inventory_inserted"] == 7


def test_api_match_returns_hits_after_seed(api_client: TestClient) -> None:
    _seed_cli_db()
    resp = api_client.post(
        "/match", json={"query": "12pm 256gb dxb", "side": "seller", "k": 5}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["side"] == "seller"
    assert body["hits"]
    # Every hit dict is well-formed.
    for hit in body["hits"]:
        assert {"id", "score", "product"} <= hit.keys()


def test_api_leads_lists_new_stage_after_pipeline(api_client: TestClient) -> None:
    _seed_and_match_cli_db()
    resp = api_client.get("/leads")
    assert resp.status_code == 200, resp.text
    leads = resp.json()
    assert leads
    assert all(lead["stage"] == "NEW" for lead in leads)
    # Default sort: close_probability descending.
    probs = [lead["close_probability"] for lead in leads]
    assert probs == sorted(probs, reverse=True)


def test_api_brief_summarizes_full_pipeline(api_client: TestClient) -> None:
    _seed_and_match_cli_db()
    resp = api_client.get("/brief")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    summary = body["summary"]
    assert summary["active_inventory"] == 15
    assert summary["active_demand"] == 4
    assert summary["matches"] > 0
    assert summary["new_pipeline"] == summary["matches"]


def test_api_ingest_with_run_matcher_writes_matches(
    api_client: TestClient,
) -> None:
    """`?run_matcher=true` schedules a BackgroundTask; TestClient runs
    background tasks synchronously after response, so by the time we
    seed the demand side and re-trigger ingest with the flag, Match rows
    must materialize."""
    # First seed inventory only — no demand yet, so matcher writes nothing.
    with CSV_FIXTURE.open("rb") as fh:
        r1 = api_client.post(
            "/ingest?run_matcher=true",
            files={"file": (CSV_FIXTURE.name, fh, "text/csv")},
        )
    assert r1.status_code == 201, r1.text
    assert r1.json()["matcher_scheduled"] is True

    # Now upload WhatsApp (buyers) + run matcher → expect leads.
    with WHATSAPP_FIXTURE.open("rb") as fh:
        r2 = api_client.post(
            "/ingest?run_matcher=true",
            files={"file": (WHATSAPP_FIXTURE.name, fh, "text/plain")},
        )
    assert r2.status_code == 201, r2.text

    leads = api_client.get("/leads").json()
    assert leads, "BackgroundTasks should have written LeadPipeline rows"


def test_api_messages_intent_filter_returns_only_buyers(
    api_client: TestClient,
) -> None:
    _seed_cli_db()
    resp = api_client.get("/messages", params={"intent": "buyer"})
    assert resp.status_code == 200, resp.text
    msgs = resp.json()
    assert msgs
    assert all(m["intent"] == "buyer" for m in msgs)


def test_api_sellers_lists_in_post_count_order(api_client: TestClient) -> None:
    _seed_cli_db()
    resp = api_client.get("/sellers")
    assert resp.status_code == 200, resp.text
    sellers = resp.json()
    counts = [s["post_count"] for s in sellers]
    assert counts == sorted(counts, reverse=True)


# ============================================================
# G. Failure recovery
# ============================================================


def test_orphan_lines_do_not_abort_batch(session: Session) -> None:
    """The fixture contains a bare 'random orphan line with no header' that
    the parser tags ORPHAN_CONTINUATION. Ingest must skip it cleanly and
    persist every other OK message — no errors."""
    stats = ingest_messages(
        parse_file(WHATSAPP_FIXTURE),
        session=session,
        source_type=SourceType.WHATSAPP,
        source_path=str(WHATSAPP_FIXTURE),
    )
    assert stats.errors == 0
    assert stats.messages_inserted > 0


def test_per_message_savepoint_isolates_single_failure(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inject a transient failure on the *third* product-extraction call.
    The bad message must be counted as an error; the rest of the batch
    must still commit. Total inserted + errors equals OK seen."""
    from trade_intel.persistence import ingest as ingest_mod

    call_count = {"n": 0}
    real_extract = ingest_mod.extract_products

    def flaky_extract(text: str | None):  # type: ignore[override]
        call_count["n"] += 1
        if call_count["n"] == 3:
            raise RuntimeError("synthetic extractor failure")
        return real_extract(text)

    monkeypatch.setattr(ingest_mod, "extract_products", flaky_extract)
    stats = ingest_messages(
        parse_file(WHATSAPP_FIXTURE),
        session=session,
        source_type=SourceType.WHATSAPP,
        source_path=str(WHATSAPP_FIXTURE),
    )
    assert stats.errors == 1
    # Everything else still flowed.
    assert stats.messages_inserted >= 1
    assert session.scalar(select(func.count(Message.id))) == stats.messages_inserted


def test_cli_parse_unsupported_extension_rejected(
    cli_db: Path, runner: CliRunner, tmp_path: Path
) -> None:
    junk = tmp_path / "stock.dat"
    junk.write_text("anything", encoding="utf-8")
    result = runner.invoke(cli_app, ["parse", str(junk)])
    assert result.exit_code != 0
    assert "cannot infer format" in (result.stdout + result.stderr).lower()


def test_api_ingest_unknown_extension_returns_400(
    api_client: TestClient,
) -> None:
    resp = api_client.post(
        "/ingest",
        files={"file": ("stock.dat", io.BytesIO(b"junk"), "application/octet-stream")},
    )
    assert resp.status_code == 400
    assert "extension" in resp.json()["detail"].lower()


def test_api_ingest_invalid_source_type_form_returns_400(
    api_client: TestClient,
) -> None:
    with WHATSAPP_FIXTURE.open("rb") as fh:
        resp = api_client.post(
            "/ingest",
            files={"file": (WHATSAPP_FIXTURE.name, fh, "text/plain")},
            data={"source_type": "telegram"},
        )
    assert resp.status_code == 400
    assert "source_type" in resp.json()["detail"].lower()


# ============================================================
# H. Matcher confidence validation
# ============================================================


def test_match_grade_aligns_with_documented_thresholds(
    session: Session,
) -> None:
    """For every Match: grade must equal what the threshold table would
    return for that final_score (A+ ≥ 0.85, A ≥ 0.70, B ≥ 0.50)."""
    _ingest_all(session)
    _run_matcher(session)
    for m in session.scalars(select(Match)).all():
        score = m.final_score or 0.0
        if score >= 0.85:
            assert m.grade is Grade.A_PLUS
        elif score >= 0.70:
            assert m.grade is Grade.A
        elif score >= 0.50:
            assert m.grade is Grade.B
        else:
            assert m.grade is Grade.C


def test_match_type_exact_only_for_same_product_and_storage(
    session: Session,
) -> None:
    """`compute_match_type` returns EXACT only when product matches AND
    spec_fit ≥ 0.99. Anything below SIMILAR → mismatched specs;
    different product → PROBABLE."""
    _ingest_all(session)
    _run_matcher(session)
    for m in session.scalars(select(Match)).all():
        dem = session.get(Demand, m.demand_id)
        inv = session.get(Inventory, m.inventory_id)
        assert dem is not None and inv is not None
        if m.match_type is MatchType.EXACT:
            assert inv.normalized_product == dem.normalized_product
            assert (m.spec_fit_score or 0) >= 0.99
        elif m.match_type is MatchType.PROBABLE:
            assert inv.normalized_product != dem.normalized_product


def test_match_final_score_equals_factor_product(session: Session) -> None:
    """final_score == semantic * spec_fit * recency * evidence."""
    _ingest_all(session)
    _run_matcher(session)
    for m in session.scalars(select(Match)).all():
        expected = (
            (m.semantic_score or 0)
            * (m.spec_fit_score or 0)
            * (m.recency_multiplier or 0)
            * (m.evidence_weight or 0)
        )
        assert abs(expected - (m.final_score or 0)) < 1e-4


def test_matcher_skips_when_inventory_index_empty(session: Session) -> None:
    """Inventory side empty → matcher logs but writes nothing."""
    # Seed buyer-only.
    ingest_messages(
        parse_file(WHATSAPP_FIXTURE),
        session=session,
        source_type=SourceType.WHATSAPP,
        source_path=str(WHATSAPP_FIXTURE),
    )
    # Deactivate every Inventory row so the index is empty.
    for inv in session.scalars(select(Inventory)).all():
        inv.is_active = False
    session.flush()

    embedder = HashEmbedder(dim=64)
    idx, _ = build_inventory_index(session, embedder=embedder)
    assert idx.size == 0
    for dem in session.scalars(
        select(Demand).where(Demand.is_active.is_(True))
    ).all():
        stats = match_demand_against_inventory(
            dem,
            session=session,
            embedder=embedder,
            inventory_index=idx,
            now=datetime(2026, 5, 28, 9, 5),
        )
        assert stats.candidates_seen == 0
        assert stats.matches_created == 0
    assert session.scalar(select(func.count(Match.id))) == 0


def test_matcher_groups_inventory_under_unified_seller_after_cross_source(
    session: Session,
) -> None:
    """Side-effect proof of cross-source merge: the unified +971501234567
    seller owns >=3 Inventory rows after WhatsApp + CSV ingest (its own
    WhatsApp post + two CSV rows). All belong to one seller_id."""
    _ingest_all(session)
    seller = session.scalar(
        select(Seller).where(Seller.phone == "+971501234567")
    )
    assert seller is not None
    inventories = session.scalars(
        select(Inventory).where(Inventory.seller_id == seller.id)
    ).all()
    assert len(inventories) >= 3
    assert all(inv.seller_id == seller.id for inv in inventories)


def test_excel_fixture_participates_in_matching(
    session: Session, excel_workbook: Path
) -> None:
    """Excel-sourced inventory is matchable on equal terms with WhatsApp
    + CSV — full cross-source pipeline coverage."""
    ingest_messages(
        parse_file(WHATSAPP_FIXTURE),
        session=session,
        source_type=SourceType.WHATSAPP,
        source_path=str(WHATSAPP_FIXTURE),
    )
    ingest_messages(
        parse_excel(excel_workbook),
        session=session,
        source_type=SourceType.EXCEL,
        source_path=str(excel_workbook),
    )
    _run_matcher(session)
    # iPhone 14 Pro buyer (Yusuf Trading) must match against the Ajman
    # Mobiles Excel row.
    yusuf = session.scalar(
        select(Demand).where(Demand.normalized_product == "iPhone 14 Pro")
    )
    assert yusuf is not None
    matches = session.scalars(
        select(Match).where(Match.demand_id == yusuf.id)
    ).all()
    assert matches
    # The top-scored Match must come from an Excel-sourced Inventory row.
    top = max(matches, key=lambda m: m.final_score or 0)
    inv = session.get(Inventory, top.inventory_id)
    assert inv is not None
    assert inv.normalized_product == "iPhone 14 Pro"
