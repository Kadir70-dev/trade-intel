"""Explainable buyer-requirement ↔ seller-offer matching on structured items."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


class ItemLike(Protocol):
    category: str
    brand: str | None
    model: str | None
    capacity_gb: int | None
    generation: str | None
    speed_mhz: int | None
    form: str | None
    interface: str | None
    health_min: int | None
    health_max: int | None
    cpu: str | None
    ram_gb: int | None
    ssd_gb: int | None
    qty: int | None
    posted_at: datetime | None


@dataclass(slots=True)
class MatchResult:
    score: int
    grade: str
    reasons: list[str] = field(default_factory=list)  # satisfied requirements
    gaps: list[str] = field(default_factory=list)  # unknowns to confirm with the seller


def grade_for(score: int) -> str:
    return "A+" if score >= 85 else "A" if score >= 70 else "B" if score >= 55 else "C"


def _brands(b: str | None) -> set[str]:
    return {x.strip().lower() for x in (b or "").split("/") if x.strip()}


def _tokens(m: str | None) -> set[str]:
    return {t for t in (m or "").lower().replace("-", " ").split() if len(t) > 1}


def _recency(posted: datetime | None, now: datetime) -> float:
    if posted is None:
        return 0.9
    d = (now - posted).days
    return 1.0 if d <= 3 else 0.95 if d <= 14 else 0.85 if d <= 45 else 0.7


def score_pair(buy: ItemLike, sell: ItemLike, *, now: datetime) -> MatchResult | None:
    """Return a scored match, or ``None`` when a hard requirement conflicts."""
    if buy.category != sell.category or buy.category in ("OTHER", "ACCESSORY"):
        return None
    cat = buy.category
    score = 0
    why: list[str] = []
    gaps: list[str] = []

    def need(label: str, b: object, s: object, pts: int, *, hard: bool = False) -> bool:
        nonlocal score
        if b is None:
            return True
        if s is None:
            gaps.append(f"confirm {label}")
            score += pts // 3
            return True
        if b == s:
            score += pts
            why.append(f"{label} {b}")
            return True
        return not hard

    if cat in ("RAM", "SSD", "HDD"):
        if buy.capacity_gb and sell.capacity_gb:
            if buy.capacity_gb != sell.capacity_gb:
                return None
            score += 35
            why.append(f"{sell.capacity_gb}GB" if sell.capacity_gb < 1024 else f"{sell.capacity_gb // 1024}TB")
        elif buy.capacity_gb or sell.capacity_gb:
            gaps.append("confirm capacity")
            score += 8
        else:
            return None
    if cat == "RAM":
        if buy.generation and sell.generation and buy.generation.rstrip("L") != sell.generation.rstrip("L"):
            return None
        need("generation", buy.generation, sell.generation, 22, hard=True)
        if buy.form and sell.form and "/" not in buy.form + sell.form and buy.form != sell.form:
            return None
        need("form", buy.form, sell.form, 8)
        need("speed", buy.speed_mhz, sell.speed_mhz, 10)
        bb, sb = _brands(buy.brand), _brands(sell.brand)
        if bb and sb:
            if bb & sb:
                score += 15
                why.append("brand " + "/".join(sorted(bb & sb)).title())
            else:
                score -= 15
                gaps.append(f"brand differs (wants {buy.brand})")
        elif bb:
            gaps.append("confirm brand")
            score += 4
        score += 10
    elif cat in ("SSD", "HDD"):
        if buy.interface and sell.interface and buy.interface != sell.interface:
            return None
        need("interface", buy.interface, sell.interface, 20, hard=True)
        need("form", (buy.form or "").lower() or None, (sell.form or "").lower() or None, 8)
        if buy.health_min is not None:
            if sell.health_max is not None:
                if sell.health_max >= buy.health_min:
                    score += 18
                    why.append(f"health up to {sell.health_max}%")
                else:
                    return None
            else:
                gaps.append("confirm battery/SSD health")
                score += 5
        score += 12
    elif cat == "LAPTOP":
        bb, sb = _brands(buy.brand), _brands(sell.brand)
        if bb and sb and not (bb & sb):
            return None
        if bb and sb:
            score += 15
            why.append(f"brand {sell.brand}")
        bt, st = _tokens(buy.model), _tokens(sell.model)
        if bt and st:
            common = bt & st
            if not common:
                return None
            score += 35 + 10 * min(len(common) - 1, 2)
            why.append("model " + " ".join(sorted(common)).upper())
        elif bt:
            gaps.append("confirm model")
            score += 5
        need("CPU", buy.cpu, sell.cpu, 12)
        need("RAM GB", buy.ram_gb, sell.ram_gb, 8)
        need("SSD GB", buy.ssd_gb, sell.ssd_gb, 8)
        score += 10
    else:  # BATTERY / PHONE
        bb, sb = _brands(buy.brand), _brands(sell.brand)
        if bb and sb and not (bb & sb):
            return None
        score += 40 + (20 if bb & sb else 0)
        if bb & sb:
            why.append(f"brand {sell.brand}")
        bt, st = _tokens(buy.model), _tokens(sell.model)
        if bt and st and not (bt & st):
            return None
        if buy.capacity_gb and sell.capacity_gb and buy.capacity_gb != sell.capacity_gb:
            return None
    if buy.qty and sell.qty:
        if sell.qty >= buy.qty:
            score += 6
            why.append(f"stock {sell.qty} ≥ need {buy.qty}")
        else:
            gaps.append(f"only {sell.qty} available (need {buy.qty})")
    elif buy.qty and not sell.qty:
        gaps.append("confirm quantity")
    score = round(min(score, 100) * _recency(sell.posted_at, now))
    if score < 40:
        return None
    return MatchResult(score=score, grade=grade_for(score), reasons=why, gaps=gaps)
