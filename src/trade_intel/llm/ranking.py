"""Broker supplier ranking — UAE wholesale deal quality.

The core matcher (:mod:`trade_intel.matching.scoring`) is the persisted source
of truth, but its ``final = semantic * spec_fit * recency * evidence`` formula
is *multiplicative*: one weak factor (e.g. a stale listing → recency 0.05)
collapses the whole score, so almost everything lands at grade C. It also
retrieves candidates via the embedder index, which with the zero-dependency
``HashEmbedder`` is semantically blind — an "iPhone 16 Pro Max" query can miss
the actual iPhone 16 Pro Max rows entirely.

This module fixes both for the chat/broker experience, without touching the
tested core:

1. **Product-aware retrieval** — pull inventory by exact + related product
   (same series/family via :func:`product_relation_score`), not by embedder
   neighbours. The right SKUs always surface.
2. **Criteria-based grade** (A+/A/B/C) that does NOT collapse on a single weak
   factor, matching the broker's mental model:
       A+ = exact product + qty fit + recent + price known
       A  = exact product + (qty fit / price) and not stale
       B  = similar product, or an exact-but-thin listing
       C  = weak / probable match only
3. **Weighted rank score** honouring the trader priority order:
   exact match > freshness > qty fit > price/LP > reliability > UAE proximity
   > MOQ fit > urgency compatibility.
4. **Business reasoning** bullets per supplier (no "duplicate row" noise);
   duplicates (same seller + same product) are collapsed to the best listing.

Nothing here is persisted and nothing calls an LLM — it is deterministic,
grounded ranking over verified DB rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from trade_intel.db.models import Demand, Inventory, Urgency
from trade_intel.matching.scoring import (
    compute_recency_multiplier,
    product_relation_score,
)

# ---------------------------------------------------------------- location

_DUBAI_TOKENS = ("dubai", "dxb", "deira", "bur dubai", "karama", "naif", "satwa")
_UAE_TOKENS = (
    "sharjah", "shj", "abu dhabi", "auh", "ajman", "uae", "gcc",
    "ras al", "fujairah", "umm al", "al ain",
)


def location_tier(loc: str | None) -> tuple[str, float]:
    """Map a free-text location to (tier, proximity). Dubai is the home market."""
    if not loc:
        return ("unknown", 0.3)
    low = loc.lower()
    if any(tok in low for tok in _DUBAI_TOKENS):
        return ("dubai", 1.0)
    if any(tok in low for tok in _UAE_TOKENS):
        return ("uae", 0.7)
    return ("other", 0.4)


# ---------------------------------------------------------------- reliability


def reliability(post_count: int | None, trust_score: float | None) -> float:
    """Supplier reliability in [0,1] — trust_score if earned, else post volume."""
    if trust_score is not None:
        return max(0.0, min(1.0, float(trust_score)))
    return min(1.0, (post_count or 0) / 30.0)


# ---------------------------------------------------------------- freshness

_FRESHNESS_TIER: dict[float, str] = {
    1.00: "HOT",
    0.90: "ACTIVE",
    0.70: "WARM",
    0.40: "COOL",
    0.15: "STALE-recoverable",
    0.05: "STALE",
    0.50: "unknown",
}


def freshness_tier(mult: float) -> str:
    return _FRESHNESS_TIER.get(round(mult, 2), "?")


# ---------------------------------------------------------------- seller name


def clean_seller(name: str | None) -> str:
    """Tidy a stored canonical name for display / WhatsApp use.

    Strips leading WhatsApp noise (``~``, ``*``, bullets) and Title-cases the
    lowercased canonical form so 'anas unicell' → 'Anas Unicell'.
    """
    if not name:
        return "unknown"
    cleaned = name.strip(" ~`*_-•·").strip()
    if not cleaned:
        return "unknown"
    # Don't touch phone-only handles (start with + or all digits).
    if cleaned.startswith("+") or cleaned.replace(" ", "").isdigit():
        return cleaned
    return cleaned.title()


# ---------------------------------------------------------------- grade

#: Trader-priority weights (sum ≈ 1.0). Additive so no single factor zeroes out.
_W_PRODUCT = 0.35
_W_FRESH = 0.20
_W_QTY = 0.15
_W_PRICE = 0.10
_W_RELIABILITY = 0.08
_W_LOCATION = 0.06
_W_MOQ = 0.03
_W_URGENCY = 0.03


def broker_grade(
    *, same: bool, similar: bool, qty_fit: bool, price_known: bool,
    fresh: bool, recent: bool,
) -> str:
    """Criteria-based confidence — never collapses on one weak factor."""
    if same:
        bits = int(qty_fit) + int(price_known) + int(fresh)
        if bits == 3:
            return "A+"
        if bits >= 2:
            return "A"
        if bits >= 1 and recent:
            return "A"
        return "B"  # exact product but thin (stale / no qty / no price)
    if similar:
        return "B"
    return "C"


# ---------------------------------------------------------------- candidate


@dataclass(slots=True)
class RankedSupplier:
    inventory_id: int
    seller: str
    seller_post_count: int
    product: str
    storage: str | None
    condition: str | None
    quantity: int | None
    price_aed: float | None
    location: str | None
    freshness: str
    match_type: str  # exact | similar | probable
    grade: str       # A+ | A | B | C
    rank_score: float
    reason: str
    reasons: list[str] = field(default_factory=list)
    same_product: bool = False
    qty_fit: bool = False
    price_known: bool = False
    recent: bool = False
    location_tier: str = "unknown"

    def as_dict(self) -> dict:
        return {
            "inventory_id": self.inventory_id,
            "seller": self.seller,
            "seller_post_count": self.seller_post_count,
            "product": self.product,
            "storage": self.storage,
            "condition": self.condition,
            "quantity": self.quantity,
            "price_aed": self.price_aed,
            "location": self.location,
            "freshness": self.freshness,
            "match_type": self.match_type,
            "grade": self.grade,
            "rank_score": round(self.rank_score, 4),
            "reason": self.reason,
            "reasons": self.reasons,
            "same_product": self.same_product,
            "qty_fit": self.qty_fit,
            "price_known": self.price_known,
            "recent": self.recent,
            "location_tier": self.location_tier,
        }


def _related_products(dem_product: str) -> set[str]:
    """Canonical names worth retrieving for this demand: the exact product plus
    same-series / same-family siblings (relation ≥ 0.65)."""
    from trade_intel.normalization.dictionary import PRODUCTS

    related = {dem_product}
    for p in PRODUCTS:
        if product_relation_score(p.canonical_name, dem_product) >= 0.65:
            related.add(p.canonical_name)
    return related


def _age_phrase(posted_at: datetime | None, *, now: datetime) -> str:
    if posted_at is None:
        return "no date"
    days = (now - posted_at).days
    if days <= 0:
        return "today"
    if days == 1:
        return "1 day old"
    return f"{days} days old"


def rank_suppliers(
    session: Session, dem: Demand, *, now: datetime, limit: int = 5
) -> list[RankedSupplier]:
    """Product-aware retrieval + criteria-based ranking for one demand.

    Returns the top ``limit`` suppliers, deduped to one (best) listing per
    seller+product, ordered by the weighted trader-priority score.
    """
    products = _related_products(dem.normalized_product)
    stmt = (
        select(Inventory)
        .where(Inventory.is_active.is_(True))
        .where(Inventory.normalized_product.in_(products))
        .options(selectinload(Inventory.seller))
    )
    invs = session.execute(stmt).scalars().all()

    dem_qty = dem.quantity
    urgent = dem.urgency is Urgency.HIGH

    ranked: list[RankedSupplier] = []
    for inv in invs:
        seller = inv.seller
        seller_name = clean_seller(seller.canonical_name if seller else None)
        post_count = seller.post_count if seller else 0
        trust = getattr(seller, "trust_score", None) if seller else None

        recency = compute_recency_multiplier(inv.posted_at, now=now)
        fresh = recency >= 0.70   # within ~3 days
        recent = recency >= 0.40  # within ~7 days

        same = inv.normalized_product == dem.normalized_product
        relation = product_relation_score(
            inv.normalized_product, dem.normalized_product
        )
        similar = (not same) and relation >= 0.65
        match_type = "exact" if same else ("similar" if similar else "probable")

        inv_qty = inv.quantity
        qty_known = inv_qty is not None
        if dem_qty is None:
            qty_fit, qty_component = True, 1.0
        elif qty_known:
            qty_fit = inv_qty >= dem_qty
            qty_component = 1.0 if qty_fit else 0.3
        else:
            qty_fit, qty_component = False, 0.6  # stock count unstated

        price_known = inv.price_aed is not None
        loc_tier, loc_prox = location_tier(inv.location)
        rel_score = reliability(post_count, trust)
        product_quality = 1.0 if same else (0.6 if similar else 0.25)
        urgency_component = 1.0 if (not urgent or recent) else 0.4

        score = (
            _W_PRODUCT * product_quality
            + _W_FRESH * recency
            + _W_QTY * qty_component
            + _W_PRICE * (1.0 if price_known else 0.0)
            + _W_RELIABILITY * rel_score
            + _W_LOCATION * loc_prox
            + _W_MOQ * qty_component
            + _W_URGENCY * urgency_component
        )

        grade = broker_grade(
            same=same, similar=similar, qty_fit=qty_fit,
            price_known=price_known, fresh=fresh, recent=recent,
        )

        # Business reasoning bullets (no "duplicate row" noise).
        bullets: list[str] = []
        if same:
            bullets.append(f"exact {inv.normalized_product} match")
        elif similar:
            bullets.append(f"similar model ({inv.normalized_product})")
        else:
            bullets.append(f"possible alternative ({inv.normalized_product})")
        tier = freshness_tier(recency)
        bullets.append(f"{tier} stock ({_age_phrase(inv.posted_at, now=now)})")
        if qty_known and qty_fit:
            bullets.append(f"{inv_qty} pcs available")
        elif qty_known and not qty_fit and dem_qty is not None:
            bullets.append(f"only {inv_qty} pcs (need {dem_qty})")
        else:
            bullets.append("qty on request")
        if price_known:
            bullets.append(f"LP AED {inv.price_aed:g}")
        else:
            bullets.append("LP pending")
        if loc_tier == "dubai":
            bullets.append("Dubai seller")
        elif loc_tier == "uae" and inv.location:
            bullets.append(f"{inv.location} seller")
        if (post_count or 0) >= 20:
            bullets.append(f"established seller ({post_count} posts)")

        # One-line reason = the 2-3 highest-signal bullets.
        reason = ", ".join(bullets[:3])

        ranked.append(
            RankedSupplier(
                inventory_id=inv.id,
                seller=seller_name,
                seller_post_count=post_count or 0,
                product=inv.normalized_product,
                storage=inv.storage,
                condition=inv.condition.value if inv.condition else None,
                quantity=inv_qty,
                price_aed=inv.price_aed,
                location=inv.location,
                freshness=tier,
                match_type=match_type,
                grade=grade,
                rank_score=score,
                reason=reason,
                reasons=bullets,
                same_product=same,
                qty_fit=qty_fit,
                price_known=price_known,
                recent=recent,
                location_tier=loc_tier,
            )
        )

    # Dedup to ONE best listing per seller — a broker wants distinct suppliers
    # to contact, not the same seller repeated for near-identical stock. This
    # also removes the "duplicate supplier" noise the old path produced.
    best: dict[str, RankedSupplier] = {}
    for r in ranked:
        key = r.seller.lower()
        cur = best.get(key)
        if cur is None or r.rank_score > cur.rank_score:
            best[key] = r

    out = sorted(best.values(), key=lambda r: r.rank_score, reverse=True)
    return out[:limit]


__all__ = [
    "RankedSupplier",
    "broker_grade",
    "freshness_tier",
    "location_tier",
    "rank_suppliers",
    "reliability",
]
