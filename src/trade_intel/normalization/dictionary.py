"""Authoritative product alias dictionary (P3.5).

Maps trader slang to canonical product names for the UAE wholesale
electronics market. The dictionary covers iPhone, iPad, and a generic
"tablets" bucket — the families that dominate the seed corpus. Other
families (Apple Watch, MacBook, AirPods, Samsung Galaxy) will land in
subsequent expansion passes once real data justifies the entries.

Design
------
- **Two-layer:** :class:`CanonicalProduct` is the *typed* identity of a
  product (canonical name, brand, family, model, ``generic`` flag).
  :data:`ALIASES` is the *flat* map ``casefolded_alias -> product``.
  The extractor in :mod:`trade_intel.normalization.products` compiles
  this map into a single Aho-Corasick-style regex alternation.
- **Pre-expanded:** every variant is registered at module import time.
  We pay the build cost once; extraction is then a single regex sweep.
- **Collision-checked:** :func:`_register` raises if the same alias maps
  to two different products — catches dictionary bugs at import.
- **Casefolded keys:** all aliases are stored via
  :func:`casefold_for_match` so the extractor can match against
  casefolded text directly (no per-match casefolding).
- **No bare-number aliases:** ``"12"`` alone is not registered as an
  iPhone — it's far too ambiguous in a chat that also talks about qty,
  storage, and price.

Slang families covered
----------------------
- **iPhone Pro Max:** ``12pm``, ``15 pm``, ``13 promax``, ``iphone15pm``…
- **iPhone Pro:** ``12p``, ``15 pro``, ``iphone12pro``…
- **iPhone Plus (14+):** ``14 plus``, ``14+``, ``iphone15plus``…
- **iPhone Mini (12, 13):** ``12 mini``, ``13mini``…
- **iPhone base:** ``iphone 12``, ``iphone15``…
- **iPad Mini 1-7:** ``mini 5``, ``mini five``, ``ipad mini 5``…
- **iPad Air 1-6:** ``air 5``, ``air five``, ``ipad air 5``…
- **iPad Pro M1-M4:** ``pro m1``, ``ipad pro m2``…
- **Generic Tablets:** ``tabs``, ``tab``, ``tablet``, ``tablets``
  (``generic=True``).

Fuzzy fallback (mentioned in ARCHITECTURE.md) is intentionally NOT in
v1 — fuzzy matches on 3-4 char slang have too many false positives.
Land it once we have enough labelled data to set a threshold.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from trade_intel.normalization.text import casefold_for_match


@dataclass(frozen=True, slots=True)
class CanonicalProduct:
    """Typed identity of a product."""

    canonical_name: str
    brand: str
    family: str
    model: str | None = None
    generic: bool = False


# ---------------------------------------------------------------- registry

_PRODUCTS: list[CanonicalProduct] = []
_ALIAS_MAP: dict[str, CanonicalProduct] = {}


def _register(aliases: list[str], product: CanonicalProduct) -> None:
    """Register ``product`` under each of ``aliases``.

    Raises ``ValueError`` if an alias is already bound to a different
    product (catches dictionary bugs at import time).
    """
    if product not in _PRODUCTS:
        _PRODUCTS.append(product)
    for alias in aliases:
        key = casefold_for_match(alias)
        if not key:
            continue
        existing = _ALIAS_MAP.get(key)
        if existing is not None and existing is not product:
            raise ValueError(
                f"Alias collision: {key!r} → "
                f"{existing.canonical_name!r} vs {product.canonical_name!r}"
            )
        _ALIAS_MAP[key] = product


# ---------------------------------------------------------------- iPhones

_IPHONE_GENS: Final[tuple[int, ...]] = (11, 12, 13, 14, 15, 16)
_IPHONE_PLUS_GENS: Final[tuple[int, ...]] = (14, 15, 16)
_IPHONE_MINI_GENS: Final[tuple[int, ...]] = (12, 13)


def _build_iphones() -> None:
    for gen in _IPHONE_GENS:
        _register(
            [f"iphone {gen}", f"iphone{gen}"],
            CanonicalProduct(
                canonical_name=f"iPhone {gen}",
                brand="Apple",
                family="iPhone",
                model=str(gen),
            ),
        )
        _register(
            [
                f"iphone {gen} pro max",
                f"iphone {gen} promax",
                f"iphone {gen} pm",
                f"iphone{gen} pm",
                f"iphone{gen}pm",
                f"iphone{gen} pro max",
                f"iphone{gen}promax",
                f"{gen} pro max",
                f"{gen} promax",
                f"{gen} pm",
                f"{gen}pm",
                f"{gen}promax",
            ],
            CanonicalProduct(
                canonical_name=f"iPhone {gen} Pro Max",
                brand="Apple",
                family="iPhone",
                model=f"{gen} Pro Max",
            ),
        )
        _register(
            [
                f"iphone {gen} pro",
                f"iphone {gen}p",
                f"iphone{gen} pro",
                f"iphone{gen}pro",
                f"iphone{gen}p",
                f"{gen} pro",
                f"{gen}pro",
                f"{gen}p",
            ],
            CanonicalProduct(
                canonical_name=f"iPhone {gen} Pro",
                brand="Apple",
                family="iPhone",
                model=f"{gen} Pro",
            ),
        )

    for gen in _IPHONE_PLUS_GENS:
        _register(
            [
                f"iphone {gen} plus",
                f"iphone{gen} plus",
                f"iphone{gen}plus",
                f"{gen} plus",
                f"{gen}plus",
                f"{gen}+",
            ],
            CanonicalProduct(
                canonical_name=f"iPhone {gen} Plus",
                brand="Apple",
                family="iPhone",
                model=f"{gen} Plus",
            ),
        )

    for gen in _IPHONE_MINI_GENS:
        _register(
            [
                f"iphone {gen} mini",
                f"iphone{gen} mini",
                f"iphone{gen}mini",
                f"{gen} mini",
                f"{gen}mini",
            ],
            CanonicalProduct(
                canonical_name=f"iPhone {gen} Mini",
                brand="Apple",
                family="iPhone",
                model=f"{gen} Mini",
            ),
        )


# ---------------------------------------------------------------- iPads

_NUMBER_WORDS: Final[dict[str, int]] = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
}

_IPAD_MINI_GENS: Final[tuple[int, ...]] = (1, 2, 3, 4, 5, 6, 7)
_IPAD_AIR_GENS: Final[tuple[int, ...]] = (1, 2, 3, 4, 5, 6)
_IPAD_PRO_CHIPS: Final[tuple[str, ...]] = ("m1", "m2", "m3", "m4")


def _word_for(n: int) -> str | None:
    for word, value in _NUMBER_WORDS.items():
        if value == n:
            return word
    return None


def _build_ipads() -> None:
    for gen in _IPAD_MINI_GENS:
        aliases = [
            f"ipad mini {gen}",
            f"mini {gen}",
            f"mini{gen}",
        ]
        word = _word_for(gen)
        if word:
            aliases.extend([f"ipad mini {word}", f"mini {word}"])
        _register(
            aliases,
            CanonicalProduct(
                canonical_name=f"iPad Mini {gen}",
                brand="Apple",
                family="iPad",
                model=f"Mini {gen}",
            ),
        )

    for gen in _IPAD_AIR_GENS:
        aliases = [
            f"ipad air {gen}",
            f"air {gen}",
            f"air{gen}",
        ]
        word = _word_for(gen)
        if word:
            aliases.extend([f"ipad air {word}", f"air {word}"])
        _register(
            aliases,
            CanonicalProduct(
                canonical_name=f"iPad Air {gen}",
                brand="Apple",
                family="iPad",
                model=f"Air {gen}",
            ),
        )

    for chip in _IPAD_PRO_CHIPS:
        chip_upper = chip.upper()
        _register(
            [
                f"ipad pro {chip}",
                f"ipad pro {chip[0]} {chip[1]}",
                f"pro {chip}",
                f"pro {chip[0]} {chip[1]}",
            ],
            CanonicalProduct(
                canonical_name=f"iPad Pro {chip_upper}",
                brand="Apple",
                family="iPad",
                model=f"Pro {chip_upper}",
            ),
        )


# ---------------------------------------------------------------- generic

def _build_generic() -> None:
    tablets = CanonicalProduct(
        canonical_name="Tablets",
        brand="",
        family="Tablet",
        model=None,
        generic=True,
    )
    _register(["tabs", "tab", "tablet", "tablets"], tablets)


_build_iphones()
_build_ipads()
_build_generic()


# ---------------------------------------------------------------- public

ALIASES: Final = MappingProxyType(_ALIAS_MAP)
PRODUCTS: Final[tuple[CanonicalProduct, ...]] = tuple(_PRODUCTS)


__all__ = [
    "ALIASES",
    "PRODUCTS",
    "CanonicalProduct",
]
