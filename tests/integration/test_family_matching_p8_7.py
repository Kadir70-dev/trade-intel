"""P8.7 — Family-aware semantic floor for UAE wholesale matching.

Bug report: a buyer demand for ``iPhone 15 Pro`` was matched against
sellers with ``15PM`` / ``15 pro max`` / ``iPhone 15`` and produced
all grade-C / final≈0.00 matches because HashEmbedder cosine on
``"iPhone 15 Pro"`` vs ``"iPhone 15 Pro Max"`` is ~0.07 — a deterministic
hash similarity, not a semantic one.

The fix is a **product-relation floor** under the semantic factor:

* same canonical → ``1.00``
* same series (``Apple|iPhone|15`` covers ``iPhone 15`` / ``iPhone 15
  Pro`` / ``iPhone 15 Pro Max`` / ``iPhone 15 Plus`` / ``iPhone 15 Mini``)
  → ``0.85``
* same brand + family (``iPhone 14`` vs ``iPhone 15``) → ``0.65``
* same brand, different family (``iPhone`` vs ``iPad``) → ``0.50``
* same category (``iPhone`` vs ``Samsung`` — both phones) → ``0.30``
* unrelated → ``0``

``score_match`` takes ``max(embedder_score, relation_score)`` so a
clearly same-series pair lifts to grade A even when the embedder
under-rates it.

These tests verify the floor table directly + reproduce the user's
exact failing scenario end-to-end against the persistence + matcher
pipeline.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime

import pytest
from sqlalchemy import create_engine, func, select
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
    SourceType,
)
from trade_intel.matching import build_inventory_index
from trade_intel.matching.scoring import (
    product_relation_score,
)
from trade_intel.ml import HashEmbedder
from trade_intel.normalization import (
    PRODUCTS,
    extract_products,
    extract_structured,
)
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


_NOW = datetime(2026, 5, 28, 9, 30)


# ============================================================
# A. product_relation_score — floor table
# ============================================================


def test_relation_same_canonical_is_one() -> None:
    assert product_relation_score(
        "iPhone 15 Pro Max", "iPhone 15 Pro Max"
    ) == 1.0


@pytest.mark.parametrize(
    "a,b",
    [
        # Same iPhone series — Pro vs Pro Max vs base vs Plus vs Mini
        ("iPhone 15 Pro", "iPhone 15 Pro Max"),
        ("iPhone 15 Pro Max", "iPhone 15 Pro"),
        ("iPhone 15", "iPhone 15 Pro Max"),
        ("iPhone 15 Plus", "iPhone 15 Pro"),
        ("iPhone 13 Mini", "iPhone 13 Pro Max"),
        ("iPhone 14 Pro", "iPhone 14 Pro Max"),
        ("iPhone 16 Pro Max", "iPhone 16 Pro"),
        # iPad: same series = same Mini/Air/Pro line *and* gen.
        ("iPad Mini 6", "iPad Mini 6"),  # exact takes precedence
    ],
)
def test_relation_same_series_returns_85(a: str, b: str) -> None:
    if a == b:
        assert product_relation_score(a, b) == 1.0
    else:
        assert product_relation_score(a, b) == 0.85


@pytest.mark.parametrize(
    "a,b",
    [
        ("iPhone 14", "iPhone 15"),  # same brand + family, diff series
        ("iPhone 13 Pro Max", "iPhone 14 Pro Max"),
        ("iPhone 12", "iPhone 15"),
        ("iPad Mini 6", "iPad Mini 5"),  # different generations
        ("iPad Air 5", "iPad Mini 6"),  # different family-line within iPad
    ],
)
def test_relation_same_brand_family_returns_65(a: str, b: str) -> None:
    assert product_relation_score(a, b) == 0.65


def test_relation_iphone_vs_ipad_returns_50() -> None:
    """Apple brand, different family (iPhone vs iPad)."""
    assert (
        product_relation_score("iPhone 15 Pro", "iPad Mini 6") == 0.50
    )


def test_relation_iphone_vs_samsung_returns_30() -> None:
    """Different brand, same category (both phones) → 0.30 floor."""
    assert product_relation_score("iPhone 15 Pro", "Samsung") == 0.30


def test_relation_phone_vs_accessory_is_zero() -> None:
    """Accessories are a different category than phones; no floor."""
    assert (
        product_relation_score("iPhone 15 Pro", "Phone Cases") == 0.0
    )
    assert (
        product_relation_score("Phone Covers", "iPhone 15 Pro Max")
        == 0.0
    )


def test_relation_unknown_canonical_returns_zero() -> None:
    """Canonical names absent from the registry score 0 (unless they
    equal verbatim)."""
    assert product_relation_score("__nope_a__", "__nope_b__") == 0.0
    assert product_relation_score("__same__", "__same__") == 1.0


def test_relation_is_symmetric() -> None:
    """relation(a, b) == relation(b, a) for every pair we test."""
    pairs = [
        ("iPhone 15 Pro", "iPhone 15 Pro Max"),
        ("iPhone 14", "iPhone 15"),
        ("iPhone 15 Pro", "iPad Mini 6"),
        ("Samsung", "iPhone 12"),
        ("Phone Covers", "iPhone 13 Pro"),
    ]
    for a, b in pairs:
        assert product_relation_score(a, b) == product_relation_score(b, a)


# ============================================================
# B. Canonical product registry has new series + category fields
# ============================================================


def test_every_iphone_has_series_and_phone_category() -> None:
    iphones = [p for p in PRODUCTS if p.family == "iPhone"]
    assert iphones, "expected at least one iPhone in registry"
    for p in iphones:
        assert p.series is not None and p.series.startswith("Apple|iPhone|"), (
            f"{p.canonical_name} missing series"
        )
        assert p.category == "phone"


def test_every_ipad_has_series_and_tablet_category() -> None:
    ipads = [p for p in PRODUCTS if p.family == "iPad"]
    assert ipads, "expected at least one iPad in registry"
    for p in ipads:
        assert p.series is not None and p.series.startswith("Apple|iPad|"), (
            f"{p.canonical_name} missing series"
        )
        assert p.category == "tablet"


def test_iphone_15_variants_share_one_series() -> None:
    by_canonical = {p.canonical_name: p for p in PRODUCTS}
    variants = [
        "iPhone 15",
        "iPhone 15 Pro",
        "iPhone 15 Pro Max",
        "iPhone 15 Plus",
    ]
    series_set = {by_canonical[v].series for v in variants}
    assert series_set == {"Apple|iPhone|15"}


# ============================================================
# C. score_match — semantic floor lifts grades
# ============================================================


def _ingest(session: Session, lines: list[str]) -> None:
    ingest_messages(
        list(parse_lines(lines, source_file="t.txt", group_name="g")),
        session=session,
        source_type=SourceType.WHATSAPP,
        source_path="t.txt",
    )
    session.commit()


def _run_matcher(session: Session) -> None:
    emb = HashEmbedder(dim=64)
    idx, _ = build_inventory_index(session, embedder=emb)
    for d in session.scalars(
        select(Demand).where(Demand.is_active.is_(True))
    ).all():
        match_demand_against_inventory(
            d,
            session=session,
            embedder=emb,
            inventory_index=idx,
            k=10,
            min_grade=Grade.C,
            now=_NOW,
        )
    session.commit()


def test_iphone_15_pro_demand_lifts_15pm_to_grade_a(session: Session) -> None:
    """User's exact bug-report case.

    Buyer wants ``iPhone 15 Pro``. Sellers offer ``15PM`` / ``iphone 15
    pro max`` / ``iPhone 15`` plus accessories. Before P8.7 every Match
    landed at grade C. After P8.7 the same-series matches reach
    grade A (final ≥ 0.70).
    """
    _ingest(
        session,
        [
            "28/05/26, 09:00 - Ali: avail 15pm 256gb sealed qty 5 "
            "LP 4800 AED dxb",
            "28/05/26, 09:01 - Bob: stock ready iphone 15 pro max "
            "256gb sealed qty 10 AED 4800",
            "28/05/26, 09:02 - Sara: avail iphone 15 256gb sealed "
            "qty 8 AED 3500 shj",
            "28/05/26, 09:03 - Mobile Hub: iphone covers all models AED 5 each",
            "28/05/26, 09:04 - Khalid: phone accessories MOQ 50",
            "28/05/26, 09:05 - Buyer: need urgent 5 iphone 15 pro "
            "256gb sealed asap dxb",
        ],
    )
    _run_matcher(session)
    # Top three matches must all be same-series → grade A or A+.
    top_three = list(
        session.scalars(
            select(Match).order_by(Match.final_score.desc()).limit(3)
        ).all()
    )
    assert len(top_three) == 3
    for m in top_three:
        assert m.grade in (Grade.A, Grade.A_PLUS), (
            f"expected A/A+, got {m.grade.value} (final={m.final_score:.3f})"
        )
        assert m.semantic_score is not None and m.semantic_score >= 0.85
        # Their target products are all in the iPhone 15 series.
        inv = session.get(Inventory, m.inventory_id)
        assert inv is not None
        assert "iPhone 15" in inv.normalized_product


def test_same_series_match_type_is_similar(session: Session) -> None:
    """A buyer demand for ``iPhone 15 Pro`` matched against an inventory
    row of ``iPhone 15 Pro Max`` produces ``MatchType.SIMILAR`` —
    same series, different canonical."""
    _ingest(
        session,
        [
            "28/05/26, 09:00 - Ali: avail 15pm 256gb sealed dxb LP 4800",
            "28/05/26, 09:01 - Buyer: need 15 pro 256gb sealed dxb urgent",
        ],
    )
    _run_matcher(session)
    m = session.scalar(
        select(Match).order_by(Match.final_score.desc())
    )
    assert m is not None
    assert m.match_type is MatchType.SIMILAR


def test_accessories_outranked_by_family_matches(session: Session) -> None:
    """An accessory inventory row ranks below any same-series iPhone for
    a phone demand — the relation floor never lifts an accessory above
    the family hits."""
    _ingest(
        session,
        [
            "28/05/26, 09:00 - Ali: avail 15pm 256gb sealed dxb LP 4800",
            "28/05/26, 09:01 - Mobile Hub: iphone covers all models AED 5 each",
            "28/05/26, 09:02 - Buyer: need iphone 15 pro 256gb sealed dxb",
        ],
    )
    _run_matcher(session)
    by_score = list(
        session.scalars(select(Match).order_by(Match.final_score.desc())).all()
    )
    assert len(by_score) >= 2
    top, second = by_score[0], by_score[1]
    top_inv = session.get(Inventory, top.inventory_id)
    second_inv = session.get(Inventory, second.inventory_id)
    assert top_inv is not None and second_inv is not None
    assert "iPhone" in top_inv.normalized_product
    assert second_inv.normalized_product in ("Phone Covers", "Phone Cases", "Accessories")
    assert (top.final_score or 0) > (second.final_score or 0)


def test_cross_family_same_brand_lifts_to_at_least_b(session: Session) -> None:
    """iPad inventory matched against an iPhone demand still gets a
    same-brand floor of 0.50 → grade B-ish under perfect freshness +
    evidence."""
    _ingest(
        session,
        [
            "28/05/26, 09:00 - Ali: avail iPad Air 5 256gb sealed dxb AED 2400",
            "28/05/26, 09:01 - Buyer: need iphone 15 pro 256gb sealed dxb",
        ],
    )
    _run_matcher(session)
    m = session.scalar(
        select(Match).order_by(Match.final_score.desc())
    )
    assert m is not None
    # iPad Air 5 vs iPhone 15 Pro → same brand, different family → 0.50.
    assert (m.semantic_score or 0) == pytest.approx(0.50, abs=1e-6)


def test_lead_pipeline_priority_lifts_above_p4(session: Session) -> None:
    """The user-reported scenario produced all P4 (final≈0.00) before
    P8.7. After P8.7 a same-series cross-variant match lands at
    P2/P3 (final ≈ 0.54 with the 0.85 series floor + asymmetric
    condition spec_fit penalty)."""
    _ingest(
        session,
        [
            "28/05/26, 09:00 - Ali: avail 15pm 256gb sealed dxb LP 4800",
            "28/05/26, 09:01 - Buyer: need iphone 15 pro 256gb sealed dxb urgent",
        ],
    )
    _run_matcher(session)
    priorities = {
        lp.priority.value
        for lp in session.scalars(select(LeadPipeline)).all()
    }
    # P1 (≥0.85) unreachable for cross-variant pairs because the
    # series floor caps semantic at 0.85; P4 (<0.50) means we
    # regressed. Anything in P2/P3 proves the lift.
    assert priorities & {"P2", "P3"}, (
        f"expected P2/P3 priority; got {sorted(priorities)}"
    )
    assert "P4" not in priorities, (
        "same-series match must escape the worst-priority bucket"
    )


# ============================================================
# D. opbx condition extraction
# ============================================================


@pytest.mark.parametrize(
    "body,expected",
    [
        ("opbx iphone 14 pro", Condition.USED),
        ("OPBX 13 pro mini", Condition.USED),
        ("avail 15pm 256gb opbx qty 5 AED 3500", Condition.USED),
    ],
)
def test_opbx_extracts_to_used(body: str, expected: Condition) -> None:
    assert extract_structured(body).condition is expected


# ============================================================
# E. Realistic UAE wholesale chat → A/A+ opportunities
# ============================================================


_UAE_TRADER_CHAT = [
    # Sellers — mixed slang from real Deira / Bur Dubai traffic
    "29/05/26, 08:30 - Hassan Mobiles DXB: avail 15pm 256gb sealed "
    "qty 20 LP 4800 AED dxb",
    "29/05/26, 08:31 - Khalid Trading: actv 14pm 1tb sealed qty 5 "
    "AED 5200 dxb",
    "29/05/26, 08:32 - Sara Wholesale: stk ready 15p 256gb sealed "
    "qty 12 AED 4200 shj",
    "29/05/26, 08:33 - Mobile Hub: jv 15pm 512gb HK qty 30 LP 4500 AED",
    "29/05/26, 08:34 - Bilal Electronics: opbx 14 pro 256gb qty 8 "
    "AED 3000 dxb",
    "29/05/26, 08:35 - Rashid Mobiles: avail mini6 64gb sealed qty 25 "
    "AED 1900 ajman",
    # Buyers — same-series cross-variants
    "29/05/26, 08:40 - Buyer1: need urgent 5 iphone 15 pro 256gb sealed dxb",
    "29/05/26, 08:41 - Buyer2: req 10 14pm 1tb today urgent",
    "29/05/26, 08:42 - Buyer3: wtb 15pm 512gb HK urgent",
    "29/05/26, 08:43 - Buyer4: looking for mini6 64gb shj asap",
]


def test_uae_trader_chat_produces_realistic_a_grade_opportunities(
    session: Session,
) -> None:
    _ingest(session, _UAE_TRADER_CHAT)
    _run_matcher(session)
    by_grade = {
        g: session.scalar(
            select(func.count(Match.id)).where(Match.grade == g)
        )
        or 0
        for g in Grade
    }
    # At least a few A/A+ — was 0 before P8.6+P8.7.
    assert by_grade[Grade.A_PLUS] + by_grade[Grade.A] >= 3, (
        f"expected ≥3 A/A+; got {by_grade}"
    )
    # And the top final score should clear 0.70 (A territory).
    top = session.scalar(select(func.max(Match.final_score))) or 0.0
    assert top >= 0.70


def test_uae_trader_chat_top_match_lands_in_correct_series(
    session: Session,
) -> None:
    """The top match for buyer ``need 5 iphone 15 pro`` should be an
    inventory row in the iPhone 15 series — either ``15PM`` (Hassan),
    ``15p`` (Sara), or ``15PM HK`` (Mobile Hub)."""
    _ingest(session, _UAE_TRADER_CHAT)
    _run_matcher(session)
    # Find the iPhone 15 Pro buyer demand.
    dem = session.scalar(
        select(Demand).where(Demand.normalized_product == "iPhone 15 Pro")
    )
    assert dem is not None
    top = session.scalar(
        select(Match)
        .where(Match.demand_id == dem.id)
        .order_by(Match.final_score.desc())
    )
    assert top is not None
    inv = session.get(Inventory, top.inventory_id)
    assert inv is not None
    assert inv.normalized_product in (
        "iPhone 15 Pro Max",
        "iPhone 15 Pro",
        "iPhone 15",
    )
    assert (top.semantic_score or 0) >= 0.85


# ============================================================
# F. Regression guards
# ============================================================


def test_classic_exact_match_still_grade_a_plus(session: Session) -> None:
    """Pre-P8.7 exact-match flow still scores A+ when all factors align."""
    _ingest(
        session,
        [
            "28/05/26, 09:00 - Ali: avail iphone 12 pro max 256gb sealed "
            "qty 5 LP 3200 AED dxb",
            "28/05/26, 09:01 - Buyer: need iphone 12 pro max 256gb sealed "
            "urgent dxb",
        ],
    )
    _run_matcher(session)
    m = session.scalar(
        select(Match).order_by(Match.final_score.desc())
    )
    assert m is not None
    assert m.grade is Grade.A_PLUS
    assert m.match_type is MatchType.EXACT
    assert (m.semantic_score or 0) == pytest.approx(1.0, abs=1e-6)


def test_accessory_to_accessory_still_matches(session: Session) -> None:
    """An accessory buyer ↔ accessory seller still produces a match.
    Buyer body uses pure-BUYER lexicon (``wtb``+``need``+``today``) so
    the intent classifier doesn't tie against ``MOQ``/``bulk``."""
    _ingest(
        session,
        [
            "29/05/26, 08:00 - Mobile Hub: iphone cases AED 5 each MOQ 20 stock ready",
            "29/05/26, 08:01 - Buyer: wtb 50 phone cases today need urgent",
        ],
    )
    _run_matcher(session)
    m = session.scalar(
        select(Match).order_by(Match.final_score.desc())
    )
    assert m is not None
    inv = session.get(Inventory, m.inventory_id)
    assert inv is not None
    assert inv.normalized_product in ("Phone Cases", "Phone Covers", "Accessories")


def test_iphone_demand_does_not_match_xiaomi_above_floor(
    session: Session,
) -> None:
    """Same category (phone) but different brand → 0.30 floor, so final
    stays below grade B. A high-evidence Xiaomi seller should not
    poach an iPhone buyer."""
    _ingest(
        session,
        [
            "29/05/26, 08:00 - Ali: avail xiaomi redmi 256gb sealed qty 20 AED 1800",
            "29/05/26, 08:01 - Buyer: need iphone 15 pro 256gb sealed dxb urgent",
        ],
    )
    _run_matcher(session)
    m = session.scalar(
        select(Match).order_by(Match.final_score.desc())
    )
    assert m is not None
    # 0.30 * spec_fit * recency * evidence ~= 0.30 * <=1 * 1 * <=1 <= 0.30, below B.
    assert m.grade is Grade.C


def test_product_extraction_recovers_15p_alias(session: Session) -> None:
    """The ``15p`` alias must still produce iPhone 15 Pro (not 15 Pro
    Max) — guards against alias-collision regressions."""
    matches = extract_products("avail 15p 256gb sealed")
    canonicals = [m.canonical.canonical_name for m in matches]
    assert "iPhone 15 Pro" in canonicals
