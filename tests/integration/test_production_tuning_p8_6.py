"""P8.6 — UAE mobile wholesale production tuning.

Bug report: real WhatsApp ingest produces only grade-C matches with
``final ≈ 0.00`` because Inventory / Demand rows persisted with
``storage = condition = quantity = price = None`` cap evidence at the
0.5 baseline and break spec_fit alignment.

This phase adds:

* ``extract_structured()`` — pulls storage / qty / price / condition /
  location / variant out of any WhatsApp body, wired into
  ``_write_product_rows`` so every Inventory + Demand row lands with
  fully populated structured cells.
* New lexicon tokens (``jv``, ``actv``, ``open box``, ``used``,
  ``refurb``, ``refurbished``).
* CLI ``run-matcher --now`` (and ``--now auto``) so matching against a
  historical export doesn't STALE every row via wall-clock ``now``.
* Embedding text trimmed to product + storage (the SKU dimensions);
  condition + location handled by spec_fit, kept out of the embedding
  to stabilize semantic signal under both HashEmbedder and MiniLM.

These tests verify the above plus exercise the user's bug-report
scenario end-to-end (UAE_Wholesale.txt-style export → A/B grades).
"""

from __future__ import annotations

import json
from collections.abc import Generator
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from typer.testing import CliRunner

from trade_intel.cli.main import app as cli_app
from trade_intel.core.intent import classify_intent
from trade_intel.db.base import Base
from trade_intel.db.models import (
    Condition,
    Demand,
    Grade,
    IntentType,
    Inventory,
    LeadPipeline,
    Match,
    SourceType,
)
from trade_intel.matching import build_inventory_index
from trade_intel.ml import HashEmbedder
from trade_intel.normalization import StructuredFields, extract_structured
from trade_intel.parsers import parse_lines
from trade_intel.persistence import (
    ingest_messages,
    match_demand_against_inventory,
)


@pytest.fixture
def session() -> Generator[Session, None, None]:
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    sm = sessionmaker(
        bind=eng, autoflush=False, autocommit=False, expire_on_commit=False
    )
    with sm() as s:
        yield s
    eng.dispose()


# ============================================================
# A. Structured extractor unit tests
# ============================================================


def test_extract_structured_empty_input_returns_all_none() -> None:
    r = extract_structured("")
    assert r == StructuredFields()
    r2 = extract_structured(None)
    assert r2.storage is None and r2.quantity is None


@pytest.mark.parametrize(
    "body,expected",
    [
        ("256gb", "256GB"),
        ("128 GB", "128GB"),
        ("512gb sealed", "512GB"),
        ("1tb", "1TB"),
        ("2 tb", "2TB"),
        ("iPhone 15 pro 256gb", "256GB"),
    ],
)
def test_storage_extraction(body: str, expected: str) -> None:
    assert extract_structured(body).storage == expected


@pytest.mark.parametrize(
    "body",
    [
        "no storage here",
        "iPhone 15 Pro Max",  # no GB / TB suffix
        "qty 256",  # qty marker beats storage-looking digit
        "256 pcs",  # pcs not gb
    ],
)
def test_storage_extraction_skips_non_storage_digits(body: str) -> None:
    assert extract_structured(body).storage is None


@pytest.mark.parametrize(
    "body,qty",
    [
        ("qty 15", 15),
        ("qty 15 pcs", 15),
        ("MOQ 20", 20),
        ("moq 100 pcs", 100),
        ("15 pcs", 15),
        ("8 pieces", 8),
        ("lot of 30", 30),
        ("pack of 50", 50),
        ("stock of 200", 200),
        ("quantity 5", 5),
    ],
)
def test_quantity_extraction(body: str, qty: int) -> None:
    r = extract_structured(body)
    assert r.quantity == qty
    assert r.quantity_raw is not None


@pytest.mark.parametrize(
    "body,price",
    [
        ("LP 3200 AED", 3200.0),
        ("AED 4800", 4800.0),
        ("1900 AED", 1900.0),
        ("price 2200", 2200.0),
        ("AED 3.5 each", 3.5),
        ("@ 4500", 4500.0),
        ("last price 5500", 5500.0),
    ],
)
def test_price_extraction(body: str, price: float) -> None:
    assert extract_structured(body).price_aed == price


def test_price_prefers_lp_marker_over_trailing_aed() -> None:
    """User-reported pattern: ``qty 25 AED 1900`` must read price 1900
    (the AED-prefixed value), NOT 25 (which is the qty)."""
    r = extract_structured("stk ready mini6 64gb sealed qty 25 AED 1900 ajman")
    assert r.price_aed == 1900.0
    assert r.quantity == 25


@pytest.mark.parametrize(
    "body,cond",
    [
        ("sealed", Condition.SEALED),
        ("seal qty 5", Condition.SEALED),
        ("box pack", Condition.SEALED),
        ("brand new", Condition.NEW),
        ("fresh stock", Condition.NEW),
        ("fresh seal", Condition.SEALED),  # "seal" wins over "fresh" by order
        ("used phone", Condition.USED),
        ("second hand 14 pro", Condition.USED),
        ("open box", Condition.USED),
        ("refurb 12pm", Condition.REFURB),
        ("refurbished 13 pro", Condition.REFURB),
    ],
)
def test_condition_extraction(body: str, cond: Condition) -> None:
    assert extract_structured(body).condition is cond


@pytest.mark.parametrize(
    "body,loc",
    [
        ("dxb pickup", "dxb"),
        ("dubai stock ready", "dxb"),
        ("shj available", "shj"),
        ("sharjah", "shj"),
        ("auh pickup", "auh"),
        ("abu dhabi", "auh"),
        ("ajman 12pm", "ajman"),
        ("deira market", "deira"),
        ("ras al khaimah", "rak"),
    ],
)
def test_location_extraction(body: str, loc: str) -> None:
    assert extract_structured(body).location == loc


@pytest.mark.parametrize(
    "body,variant",
    [
        ("jv stock 16pm", "JV"),
        ("HK 15 pro max", "HK"),
        ("USA version 14 pro", "USA"),
        ("us version", "USA"),
        ("japan model", "JPN"),
        ("jpn", "JPN"),
        ("middle east variant", "ME"),
        ("indian set", "IN"),
        ("china stock", "CN"),
    ],
)
def test_variant_extraction(body: str, variant: str) -> None:
    assert extract_structured(body).variant == variant


# ============================================================
# B. Persistence wires structured fields onto Inventory / Demand
# ============================================================


def test_inventory_lands_with_full_structured_fields(session: Session) -> None:
    """A fully-loaded seller body must produce an Inventory row with
    every structured cell populated."""
    msgs = list(
        parse_lines(
            [
                "28/05/26, 09:00 - Ali Mobiles: avail 12pm 256gb sealed "
                "qty 15 LP 3200 AED dxb"
            ],
            source_file="t.txt",
            group_name="g",
        )
    )
    stats = ingest_messages(
        msgs,
        session=session,
        source_type=SourceType.WHATSAPP,
        source_path="t.txt",
    )
    session.commit()
    assert stats.inventory_inserted == 1
    inv = session.scalar(select(Inventory))
    assert inv is not None
    assert inv.normalized_product == "iPhone 12 Pro Max"
    assert inv.storage == "256GB"
    assert inv.condition is Condition.SEALED
    assert inv.quantity == 15
    assert inv.price_aed == 3200.0
    assert inv.location == "dxb"


def test_demand_lands_with_storage_and_location(session: Session) -> None:
    msgs = list(
        parse_lines(
            ["28/05/26, 09:00 - Buyer: need urgent 15pm 1tb sealed dxb"],
            source_file="t.txt",
            group_name="g",
        )
    )
    ingest_messages(
        msgs,
        session=session,
        source_type=SourceType.WHATSAPP,
        source_path="t.txt",
    )
    session.commit()
    dem = session.scalar(select(Demand))
    assert dem is not None
    assert dem.normalized_product == "iPhone 15 Pro Max"
    assert dem.storage == "1TB"
    assert dem.location == "dxb"


def test_inventory_jv_variant_stored(session: Session) -> None:
    msgs = list(
        parse_lines(
            [
                "28/05/26, 09:00 - Sara: jv stock 16pm 512gb HK qty 30 LP 4500"
            ],
            source_file="t.txt",
            group_name="g",
        )
    )
    ingest_messages(
        msgs,
        session=session,
        source_type=SourceType.WHATSAPP,
        source_path="t.txt",
    )
    session.commit()
    inv = session.scalar(select(Inventory))
    assert inv is not None
    # "jv" matches first in the variants table.
    assert inv.variant == "JV"
    assert inv.storage == "512GB"


# ============================================================
# C. End-to-end: structured pipeline lifts grades to A/B
# ============================================================


_UAE_WHOLESALE_CORPUS = [
    # Sellers — fully-specified
    "28/05/26, 09:00 - Ali Mobiles DXB: avail 12pm 256gb sealed qty 15 "
    "LP 3200 AED dxb",
    "28/05/26, 09:01 - Khalid Trading: stock ready 15pm 1tb sealed qty 5 "
    "dxb 4800 AED",
    "28/05/26, 09:02 - Sara Wholesale: jv 16pm 512gb HK qty 30 LP 4500 AED",
    "28/05/26, 09:03 - Mobile Hub: stk ready mini6 64gb sealed qty 25 "
    "AED 1900 ajman",
    # Buyers — matching specs
    "28/05/26, 09:04 - Buyer1: need urgent 8 12pm 256gb sealed asap shj",
    "28/05/26, 09:05 - Buyer2: req 5 15pm 1tb today dxb urgent",
    "28/05/26, 09:06 - Buyer3: looking for 20 mini6 64gb today shj",
    "28/05/26, 09:07 - Buyer4: wtb 10 16pm 512gb HK urgent",
]

_NOW = datetime(2026, 5, 28, 9, 30)


def test_uae_wholesale_corpus_produces_high_grade_matches(
    session: Session,
) -> None:
    """The user's bug-report scenario — sellers + buyers with matching
    SKUs must now produce at least one A/A+ grade match, not all-C."""
    msgs = list(
        parse_lines(
            _UAE_WHOLESALE_CORPUS, source_file="uae.txt", group_name="g"
        )
    )
    ingest_messages(
        msgs,
        session=session,
        source_type=SourceType.WHATSAPP,
        source_path="uae.txt",
    )
    session.commit()

    # Run matcher against the corpus's own time-base.
    emb = HashEmbedder(dim=64)
    idx, _ = build_inventory_index(session, embedder=emb)
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
            now=_NOW,
        )
    session.commit()

    by_grade = {
        g: session.scalar(
            select(func.count(Match.id)).where(Match.grade == g)
        )
        or 0
        for g in Grade
    }
    # Expectation: at least one A/A+ exists (was 0 before P8.6).
    assert by_grade[Grade.A_PLUS] + by_grade[Grade.A] >= 3, (
        f"expected ≥3 A/A+ matches; got {by_grade}"
    )
    # And the top final_score is meaningfully above the old C-floor.
    top_score = (
        session.scalar(select(func.max(Match.final_score))) or 0.0
    )
    assert top_score >= 0.70, f"top score should reach grade A territory; got {top_score:.3f}"


def test_uae_wholesale_corpus_lead_pipeline_priority_lift(
    session: Session,
) -> None:
    """LeadPipeline priorities must climb to P1/P2, not stick at P4."""
    msgs = list(
        parse_lines(
            _UAE_WHOLESALE_CORPUS, source_file="uae.txt", group_name="g"
        )
    )
    ingest_messages(
        msgs,
        session=session,
        source_type=SourceType.WHATSAPP,
        source_path="uae.txt",
    )
    session.commit()
    emb = HashEmbedder(dim=64)
    idx, _ = build_inventory_index(session, embedder=emb)
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
            now=_NOW,
        )
    session.commit()
    priorities = {
        lp.priority.value
        for lp in session.scalars(select(LeadPipeline)).all()
    }
    # P1 corresponds to score ≥ 0.85 — the goal of structured tuning.
    assert "P1" in priorities, f"expected P1 priority; got {sorted(priorities)}"


def test_inventory_index_uses_product_centric_embedding(
    session: Session,
) -> None:
    """The embedding text shape change should mean that same-product +
    same-storage inv & dem produce semantic == 1.0 even when other
    structured fields differ (condition / location)."""
    msgs = list(
        parse_lines(
            [
                "28/05/26, 09:00 - Ali: avail 12pm 256gb sealed dxb LP 3200",
                "28/05/26, 09:01 - Buyer: need 12pm 256gb shj urgent",
            ],
            source_file="t.txt",
            group_name="g",
        )
    )
    ingest_messages(
        msgs,
        session=session,
        source_type=SourceType.WHATSAPP,
        source_path="t.txt",
    )
    session.commit()
    emb = HashEmbedder(dim=64)
    idx, _ = build_inventory_index(session, embedder=emb)
    dem = session.scalar(select(Demand))
    assert dem is not None
    match_demand_against_inventory(
        dem,
        session=session,
        embedder=emb,
        inventory_index=idx,
        k=10,
        min_grade=Grade.C,
        now=_NOW,
    )
    session.commit()
    top = session.scalar(
        select(Match).order_by(Match.final_score.desc())
    )
    assert top is not None
    # Same product + same storage → identical embedding text → semantic=1.0.
    assert abs((top.semantic_score or 0.0) - 1.0) < 1e-6


# ============================================================
# D. CLI run-matcher --now flag
# ============================================================


@pytest.fixture
def cli_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[Path, None, None]:
    db = tmp_path / "p8_6.db"
    monkeypatch.setenv("SQLITE_URL", f"sqlite:///{db}")
    from trade_intel.config import get_settings
    from trade_intel.db.session import get_engine, get_sessionmaker

    for c in (get_settings, get_engine, get_sessionmaker):
        c.cache_clear()
    from trade_intel.db import init_db

    init_db()
    yield db
    for c in (get_settings, get_engine, get_sessionmaker):
        c.cache_clear()


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _seed_historical_corpus(tmp_path: Path) -> Path:
    """Seed a historical WhatsApp export — old timestamps that would
    STALE under wall-clock recency."""
    text = "\n".join(_UAE_WHOLESALE_CORPUS) + "\n"
    p = tmp_path / "uae_wholesale.txt"
    p.write_text(text, encoding="utf-8")
    return p


def test_cli_run_matcher_now_auto_anchors_to_inventory_max_posted(
    cli_db: Path, runner: CliRunner, tmp_path: Path
) -> None:
    """``--now auto`` should anchor recency to the corpus's own
    timeline so historical exports produce real matches."""
    chat = _seed_historical_corpus(tmp_path)
    parse_result = runner.invoke(cli_app, ["parse", str(chat), "--json"])
    assert parse_result.exit_code == 0, parse_result.stdout
    # Run matcher with --now auto — must produce matches (would all be
    # STALE under wall-clock 2026-XX-XX vs corpus 2026-05-28).
    res = runner.invoke(cli_app, ["run-matcher", "--now", "auto", "--json"])
    assert res.exit_code == 0, res.stdout
    payload = json.loads(res.stdout)
    assert payload["matches_created"] > 0


def test_cli_run_matcher_now_iso_string(
    cli_db: Path, runner: CliRunner, tmp_path: Path
) -> None:
    chat = _seed_historical_corpus(tmp_path)
    runner.invoke(cli_app, ["parse", str(chat), "--json"])
    res = runner.invoke(
        cli_app,
        [
            "run-matcher",
            "--now",
            "2026-05-28T09:30:00",
            "--json",
        ],
    )
    assert res.exit_code == 0, res.stdout
    payload = json.loads(res.stdout)
    assert payload["matches_created"] > 0


def test_cli_run_matcher_now_rejects_bad_value(
    cli_db: Path, runner: CliRunner, tmp_path: Path
) -> None:
    chat = _seed_historical_corpus(tmp_path)
    runner.invoke(cli_app, ["parse", str(chat), "--json"])
    res = runner.invoke(cli_app, ["run-matcher", "--now", "not-a-date"])
    assert res.exit_code != 0


# ============================================================
# E. Lexicon coverage for new slang
# ============================================================


@pytest.mark.parametrize(
    "body",
    [
        "jv stock 16pm available",
        "actv 12pm 256gb",
        "open box iPhone 14 pro AED 2200",
        "used iPhone 14 pro 256gb",
        "refurb 13 pro mini",
        "refurbished 12pm 128gb",
    ],
)
def test_new_slang_classifies_as_seller(body: str) -> None:
    assert classify_intent(body).intent is IntentType.SELLER


# ============================================================
# F. Regression guards
# ============================================================


def test_classic_pipeline_still_works(session: Session) -> None:
    """Pre-P8.6 messages must still ingest cleanly + match."""
    msgs = list(
        parse_lines(
            [
                "28/05/26, 09:00 - Ali Mobiles: available 5 12pm 256gb fresh",
                "28/05/26, 09:01 - Sara: need urgent 5 12pm 256gb",
            ],
            source_file="t.txt",
            group_name="g",
        )
    )
    stats = ingest_messages(
        msgs,
        session=session,
        source_type=SourceType.WHATSAPP,
        source_path="t.txt",
    )
    session.commit()
    assert stats.inventory_inserted == 1
    assert stats.demand_inserted == 1


def test_structured_extractor_never_raises_on_garbage() -> None:
    """Property-style spot check — structured extractor is total."""
    for s in [
        None,
        "",
        " ",
        "🎉🎉🎉",
        "qwerty random noise",
        "12345 67890",
        "AED AED AED",
        "qty qty qty",
        "iphone iphone iphone",
    ]:
        r = extract_structured(s)
        assert isinstance(r, StructuredFields)


def test_brokers_still_skip_inventory_creation(session: Session) -> None:
    """BROKER messages still must NOT create Inventory / Demand rows."""
    msgs = list(
        parse_lines(
            [
                "28/05/26, 09:00 - Ahmed: looking buyer for 50 pcs "
                "mini6 64gb sealed dxb LP 1800 AED commission ok"
            ],
            source_file="t.txt",
            group_name="g",
        )
    )
    stats = ingest_messages(
        msgs,
        session=session,
        source_type=SourceType.WHATSAPP,
        source_path="t.txt",
    )
    session.commit()
    assert stats.inventory_inserted == 0
    assert stats.demand_inserted == 0
    assert session.scalar(select(func.count(Inventory.id))) == 0
