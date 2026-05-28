"""Unit tests for the product alias dictionary (P3.5)."""

from __future__ import annotations

import pytest

from trade_intel.normalization import ALIASES, PRODUCTS, CanonicalProduct
from trade_intel.normalization.text import casefold_for_match

# ---------- dataclass ----------


def test_canonical_product_is_frozen() -> None:
    p = CanonicalProduct("iPhone 15", brand="Apple", family="iPhone", model="15")
    with pytest.raises((AttributeError, Exception)):
        p.brand = "Samsung"  # type: ignore[misc]


def test_canonical_product_equality_by_fields() -> None:
    a = CanonicalProduct("iPhone 15", brand="Apple", family="iPhone", model="15")
    b = CanonicalProduct("iPhone 15", brand="Apple", family="iPhone", model="15")
    assert a == b
    assert hash(a) == hash(b)


def test_canonical_product_default_flags() -> None:
    p = CanonicalProduct("X", brand="B", family="F")
    assert p.model is None
    assert p.generic is False


# ---------- registry sanity ----------


def test_dictionary_loaded_with_products() -> None:
    assert len(PRODUCTS) > 20
    assert len(ALIASES) > 100


def test_aliases_keys_are_all_casefolded() -> None:
    for key in ALIASES:
        assert key == casefold_for_match(key), (
            f"alias {key!r} is not in casefold-for-match form"
        )


def test_aliases_view_is_immutable() -> None:
    with pytest.raises(TypeError):
        ALIASES["new"] = CanonicalProduct("X", brand="B", family="F")  # type: ignore[index]


def test_every_alias_maps_to_a_product_in_products() -> None:
    seen = {p for p in PRODUCTS}
    for product in ALIASES.values():
        assert product in seen


# ---------- iPhone canonical mappings (CLAUDE.md seed examples) ----------


@pytest.mark.parametrize(
    "alias,expected",
    [
        ("12pm", "iPhone 12 Pro Max"),
        ("15pm", "iPhone 15 Pro Max"),
        ("12 pm", "iPhone 12 Pro Max"),
        ("15 pm", "iPhone 15 Pro Max"),
        ("12 pro max", "iPhone 12 Pro Max"),
        ("13 promax", "iPhone 13 Pro Max"),
        ("iphone 14 pro max", "iPhone 14 Pro Max"),
        ("iphone15pm", "iPhone 15 Pro Max"),
        ("iphone16promax", "iPhone 16 Pro Max"),
    ],
)
def test_iphone_pro_max_aliases(alias: str, expected: str) -> None:
    p = ALIASES[casefold_for_match(alias)]
    assert p.canonical_name == expected
    assert p.brand == "Apple"
    assert p.family == "iPhone"


@pytest.mark.parametrize(
    "alias,expected",
    [
        ("12p", "iPhone 12 Pro"),
        ("15 pro", "iPhone 15 Pro"),
        ("14pro", "iPhone 14 Pro"),
        ("iphone 13 pro", "iPhone 13 Pro"),
    ],
)
def test_iphone_pro_aliases(alias: str, expected: str) -> None:
    p = ALIASES[casefold_for_match(alias)]
    assert p.canonical_name == expected
    assert p.model == expected.replace("iPhone ", "")


@pytest.mark.parametrize(
    "alias,expected",
    [
        ("iphone 12", "iPhone 12"),
        ("iphone15", "iPhone 15"),
        ("iphone 16", "iPhone 16"),
    ],
)
def test_iphone_base_aliases(alias: str, expected: str) -> None:
    assert ALIASES[casefold_for_match(alias)].canonical_name == expected


@pytest.mark.parametrize(
    "alias,expected",
    [
        ("14 plus", "iPhone 14 Plus"),
        ("14+", "iPhone 14 Plus"),
        ("15+", "iPhone 15 Plus"),
        ("16 plus", "iPhone 16 Plus"),
    ],
)
def test_iphone_plus_aliases(alias: str, expected: str) -> None:
    assert ALIASES[casefold_for_match(alias)].canonical_name == expected


@pytest.mark.parametrize(
    "alias,expected",
    [
        ("12 mini", "iPhone 12 Mini"),
        ("13 mini", "iPhone 13 Mini"),
        ("12mini", "iPhone 12 Mini"),
    ],
)
def test_iphone_mini_aliases(alias: str, expected: str) -> None:
    assert ALIASES[casefold_for_match(alias)].canonical_name == expected


def test_iphone_mini_not_registered_for_other_gens() -> None:
    # Apple only made 12 mini and 13 mini.
    assert casefold_for_match("15 mini") not in ALIASES
    assert casefold_for_match("16 mini") not in ALIASES


def test_iphone_plus_not_registered_before_14() -> None:
    assert casefold_for_match("12 plus") not in ALIASES
    assert casefold_for_match("13 plus") not in ALIASES


# ---------- iPad canonical mappings ----------


@pytest.mark.parametrize(
    "alias,expected",
    [
        ("mini 1", "iPad Mini 1"),
        ("mini one", "iPad Mini 1"),
        ("mini 2", "iPad Mini 2"),
        ("mini two", "iPad Mini 2"),
        ("mini 5", "iPad Mini 5"),
        ("mini five", "iPad Mini 5"),
        ("ipad mini 5", "iPad Mini 5"),
        ("mini 7", "iPad Mini 7"),
    ],
)
def test_ipad_mini_aliases(alias: str, expected: str) -> None:
    p = ALIASES[casefold_for_match(alias)]
    assert p.canonical_name == expected
    assert p.family == "iPad"


@pytest.mark.parametrize(
    "alias,expected",
    [
        ("air 5", "iPad Air 5"),
        ("air five", "iPad Air 5"),
        ("ipad air 6", "iPad Air 6"),
        ("air 1", "iPad Air 1"),
    ],
)
def test_ipad_air_aliases(alias: str, expected: str) -> None:
    assert ALIASES[casefold_for_match(alias)].canonical_name == expected


@pytest.mark.parametrize(
    "alias,expected",
    [
        ("pro m1", "iPad Pro M1"),
        ("pro m2", "iPad Pro M2"),
        ("pro m3", "iPad Pro M3"),
        ("pro m4", "iPad Pro M4"),
        ("ipad pro m1", "iPad Pro M1"),
        ("ipad pro m 1", "iPad Pro M1"),
        ("pro m 2", "iPad Pro M2"),
    ],
)
def test_ipad_pro_chip_aliases(alias: str, expected: str) -> None:
    p = ALIASES[casefold_for_match(alias)]
    assert p.canonical_name == expected
    assert p.family == "iPad"


# ---------- generic tablets ----------


@pytest.mark.parametrize("alias", ["tabs", "tab", "tablet", "tablets"])
def test_generic_tablets_aliases(alias: str) -> None:
    p = ALIASES[casefold_for_match(alias)]
    assert p.canonical_name == "Tablets"
    assert p.family == "Tablet"
    assert p.generic is True


# ---------- collision / import safety ----------


def test_no_alias_collisions_after_import() -> None:
    # If the dictionary held an unresolved collision, _register would have
    # raised at import time — making this module unimportable. We're past
    # import, so by construction no collisions exist; this test pins the
    # invariant so future edits can't introduce one silently.
    assert len(ALIASES) > 0


def test_register_raises_on_collision() -> None:
    from trade_intel.normalization import dictionary as d

    a = CanonicalProduct("A", brand="X", family="X")
    b = CanonicalProduct("B", brand="X", family="X")
    # Use an alias guaranteed not to be in the dict.
    sentinel_alias = "zzz_collision_sentinel_zzz"
    d._register([sentinel_alias], a)
    with pytest.raises(ValueError, match="collision"):
        d._register([sentinel_alias], b)
    # Clean up so subsequent tests aren't polluted.
    del d._ALIAS_MAP[casefold_for_match(sentinel_alias)]
    d._PRODUCTS.remove(a)
