"""Match scoring (P4.3) + soft-match policy (P4.4) + confidence grading (P4.5).

The final match score is the product of four bounded multipliers::

    final = semantic * spec_fit * recency * evidence

Each factor lives in :func:`compute_*` so the formula stays auditable.
Score → :class:`Grade` thresholds match the CLAUDE.md +
ARCHITECTURE.md table::

    A+ ≥ 0.85    A ≥ 0.70    B ≥ 0.50    C < 0.50

Freshness tiers (CLAUDE.md):

    0-6h  HOT  → 1.00
    6-24h ACTIVE → 0.90
    1-3d  WARM → 0.70
    4-7d  COOL → 0.40
    7-14d STALE-recoverable → 0.15
    15d+  STALE → 0.05

Soft-match policy (P4.4) — when storage / condition are unknown on
either side, do **not** treat it as a mismatch; apply a smaller penalty
so a buyer who didn't specify storage can still see a 256GB inventory
row. Hard mismatch (both sides specified, values disagree) drops
``spec_fit`` to ``0.4`` so the row is demoted but not dropped.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from trade_intel.db.models.enums import Grade, MatchType

if TYPE_CHECKING:
    from trade_intel.db.models import Demand, Inventory


#: Score → grade thresholds (CLAUDE.md / ARCHITECTURE.md).
A_PLUS_GRADE_THRESHOLD: float = 0.85
A_GRADE_THRESHOLD: float = 0.70
B_GRADE_THRESHOLD: float = 0.50


@dataclass(frozen=True, slots=True)
class MatchScoreResult:
    """Decomposed scoring breakdown for one demand↔inventory pair."""

    semantic: float
    spec_fit: float
    recency: float
    evidence: float
    final: float
    grade: Grade
    match_type: MatchType


# ---------------------------------------------------------------- recency


def compute_recency_multiplier(
    posted_at: datetime | None, *, now: datetime
) -> float:
    """Map an age in hours to the CLAUDE.md freshness multiplier.

    Returns ``0.5`` (neutral) when ``posted_at`` is None — we don't want
    to penalize rows missing a timestamp as harshly as a 15-day-old one,
    but we also can't claim freshness without evidence.
    """
    if posted_at is None:
        return 0.5
    age_hours = (now - posted_at).total_seconds() / 3600.0
    if age_hours < 0:
        # Future timestamp (clock skew / bad data) — treat as HOT.
        return 1.00
    if age_hours <= 6:
        return 1.00
    if age_hours <= 24:
        return 0.90
    if age_hours <= 72:  # 3 days
        return 0.70
    if age_hours <= 168:  # 7 days
        return 0.40
    if age_hours <= 336:  # 14 days
        return 0.15
    return 0.05


# ---------------------------------------------------------------- spec fit


_HARD_MISMATCH_PENALTY: float = 0.4
_MISSING_INFO_PENALTY: float = 0.85
_QTY_SHORTFALL_PENALTY: float = 0.7


def _norm_storage(s: str | None) -> str | None:
    """Canonicalize a storage cell so '256gb' / '256 GB' / '256' all compare."""
    if not s:
        return None
    cleaned = s.strip().lower().replace(" ", "")
    if cleaned.endswith("gb"):
        cleaned = cleaned[:-2]
    if cleaned.endswith("tb"):
        # Normalize 1tb → "1024" so comparison is numeric-friendly.
        try:
            return str(int(float(cleaned[:-2]) * 1024))
        except ValueError:
            return cleaned
    # Drop trailing non-digits so '256 gb' → '256'.
    digits = "".join(ch for ch in cleaned if ch.isdigit())
    return digits or cleaned


def _condition_compatible(
    inv_cond: str | None, dem_cond: str | None
) -> bool:
    """Same condition or 'unknown' on either side."""
    if not inv_cond or not dem_cond:
        return True
    a = inv_cond.strip().lower()
    b = dem_cond.strip().lower()
    if a == b:
        return True
    # 'sealed' implies 'new'; treat as compatible.
    pairs = {("sealed", "new"), ("new", "sealed")}
    return (a, b) in pairs


def compute_spec_fit(inv: Inventory, dem: Demand) -> float:
    """How well do the structured specs (storage / condition / qty) line up?

    Soft-match policy (P4.4):
      - Missing on either side → small penalty, do not exclude.
      - Hard mismatch (both specified, disagree) → :data:`_HARD_MISMATCH_PENALTY`.
      - Insufficient supply (inv.qty < demanded qty) → small penalty.
    """
    score = 1.0

    inv_storage = _norm_storage(getattr(inv, "storage", None))
    dem_storage = _norm_storage(getattr(dem, "storage", None))
    if inv_storage and dem_storage:
        if inv_storage != dem_storage:
            score *= _HARD_MISMATCH_PENALTY
    elif inv_storage or dem_storage:
        score *= _MISSING_INFO_PENALTY

    inv_cond = getattr(inv, "condition", None)
    dem_cond = getattr(dem, "condition", None)
    inv_cond_str = inv_cond.value if inv_cond is not None else None
    dem_cond_str = dem_cond.value if dem_cond is not None else None
    if not _condition_compatible(inv_cond_str, dem_cond_str):
        score *= _HARD_MISMATCH_PENALTY

    inv_qty = getattr(inv, "quantity", None)
    dem_qty = getattr(dem, "quantity", None)
    if inv_qty is not None and dem_qty is not None and inv_qty < dem_qty:
        score *= _QTY_SHORTFALL_PENALTY

    return max(0.0, min(1.0, score))


# ---------------------------------------------------------------- evidence


def compute_evidence_weight(inv: Inventory, dem: Demand) -> float:
    """Reward post completeness — rows with more structured fields are
    more trustworthy."""
    score = 0.5  # baseline so an empty pair still scores something
    if getattr(inv, "quantity", None) is not None:
        score += 0.1
    if getattr(inv, "price_aed", None) is not None:
        score += 0.1
    if getattr(inv, "storage", None):
        score += 0.05
    if getattr(inv, "condition", None) is not None:
        score += 0.05
    if getattr(dem, "quantity", None) is not None:
        score += 0.1
    if getattr(dem, "storage", None):
        score += 0.05
    if getattr(dem, "budget_aed", None) is not None:
        score += 0.05
    return min(1.0, score)


# ---------------------------------------------------------------- grading


def grade_for(score: float) -> Grade:
    """Map a final score in ``[0, 1]`` to its grade tier."""
    if score >= A_PLUS_GRADE_THRESHOLD:
        return Grade.A_PLUS
    if score >= A_GRADE_THRESHOLD:
        return Grade.A
    if score >= B_GRADE_THRESHOLD:
        return Grade.B
    return Grade.C


# ---------------------------------------------------------------- match type


def compute_match_type(inv: Inventory, dem: Demand, *, spec_fit: float) -> MatchType:
    """Coarse category — exact / similar / probable.

    - EXACT: same normalized_product **and** ``spec_fit == 1.0`` (all
      structured specs aligned or absent on both sides).
    - SIMILAR: same normalized_product OR same series (e.g. iPhone 15
      Pro vs iPhone 15 Pro Max) — specs may diverge.
    - PROBABLE: different normalized_product + different series
      (semantic / brand neighbour only).
    """
    if inv.normalized_product == dem.normalized_product:
        return MatchType.EXACT if spec_fit >= 0.99 else MatchType.SIMILAR
    # P8.7: same series → SIMILAR. Different product, different series →
    # PROBABLE (the catch-all).
    relation = _series_relation(inv.normalized_product, dem.normalized_product)
    if relation >= 0.80:
        return MatchType.SIMILAR
    return MatchType.PROBABLE


# ---------------------------------------------------------------- product relation


#: Same-canonical → 1.00.
_REL_EXACT: float = 1.00
#: Same series (e.g. iPhone 15 Pro vs iPhone 15 Pro Max → "Apple|iPhone|15").
_REL_SAME_SERIES: float = 0.85
#: Same brand + family (e.g. iPhone 14 vs iPhone 15).
_REL_SAME_FAMILY: float = 0.65
#: Same brand, different family (e.g. iPhone vs iPad).
_REL_SAME_BRAND: float = 0.50
#: Same category (e.g. iPhone vs Samsung — both phones).
_REL_SAME_CATEGORY: float = 0.30


def product_relation_score(inv_canonical: str, dem_canonical: str) -> float:
    """Lexical-family similarity floor for the semantic score.

    HashEmbedder (and to a smaller degree MiniLM) under-rates obvious
    same-series matches like ``iPhone 15 Pro`` vs ``iPhone 15 Pro Max``.
    This function returns a deterministic floor based on the
    :class:`CanonicalProduct` registry so :func:`score_match` can take
    ``max(semantic_score, relation_score)`` and lift the same-series
    pair to a respectable score regardless of embedder noise.

    Returns a value in ``[0, 1]``:

    * ``1.00`` — same canonical product
    * ``0.85`` — same series (e.g. iPhone 15 Pro / iPhone 15 Pro Max)
    * ``0.65`` — same brand + family (e.g. iPhone 14 / iPhone 15)
    * ``0.50`` — same brand, different family
    * ``0.30`` — same category (e.g. iPhone vs Samsung — both phones)
    * ``0.00`` — unrelated

    A canonical name absent from the registry returns 0 unless it
    equals the other side verbatim.
    """
    if inv_canonical == dem_canonical:
        return _REL_EXACT
    # Lookup against the canonical-name index (lazy import so the
    # matching subpackage stays import-free of normalization).
    from trade_intel.normalization.dictionary import PRODUCTS

    by_canonical = {p.canonical_name: p for p in PRODUCTS}
    inv = by_canonical.get(inv_canonical)
    dem = by_canonical.get(dem_canonical)
    if inv is None or dem is None:
        return 0.0
    if inv.series and dem.series and inv.series == dem.series:
        return _REL_SAME_SERIES
    if inv.brand and inv.brand == dem.brand:
        if inv.family and inv.family == dem.family:
            return _REL_SAME_FAMILY
        return _REL_SAME_BRAND
    if inv.category and inv.category == dem.category:
        return _REL_SAME_CATEGORY
    return 0.0


def _series_relation(inv_canonical: str, dem_canonical: str) -> float:
    """Internal helper — used only by :func:`compute_match_type` to
    decide SIMILAR vs PROBABLE without re-running the full relation
    table."""
    return product_relation_score(inv_canonical, dem_canonical)


# ---------------------------------------------------------------- top-level


def score_match(
    inv: Inventory,
    dem: Demand,
    *,
    semantic_score: float,
    now: datetime,
) -> MatchScoreResult:
    """Compose the final :class:`MatchScoreResult` for a demand↔inventory pair.

    The semantic factor is the ``max`` of:

    * the embedder-provided ``semantic_score`` (cosine in ``[0, 1]``)
    * :func:`product_relation_score` — the deterministic lexical-family
      floor (P8.7 production tuning).

    This means HashEmbedder noise can never drag a clearly same-series
    pair below grade C: even if cosine reads 0.07, the relation floor
    of 0.85 lifts the semantic factor to A-territory.
    """
    semantic_raw = max(0.0, min(1.0, float(semantic_score)))
    relation = product_relation_score(
        inv.normalized_product, dem.normalized_product
    )
    semantic = max(semantic_raw, relation)
    spec = compute_spec_fit(inv, dem)
    recency = compute_recency_multiplier(inv.posted_at, now=now)
    evidence = compute_evidence_weight(inv, dem)
    final = semantic * spec * recency * evidence
    final = max(0.0, min(1.0, final))
    return MatchScoreResult(
        semantic=semantic,
        spec_fit=spec,
        recency=recency,
        evidence=evidence,
        final=final,
        grade=grade_for(final),
        match_type=compute_match_type(inv, dem, spec_fit=spec),
    )


__all__ = [
    "A_GRADE_THRESHOLD",
    "A_PLUS_GRADE_THRESHOLD",
    "B_GRADE_THRESHOLD",
    "MatchScoreResult",
    "compute_evidence_weight",
    "compute_match_type",
    "compute_recency_multiplier",
    "compute_spec_fit",
    "grade_for",
    "product_relation_score",
    "score_match",
]
