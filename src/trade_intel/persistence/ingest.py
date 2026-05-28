"""Wire parsed WhatsApp / CSV / Excel messages into the database (P3.7).

Takes a stream of :class:`ParsedMessage` records (typically from
:func:`parse_file` or :func:`parse_lines`) and persists them into the
``groups`` / ``sellers`` / ``buyers`` / ``messages`` / ``inventory`` /
``demand`` tables. Idempotent via the D-007 SHA-256 ``message_hash``:
re-ingesting the same source file produces zero new rows.

Per-message responsibility chain
--------------------------------
1. **Filter by status:** only ``ParseStatus.OK`` rows become writes.
   System / media / orphan / malformed are counted and dropped.
2. **Dedup:** check ``message_hash`` against the in-memory
   :class:`DedupSet` (preloaded from ``messages.hash`` on first call if
   the caller didn't pass one). Hits become ``skipped_dup`` increments.
3. **Classify intent** (:func:`classify_intent`) and **extract
   products** (:func:`extract_products`) from the normalized body.
4. **Identity merge (D-009):** route to the right side table.
   - ``IntentType.SELLER`` → :func:`_get_or_create_seller`
   - ``IntentType.BUYER`` → :func:`_get_or_create_buyer`
   - ``IntentType.BROKER`` / ``UNKNOWN`` → no side-table touch (message
     still stored with intent column populated so downstream can act).
   Phone-first match (when ``sender_identity.phone`` is present),
   canonical-name fallback (with NULL phone). On match, the existing
   row's ``post_count`` / ``last_seen`` / ``aliases`` are reconciled.
5. **Insert Message** linking ``group_id``, ``seller_id`` / ``buyer_id``,
   ``intent``, ``intent_confidence``, ``hash``.
6. **Insert Inventory / Demand rows** — one per extracted product, on
   matching intent (SELLER → Inventory, BUYER → Demand with urgency).
   No rows created for BROKER / UNKNOWN intents in v1; the message row
   still carries the intent so Phase 4 matching can revisit if needed.

Transactional model
-------------------
- Caller owns the session. Wrap in ``session_scope()`` or commit
  explicitly. We do **not** commit here.
- Each per-message write is bracketed by ``session.begin_nested()`` so
  one bad row does not poison the whole batch. The dedup set only
  registers a hash AFTER its savepoint commits successfully.
- Errors are logged with the message-hash prefix and the
  ``IngestStats.errors`` counter is bumped.

Threading
---------
Single-session, single-thread. Concurrent ingest into the same session
is unsupported (SQLAlchemy session is not thread-safe).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, fields

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_intel.core.intent import IntentResult, classify_intent
from trade_intel.core.logging import get_logger
from trade_intel.db.models import (
    Buyer,
    Demand,
    Group,
    IntentType,
    Inventory,
    Message,
    Seller,
    SourceType,
)
from trade_intel.normalization import SenderIdentity, extract_products
from trade_intel.parsers import (
    DedupSet,
    ParsedMessage,
    ParseStatus,
)

log = get_logger(__name__, component="persistence.ingest")


@dataclass(slots=True)
class IngestStats:
    """Counters returned by :func:`ingest_messages`."""

    messages_seen: int = 0
    messages_inserted: int = 0
    messages_skipped_dup: int = 0
    messages_skipped_system: int = 0
    messages_skipped_media: int = 0
    messages_skipped_malformed: int = 0
    messages_skipped_orphan: int = 0
    inventory_inserted: int = 0
    demand_inserted: int = 0
    sellers_created: int = 0
    sellers_updated: int = 0
    buyers_created: int = 0
    buyers_updated: int = 0
    errors: int = 0
    _skipped_status_field: dict[ParseStatus, str] = field(
        default_factory=lambda: {
            ParseStatus.SYSTEM: "messages_skipped_system",
            ParseStatus.MEDIA: "messages_skipped_media",
            ParseStatus.MALFORMED: "messages_skipped_malformed",
            ParseStatus.ORPHAN_CONTINUATION: "messages_skipped_orphan",
        },
        init=False,
        repr=False,
    )

    def as_dict(self) -> dict[str, int]:
        """Log-friendly snapshot."""
        return {
            f.name: getattr(self, f.name)
            for f in fields(self)
            if not f.name.startswith("_")
        }


def ingest_messages(
    messages: Iterable[ParsedMessage],
    *,
    session: Session,
    source_type: SourceType,
    group_name: str | None = None,
    source_path: str | None = None,
    dedup_set: DedupSet | None = None,
) -> IngestStats:
    """Persist a stream of :class:`ParsedMessage` records.

    Args:
        messages: any iterable of :class:`ParsedMessage` (e.g.
            ``parse_file(...)``).
        session: an open SQLAlchemy session. **Caller owns commit.**
        source_type: provenance flag stored on the Group row.
        group_name: human-readable chat name. If omitted, falls back to
            the parsed ``group_name`` on the first message, then to
            ``"unknown"``.
        source_path: file path / URL for the Group. Used as part of the
            uniqueness key during get-or-create.
        dedup_set: optional pre-seeded :class:`DedupSet`. If omitted,
            one is built from the current ``messages.hash`` column for
            cross-session idempotency.

    Returns:
        :class:`IngestStats` with per-category counts.
    """
    if dedup_set is None:
        dedup_set = DedupSet.from_hashes(
            session.scalars(select(Message.hash)).all()
        )

    stats = IngestStats()
    group: Group | None = None
    resolved_group_name = group_name
    # Preload sender lookup tables (P8.4): two SELECTs instead of N per
    # message. The cache is updated on successful savepoint commit so
    # rolled-back inserts don't leak entries.
    cache = _SenderCache.from_session(session)

    for parsed in messages:
        stats.messages_seen += 1

        status = parsed.parse_status
        if status is not ParseStatus.OK:
            field_name = stats._skipped_status_field.get(status)
            if field_name is not None:
                setattr(stats, field_name, getattr(stats, field_name) + 1)
            continue

        if parsed.message_hash in dedup_set:
            stats.messages_skipped_dup += 1
            continue

        pending_cache: list[_PendingCacheEntry] = []
        savepoint = session.begin_nested()
        try:
            if group is None:
                if resolved_group_name is None:
                    resolved_group_name = parsed.group_name or "unknown"
                group = _get_or_create_group(
                    session,
                    name=resolved_group_name,
                    source_type=source_type,
                    source_path=source_path,
                )

            intent_result = classify_intent(parsed.normalized_message)
            product_matches = extract_products(parsed.normalized_message)

            seller_id, buyer_id = _resolve_sender(
                session=session,
                intent=intent_result.intent,
                identity=parsed.sender_identity,
                sender_name=parsed.sender_name,
                posted_at=parsed.timestamp,
                stats=stats,
                cache=cache,
                pending=pending_cache,
            )

            msg = Message(
                group_id=group.id,
                sender_name=parsed.sender_name,
                sender_phone=parsed.sender_phone,
                raw_text=parsed.raw_message,
                message_ts=parsed.timestamp,
                intent=intent_result.intent,
                intent_confidence=intent_result.confidence,
                hash=parsed.message_hash,
                buyer_id=buyer_id,
                seller_id=seller_id,
            )
            session.add(msg)
            session.flush()

            _write_product_rows(
                session=session,
                message=msg,
                products=product_matches,
                intent_result=intent_result,
                seller_id=seller_id,
                buyer_id=buyer_id,
                posted_at=parsed.timestamp,
                stats=stats,
            )

            session.flush()
            savepoint.commit()
            dedup_set.add(parsed.message_hash)
            for entry in pending_cache:
                cache.commit_entry(entry)
            stats.messages_inserted += 1
        except Exception as exc:
            savepoint.rollback()
            pending_cache.clear()
            stats.errors += 1
            log.exception(
                "ingest_message_failed",
                hash_prefix=parsed.message_hash[:12],
                line_no=parsed.line_no,
                error=str(exc),
            )
            continue

    log.info(
        "ingest_completed",
        group=resolved_group_name,
        source_type=source_type.value,
        **stats.as_dict(),
    )
    return stats


# ---------------------------------------------------------------- helpers


@dataclass(slots=True)
class _PendingCacheEntry:
    """One tentative cache add — promoted on savepoint commit."""

    kind: str  # "seller" or "buyer"
    by_phone: bool
    key: str
    obj: object  # Seller | Buyer (avoids a circular import on the type)


@dataclass(slots=True)
class _SenderCache:
    """In-memory dict cache for the phone / canonical_name lookups
    inside :func:`_get_or_create_seller` / `_get_or_create_buyer`.

    Eliminates two SQL SELECTs per ingested message at the cost of two
    SELECTs up front (one per side table). Updates are buffered until
    the per-message savepoint commits so a rollback leaves no leak.
    """

    sellers_by_phone: dict[str, Seller] = field(default_factory=dict)
    sellers_by_name_no_phone: dict[str, Seller] = field(default_factory=dict)
    buyers_by_phone: dict[str, Buyer] = field(default_factory=dict)
    buyers_by_name_no_phone: dict[str, Buyer] = field(default_factory=dict)

    @classmethod
    def from_session(cls, session: Session) -> _SenderCache:
        c = cls()
        for s in session.scalars(select(Seller)).all():
            if s.phone:
                c.sellers_by_phone[s.phone] = s
            else:
                c.sellers_by_name_no_phone[s.canonical_name] = s
        for b in session.scalars(select(Buyer)).all():
            if b.phone:
                c.buyers_by_phone[b.phone] = b
            else:
                c.buyers_by_name_no_phone[b.canonical_name] = b
        return c

    def commit_entry(self, e: _PendingCacheEntry) -> None:
        if e.kind == "seller":
            if e.by_phone:
                self.sellers_by_phone[e.key] = e.obj  # type: ignore[assignment]
            else:
                self.sellers_by_name_no_phone[e.key] = e.obj  # type: ignore[assignment]
        else:
            if e.by_phone:
                self.buyers_by_phone[e.key] = e.obj  # type: ignore[assignment]
            else:
                self.buyers_by_name_no_phone[e.key] = e.obj  # type: ignore[assignment]


def _get_or_create_group(
    session: Session,
    *,
    name: str,
    source_type: SourceType,
    source_path: str | None,
) -> Group:
    """Get-or-create a Group row.

    Uniqueness key: ``(source_path, source_type)`` when ``source_path``
    is provided, else ``(name, source_type)``.
    """
    if source_path is not None:
        existing = session.scalar(
            select(Group).where(
                Group.source_path == source_path,
                Group.source_type == source_type,
            )
        )
    else:
        existing = session.scalar(
            select(Group).where(
                Group.name == name,
                Group.source_type == source_type,
            )
        )
    if existing is not None:
        return existing
    group = Group(name=name, source_type=source_type, source_path=source_path)
    session.add(group)
    session.flush()
    return group


def _identity_canonical(
    identity: SenderIdentity | None, fallback_sender_name: str | None
) -> str:
    """Derive a non-empty canonical_name from the identity record."""
    if identity is None:
        return fallback_sender_name or "unknown"
    return (
        identity.normalized_name
        or identity.display_name
        or identity.raw
        or fallback_sender_name
        or "unknown"
    )


def _merge_aliases(
    existing: list[str] | None, new: tuple[str, ...]
) -> list[str] | None:
    """Union + sort aliases. Returns None when both inputs are empty."""
    merged = set(existing or []) | set(new or ())
    return sorted(merged) if merged else None


def _resolve_sender(
    *,
    session: Session,
    intent: IntentType,
    identity: SenderIdentity | None,
    sender_name: str | None,
    posted_at,
    stats: IngestStats,
    cache: _SenderCache,
    pending: list[_PendingCacheEntry],
) -> tuple[int | None, int | None]:
    """Route to Seller / Buyer based on intent; return (seller_id, buyer_id)."""
    if intent is IntentType.SELLER:
        seller, created = _get_or_create_seller(
            session, identity, sender_name, posted_at,
            cache=cache, pending=pending,
        )
        if created:
            stats.sellers_created += 1
        else:
            stats.sellers_updated += 1
        return seller.id, None
    if intent is IntentType.BUYER:
        buyer, created = _get_or_create_buyer(
            session, identity, sender_name, posted_at,
            cache=cache, pending=pending,
        )
        if created:
            stats.buyers_created += 1
        else:
            stats.buyers_updated += 1
        return None, buyer.id
    return None, None


def _get_or_create_seller(
    session: Session,
    identity: SenderIdentity | None,
    sender_name: str | None,
    posted_at,
    *,
    cache: _SenderCache,
    pending: list[_PendingCacheEntry],
) -> tuple[Seller, bool]:
    canonical = _identity_canonical(identity, sender_name)
    phone = identity.phone if identity else None
    aliases = identity.aliases if identity else ()

    if phone:
        existing = cache.sellers_by_phone.get(phone)
        if existing is not None:
            _touch_seller(existing, aliases, posted_at)
            return existing, False

    # Name fallback: canonical_name + NULL phone (so we don't merge with a
    # different person who happens to share a normalized name root).
    existing = cache.sellers_by_name_no_phone.get(canonical)
    if existing is not None:
        _touch_seller(existing, aliases, posted_at)
        return existing, False

    seller = Seller(
        canonical_name=canonical,
        phone=phone,
        aliases=list(aliases) if aliases else None,
        first_seen=posted_at,
        last_seen=posted_at,
        post_count=1,
    )
    session.add(seller)
    session.flush()
    if phone:
        pending.append(_PendingCacheEntry("seller", True, phone, seller))
    else:
        pending.append(_PendingCacheEntry("seller", False, canonical, seller))
    return seller, True


def _get_or_create_buyer(
    session: Session,
    identity: SenderIdentity | None,
    sender_name: str | None,
    posted_at,
    *,
    cache: _SenderCache,
    pending: list[_PendingCacheEntry],
) -> tuple[Buyer, bool]:
    canonical = _identity_canonical(identity, sender_name)
    phone = identity.phone if identity else None
    aliases = identity.aliases if identity else ()

    if phone:
        existing = cache.buyers_by_phone.get(phone)
        if existing is not None:
            _touch_buyer(existing, aliases, posted_at)
            return existing, False

    existing = cache.buyers_by_name_no_phone.get(canonical)
    if existing is not None:
        _touch_buyer(existing, aliases, posted_at)
        return existing, False

    buyer = Buyer(
        canonical_name=canonical,
        phone=phone,
        aliases=list(aliases) if aliases else None,
        first_seen=posted_at,
        last_seen=posted_at,
        request_count=1,
    )
    session.add(buyer)
    session.flush()
    if phone:
        pending.append(_PendingCacheEntry("buyer", True, phone, buyer))
    else:
        pending.append(_PendingCacheEntry("buyer", False, canonical, buyer))
    return buyer, True


def _touch_seller(
    seller: Seller, aliases: tuple[str, ...], posted_at
) -> None:
    if posted_at is not None:
        if seller.first_seen is None or posted_at < seller.first_seen:
            seller.first_seen = posted_at
        if seller.last_seen is None or posted_at > seller.last_seen:
            seller.last_seen = posted_at
    seller.post_count = (seller.post_count or 0) + 1
    merged = _merge_aliases(seller.aliases, aliases)
    if merged != (seller.aliases or None):
        seller.aliases = merged


def _touch_buyer(buyer: Buyer, aliases: tuple[str, ...], posted_at) -> None:
    if posted_at is not None:
        if buyer.first_seen is None or posted_at < buyer.first_seen:
            buyer.first_seen = posted_at
        if buyer.last_seen is None or posted_at > buyer.last_seen:
            buyer.last_seen = posted_at
    buyer.request_count = (buyer.request_count or 0) + 1
    merged = _merge_aliases(buyer.aliases, aliases)
    if merged != (buyer.aliases or None):
        buyer.aliases = merged


def _write_product_rows(
    *,
    session: Session,
    message: Message,
    products,
    intent_result: IntentResult,
    seller_id: int | None,
    buyer_id: int | None,
    posted_at,
    stats: IngestStats,
) -> None:
    """Insert Inventory (SELLER) or Demand (BUYER) rows per product match."""
    if not products:
        return
    intent = intent_result.intent
    if intent is IntentType.SELLER:
        for pm in products:
            session.add(
                Inventory(
                    message_id=message.id,
                    seller_id=seller_id,
                    brand=pm.canonical.brand or None,
                    product_raw=pm.surface,
                    normalized_product=pm.canonical.canonical_name,
                    posted_at=posted_at,
                    is_active=True,
                )
            )
            stats.inventory_inserted += 1
    elif intent is IntentType.BUYER:
        for pm in products:
            session.add(
                Demand(
                    message_id=message.id,
                    buyer_id=buyer_id,
                    brand=pm.canonical.brand or None,
                    product_raw=pm.surface,
                    normalized_product=pm.canonical.canonical_name,
                    posted_at=posted_at,
                    urgency=intent_result.urgency,
                    is_active=True,
                )
            )
            stats.demand_inserted += 1
    # BROKER / UNKNOWN: no Inventory / Demand row in v1.


__all__ = ["IngestStats", "ingest_messages"]
