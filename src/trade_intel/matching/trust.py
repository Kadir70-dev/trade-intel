"""Supplier trust / reputation engine (Phase 3).

A deterministic 0-100 trust score per seller, computed entirely from real
trader behaviour already in the DB — no LLM, no extra services, cheap to run on
a VPS. The score is persisted to ``sellers.trust_score`` (refresh via
``trade-intel trust --recompute``) so the ranking layer reads it for free.

Signals (all derived from inventory / messages / matches):

* **inventory freshness** — how recent the seller's newest live stock is.
* **active seller** — recency of last activity (proxy for responsiveness;
  true reply-speed lands with the Phase 4 CRM).
* **LP availability** — share of listings that quote a price.
* **quantity reliability** — share of listings that state a real quantity.
* **repeat / established** — posting volume (an established broadcaster).
* **successful matches** — A/A+ grade matches the seller's stock produced.
* **duplicate-spam penalty** — many near-identical listings looks like spam.
* **ghost penalty** — a seller with no live inventory can't be trusted as
  supply and is capped low.

Grade bands: A+ ≥85, A ≥70, B ≥50, C ≥30, Risky <30.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trade_intel.db.models import Grade, Match, Seller
from trade_intel.matching.scoring import compute_recency_multiplier

# Positive-component weights (sum 1.0). Penalties are subtracted after.
_W_FRESH = 0.22
_W_ACTIVITY = 0.20
_W_LP = 0.18
_W_QTY = 0.15
_W_ESTABLISHED = 0.15
_W_SUCCESS = 0.10

_MAX_SPAM_PENALTY = 25.0
_ESTABLISHED_FULL = 40  # post_count at which "established" maxes out
_SUCCESS_FULL = 5       # A/A+ matches at which "success" maxes out


@dataclass(slots=True)
class TrustScore:
    seller_id: int
    seller: str
    score: float          # 0-100
    grade: str            # A+ | A | B | C | Risky
    components: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "seller_id": self.seller_id,
            "seller": self.seller,
            "score": round(self.score, 1),
            "grade": self.grade,
            "components": {k: round(v, 3) for k, v in self.components.items()},
            "reasons": self.reasons,
        }


def grade_for_trust(score: float) -> str:
    if score >= 85:
        return "A+"
    if score >= 70:
        return "A"
    if score >= 50:
        return "B"
    if score >= 30:
        return "C"
    return "Risky"


def _activity(last_seen: datetime | None, *, now: datetime) -> tuple[float, str]:
    if last_seen is None:
        return 0.2, "activity unknown"
    days = (now - last_seen).days
    if days <= 1:
        return 1.0, "active today"
    if days <= 3:
        return 0.85, f"active ({days}d ago)"
    if days <= 7:
        return 0.65, f"active ({days}d ago)"
    if days <= 14:
        return 0.45, f"quiet ({days}d ago)"
    if days <= 30:
        return 0.25, f"quiet ({days}d ago)"
    return 0.10, f"inactive (>{30}d)"


def _good_match_count(session: Session, inv_ids: list[int]) -> int:
    if not inv_ids:
        return 0
    stmt = (
        select(func.count(Match.id))
        .where(Match.inventory_id.in_(inv_ids))
        .where(Match.grade.in_([Grade.A_PLUS, Grade.A]))
    )
    return int(session.execute(stmt).scalar_one() or 0)


def compute_trust(seller: Seller, *, session: Session, now: datetime) -> TrustScore:
    """Compute one seller's trust score from their behaviour signals."""
    inv = [i for i in seller.inventory_items if i.is_active]
    n = len(inv)
    reasons: list[str] = []
    comp: dict[str, float] = {}

    act, act_reason = _activity(seller.last_seen, now=now)
    comp["activity"] = act
    reasons.append(act_reason)

    if n == 0:
        # Ghost / non-supplier: no live stock to assess. Cap low (activity only).
        score = act * 40.0
        reasons.append("⚠ no live inventory")
        return TrustScore(
            seller_id=seller.id,
            seller=seller.canonical_name,
            score=max(0.0, min(100.0, score)),
            grade=grade_for_trust(score),
            components=comp,
            reasons=reasons,
        )

    # Freshness of the newest live listing.
    newest = max((i.posted_at for i in inv if i.posted_at), default=None)
    fresh = compute_recency_multiplier(newest, now=now)
    comp["freshness"] = fresh

    with_price = sum(1 for i in inv if i.price_aed is not None) / n
    with_qty = sum(1 for i in inv if i.quantity is not None) / n
    comp["lp_availability"] = with_price
    comp["qty_reliability"] = with_qty

    established = min(1.0, (seller.post_count or n) / _ESTABLISHED_FULL)
    comp["established"] = established

    good = _good_match_count(session, [i.id for i in inv])
    success = min(1.0, good / _SUCCESS_FULL)
    comp["success"] = success

    distinct = len({i.normalized_product for i in inv})
    dup_ratio = 1.0 - distinct / n
    comp["dup_ratio"] = dup_ratio

    base = (
        _W_FRESH * fresh
        + _W_ACTIVITY * act
        + _W_LP * with_price
        + _W_QTY * with_qty
        + _W_ESTABLISHED * established
        + _W_SUCCESS * success
    ) * 100.0

    # Duplicate-spam penalty: only bites when there are enough listings AND a
    # high fraction are the same product (looks like copy-paste spam).
    penalty = 0.0
    if n >= 4 and dup_ratio > 0.5:
        penalty = min(_MAX_SPAM_PENALTY, (dup_ratio - 0.5) * 50.0)

    score = max(0.0, min(100.0, base - penalty))

    # Business reasons (highest-signal first).
    tier = _fresh_tier(fresh)
    reasons.append(f"{tier} inventory")
    if with_price >= 0.5:
        reasons.append(f"shares LP ({with_price * 100:.0f}%)")
    elif with_price == 0:
        reasons.append("rarely shares LP")
    if with_qty >= 0.5:
        reasons.append(f"states qty ({with_qty * 100:.0f}%)")
    if seller.post_count and seller.post_count >= 20:
        reasons.append(f"established ({seller.post_count} posts)")
    if good:
        reasons.append(f"{good} A-grade match(es)")
    if penalty > 0:
        reasons.append(f"⚠ duplicate-heavy listings (-{penalty:.0f})")

    return TrustScore(
        seller_id=seller.id,
        seller=seller.canonical_name,
        score=score,
        grade=grade_for_trust(score),
        components=comp,
        reasons=reasons,
    )


_FRESH_TIERS = {1.00: "HOT", 0.90: "ACTIVE", 0.70: "WARM", 0.40: "COOL",
                0.15: "stale", 0.05: "stale", 0.50: "undated"}


def _fresh_tier(mult: float) -> str:
    return _FRESH_TIERS.get(round(mult, 2), "?")


def recompute_all(session: Session, *, now: datetime) -> list[TrustScore]:
    """Recompute + persist trust for every seller. Returns scores sorted desc
    (a supplier leaderboard). The caller owns the commit."""
    sellers = session.execute(select(Seller)).scalars().all()
    scores: list[TrustScore] = []
    for seller in sellers:
        ts = compute_trust(seller, session=session, now=now)
        seller.trust_score = ts.score
        scores.append(ts)
    scores.sort(key=lambda t: t.score, reverse=True)
    return scores


__all__ = [
    "TrustScore",
    "compute_trust",
    "grade_for_trust",
    "recompute_all",
]
