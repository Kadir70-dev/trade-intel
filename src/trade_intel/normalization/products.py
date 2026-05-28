"""Product extraction from message text (P3.5).

Finds product references in a WhatsApp / CSV / Excel message body using
the alias map in :mod:`trade_intel.normalization.dictionary`. Returns
typed :class:`ProductMatch` records the persistence layer (P3.7) can
write into ``inventory.normalized_product`` / ``demand.normalized_product``
and the embedding layer (Phase 4) can use as a candidate-generation key.

Algorithm
---------
1. Flatten + casefold the input via :func:`normalize_text` so NFKC-fold
   (full-width digits → ASCII), invisible-noise strip, and whitespace
   collapse all happen up front. Offsets reported by :class:`ProductMatch`
   are positions in this FLATTENED text, not the original.
2. Sweep a single compiled regex alternation of every alias, sorted
   longest-first so ``"iphone 15 pro max"`` beats ``"15 pro max"`` when
   both could match at the same position.
3. Use ``(?<!\\w)…(?!\\w)`` lookarounds (not ``\\b``) for boundaries —
   handles ``"14+"`` cleanly, which ``\\b`` would miss (the trailing
   ``+`` is a non-word char).
4. Map each hit back to its :class:`CanonicalProduct` via
   :data:`ALIASES`.

Confidence is ``1.0`` for every dict hit in v1 (no fuzzy fallback
yet — see :mod:`trade_intel.normalization.dictionary`). The field exists
so Phase 4 / future fuzzy can lower it without an API change. Use
``match.canonical.generic`` to distinguish a SKU-level hit from a
category-level one (``"tabs"`` → generic Tablets).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from trade_intel.normalization.dictionary import ALIASES, CanonicalProduct
from trade_intel.normalization.text import normalize_text


@dataclass(frozen=True, slots=True)
class ProductMatch:
    """One product reference found in a message body."""

    canonical: CanonicalProduct
    surface: str
    start: int
    end: int
    confidence: float = 1.0


# Longest aliases first so the regex engine prefers them in alternation.
# Tie-break alphabetically for determinism across runs.
_SORTED_KEYS: tuple[str, ...] = tuple(
    sorted(ALIASES.keys(), key=lambda s: (-len(s), s))
)

_PATTERN: re.Pattern[str] = re.compile(
    r"(?<!\w)(?:" + "|".join(re.escape(k) for k in _SORTED_KEYS) + r")(?!\w)"
)


def extract_products(text: str | None) -> list[ProductMatch]:
    """Find every product reference in ``text``.

    Returns matches in source order. Non-overlapping by construction
    (regex consumes its match before scanning forward). Empty / None
    input → ``[]``.
    """
    if not text:
        return []
    flat = normalize_text(text, casefold=True, flatten=True)
    if not flat:
        return []
    matches: list[ProductMatch] = []
    for m in _PATTERN.finditer(flat):
        product = ALIASES.get(m.group(0))
        if product is None:  # pragma: no cover — alphabet of pattern == keys
            continue
        matches.append(
            ProductMatch(
                canonical=product,
                surface=m.group(0),
                start=m.start(),
                end=m.end(),
                confidence=1.0,
            )
        )
    return matches


__all__ = ["ProductMatch", "extract_products"]
