"""Populate ``message_items`` from raw messages (idempotent, incremental)."""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from trade_intel.analysis.specs import extract_items
from trade_intel.core.logging import get_logger
from trade_intel.db.models import IntentType, Message, MessageItem

log = get_logger(__name__, component="analysis.builder")


def sender_key(phone: str | None, name: str | None) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) >= 8:
        return "+" + digits
    n = re.sub(r"\D", "", name or "")
    if len(n) >= 8:  # WhatsApp shows the number as the display name
        return "+" + n
    return (name or "unknown").strip()[:64] or "unknown"


def build_items(session: Session, *, rebuild: bool = False) -> int:
    """Parse messages that have no items yet (or all of them when ``rebuild``).

    Returns the number of item rows written. Side precedence: explicit trading
    keyword in the text → legacy classifier verdict → the sender's majority side.
    """
    if rebuild:
        session.execute(delete(MessageItem))
        session.flush()
    done = set(session.scalars(select(MessageItem.message_id).distinct()).all())
    written = 0
    pending: list[MessageItem] = []
    for msg in session.scalars(select(Message).order_by(Message.id)).yield_per(500):
        if msg.id in done or not msg.raw_text:
            continue
        side, items = extract_items(msg.raw_text)
        if not items:
            continue
        source = "text"
        if side is None:
            if msg.intent == IntentType.BUYER:
                side, source = "buy", "classifier"
            elif msg.intent == IntentType.SELLER:
                side, source = "sell", "classifier"
        key = sender_key(msg.sender_phone, msg.sender_name)
        for it in items:
            pending.append(
                MessageItem(
                    message_id=msg.id,
                    side=side,
                    side_source=source if side else None,
                    sender_key=key,
                    sender_name=(msg.sender_name or None) if msg.sender_name != key else None,
                    sender_phone=key if key.startswith("+") else None,
                    category=it.category,
                    title=it.title()[:200],
                    brand=it.brand,
                    model=it.model,
                    capacity_gb=it.capacity_gb,
                    generation=it.generation,
                    speed_mhz=it.speed_mhz,
                    form=it.form,
                    interface=it.interface,
                    health_min=it.health_min,
                    health_max=it.health_max,
                    condition=it.condition,
                    cpu=it.cpu,
                    ram_gb=it.ram_gb,
                    ssd_gb=it.ssd_gb,
                    qty=it.qty,
                    price_aed=it.price_aed,
                    flags=it.flags or None,
                    raw_line=it.raw_line[:1000],
                    item_key=it.key()[:200],
                    posted_at=msg.message_ts,
                )
            )
    session.add_all(pending)
    session.flush()
    written = len(pending)

    # Sender-majority fallback for items still without a side.
    rows = session.scalars(select(MessageItem).where(MessageItem.side.is_not(None))).all()
    votes: dict[str, Counter[str]] = defaultdict(Counter)
    for r in rows:
        if r.side_source == "text":
            votes[r.sender_key][r.side or ""] += 1
    for r in session.scalars(select(MessageItem).where(MessageItem.side.is_(None))):
        v = votes.get(r.sender_key)
        if v:
            r.side, r.side_source = v.most_common(1)[0][0], "sender"
    log.info("items_built", written=written, rebuild=rebuild)
    return written
