"""Structured-field extractor for WhatsApp / CSV / Excel bodies.

Pulls **storage / quantity / price / condition / location / variant**
out of a free-form trader message so the persistence layer can land
them on the Inventory / Demand row. This is what unlocks the matcher's
spec_fit and evidence_weight signals (post-P8.6 production tuning).

Design:
- Single pass over the casefold-flattened text (the same form intent +
  product extraction work on).
- Each field is independent — missing → ``None``, never raises.
- Storage is normalized to a canonical surface form (``"256GB"`` /
  ``"1TB"``) so downstream comparisons collapse "256gb" / "256 GB" /
  "256" via :func:`compute_spec_fit._norm_storage`.
- Condition handles the UAE wholesale vocabulary: ``sealed`` /
  ``fresh`` / ``new`` / ``used`` / ``open box`` / ``refurb`` /
  ``refurbished``. JV / HK / USA / JPN are regional variants, not
  conditions — they land in the ``variant`` column.
- Price prefers the form ``LP <num>`` / ``AED <num>`` / ``<num> AED``;
  the first plausible hit wins. A trailing ``each`` / ``per pc`` is
  preserved as ``price_raw`` so a unit-vs-bulk price misread is
  diagnosable.
- Quantity prefers explicit markers (``qty 15`` / ``MOQ 20`` /
  ``20 pcs``) over a bare leading digit, because traders post a lot of
  digits (storage, prices, phone numbers) — a bare digit alone is too
  ambiguous to claim as "stock quantity".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from trade_intel.db.models.enums import Condition
from trade_intel.normalization.text import casefold_for_match

# ---------------------------------------------------------------- types


@dataclass(frozen=True, slots=True)
class StructuredFields:
    """All structured cells extracted from a single message body.

    Missing fields stay ``None`` — the caller decides whether to
    persist them as such. ``quantity_raw`` / ``price_raw`` keep the
    surface form so downstream reviewers can audit the inference.
    """

    storage: str | None = None
    quantity: int | None = None
    quantity_raw: str | None = None
    price_aed: float | None = None
    price_raw: str | None = None
    condition: Condition | None = None
    location: str | None = None
    variant: str | None = None


# ---------------------------------------------------------------- vocabularies

# UAE / GCC city tokens — keep small + literal so an offhand "shipping
# from x" doesn't get misread. Order matters only for tie-breaks; first
# hit wins.
_LOCATIONS: tuple[tuple[str, str], ...] = (
    # Long names collapse onto their canonical 3-letter codes so
    # "Sharjah", "shj" → both compare equal under spec_fit's
    # location dimension. Order matters: multi-word + longer-name
    # entries first so "abu dhabi" isn't shadowed by "abu" + "dhabi".
    ("abu dhabi", "auh"),
    ("ras al khaimah", "rak"),
    ("umm al quwain", "uaq"),
    ("bur dubai", "dxb"),
    ("auh", "auh"),
    ("sharjah", "shj"),
    ("shj", "shj"),
    ("ajman", "ajman"),
    ("rak", "rak"),
    ("uaq", "uaq"),
    ("fujairah", "fujairah"),
    ("dubai", "dxb"),
    ("dxb", "dxb"),
    ("deira", "deira"),
)

# JV / HK / USA / JPN / India variants — regional sourcing matters in
# the GCC grey market, so capture as a separate dimension.
_VARIANTS: tuple[tuple[str, str], ...] = (
    ("jv", "JV"),
    ("hk", "HK"),
    ("usa", "USA"),
    ("us version", "USA"),
    ("japan", "JPN"),
    ("jpn", "JPN"),
    ("japn", "JPN"),
    ("india", "IN"),
    ("indian", "IN"),
    ("china", "CN"),
    ("chn", "CN"),
    ("middle east", "ME"),
    ("me version", "ME"),
)

_CONDITION_MAP: tuple[tuple[str, Condition], ...] = (
    # Most specific first — "open box" wins over "open"; "second hand"
    # wins over "hand".
    ("open box", Condition.USED),
    ("openbox", Condition.USED),
    ("opbx", Condition.USED),
    ("second hand", Condition.USED),
    ("refurbished", Condition.REFURB),
    ("refurb", Condition.REFURB),
    ("sealed", Condition.SEALED),
    ("seal", Condition.SEALED),
    ("box pack", Condition.SEALED),
    ("boxpack", Condition.SEALED),
    ("fresh stock", Condition.NEW),
    ("fresh", Condition.NEW),
    ("brand new", Condition.NEW),
    ("new", Condition.NEW),
    ("used", Condition.USED),
)

# ---------------------------------------------------------------- regexes

# Storage: 64 / 128 / 256 / 512 GB or 1 / 2 TB. NBSP-safe via the
# already-flattened input. Trailing GB/TB optional ONLY when the
# number is followed by a storage-like context — we DON'T extract
# bare numbers without a unit. Allow 1-digit for TB ("1tb") but
# require ≥2 digits for GB to avoid catching prices like "5 gb".
_STORAGE_RE = re.compile(
    r"(?<!\w)(?:(\d{2,4})\s*(gb)|(\d{1,2})\s*(tb))(?!\w)",
    re.IGNORECASE,
)

# Quantity: "qty 15" / "MOQ 20" / "15 pcs" / "20 pieces" / "lot of 30".
# Multi-word markers come first so "qty" inside "quantity" doesn't fire.
_QUANTITY_RE = re.compile(
    r"(?<!\w)(?:"
    r"(qty|quantity|moq|lot of|pack of|stock of)\s*[:=]?\s*(\d{1,5})"
    r"|"
    r"(\d{1,5})\s*(pcs|pieces|units|nos)"
    r")(?!\w)",
    re.IGNORECASE,
)

# Price: prefer EXPLICITLY-marked prices (``LP 3200`` / ``AED 3200``)
# over trailing ``<num> AED``. The trailing form is ambiguous in
# wholesale chatter — ``qty 25 AED 1900`` would mis-parse "25" as a
# trailing-AED match. We scan the prefixed form first, fall back to
# the trailing form only when no prefixed hit exists.
_PRICE_PREFIX_RE = re.compile(
    r"(?<!\w)(?:lp|last\s+price|price|aed|@|د\.إ)\s*[:=]?\s*"
    r"(\d{1,6}(?:\.\d{1,2})?)(?!\w)",
    re.IGNORECASE,
)
_PRICE_TRAILING_RE = re.compile(
    r"(?<!\w)(\d{1,6}(?:\.\d{1,2})?)\s*aed(?!\w)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------- helpers


def _extract_storage(text: str) -> str | None:
    m = _STORAGE_RE.search(text)
    if m is None:
        return None
    if m.group(1) is not None:  # "256gb"
        return f"{int(m.group(1))}GB"
    return f"{int(m.group(3))}TB"  # "1tb"


def _extract_quantity(text: str) -> tuple[int | None, str | None]:
    m = _QUANTITY_RE.search(text)
    if m is None:
        return None, None
    if m.group(2) is not None:  # "qty 15" / "MOQ 20"
        try:
            return int(m.group(2)), m.group(0)
        except ValueError:
            return None, None
    if m.group(3) is not None:  # "15 pcs"
        try:
            return int(m.group(3)), m.group(0)
        except ValueError:
            return None, None
    return None, None


def _extract_price(text: str) -> tuple[float | None, str | None]:
    # 1) Prefer explicitly-marked prices ("LP 3200" / "AED 3200").
    for m in _PRICE_PREFIX_RE.finditer(text):
        num = m.group(1)
        try:
            val = float(num)
        except ValueError:
            continue
        # Bare 1-digit integers (no decimal) are almost never prices in
        # AED — they're usually qty / SKU fragments. Decimal kept (e.g.
        # "AED 3.5 each").
        if val < 10.0 and "." not in num:
            continue
        return val, m.group(0)
    # 2) Fall back to trailing form ("3200 AED"). Ambiguous; raise the
    # floor to 100 to avoid catching qty digits.
    for m in _PRICE_TRAILING_RE.finditer(text):
        num = m.group(1)
        try:
            val = float(num)
        except ValueError:
            continue
        if val < 100.0:
            continue
        return val, m.group(0)
    return None, None


def _extract_condition(text: str) -> Condition | None:
    for needle, cond in _CONDITION_MAP:
        if needle in text:
            return cond
    return None


def _extract_location(text: str) -> str | None:
    for needle, canonical in _LOCATIONS:
        if re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", text):
            return canonical
    return None


def _extract_variant(text: str) -> str | None:
    for needle, canonical in _VARIANTS:
        if re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", text):
            return canonical
    return None


# ---------------------------------------------------------------- entrypoint


def extract_structured(text: str | None) -> StructuredFields:
    """Pull every structured cell from one message body.

    Returns an all-``None`` :class:`StructuredFields` for ``None`` /
    empty / whitespace-only input. Never raises.
    """
    if not text or not text.strip():
        return StructuredFields()
    flat = casefold_for_match(text)
    quantity, quantity_raw = _extract_quantity(flat)
    price, price_raw = _extract_price(flat)
    return StructuredFields(
        storage=_extract_storage(flat),
        quantity=quantity,
        quantity_raw=quantity_raw,
        price_aed=price,
        price_raw=price_raw,
        condition=_extract_condition(flat),
        location=_extract_location(flat),
        variant=_extract_variant(flat),
    )


__all__ = ["StructuredFields", "extract_structured"]
