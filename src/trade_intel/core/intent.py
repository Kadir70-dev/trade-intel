"""Rule-based intent classifier (P3.6 — v1).

Classifies a WhatsApp message body as BUYER / SELLER / BROKER / UNKNOWN
using a weighted keyword lexicon. Surfaces two orthogonal axes on top:

- **urgency** (HIGH / MEDIUM / LOW) — "urgent", "asap", "today" lift
  HIGH; "tomorrow", "no rush", "when ready" lift LOW; default MEDIUM.
- **is_price_inquiry** — true when LP / PM / best-rate language is
  present. Fires for both buyer and seller side ("send LP" can come
  from either), so it's orthogonal to intent rather than a contributor.

Design
------
- One unified lexicon ``keyword -> [(kind, weight), ...]``. Multi-kind
  is supported so the same keyword can lift several axes at once
  (``"urgent"`` → buyer signal + urgency_high; ``"bridge"`` → broker).
- Multi-word phrases ("looking for", "stock ready", "buyer needed for")
  are first-class. Longest-first alternation in a single compiled regex
  ensures ``"looking buyer for"`` (broker, weight 3) wins over its
  shorter ``"looking"`` / ``"buyer"`` components at the same span.
- Boundaries: ``(?<!\\w)…(?!\\w)`` — same scheme as the product
  extractor, so trailing punctuation like "lp." / "lp," still matches
  and ``"lp"`` doesn't fire inside ``"helpline"``.
- Input is flattened + casefolded once via :func:`normalize_text`.
- Intent winner = highest cumulative weight across the three categories.
  Ties → UNKNOWN. Confidence = winner_weight / total_intent_weight.
- Never raises. Empty / None / whitespace-only / noise-only input →
  :data:`_EMPTY` (UNKNOWN, MEDIUM, 0.0, no price, no signals).

Limitations (v1)
----------------
- No negation handling ("don't need any" still scores BUYER).
- ASCII keyword list only. Arabic intent terms (مطلوب, متوفر) deferred
  until labelled bilingual data exists.
- "have" / "qty" are weighted weakly because they're conversational
  noise as often as they're stock signals. They contribute but don't
  dominate.
- ML upgrade lands later; this layer is the rule-based v1 baseline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal

from trade_intel.db.models.enums import IntentType, Urgency
from trade_intel.normalization.text import casefold_for_match, normalize_text

SignalKind = Literal[
    "buyer",
    "seller",
    "broker",
    "urgency_high",
    "urgency_low",
    "price",
]


@dataclass(frozen=True, slots=True)
class IntentSignal:
    """One keyword match contributing to the classification."""

    keyword: str
    kind: SignalKind
    weight: float
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class IntentResult:
    """Result of :func:`classify_intent`."""

    intent: IntentType
    urgency: Urgency
    confidence: float
    is_price_inquiry: bool
    buyer_score: float
    seller_score: float
    broker_score: float
    signals: tuple[IntentSignal, ...]


# ---------------------------------------------------------------- lexicon

_LEXICON: dict[str, list[tuple[SignalKind, float]]] = {}


def _add(keyword: str, kind: SignalKind, weight: float) -> None:
    """Register ``keyword`` under ``(kind, weight)``. Multi-kind is allowed."""
    key = casefold_for_match(keyword)
    if not key:
        return
    _LEXICON.setdefault(key, []).append((kind, weight))


def _build_buyer() -> None:
    strong = (
        "need",
        "needs",
        "needed",
        "needing",
        "need qty",
        "req",
        "reqd",
        "required",
        "require",
        "requirement",
        "want",
        "wants",
        "wanted",
        "wanting",
        "want qty",
        "looking for",
        "looking",
        "lookin for",
        "lookin",
        "searching for",
        "searching",
        "search for",
        "wanted to buy",
        "want to buy",
        "wtb",
        "anyone has",
        "anyone have",
        "anyone got",
    )
    weak = ("buyer", "buying", "buy", "to buy", "urgent", "asap")
    for kw in strong:
        _add(kw, "buyer", 2.0)
    for kw in weak:
        _add(kw, "buyer", 1.0)


def _build_seller() -> None:
    strong = (
        "available",
        "availability",
        "avail",
        "av",
        "stock ready",
        "in stock",
        "ready stock",
        "have stock",
        "stock available",
        "stocks available",
        "for sale",
        "on sale",
        "wts",
        "fresh stock",
        "new lot",
        "fresh lot",
        "in hand",
        "stock in hand",
        # Wholesale-trader shorthand (UAE Deira / Sharjah / Bur Dubai).
        # MOQ ("Minimum Order Quantity") is essentially seller-only language.
        "moq",
        "min order",
        "minimum order",
        "bulk available",
        "wholesale",
        "wholesale price",
        "price aed",
        "supplier",
        "ready to dispatch",
        "ready to ship",
        "ready for pickup",
    )
    weak = (
        "qty",
        "have",
        "having",
        "got",
        "ready",
        "fresh",
        "stock",
        "stocks",
        "stk",
        "selling",
        "sell",
        "sale",
        "offer",
        "offering",
        "seal",
        "sealed",
        "box pack",
        "boxpack",
        "pcs",
        "bulk",
        "each",
        # UAE grey-market / regional sourcing slang.
        "jv",  # joint-venture / parallel-import stock
        "actv",  # active stock shorthand
        "open box",
        "used",
        "refurb",
        "refurbished",
    )
    for kw in strong:
        _add(kw, "seller", 2.0)
    for kw in weak:
        _add(kw, "seller", 1.0)


def _build_broker() -> None:
    strong3 = (
        "looking buyer for",
        "buyer needed for",
        "buyer required for",
        "looking for buyer",
        "looking seller for",
        "seller needed for",
        "seller required for",
        "looking for seller",
        "have buyer for",
        "have seller for",
    )
    strong2 = ("bridge", "middleman", "intro", "introduce", "commission")
    for kw in strong3:
        _add(kw, "broker", 3.0)
    for kw in strong2:
        _add(kw, "broker", 2.0)


def _build_price() -> None:
    for kw in (
        "lp",
        "last price",
        "pm",
        "price message",
        "pm me",
        "best rate",
        "best price",
        "quote",
        "quotation",
        "send price",
        "send rate",
        "ur price",
        "your price",
    ):
        _add(kw, "price", 1.0)


def _build_urgency() -> None:
    for kw in (
        "urgent",
        "urgently",
        "asap",
        "immediate",
        "immediately",
        "right now",
        "right away",
        "today",
        "by today",
    ):
        _add(kw, "urgency_high", 1.0)
    for kw in (
        "no rush",
        "no hurry",
        "when ready",
        "anytime",
        "later",
        "tomorrow",
        "next week",
    ):
        _add(kw, "urgency_low", 1.0)


_build_buyer()
_build_seller()
_build_broker()
_build_price()
_build_urgency()

LEXICON: Final = MappingProxyType(_LEXICON)

_KEYS: Final[tuple[str, ...]] = tuple(
    sorted(_LEXICON.keys(), key=lambda s: (-len(s), s))
)

_PATTERN: re.Pattern[str] = re.compile(
    r"(?<!\w)(?:" + "|".join(re.escape(k) for k in _KEYS) + r")(?!\w)"
)


_EMPTY: Final = IntentResult(
    intent=IntentType.UNKNOWN,
    urgency=Urgency.MEDIUM,
    confidence=0.0,
    is_price_inquiry=False,
    buyer_score=0.0,
    seller_score=0.0,
    broker_score=0.0,
    signals=(),
)


def classify_intent(text: str | None) -> IntentResult:
    """Classify ``text`` as BUYER / SELLER / BROKER / UNKNOWN.

    Returns :data:`_EMPTY` for None / empty / whitespace-only /
    noise-only input. Never raises.

    Signals are returned in source order (by ``start`` offset). When a
    single keyword has multiple kinds (e.g. ``"urgent"`` = buyer +
    urgency_high), each kind emits its own :class:`IntentSignal` at the
    same span, in the order it was registered.
    """
    if not text:
        return _EMPTY
    flat = normalize_text(text, casefold=True, flatten=True)
    if not flat:
        return _EMPTY

    signals: list[IntentSignal] = []
    buyer = seller = broker = 0.0
    has_high = has_low = False
    is_price = False

    for m in _PATTERN.finditer(flat):
        key = m.group(0)
        for kind, weight in _LEXICON[key]:
            signals.append(
                IntentSignal(
                    keyword=key,
                    kind=kind,
                    weight=weight,
                    start=m.start(),
                    end=m.end(),
                )
            )
            if kind == "buyer":
                buyer += weight
            elif kind == "seller":
                seller += weight
            elif kind == "broker":
                broker += weight
            elif kind == "urgency_high":
                has_high = True
            elif kind == "urgency_low":
                has_low = True
            elif kind == "price":
                is_price = True

    urgency = (
        Urgency.HIGH if has_high
        else (Urgency.LOW if has_low else Urgency.MEDIUM)
    )

    scores: tuple[tuple[IntentType, float], ...] = (
        (IntentType.BUYER, buyer),
        (IntentType.SELLER, seller),
        (IntentType.BROKER, broker),
    )
    total = buyer + seller + broker
    if total == 0:
        intent = IntentType.UNKNOWN
        confidence = 0.0
    else:
        winner_score = max(s for _, s in scores)
        winners = [t for t, s in scores if s == winner_score]
        if len(winners) > 1:
            intent = IntentType.UNKNOWN
            confidence = 0.0
        else:
            intent = winners[0]
            confidence = winner_score / total

    return IntentResult(
        intent=intent,
        urgency=urgency,
        confidence=confidence,
        is_price_inquiry=is_price,
        buyer_score=buyer,
        seller_score=seller,
        broker_score=broker,
        signals=tuple(signals),
    )


__all__ = [
    "LEXICON",
    "IntentResult",
    "IntentSignal",
    "SignalKind",
    "classify_intent",
]
