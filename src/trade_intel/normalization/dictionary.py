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
    """Typed identity of a product.

    ``series`` (added P8.7) is a coarse SKU-family key — products that
    share a series are variants of the same model line (e.g. ``iPhone
    15`` / ``iPhone 15 Pro`` / ``iPhone 15 Pro Max`` all share series
    ``"Apple|iPhone|15"``). Used by :func:`~trade_intel.matching.scoring.product_relation_score`
    to floor the semantic score for in-family matches so HashEmbedder
    noise can't tank an obviously-related pair.

    ``category`` is even coarser — phones group together, accessories
    group together, etc. Distinct from ``family`` because the family is
    a brand-specific line ("iPhone" vs "Galaxy"), while the category
    is industry-level ("phone" vs "accessory").
    """

    canonical_name: str
    brand: str
    family: str
    model: str | None = None
    generic: bool = False
    series: str | None = None
    category: str = ""


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
        series = f"Apple|iPhone|{gen}"
        _register(
            [f"iphone {gen}", f"iphone{gen}"],
            CanonicalProduct(
                canonical_name=f"iPhone {gen}",
                brand="Apple",
                family="iPhone",
                model=str(gen),
                series=series,
                category="phone",
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
                series=series,
                category="phone",
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
                series=series,
                category="phone",
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
                series=f"Apple|iPhone|{gen}",
                category="phone",
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
                series=f"Apple|iPhone|{gen}",
                category="phone",
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
                series=f"Apple|iPad|Mini|{gen}",
                category="tablet",
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
                series=f"Apple|iPad|Air|{gen}",
                category="tablet",
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
                series=f"Apple|iPad|Pro|{chip_upper}",
                category="tablet",
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
        category="tablet",
    )
    _register(["tabs", "tab", "tablet", "tablets"], tablets)


def _build_accessories() -> None:
    """Generic accessory buckets — cases / covers / chargers / cables.

    UAE wholesale traders frequently move accessories in bulk ("iPhone
    cases all models, MOQ 20 pcs"). Without a canonical product entry
    these messages skip ``_write_product_rows`` and never land in
    Inventory. Generic flag is set so downstream callers can tell a
    "Phone Cases" line apart from a SKU-specific iPhone 15 Pro Max line.
    """
    cases = CanonicalProduct(
        canonical_name="Phone Cases",
        brand="",
        family="Accessories",
        model=None,
        generic=True,
        category="accessory",
    )
    _register(
        [
            "phone case",
            "phone cases",
            "mobile case",
            "mobile cases",
            "iphone case",
            "iphone cases",
            "case",
            "cases",
        ],
        cases,
    )
    covers = CanonicalProduct(
        canonical_name="Phone Covers",
        brand="",
        family="Accessories",
        model=None,
        generic=True,
        category="accessory",
    )
    _register(
        [
            "phone cover",
            "phone covers",
            "mobile cover",
            "mobile covers",
            "back cover",
            "back covers",
            "iphone cover",
            "iphone covers",
            "cover",
            "covers",
        ],
        covers,
    )
    accessories = CanonicalProduct(
        canonical_name="Accessories",
        brand="",
        family="Accessories",
        model=None,
        generic=True,
        category="accessory",
    )
    _register(
        ["accessory", "accessories", "phone accessories", "mobile accessories"],
        accessories,
    )
    chargers = CanonicalProduct(
        canonical_name="Chargers",
        brand="",
        family="Accessories",
        model=None,
        generic=True,
        category="accessory",
    )
    _register(
        [
            "charger",
            "chargers",
            "charging cable",
            "charging cables",
            "phone charger",
            "phone chargers",
        ],
        chargers,
    )


def _build_android_phones() -> None:
    """Coarse Android brand buckets. Wholesale traders post Android by
    brand + series ("Xiaomi Redmi covers all models") far more often
    than by exact SKU, so a generic brand entry catches the most common
    surface forms. SKU-level expansion can land later when labelled
    data exists.
    """
    xiaomi_redmi = CanonicalProduct(
        canonical_name="Xiaomi Redmi",
        brand="Xiaomi",
        family="Redmi",
        model=None,
        generic=True,
        category="phone",
    )
    _register(
        ["xiaomi redmi", "redmi", "xiaomi", "mi phone", "mi phones"],
        xiaomi_redmi,
    )
    samsung = CanonicalProduct(
        canonical_name="Samsung",
        brand="Samsung",
        family="",
        model=None,
        generic=True,
        category="phone",
    )
    _register(
        ["samsung", "samsung phone", "samsung phones", "galaxy"],
        samsung,
    )
    huawei = CanonicalProduct(
        canonical_name="Huawei",
        brand="Huawei",
        family="",
        model=None,
        generic=True,
        category="phone",
    )
    _register(["huawei", "huawei phone", "huawei phones"], huawei)
    oppo = CanonicalProduct(
        canonical_name="Oppo",
        brand="Oppo",
        family="",
        model=None,
        generic=True,
        category="phone",
    )
    _register(["oppo", "oppo phone", "oppo phones"], oppo)


_build_iphones()
_build_ipads()
_build_generic()
_build_accessories()
_build_android_phones()


# ---------------------------------------------------------------- public

ALIASES: Final = MappingProxyType(_ALIAS_MAP)
PRODUCTS: Final[tuple[CanonicalProduct, ...]] = tuple(_PRODUCTS)


__all__ = [
    "ALIASES",
    "PRODUCTS",
    "CanonicalProduct",
]
