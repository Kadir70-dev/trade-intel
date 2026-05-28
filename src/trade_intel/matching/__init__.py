"""Semantic matching engine (Phase 4).

- :mod:`scoring` — match scorer (P4.3) + soft-match policy (P4.4) +
  freshness multiplier (CLAUDE.md tiers) + confidence grading (P4.5).
- :mod:`candidates` — build vector indexes from DB rows and query for
  top-K candidates by intent direction (demand → inventory, inventory →
  demand).
"""

from trade_intel.matching.candidates import (
    build_demand_index,
    build_inventory_index,
    embedding_text_for_demand,
    embedding_text_for_inventory,
    find_demand_candidates,
    find_inventory_candidates,
)
from trade_intel.matching.scoring import (
    A_GRADE_THRESHOLD,
    A_PLUS_GRADE_THRESHOLD,
    B_GRADE_THRESHOLD,
    MatchScoreResult,
    compute_evidence_weight,
    compute_match_type,
    compute_recency_multiplier,
    compute_spec_fit,
    grade_for,
    score_match,
)

__all__ = [
    "A_GRADE_THRESHOLD",
    "A_PLUS_GRADE_THRESHOLD",
    "B_GRADE_THRESHOLD",
    "MatchScoreResult",
    "build_demand_index",
    "build_inventory_index",
    "compute_evidence_weight",
    "compute_match_type",
    "compute_recency_multiplier",
    "compute_spec_fit",
    "embedding_text_for_demand",
    "embedding_text_for_inventory",
    "find_demand_candidates",
    "find_inventory_candidates",
    "grade_for",
    "score_match",
]
