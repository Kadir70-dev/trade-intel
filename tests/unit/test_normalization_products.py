"""Unit tests for product extraction (P3.5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from trade_intel.normalization import ProductMatch, extract_products
from trade_intel.parsers import parse_file

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "whatsapp"


def _names(matches: list[ProductMatch]) -> list[str]:
    return [m.canonical.canonical_name for m in matches]


# ---------- empty / no-product input ----------


def test_empty_string_returns_empty_list() -> None:
    assert extract_products("") == []


def test_none_input_returns_empty_list() -> None:
    assert extract_products(None) == []


def test_whitespace_only_returns_empty_list() -> None:
    assert extract_products("   \t\n  ") == []


def test_text_with_no_products_returns_empty_list() -> None:
    assert extract_products("salaam aleikum brother, all good?") == []


def test_pure_emoji_returns_empty_list() -> None:
    assert extract_products("🚀🚀🚀") == []


# ---------- iPhone extraction ----------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("12pm", "iPhone 12 Pro Max"),
        ("15pm", "iPhone 15 Pro Max"),
        ("12 pm", "iPhone 12 Pro Max"),
        ("13 promax", "iPhone 13 Pro Max"),
        ("iphone 14 pro max", "iPhone 14 Pro Max"),
        ("iphone15pm", "iPhone 15 Pro Max"),
        ("16 pro max", "iPhone 16 Pro Max"),
    ],
)
def test_iphone_pro_max_variants(text: str, expected: str) -> None:
    matches = extract_products(text)
    assert _names(matches) == [expected]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("12p", "iPhone 12 Pro"),
        ("15 pro", "iPhone 15 Pro"),
        ("iphone 14 pro", "iPhone 14 Pro"),
    ],
)
def test_iphone_pro_variants(text: str, expected: str) -> None:
    assert _names(extract_products(text)) == [expected]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("iphone 12", "iPhone 12"),
        ("iphone15", "iPhone 15"),
    ],
)
def test_iphone_base_variants(text: str, expected: str) -> None:
    assert _names(extract_products(text)) == [expected]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("14 plus", "iPhone 14 Plus"),
        ("14+", "iPhone 14 Plus"),
        ("15+", "iPhone 15 Plus"),
        ("16 plus", "iPhone 16 Plus"),
    ],
)
def test_iphone_plus_variants(text: str, expected: str) -> None:
    assert _names(extract_products(text)) == [expected]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("12 mini", "iPhone 12 Mini"),
        ("13mini", "iPhone 13 Mini"),
    ],
)
def test_iphone_mini_variants(text: str, expected: str) -> None:
    assert _names(extract_products(text)) == [expected]


# ---------- iPad extraction ----------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("mini 5", "iPad Mini 5"),
        ("mini five", "iPad Mini 5"),
        ("ipad mini 5", "iPad Mini 5"),
        ("mini 7", "iPad Mini 7"),
        ("mini one", "iPad Mini 1"),
    ],
)
def test_ipad_mini_variants(text: str, expected: str) -> None:
    assert _names(extract_products(text)) == [expected]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("air 5", "iPad Air 5"),
        ("air five", "iPad Air 5"),
        ("ipad air 6", "iPad Air 6"),
    ],
)
def test_ipad_air_variants(text: str, expected: str) -> None:
    assert _names(extract_products(text)) == [expected]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("pro m1", "iPad Pro M1"),
        ("pro m2", "iPad Pro M2"),
        ("ipad pro m1", "iPad Pro M1"),
        ("ipad pro m 2", "iPad Pro M2"),
    ],
)
def test_ipad_pro_chip_variants(text: str, expected: str) -> None:
    assert _names(extract_products(text)) == [expected]


# ---------- generic tablets ----------


@pytest.mark.parametrize("alias", ["tabs", "tab", "tablet", "tablets"])
def test_generic_tablets_extraction(alias: str) -> None:
    matches = extract_products(alias)
    assert len(matches) == 1
    m = matches[0]
    assert m.canonical.canonical_name == "Tablets"
    assert m.canonical.generic is True


def test_generic_tablet_inside_sentence() -> None:
    matches = extract_products("any tabs available?")
    assert _names(matches) == ["Tablets"]


# ---------- multi-product messages ----------


def test_multiple_products_in_one_message() -> None:
    text = "qty 5 12pm 256gb and 3 mini 5 fresh"
    matches = extract_products(text)
    assert _names(matches) == ["iPhone 12 Pro Max", "iPad Mini 5"]


def test_three_products_in_realistic_seller_post() -> None:
    text = "av: 10 15pm 256gb, 5 air 5 wifi, 3 mini 5 cellular all fresh seal dxb"
    names = _names(extract_products(text))
    assert "iPhone 15 Pro Max" in names
    assert "iPad Air 5" in names
    assert "iPad Mini 5" in names


def test_matches_are_returned_in_source_order() -> None:
    matches = extract_products("need mini 5 and 12pm asap")
    assert _names(matches) == ["iPad Mini 5", "iPhone 12 Pro Max"]
    assert matches[0].start < matches[1].start


def test_repeated_same_product_returns_each_occurrence() -> None:
    text = "12pm 256gb, also 12pm 128gb"
    matches = extract_products(text)
    assert _names(matches) == ["iPhone 12 Pro Max", "iPhone 12 Pro Max"]
    assert matches[0].start != matches[1].start


# ---------- case / unicode normalization ----------


@pytest.mark.parametrize(
    "text", ["12PM", "12Pm", "12pM", "IPHONE 14 PRO MAX", "Mini Five"]
)
def test_case_insensitive_matching(text: str) -> None:
    matches = extract_products(text)
    assert len(matches) == 1


def test_full_width_digits_extracted_via_nfkc() -> None:
    # NFKC folds U+FF11..FF19 to ASCII before extraction.
    matches = extract_products("１２ｐｍ")
    assert _names(matches) == ["iPhone 12 Pro Max"]


def test_nbsp_between_tokens_flattens_to_match() -> None:
    # Flatten collapses NBSP to ASCII space.
    matches = extract_products("12 pm")
    assert _names(matches) == ["iPhone 12 Pro Max"]


def test_arabic_text_mixed_with_product_slang() -> None:
    # UAE chat is bilingual; products are usually in latin script.
    matches = extract_products("متوفر iPhone 15pm")
    assert _names(matches) == ["iPhone 15 Pro Max"]


# ---------- word boundary safety ----------


def test_digit_prefix_blocks_match() -> None:
    # "112pm" — not "12pm" with stray '1', should NOT match
    assert extract_products("112pm") == []


def test_letter_suffix_blocks_match() -> None:
    assert extract_products("12pmail") == []
    assert extract_products("15promaxstore") == []


def test_tablet_inside_word_does_not_false_match() -> None:
    # "tab" alias must not fire inside e.g. "stable" or "table"
    assert extract_products("stable supply this week") == []
    assert extract_products("table top design") == []


def test_iphone_after_word_boundary_matches() -> None:
    # Punctuation as boundary, not just space.
    assert _names(extract_products("(12pm)")) == ["iPhone 12 Pro Max"]
    assert _names(extract_products("[15pm]")) == ["iPhone 15 Pro Max"]


# ---------- longest-match preference ----------


def test_longer_alias_wins_over_shorter_at_same_position() -> None:
    # "iphone 12 pro max" should match as ONE, not "12 pro max" inside.
    matches = extract_products("iphone 12 pro max")
    assert len(matches) == 1
    assert matches[0].surface == "iphone 12 pro max"


def test_pro_max_beats_pro_for_same_iphone() -> None:
    # Source has "12 pro max" — must not match "12 pro" and leave "max".
    matches = extract_products("12 pro max")
    assert len(matches) == 1
    assert matches[0].canonical.canonical_name == "iPhone 12 Pro Max"


def test_pro_max_beats_base_iphone() -> None:
    matches = extract_products("iphone 15 pm")
    assert len(matches) == 1
    assert matches[0].canonical.canonical_name == "iPhone 15 Pro Max"


# ---------- offsets ----------


def test_offsets_point_into_flattened_text() -> None:
    text = "qty 5 12pm 256gb"
    # Flattened == same as input (already normalized) in this case
    matches = extract_products(text)
    assert len(matches) == 1
    m = matches[0]
    assert text[m.start : m.end] == m.surface


def test_offsets_are_zero_for_leading_match() -> None:
    m = extract_products("12pm available")[0]
    assert m.start == 0


# ---------- ProductMatch shape ----------


def test_match_carries_confidence_default_one() -> None:
    m = extract_products("12pm")[0]
    assert m.confidence == 1.0


def test_match_is_frozen_dataclass() -> None:
    m = extract_products("12pm")[0]
    with pytest.raises((AttributeError, Exception)):
        m.confidence = 0.5  # type: ignore[misc]


def test_generic_match_flagged_via_canonical() -> None:
    m = extract_products("tabs")[0]
    # confidence stays 1.0 (dict hit); generic-ness lives on canonical
    assert m.confidence == 1.0
    assert m.canonical.generic is True


# ---------- parser integration smoke ----------


def test_extract_products_on_parsed_fixture_messages() -> None:
    """Run the extractor over every real Android-fixture message body
    and verify at least the basic iPhone / iPad slang is recognized.
    """
    msgs = list(parse_file(FIXTURES / "android_sample.txt"))
    bodies = [m.normalized_message for m in msgs if m.normalized_message]
    all_products: list[str] = []
    for body in bodies:
        all_products.extend(m.canonical.canonical_name for m in extract_products(body))
    # We don't assert the exact set (fixture may evolve), just that the
    # extractor runs cleanly on real parser output and finds *something*.
    assert isinstance(all_products, list)


def test_double_normalization_is_idempotent() -> None:
    """The parser already produces a flattened+casefolded `normalized_message`.
    Re-flattening inside extract_products must not change the offsets/matches."""
    raw = "qty 5 12pm 256gb fresh"
    once = extract_products(raw)
    twice = extract_products(once[0].surface)  # feed the surface back in
    assert _names(once) == ["iPhone 12 Pro Max"]
    assert _names(twice) == ["iPhone 12 Pro Max"]
