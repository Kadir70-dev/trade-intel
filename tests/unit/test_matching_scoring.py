"""Unit tests for match scoring (P4.3, P4.4, P4.5)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest

from trade_intel.db.models.enums import Condition, Grade, MatchType
from trade_intel.matching.scoring import (
    A_GRADE_THRESHOLD,
    A_PLUS_GRADE_THRESHOLD,
    B_GRADE_THRESHOLD,
    compute_evidence_weight,
    compute_match_type,
    compute_recency_multiplier,
    compute_spec_fit,
    grade_for,
    score_match,
)

# ---------- lightweight stubs ----------


@dataclass
class StubInventory:
    normalized_product: str = "iPhone 12 Pro Max"
    storage: str | None = None
    condition: Condition | None = None
    quantity: int | None = None
    price_aed: float | None = None
    location: str | None = None
    posted_at: datetime | None = None


@dataclass
class StubDemand:
    normalized_product: str = "iPhone 12 Pro Max"
    storage: str | None = None
    condition: Condition | None = None
    quantity: int | None = None
    budget_aed: float | None = None
    location: str | None = None
    posted_at: datetime | None = None


NOW = datetime(2026, 5, 28, 12, 0)


# ---------- recency tiers (CLAUDE.md) ----------


@pytest.mark.parametrize(
    "hours_ago,expected_mult",
    [
        (0, 1.00),       # HOT
        (3, 1.00),
        (6, 1.00),
        (7, 0.90),       # ACTIVE
        (24, 0.90),
        (25, 0.70),      # WARM
        (72, 0.70),
        (73, 0.40),      # COOL
        (168, 0.40),
        (169, 0.15),     # STALE-recoverable
        (336, 0.15),
        (337, 0.05),     # STALE
        (1000, 0.05),
    ],
)
def test_recency_multiplier_tiers(hours_ago: int, expected_mult: float) -> None:
    posted = NOW - timedelta(hours=hours_ago)
    assert compute_recency_multiplier(posted, now=NOW) == expected_mult


def test_recency_multiplier_missing_ts_is_neutral() -> None:
    assert compute_recency_multiplier(None, now=NOW) == 0.5


def test_recency_multiplier_future_timestamp_treats_as_hot() -> None:
    posted = NOW + timedelta(hours=2)
    assert compute_recency_multiplier(posted, now=NOW) == 1.00


# ---------- spec_fit ----------


def test_spec_fit_full_match() -> None:
    inv = StubInventory(storage="256GB", condition=Condition.SEALED, quantity=10)
    dem = StubDemand(storage="256GB", condition=Condition.SEALED, quantity=5)
    assert compute_spec_fit(inv, dem) == 1.0


def test_spec_fit_storage_hard_mismatch_demotes() -> None:
    inv = StubInventory(storage="256GB")
    dem = StubDemand(storage="128GB")
    assert compute_spec_fit(inv, dem) < 0.5


def test_spec_fit_storage_normalized_across_formats() -> None:
    inv = StubInventory(storage="256 GB")
    dem = StubDemand(storage="256gb")
    assert compute_spec_fit(inv, dem) == 1.0


def test_spec_fit_missing_storage_one_side_soft_penalty() -> None:
    inv = StubInventory(storage="256GB")
    dem = StubDemand(storage=None)
    score = compute_spec_fit(inv, dem)
    # Penalized but still high.
    assert 0.7 < score < 1.0


def test_spec_fit_missing_storage_both_sides_no_penalty() -> None:
    inv = StubInventory(storage=None)
    dem = StubDemand(storage=None)
    assert compute_spec_fit(inv, dem) == 1.0


def test_spec_fit_condition_sealed_compatible_with_new() -> None:
    inv = StubInventory(condition=Condition.SEALED)
    dem = StubDemand(condition=Condition.NEW)
    assert compute_spec_fit(inv, dem) == 1.0


def test_spec_fit_condition_new_vs_used_mismatch() -> None:
    inv = StubInventory(condition=Condition.NEW)
    dem = StubDemand(condition=Condition.USED)
    assert compute_spec_fit(inv, dem) < 0.5


def test_spec_fit_qty_shortfall_penalty() -> None:
    inv = StubInventory(quantity=3)
    dem = StubDemand(quantity=10)
    score = compute_spec_fit(inv, dem)
    assert 0.6 < score < 1.0


def test_spec_fit_qty_sufficient_no_penalty() -> None:
    inv = StubInventory(quantity=20)
    dem = StubDemand(quantity=5)
    assert compute_spec_fit(inv, dem) == 1.0


def test_spec_fit_stacks_storage_and_condition_mismatch() -> None:
    inv = StubInventory(storage="256GB", condition=Condition.NEW)
    dem = StubDemand(storage="128GB", condition=Condition.USED)
    # Two hard mismatches compound.
    score = compute_spec_fit(inv, dem)
    assert score < 0.2


def test_spec_fit_clamps_to_zero_one() -> None:
    inv = StubInventory(storage="256GB", condition=Condition.NEW, quantity=1)
    dem = StubDemand(storage="128GB", condition=Condition.USED, quantity=100)
    score = compute_spec_fit(inv, dem)
    assert 0.0 <= score <= 1.0


# ---------- evidence ----------


def test_evidence_baseline_empty() -> None:
    assert compute_evidence_weight(StubInventory(), StubDemand()) == 0.5


def test_evidence_climbs_with_completeness() -> None:
    inv = StubInventory(quantity=5, price_aed=3200, storage="256GB", condition=Condition.SEALED)
    dem = StubDemand(quantity=5, storage="256GB", budget_aed=3500)
    assert compute_evidence_weight(inv, dem) == 1.0


def test_evidence_capped_at_one() -> None:
    """Even with every field present, the weight tops out at 1.0."""
    inv = StubInventory(quantity=5, price_aed=3200, storage="256GB", condition=Condition.SEALED)
    dem = StubDemand(quantity=5, storage="256GB", budget_aed=3500)
    assert compute_evidence_weight(inv, dem) <= 1.0


def test_evidence_partial_completeness_is_between() -> None:
    inv = StubInventory(quantity=5)
    dem = StubDemand(quantity=5)
    score = compute_evidence_weight(inv, dem)
    assert 0.5 < score < 1.0


# ---------- grading ----------


@pytest.mark.parametrize(
    "score,expected",
    [
        (1.00, Grade.A_PLUS),
        (0.85, Grade.A_PLUS),
        (0.84, Grade.A),
        (0.70, Grade.A),
        (0.69, Grade.B),
        (0.50, Grade.B),
        (0.49, Grade.C),
        (0.00, Grade.C),
    ],
)
def test_grade_thresholds(score: float, expected: Grade) -> None:
    assert grade_for(score) is expected


def test_grade_constants_match_claude_md() -> None:
    assert A_PLUS_GRADE_THRESHOLD == 0.85
    assert A_GRADE_THRESHOLD == 0.70
    assert B_GRADE_THRESHOLD == 0.50


# ---------- match_type ----------


def test_match_type_exact_when_same_product_and_perfect_specs() -> None:
    inv = StubInventory()
    dem = StubDemand()
    assert compute_match_type(inv, dem, spec_fit=1.0) is MatchType.EXACT


def test_match_type_similar_when_same_product_partial_specs() -> None:
    inv = StubInventory(storage="256GB")
    dem = StubDemand(storage="128GB")
    assert compute_match_type(inv, dem, spec_fit=0.4) is MatchType.SIMILAR


def test_match_type_probable_when_different_product() -> None:
    inv = StubInventory(normalized_product="iPhone 12 Pro Max")
    dem = StubDemand(normalized_product="iPhone 13 Pro Max")
    assert compute_match_type(inv, dem, spec_fit=1.0) is MatchType.PROBABLE


# ---------- score_match (composition) ----------


def test_score_match_perfect_inputs_grade_a_plus() -> None:
    inv = StubInventory(
        storage="256GB",
        condition=Condition.SEALED,
        quantity=10,
        price_aed=3200,
        posted_at=NOW - timedelta(hours=2),
    )
    dem = StubDemand(
        storage="256GB",
        condition=Condition.SEALED,
        quantity=5,
        budget_aed=3500,
    )
    result = score_match(inv, dem, semantic_score=1.0, now=NOW)
    assert result.semantic == 1.0
    assert result.spec_fit == 1.0
    assert result.recency == 1.0
    assert result.evidence == 1.0
    assert result.final == 1.0
    assert result.grade is Grade.A_PLUS
    assert result.match_type is MatchType.EXACT


def test_score_match_stale_post_drops_to_c() -> None:
    inv = StubInventory(posted_at=NOW - timedelta(days=20))
    dem = StubDemand()
    result = score_match(inv, dem, semantic_score=1.0, now=NOW)
    assert result.grade is Grade.C
    # 1.0 * 1.0 * 0.05 * 0.5 = 0.025
    assert result.final == pytest.approx(0.025, abs=1e-3)


def test_score_match_clamps_semantic_to_unit_interval() -> None:
    """The raw embedder score must be clamped to ``[0, 1]`` before the
    relation-floor max is applied. Use a product name that's NOT in
    the canonical registry so relation_score is 0 and the clamp is
    the only contributor to ``result.semantic``."""
    inv = StubInventory(normalized_product="__not_in_registry_inv__", posted_at=NOW)
    dem = StubDemand(normalized_product="__not_in_registry_dem__")
    over = score_match(inv, dem, semantic_score=1.5, now=NOW)
    under = score_match(inv, dem, semantic_score=-0.5, now=NOW)
    assert 0.0 <= over.semantic <= 1.0
    assert 0.0 <= under.semantic <= 1.0
    assert over.semantic == 1.0
    assert under.semantic == 0.0


def test_score_match_final_is_product_of_factors() -> None:
    inv = StubInventory(
        storage="256GB",
        quantity=5,
        price_aed=3200,
        posted_at=NOW - timedelta(hours=12),  # 0.90 multiplier
    )
    dem = StubDemand(storage="256GB", quantity=5)
    result = score_match(inv, dem, semantic_score=0.8, now=NOW)
    expected = result.semantic * result.spec_fit * result.recency * result.evidence
    assert result.final == pytest.approx(expected, abs=1e-6)


def test_score_match_returns_immutable_result() -> None:
    inv = StubInventory(posted_at=NOW)
    dem = StubDemand()
    result = score_match(inv, dem, semantic_score=0.5, now=NOW)
    with pytest.raises((AttributeError, Exception)):
        result.final = 0.0  # type: ignore[misc]
