"""Shared row → ParsedMessage synthesis for CSV / Excel inventory sheets (P3.8).

Both :mod:`csv_parser` and :mod:`excel_parser` reduce a tabular row to a
:class:`ParsedMessage` so the existing dedup → classify → extract →
persist pipeline can ingest them with zero changes downstream.

Column synonyms
---------------
:data:`HEADER_SYNONYMS` maps casefolded common header names (``"seller"``,
``"supplier"``, ``"vendor"``, ``"from"``, ``"qty"``, ``"quantity"``,
``"lp"``, ``"last price"``, …) to canonical fields. First match wins
when multiple headers map to the same canonical field.

Body synthesis
--------------
The row is reconstructed as a trader-chat-style string::

    "available qty {qty} {product} {storage} {color} {condition} lp {price} {location}"

Each segment is included only when its source cell is non-empty. The
``"available"`` prefix is always prepended (inventory sheets are
seller-side by default) **unless** the optional ``intent_hint`` cell
matches a buyer phrase (``"buy"``, ``"need"``, ``"demand"``, ``"want"``,
``"req"``), in which case the hint is appended verbatim and the existing
intent classifier picks it up.

The synthesized text then flows through ``classify_intent`` +
``extract_products`` (via :func:`ingest_messages`) exactly like a
WhatsApp message body, so SELLER/BUYER routing, urgency, and product
canonicalization all work without bespoke per-format logic.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Sequence
from datetime import date, datetime

from trade_intel.normalization import SenderIdentity, extract_identity
from trade_intel.normalization.text import casefold_for_match, normalize_text
from trade_intel.parsers.whatsapp import (
    ParsedMessage,
    ParseStatus,
    hash_for,
)

# Casefolded header name → canonical field name.
HEADER_SYNONYMS: dict[str, str] = {
    # sender
    "seller": "sender_name",
    "supplier": "sender_name",
    "vendor": "sender_name",
    "trader": "sender_name",
    "from": "sender_name",
    "source": "sender_name",
    "company": "sender_name",
    "name": "sender_name",
    "shop": "sender_name",
    "owner": "sender_name",
    # phone
    "phone": "sender_phone",
    "phone number": "sender_phone",
    "contact": "sender_phone",
    "mobile": "sender_phone",
    "tel": "sender_phone",
    "whatsapp": "sender_phone",
    "wa": "sender_phone",
    # product
    "product": "product",
    "item": "product",
    "description": "product",
    "model": "product",
    "device": "product",
    "sku": "product",
    "title": "product",
    # qty
    "qty": "qty",
    "quantity": "qty",
    "stock": "qty",
    "count": "qty",
    "pcs": "qty",
    "pieces": "qty",
    "units": "qty",
    # price
    "price": "price",
    "lp": "price",
    "last price": "price",
    "rate": "price",
    "cost": "price",
    "asking": "price",
    "amount": "price",
    "aed": "price",
    # storage
    "storage": "storage",
    "memory": "storage",
    "capacity": "storage",
    "size": "storage",
    "gb": "storage",
    # color
    "color": "color",
    "colour": "color",
    # condition
    "condition": "condition",
    "grade": "condition",
    "status": "condition",
    "quality": "condition",
    # location
    "location": "location",
    "city": "location",
    "region": "location",
    "area": "location",
    # date
    "date": "date",
    "timestamp": "date",
    "time": "date",
    "posted": "date",
    "posted at": "date",
    "posted_at": "date",
    # intent hint (optional column for buyer/demand sheets)
    "intent": "intent_hint",
    "type": "intent_hint",
    "side": "intent_hint",
}

_BUYER_HINT_RE = re.compile(r"\b(buy|need|demand|want|req)", re.IGNORECASE)

_DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%m/%d/%Y %H:%M",
    "%m/%d/%Y",
)


def map_headers(headers: Sequence[object]) -> dict[str, int]:
    """Map a header row to ``canonical_field -> column_index``.

    Falsy cells are skipped. First header to claim a canonical field
    wins (so ``["Seller", "Supplier"]`` binds ``sender_name`` to col 0).
    """
    mapping: dict[str, int] = {}
    for idx, header in enumerate(headers):
        if header is None:
            continue
        key = casefold_for_match(str(header).strip())
        canonical = HEADER_SYNONYMS.get(key)
        if canonical and canonical not in mapping:
            mapping[canonical] = idx
    return mapping


def _cell(row: Sequence[object], idx: int | None) -> str | None:
    """Pull a stringified, stripped cell value; return None if empty/missing."""
    if idx is None or idx < 0 or idx >= len(row):
        return None
    cell = row[idx]
    if cell is None:
        return None
    text = str(cell).strip()
    return text or None


def parse_date_cell(value: object | None) -> datetime | None:
    """Coerce a cell value to a :class:`datetime`, or None.

    Excel typically hands us :class:`datetime` / :class:`date` objects
    directly; CSV gives strings. We try a small set of common formats
    and return None on no match (the hash logic folds in source_file +
    row_no for ts-less records so idempotency still holds).
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    text = str(value).strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _synthesize_body(
    *,
    product: str,
    qty: str | None,
    storage: str | None,
    color: str | None,
    condition: str | None,
    price: str | None,
    location: str | None,
    intent_hint: str | None,
) -> str:
    """Build a trader-chat-style line from structured column values."""
    parts: list[str] = []
    is_buyer_hint = bool(intent_hint and _BUYER_HINT_RE.search(intent_hint))
    if not is_buyer_hint:
        parts.append("available")
    if qty:
        parts.append(f"qty {qty}")
    parts.append(product)
    if storage:
        parts.append(storage)
    if color:
        parts.append(color)
    if condition:
        parts.append(condition)
    if price:
        parts.append(f"lp {price}")
    if location:
        parts.append(location)
    if intent_hint:
        parts.append(intent_hint)
    return " ".join(parts)


def _merge_phone_into_identity(
    identity: SenderIdentity, phone_raw: str | None
) -> SenderIdentity:
    """When the phone lives in its own column, fold it into the identity.

    Tabular sources (CSV / Excel) often split sender name and phone
    across two cells. :func:`extract_identity` of just the name has no
    phone, so the persistence layer's phone-first dedup misses. This
    helper re-emits the identity with the phone (and a phone alias)
    merged in — display_name / normalized_name / business detection are
    left untouched.
    """
    if identity.phone or not phone_raw:
        return identity
    extracted = extract_identity(phone_raw).phone
    if not extracted:
        return identity
    new_aliases = identity.aliases
    if extracted not in new_aliases:
        new_aliases = (*new_aliases, extracted)
    return dataclasses.replace(identity, phone=extracted, aliases=new_aliases)


def row_to_parsed_message(
    row: Sequence[object],
    *,
    mapping: dict[str, int],
    source_file: str,
    group_name: str | None,
    row_no: int,
    default_sender: str | None = None,
) -> ParsedMessage | None:
    """Synthesize a :class:`ParsedMessage` from one tabular row.

    Returns ``None`` when the row has no product-like content (so empty
    or footer rows are silently skipped by the caller).
    """
    product = _cell(row, mapping.get("product"))
    if not product:
        return None

    sender_name = _cell(row, mapping.get("sender_name")) or default_sender
    phone_raw = _cell(row, mapping.get("sender_phone"))
    timestamp = parse_date_cell(
        row[mapping["date"]] if "date" in mapping and mapping["date"] < len(row) else None
    )

    body = _synthesize_body(
        product=product,
        qty=_cell(row, mapping.get("qty")),
        storage=_cell(row, mapping.get("storage")),
        color=_cell(row, mapping.get("color")),
        condition=_cell(row, mapping.get("condition")),
        price=_cell(row, mapping.get("price")),
        location=_cell(row, mapping.get("location")),
        intent_hint=_cell(row, mapping.get("intent_hint")),
    )

    identity = _merge_phone_into_identity(
        extract_identity(sender_name), phone_raw
    )
    sender_phone = identity.phone if identity else None

    msg = ParsedMessage(
        timestamp=timestamp,
        sender_name=sender_name,
        raw_message=body,
        normalized_message=normalize_text(body, casefold=True, flatten=True),
        group_name=group_name,
        message_hash="",
        source_file=source_file,
        is_media=False,
        parse_status=ParseStatus.OK,
        line_no=row_no,
        sender_phone=sender_phone,
        sender_identity=identity,
    )
    msg.message_hash = hash_for(msg)
    return msg


__all__ = [
    "HEADER_SYNONYMS",
    "map_headers",
    "parse_date_cell",
    "row_to_parsed_message",
]
