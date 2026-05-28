"""Sender-identity extraction from raw WhatsApp display names.

Phase 3.3 contract: given the raw string between the timestamp and the
first ``": "`` in a WhatsApp header, return a structured
:class:`SenderIdentity` that downstream layers (Phase 3.7 persistence,
Phase 4 matching) can use to deduplicate sellers / buyers across groups
(per DECISIONS.md D-009 — phone-first, name-root fallback).

Design principles
-----------------
- **Future merge-safe**: every disambiguating signal becomes its own
  field. ``phone`` is the authoritative identity when present;
  ``normalized_name`` is the matching key when phone is absent;
  ``aliases`` capture alternative forms (location-stripped, raw,
  emoji-stripped) so cross-group merges can rendezvous on any one.
- **Conflict tolerant**: nothing here raises. Empty input returns an
  empty identity; malformed phone-ish strings → ``phone=None``, not an
  exception.
- **Duplicate-aware**: aliases are casefolded + edge-punctuation-
  stripped + de-duplicated. ``"Ali Mobiles"`` and ``"ali mobiles"``
  produce the same normalized key.
- **Emoji-safe**: emojis are preserved in ``raw`` and ``display_name``
  but stripped from ``normalized_name`` / ``aliases`` so ``"Ali 🚀"``
  still matches ``"Ali"``.
- **Unicode-safe**: Arabic and other non-Latin handles round-trip via
  NFKC + Unicode case-folding (which is a no-op for non-cased scripts).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from trade_intel.normalization.text import normalize_text

# ---------------------------------------------------------------- regexes


# Phone-number-shaped run. Negative-width assertions on both sides keep
# us from matching "12" in "iPhone 12" or "3200" in "AED 3200".
_PHONE_RE = re.compile(
    r"(?<![A-Za-z\d])(\+?\d[\d\s\-().]{6,18}\d)(?![A-Za-z\d])"
)

# Whatever else is in the string after the phone is stripped — if any
# letter survives, it's not a phone-only handle.
_PHONE_SHAPE_RE = re.compile(r"^[\+\d\s\-().]+$")

# Pictographic / dingbat / regional-indicator code points considered
# "emoji". ZWJ + variation selectors are included so a complete emoji
# sequence (👨‍👩‍👧, 1️⃣) strips as a unit.
_EMOJI_RE = re.compile(
    "["
    "\U0001f300-\U0001f5ff"
    "\U0001f600-\U0001f64f"
    "\U0001f680-\U0001f6ff"
    "\U0001f700-\U0001f77f"
    "\U0001f780-\U0001f7ff"
    "\U0001f800-\U0001f8ff"
    "\U0001f900-\U0001f9ff"
    "\U0001fa00-\U0001fa6f"
    "\U0001fa70-\U0001faff"
    "☀-⛿"
    "✀-➿"
    "\U0001f1e6-\U0001f1ff"
    "︎-️"
    "‍"
    "]+"
)

# Punctuation we strip from the *edges* of a name. Internal hyphens stay
# (handles like ``"Al-Futtaim"``); edge ones go.
_EDGE_PUNCT = " -_()[]{}<>'\"|,;.:!?"

# Bracket / paren characters we squash from the *middle* of a normalized
# name so ``"Ahmed (Sharjah)"`` and ``"Ahmed Sharjah"`` match.
_BRACKET_RE = re.compile(r"[()\[\]{}<>]")


# ---------------------------------------------------------------- vocabularies


BUSINESS_KEYWORDS: frozenset[str] = frozenset(
    {
        "trading",
        "mobiles",
        "mobile",
        "electronics",
        "electronic",
        "llc",
        "wll",
        "fz",
        "fze",
        "fzco",
        "fzc",
        "co",
        "company",
        "group",
        "store",
        "shop",
        "world",
        "hub",
        "center",
        "centre",
        "phones",
        "phone",
        "computers",
        "computer",
        "wholesale",
        "traders",
        "trader",
        "import",
        "imports",
        "export",
        "exports",
        "exchange",
        "tech",
        "technology",
        "tablets",
        "tablet",
        "watches",
        "watch",
        "accessories",
        "accessory",
        "gadgets",
        "gadget",
        "communications",
        "communication",
        "telecom",
        "international",
        "global",
        "supplies",
        "supply",
        "enterprise",
        "enterprises",
        "corporation",
        "ltd",
        "limited",
        "inc",
    }
)
_BUSINESS_RE = re.compile(
    r"\b(?:"
    + "|".join(re.escape(kw) for kw in sorted(BUSINESS_KEYWORDS, key=len, reverse=True))
    + r")\b",
    re.IGNORECASE,
)


LOCATION_TOKENS: frozenset[str] = frozenset(
    {
        "dxb",
        "dubai",
        "auh",
        "abudhabi",
        "abu dhabi",
        "shj",
        "sharjah",
        "ajm",
        "ajman",
        "rak",
        "fuj",
        "fujairah",
        "uaq",
        "uae",
        "gcc",
        "ksa",
        "saudi",
        "oman",
        "muscat",
        "doha",
        "qatar",
        "bahrain",
        "kuwait",
        "deira",
        "burdubai",
    }
)
# Matches "<delim>+ <loc> <trailing-delim>* <end>". Requires at least
# one prefix delimiter so ``"AliDXB"`` (no separator) is left alone.
_LOCATION_TRAILER_RE = re.compile(
    r"[\s\-_(\[]+(?:"
    + "|".join(sorted((re.escape(t) for t in LOCATION_TOKENS), key=len, reverse=True))
    + r")[\s)\]_-]*$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------- dataclass


@dataclass(slots=True, frozen=True)
class SenderIdentity:
    """Structured identity signals for a WhatsApp sender header."""

    raw: str
    display_name: str | None
    normalized_name: str
    phone: str | None
    probable_business_name: str | None
    aliases: tuple[str, ...]
    is_phone_only: bool
    is_business: bool
    has_emoji: bool


_EMPTY = SenderIdentity(
    raw="",
    display_name=None,
    normalized_name="",
    phone=None,
    probable_business_name=None,
    aliases=(),
    is_phone_only=False,
    is_business=False,
    has_emoji=False,
)


# ---------------------------------------------------------------- public API


def extract_identity(raw: str | None) -> SenderIdentity:
    """Build a :class:`SenderIdentity` from a raw sender display string.

    Never raises. ``None``, empty, or whitespace-only input returns
    :data:`_EMPTY` (an :class:`SenderIdentity` with no signals).
    """
    if not raw or not raw.strip():
        return _EMPTY

    cleaned = normalize_text(raw, flatten=True)
    if not cleaned:
        return SenderIdentity(
            raw=raw,
            display_name=None,
            normalized_name="",
            phone=None,
            probable_business_name=None,
            aliases=(),
            is_phone_only=False,
            is_business=False,
            has_emoji=bool(_EMOJI_RE.search(raw)),
        )

    has_emoji = bool(_EMOJI_RE.search(cleaned))
    phone = _extract_phone(cleaned)
    phone_only = _is_phone_only(cleaned)
    display_name = cleaned or None

    no_emoji = _EMOJI_RE.sub("", cleaned).strip()
    normalized_name = _normalize_for_match(no_emoji)
    # Phone-only headers use the canonical phone as their matching key.
    if phone_only and phone:
        normalized_name = phone

    is_biz = bool(_BUSINESS_RE.search(no_emoji))
    probable_business = (
        _strip_location(no_emoji).strip(_EDGE_PUNCT).strip() if is_biz else None
    ) or None

    aliases = _build_aliases(
        raw=raw,
        cleaned=cleaned,
        no_emoji=no_emoji,
        normalized=normalized_name,
        phone=phone,
    )

    return SenderIdentity(
        raw=raw,
        display_name=display_name,
        normalized_name=normalized_name,
        phone=phone,
        probable_business_name=probable_business,
        aliases=aliases,
        is_phone_only=phone_only,
        is_business=is_biz,
        has_emoji=has_emoji,
    )


def normalize_sender_name(s: str | None) -> str:
    """Casefold + NFKC + emoji-strip a sender name to its matching key."""
    return extract_identity(s).normalized_name


# ---------------------------------------------------------------- internals


def _normalize_for_match(s: str) -> str:
    """Casefold + bracket-squash + whitespace collapse + edge-strip."""
    if not s:
        return ""
    s = _BRACKET_RE.sub(" ", s)
    s = s.casefold()
    s = re.sub(r"\s+", " ", s).strip()
    return s.strip(_EDGE_PUNCT).strip()


def _extract_phone(cleaned: str) -> str | None:
    """Pull a canonical phone string from a cleaned sender name.

    Returns ``+CC...digits...`` when a leading ``+`` is present,
    otherwise a digit-only string. Returns ``None`` if no plausible
    phone is found (too short / too long / no plus and < 10 digits).
    """
    match = _PHONE_RE.search(cleaned)
    if not match:
        return None
    snippet = match.group(0)
    plus = snippet.lstrip().startswith("+")
    digits = re.sub(r"\D", "", snippet)
    if not (7 <= len(digits) <= 15):
        return None
    if not plus and len(digits) < 10:
        # Too short to be confidently an international number without a
        # country code.
        return None
    return ("+" + digits) if plus else digits


def _is_phone_only(cleaned: str) -> bool:
    """True when the sender header is just a phone, no name letters."""
    return bool(_PHONE_SHAPE_RE.fullmatch(cleaned)) if cleaned else False


def _strip_location(name: str) -> str:
    """Recursively trim trailing location tokens.

    ``"Ali DXB"`` → ``"Ali"``;
    ``"Ali (Sharjah)"`` → ``"Ali"``;
    ``"Ali UAE GCC"`` → ``"Ali"`` (two passes).
    """
    prev = ""
    cur = name
    while cur != prev:
        prev = cur
        cur = _LOCATION_TRAILER_RE.sub("", cur).rstrip()
    return cur


def _build_aliases(
    *,
    raw: str,
    cleaned: str,
    no_emoji: str,
    normalized: str,
    phone: str | None,
) -> tuple[str, ...]:
    """Collect every casefolded alternative form a sender might be known by."""
    seen: list[str] = []

    def add(s: str | None) -> None:
        if not s:
            return
        key = _normalize_for_match(s)
        if key and key not in seen:
            seen.append(key)

    add(raw)
    add(cleaned)
    add(no_emoji)
    add(normalized)
    add(_strip_location(no_emoji))
    add(_strip_location(cleaned))
    # Two punctuation variants so we collapse both
    # "Al-Futtaim" ↔ "Al Futtaim" (space substitution)
    # "L.L.C"      ↔ "LLC"        (empty substitution)
    add(re.sub(r"[^\w\s]", " ", no_emoji))
    add(re.sub(r"[^\w\s]", "", no_emoji))
    if phone:
        add(phone)
    return tuple(seen)


__all__ = [
    "BUSINESS_KEYWORDS",
    "LOCATION_TOKENS",
    "SenderIdentity",
    "extract_identity",
    "normalize_sender_name",
]
