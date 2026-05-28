"""Match + LeadPipeline persistence (P4.6).

Given a demand (or inventory) row and a pre-built opposite-side
:class:`VectorIndex`, query for top-K candidates, score each pair via
:func:`score_match`, and persist Match rows + LeadPipeline entries for
matches that clear ``min_grade``.

Idempotency: a uniqueness check on ``(demand_id, inventory_id)`` skips
re-inserting an existing pair — if the pair already exists, the
existing row's score columns are updated in place and a fresh
``RESCORED`` last_action is stamped on the linked LeadPipeline (if any).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_intel.core.logging import get_logger
from trade_intel.db.models import (
    Demand,
    Grade,
    Inventory,
    LeadPipeline,
    Match,
    PipelineStage,
    Priority,
)
from trade_intel.matching.candidates import (
    find_demand_candidates,
    find_inventory_candidates,
)
from trade_intel.matching.scoring import (
    MatchScoreResult,
    score_match,
)

if TYPE_CHECKING:
    from trade_intel.ml.embeddings import Embedder
    from trade_intel.ml.faiss_index import VectorIndex

log = get_logger(__name__, component="persistence.matches")


# Score → priority. Higher score → higher priority (P1 best).
_PRIORITY_FROM_SCORE = (
    (0.85, Priority.P1),
    (0.70, Priority.P2),
    (0.50, Priority.P3),
)


_GRADE_ORDER: dict[Grade, int] = {
    Grade.C: 0,
    Grade.B: 1,
    Grade.A: 2,
    Grade.A_PLUS: 3,
}


@dataclass(slots=True)
class MatchWriteStats:
    """Per-call counters for match writing."""

    candidates_seen: int = 0
    matches_created: int = 0
    matches_updated: int = 0
    matches_below_threshold: int = 0
    lead_pipelines_created: int = 0
    lead_pipelines_touched: int = 0

    def as_dict(self) -> dict[str, int]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


def _priority_for(score: float) -> Priority:
    for threshold, prio in _PRIORITY_FROM_SCORE:
        if score >= threshold:
            return prio
    return Priority.P4


def _meets_min_grade(grade: Grade, min_grade: Grade) -> bool:
    return _GRADE_ORDER[grade] >= _GRADE_ORDER[min_grade]


def _reasoning_for(result: MatchScoreResult) -> str:
    """Compact JSON blob of the score breakdown — handy for review UIs."""
    return json.dumps(
        {
            "semantic": round(result.semantic, 4),
            "spec_fit": round(result.spec_fit, 4),
            "recency": round(result.recency, 4),
            "evidence": round(result.evidence, 4),
            "final": round(result.final, 4),
            "match_type": result.match_type.value,
            "grade": result.grade.value,
        },
        sort_keys=True,
    )


def _upsert_match(
    session: Session,
    *,
    demand: Demand,
    inventory: Inventory,
    result: MatchScoreResult,
    stats: MatchWriteStats,
    existing_matches: dict[tuple[int | None, int | None], Match] | None = None,
) -> tuple[Match, bool]:
    """Insert or update a Match row for this (demand, inventory) pair.

    When ``existing_matches`` is provided, the lookup is satisfied from
    that dict — the per-pair ``SELECT`` is skipped. The caller is
    responsible for preloading it (one query per demand).
    """
    if existing_matches is not None:
        existing = existing_matches.get((demand.id, inventory.id))
    else:
        existing = session.scalar(
            select(Match).where(
                Match.demand_id == demand.id,
                Match.inventory_id == inventory.id,
            )
        )
    if existing is not None:
        existing.match_type = result.match_type
        existing.semantic_score = result.semantic
        existing.spec_fit_score = result.spec_fit
        existing.recency_multiplier = result.recency
        existing.evidence_weight = result.evidence
        existing.final_score = result.final
        existing.grade = result.grade
        existing.reasoning = _reasoning_for(result)
        stats.matches_updated += 1
        return existing, False
    match = Match(
        demand_id=demand.id,
        inventory_id=inventory.id,
        match_type=result.match_type,
        semantic_score=result.semantic,
        spec_fit_score=result.spec_fit,
        recency_multiplier=result.recency,
        evidence_weight=result.evidence,
        final_score=result.final,
        grade=result.grade,
        reasoning=_reasoning_for(result),
    )
    session.add(match)
    session.flush()
    stats.matches_created += 1
    if existing_matches is not None:
        existing_matches[(demand.id, inventory.id)] = match
    return match, True


def _upsert_lead_pipeline(
    session: Session,
    *,
    match: Match,
    score: float,
    now: datetime,
    stats: MatchWriteStats,
    created_match: bool,
    existing_leads: dict[int | None, LeadPipeline] | None = None,
) -> LeadPipeline:
    """Create a NEW LeadPipeline row on first match; touch ``last_action`` on rescore.

    When ``existing_leads`` is provided (keyed by match_id), the lookup
    is satisfied from that dict.
    """
    if existing_leads is not None:
        existing = existing_leads.get(match.id)
    else:
        existing = session.scalar(
            select(LeadPipeline).where(LeadPipeline.match_id == match.id)
        )
    priority = _priority_for(score)
    if existing is not None:
        existing.priority = priority
        existing.close_probability = score
        existing.last_action = (
            "MATCH_CREATED" if created_match else "MATCH_RESCORED"
        )
        existing.last_action_at = now
        stats.lead_pipelines_touched += 1
        return existing
    pipeline = LeadPipeline(
        match_id=match.id,
        stage=PipelineStage.NEW,
        priority=priority,
        close_probability=score,
        last_action="MATCH_CREATED",
        last_action_at=now,
    )
    session.add(pipeline)
    session.flush()
    stats.lead_pipelines_created += 1
    if existing_leads is not None:
        existing_leads[match.id] = pipeline
    return pipeline


def match_demand_against_inventory(
    demand: Demand,
    *,
    session: Session,
    embedder: Embedder,
    inventory_index: VectorIndex,
    k: int = 10,
    min_grade: Grade = Grade.C,
    now: datetime | None = None,
) -> MatchWriteStats:
    """Score every top-K inventory candidate for ``demand`` and persist
    Match + LeadPipeline rows for grades ≥ ``min_grade``."""
    stats = MatchWriteStats()
    candidates = find_inventory_candidates(
        demand, embedder=embedder, index=inventory_index, k=k
    )
    stats.candidates_seen = len(candidates)
    if not candidates:
        log.info("match_demand_no_candidates", demand_id=demand.id)
        return stats

    when = now if now is not None else datetime.now(UTC).replace(tzinfo=None)
    # P8.4: preload existing Match + LeadPipeline rows for this demand
    # so the per-pair upsert lookups are dict hits instead of SQL.
    existing_matches = {
        (m.demand_id, m.inventory_id): m
        for m in session.scalars(
            select(Match).where(Match.demand_id == demand.id)
        ).all()
    }
    existing_leads = (
        {
            lp.match_id: lp
            for lp in session.scalars(
                select(LeadPipeline).where(
                    LeadPipeline.match_id.in_(
                        [m.id for m in existing_matches.values()]
                    )
                )
            ).all()
        }
        if existing_matches
        else {}
    )
    for inventory_id, sem_score in candidates:
        inventory = session.get(Inventory, inventory_id)
        if inventory is None:  # row disappeared between index-build and now
            continue
        result = score_match(inventory, demand, semantic_score=sem_score, now=when)
        if not _meets_min_grade(result.grade, min_grade):
            stats.matches_below_threshold += 1
            continue
        match, created = _upsert_match(
            session,
            demand=demand,
            inventory=inventory,
            result=result,
            stats=stats,
            existing_matches=existing_matches,
        )
        _upsert_lead_pipeline(
            session,
            match=match,
            score=result.final,
            now=when,
            stats=stats,
            created_match=created,
            existing_leads=existing_leads,
        )
    log.info(
        "match_demand_completed",
        demand_id=demand.id,
        product=demand.normalized_product,
        **stats.as_dict(),
    )
    return stats


def match_inventory_against_demand(
    inventory: Inventory,
    *,
    session: Session,
    embedder: Embedder,
    demand_index: VectorIndex,
    k: int = 10,
    min_grade: Grade = Grade.C,
    now: datetime | None = None,
) -> MatchWriteStats:
    """Mirror of :func:`match_demand_against_inventory` — query a demand
    index for top-K candidates and persist matches for each."""
    stats = MatchWriteStats()
    candidates = find_demand_candidates(
        inventory, embedder=embedder, index=demand_index, k=k
    )
    stats.candidates_seen = len(candidates)
    if not candidates:
        log.info("match_inventory_no_candidates", inventory_id=inventory.id)
        return stats

    when = now if now is not None else datetime.now(UTC).replace(tzinfo=None)
    # P8.4: preload existing Match + LeadPipeline rows for this
    # inventory so per-pair upserts are dict hits, not SQL.
    existing_matches = {
        (m.demand_id, m.inventory_id): m
        for m in session.scalars(
            select(Match).where(Match.inventory_id == inventory.id)
        ).all()
    }
    existing_leads = (
        {
            lp.match_id: lp
            for lp in session.scalars(
                select(LeadPipeline).where(
                    LeadPipeline.match_id.in_(
                        [m.id for m in existing_matches.values()]
                    )
                )
            ).all()
        }
        if existing_matches
        else {}
    )
    for demand_id, sem_score in candidates:
        demand = session.get(Demand, demand_id)
        if demand is None:
            continue
        result = score_match(inventory, demand, semantic_score=sem_score, now=when)
        if not _meets_min_grade(result.grade, min_grade):
            stats.matches_below_threshold += 1
            continue
        match, created = _upsert_match(
            session,
            demand=demand,
            inventory=inventory,
            result=result,
            stats=stats,
            existing_matches=existing_matches,
        )
        _upsert_lead_pipeline(
            session,
            match=match,
            score=result.final,
            now=when,
            stats=stats,
            created_match=created,
            existing_leads=existing_leads,
        )
    log.info(
        "match_inventory_completed",
        inventory_id=inventory.id,
        product=inventory.normalized_product,
        **stats.as_dict(),
    )
    return stats


__all__ = [
    "MatchWriteStats",
    "match_demand_against_inventory",
    "match_inventory_against_demand",
]
