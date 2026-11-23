"""Read-side analytics over ``message_items`` (all JSON-ready dicts)."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_intel.analysis.matching import score_pair
from trade_intel.db.models import ImportRecord, Message, MessageItem

CATEGORY_LABEL = {
    "RAM": "RAM", "SSD": "SSD", "HDD": "HDD", "LAPTOP": "Laptops", "BATTERY": "Batteries",
    "PHONE": "Phones", "ACCESSORY": "Accessories", "OTHER": "Other",
}


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def _item_dict(it: MessageItem, text: str | None, reposts: int, first_seen: datetime | None) -> dict[str, Any]:
    return {
        "id": it.id,
        "side": it.side,
        "side_source": it.side_source,
        "category": it.category,
        "title": it.title,
        "brand": it.brand,
        "model": it.model,
        "capacity_gb": it.capacity_gb,
        "generation": it.generation,
        "speed_mhz": it.speed_mhz,
        "form": it.form,
        "interface": it.interface,
        "health_min": it.health_min,
        "health_max": it.health_max,
        "condition": it.condition,
        "cpu": it.cpu,
        "ram_gb": it.ram_gb,
        "ssd_gb": it.ssd_gb,
        "qty": it.qty,
        "price_aed": it.price_aed,
        "flags": it.flags or [],
        "raw_line": it.raw_line,
        "message_text": (text or "")[:1500],
        "sender": {"key": it.sender_key, "name": it.sender_name, "phone": it.sender_phone},
        "posted_at": _iso(it.posted_at),
        "first_seen": _iso(first_seen),
        "reposts": reposts,
    }


def deduped(
    session: Session,
    *,
    side: str | None = None,
    category: str | None = None,
    q: str | None = None,
    sender: str | None = None,
    with_text: bool = True,
) -> list[dict[str, Any]]:
    """Latest instance of each (sender, side, requirement) with its repost count."""
    stmt = select(MessageItem, Message.raw_text).join(Message, Message.id == MessageItem.message_id)
    if side:
        stmt = stmt.where(MessageItem.side == side)
    if category:
        stmt = stmt.where(MessageItem.category == category)
    if sender:
        stmt = stmt.where(MessageItem.sender_key == sender)
    groups: dict[tuple[str, str | None, str], list[tuple[MessageItem, str | None]]] = defaultdict(list)
    for it, text in session.execute(stmt).all():
        groups[(it.sender_key, it.side, it.item_key)].append((it, text))
    out: list[dict[str, Any]] = []
    needle = (q or "").lower().strip()
    for rows in groups.values():
        rows.sort(key=lambda r: r[0].posted_at or datetime.min)
        latest, text = rows[-1]
        first = rows[0][0].posted_at
        d = _item_dict(latest, text if with_text else None, len(rows), first)
        if needle and needle not in (d["title"] + " " + d["raw_line"] + " " + (d["sender"]["key"] or "")).lower():
            continue
        out.append(d)
    out.sort(key=lambda d: d["posted_at"] or "", reverse=True)
    return out


def overview(session: Session, days: int = 60) -> dict[str, Any]:
    now = _now()
    since = now - timedelta(days=days)
    msgs = session.execute(select(Message.message_ts, Message.sender_phone, Message.sender_name)).all()
    items = deduped(session, with_text=False)
    raw_items = session.execute(
        select(MessageItem.side, MessageItem.category, MessageItem.posted_at, MessageItem.sender_key)
    ).all()

    # Daily activity
    daily: dict[str, dict[str, int]] = defaultdict(lambda: {"messages": 0, "buy": 0, "sell": 0})
    for ts, _p, _n in msgs:
        if ts and ts >= since:
            daily[ts.date().isoformat()]["messages"] += 1
    for side, _c, ts, _k in raw_items:
        if ts and ts >= since and side in ("buy", "sell"):
            daily[ts.date().isoformat()][side] += 1
    timeline = [{"date": d, **v} for d, v in sorted(daily.items())]

    # Hour x weekday heat map (UTC wall-clock as exported)
    heat = [[0] * 24 for _ in range(7)]
    for ts, _p, _n in msgs:
        if ts:
            heat[ts.weekday()][ts.hour] += 1

    buys = [i for i in items if i["side"] == "buy"]
    sells = [i for i in items if i["side"] == "sell"]
    cat_mix: dict[str, dict[str, int]] = defaultdict(lambda: {"buy": 0, "sell": 0})
    for i in items:
        if i["side"] in ("buy", "sell"):
            cat_mix[i["category"]][i["side"]] += 1
    categories = [
        {"category": c, "label": CATEGORY_LABEL.get(c, c), **v, "total": v["buy"] + v["sell"]}
        for c, v in sorted(cat_mix.items(), key=lambda kv: -(kv[1]["buy"] + kv[1]["sell"]))
    ]

    def count(field: str, rows: list[dict[str, Any]], cat: str | None = None, top: int = 10) -> list[dict[str, Any]]:
        c: Counter[str] = Counter()
        for r in rows:
            if cat and r["category"] != cat:
                continue
            v = r.get(field)
            if v:
                for part in str(v).split("/"):
                    c[part.strip()] += 1
        return [{"name": k, "value": v} for k, v in c.most_common(top)]

    def cap_dist(rows: list[dict[str, Any]], cat: str) -> list[dict[str, Any]]:
        c: Counter[int] = Counter(r["capacity_gb"] for r in rows if r["category"] == cat and r["capacity_gb"])
        return [
            {"name": (f"{k // 1024}TB" if k >= 1024 else f"{k}GB"), "value": v, "gb": k}
            for k, v in sorted(c.items())
        ]

    wanted: Counter[str] = Counter(i["title"] for i in buys)
    offered: Counter[str] = Counter(i["title"] for i in sells)
    # Demand vs supply gap per category+capacity (RAM/SSD): what buyers want that nobody offers.
    gap_rows = []
    for cat in ("RAM", "SSD", "HDD"):
        bd = {x["gb"]: x["value"] for x in cap_dist(buys, cat)}
        sd = {x["gb"]: x["value"] for x in cap_dist(sells, cat)}
        for gb in sorted(set(bd) | set(sd)):
            gap_rows.append({
                "name": f"{cat} {gb // 1024}TB" if gb >= 1024 else f"{cat} {gb}GB",
                "demand": bd.get(gb, 0), "supply": sd.get(gb, 0),
            })
    gap_rows.sort(key=lambda r: -(r["demand"] + r["supply"]))

    # Trader leaderboards
    by_sender: dict[str, dict[str, Any]] = {}
    for i in items:
        s = by_sender.setdefault(i["sender"]["key"], {"key": i["sender"]["key"], "buy": 0, "sell": 0, "last": None})
        if i["side"] in ("buy", "sell"):
            s[i["side"]] += 1
        s["last"] = max(filter(None, [s["last"], i["posted_at"]]), default=None)
    top_buyers = sorted((s for s in by_sender.values() if s["buy"]), key=lambda s: -s["buy"])[:8]
    top_sellers = sorted((s for s in by_sender.values() if s["sell"]), key=lambda s: -s["sell"])[:8]

    last7 = now - timedelta(days=7)
    opp = opportunities(session, min_score=40, limit=2000)
    grades = Counter(o["grade"] for o in opp)
    imports = session.scalar(select(ImportRecord.id).order_by(ImportRecord.id.desc()).limit(1))
    return {
        "generated_at": _iso(now),
        "kpis": {
            "messages": len(msgs),
            "traders": len({k for k in by_sender}),
            "buyers": sum(1 for s in by_sender.values() if s["buy"]),
            "sellers": sum(1 for s in by_sender.values() if s["sell"]),
            "requirements": len(buys),
            "offers": len(sells),
            "opportunities": len(opp),
            "hot_opportunities": grades["A+"] + grades["A"],
            "fresh_7d": sum(1 for i in items if i["posted_at"] and i["posted_at"] >= _iso(last7)),
            "has_imports": imports is not None,
            "unclassified": sum(1 for i in items if i["side"] not in ("buy", "sell")),
        },
        "timeline": timeline,
        "heatmap": heat,
        "categories": categories,
        "grades": [{"grade": g, "value": grades[g]} for g in ("A+", "A", "B", "C")],
        "ram_brands_wanted": count("brand", buys, "RAM"),
        "ram_brands_offered": count("brand", sells, "RAM"),
        "ram_generation": [
            {"name": g, "buy": sum(1 for i in buys if i["category"] == "RAM" and i["generation"] == g),
             "sell": sum(1 for i in sells if i["category"] == "RAM" and i["generation"] == g)}
            for g in ("DDR3", "DDR3L", "DDR4", "DDR5")
        ],
        "ram_capacity": [
            {"name": n, "buy": sum(1 for i in buys if i["category"] == "RAM" and i["capacity_gb"] == gb),
             "sell": sum(1 for i in sells if i["category"] == "RAM" and i["capacity_gb"] == gb)}
            for n, gb in (("2GB", 2), ("4GB", 4), ("8GB", 8), ("16GB", 16), ("32GB", 32), ("64GB", 64))
        ],
        "ssd_capacity": [
            {"name": x["name"], "buy": sum(1 for i in buys if i["category"] == "SSD" and i["capacity_gb"] == x["gb"]),
             "sell": sum(1 for i in sells if i["category"] == "SSD" and i["capacity_gb"] == x["gb"])}
            for x in cap_dist(items, "SSD")
        ],
        "laptop_brands_wanted": count("brand", buys, "LAPTOP"),
        "laptop_brands_offered": count("brand", sells, "LAPTOP"),
        "demand_supply_gap": gap_rows[:12],
        "top_wanted": [{"name": k, "value": v} for k, v in wanted.most_common(10)],
        "top_offered": [{"name": k, "value": v} for k, v in offered.most_common(10)],
        "top_buyers": top_buyers,
        "top_sellers": top_sellers,
    }


def traders(session: Session, side: str | None = None) -> list[dict[str, Any]]:
    items = deduped(session, with_text=False)
    agg: dict[str, dict[str, Any]] = {}
    for i in items:
        k = i["sender"]["key"]
        a = agg.setdefault(k, {
            "key": k, "name": i["sender"]["name"], "phone": i["sender"]["phone"],
            "buy": 0, "sell": 0, "categories": Counter(), "last_active": None, "reposts": 0, "qty_total": 0,
        })
        if i["side"] in ("buy", "sell"):
            a[i["side"]] += 1
        a["categories"][i["category"]] += 1
        a["reposts"] += i["reposts"]
        a["qty_total"] += i["qty"] or 0
        a["last_active"] = max(filter(None, [a["last_active"], i["posted_at"]]), default=None)
    rows = []
    for a in agg.values():
        role = "both" if a["buy"] and a["sell"] else "buyer" if a["buy"] else "seller" if a["sell"] else "unknown"
        if side and role not in (side, "both"):
            continue
        rows.append({**a, "role": role, "categories": [{"category": c, "count": n} for c, n in a["categories"].most_common()]})
    rows.sort(key=lambda r: (r["last_active"] or ""), reverse=True)
    return rows


def trader_detail(session: Session, key: str) -> dict[str, Any] | None:
    items = deduped(session, sender=key)
    if not items:
        return None
    msgs = session.execute(
        select(Message.message_ts, Message.raw_text).where(
            (Message.sender_phone == key) | (Message.sender_name == key)
            | (Message.sender_phone == key.lstrip("+")) | (Message.sender_name == key.lstrip("+"))
        ).order_by(Message.message_ts.desc()).limit(30)
    ).all()
    opp = [o for o in opportunities(session, min_score=40, limit=500) if o["buyer"]["key"] == key or o["seller"]["key"] == key]
    return {
        "key": key,
        "requirements": [i for i in items if i["side"] == "buy"],
        "offers": [i for i in items if i["side"] == "sell"],
        "unclassified": [i for i in items if i["side"] not in ("buy", "sell")],
        "messages": [{"at": _iso(t), "text": (x or "")[:1200]} for t, x in msgs],
        "opportunities": opp[:25],
    }


def draft_message(buyer: dict[str, Any], seller: dict[str, Any]) -> str:
    b = buyer
    qty = f"{b['qty']} pcs " if b.get("qty") else ""
    return (
        f"Salam bhai, I have a buyer looking for {qty}{b['title']}. "
        f"Do you have it available? Please share price + quantity + condition."
    )


def opportunities(
    session: Session, *, min_score: int = 40, limit: int = 100, category: str | None = None
) -> list[dict[str, Any]]:
    now = _now()
    rows = deduped(session, category=category)
    buys = [r for r in rows if r["side"] == "buy"]
    sells = [r for r in rows if r["side"] == "sell"]

    class _V:  # lightweight attribute view over item dicts
        def __init__(self, d: dict[str, Any]) -> None:
            self.__dict__.update(d)
            self.posted_at = datetime.fromisoformat(d["posted_at"]) if d["posted_at"] else None

    out: list[dict[str, Any]] = []
    sv = [(s, _V(s)) for s in sells]
    for b in buys:
        bv = _V(b)
        for s, sview in sv:
            if s["sender"]["key"] == b["sender"]["key"]:
                continue
            m = score_pair(bv, sview, now=now)  # type: ignore[arg-type]
            if m and m.score >= min_score:
                out.append({
                    "score": m.score, "grade": m.grade, "reasons": m.reasons, "gaps": m.gaps,
                    "category": b["category"],
                    "buyer": {**b["sender"], "requirement": b["title"], "qty": b["qty"],
                              "raw_line": b["raw_line"], "posted_at": b["posted_at"], "reposts": b["reposts"]},
                    "seller": {**s["sender"], "offer": s["title"], "qty": s["qty"],
                               "raw_line": s["raw_line"], "posted_at": s["posted_at"], "price_aed": s["price_aed"]},
                    "draft": draft_message(b, s),
                })
    out.sort(key=lambda o: (-o["score"], o["buyer"]["posted_at"] or ""), reverse=False)
    out.sort(key=lambda o: -o["score"])
    return out[:limit]
