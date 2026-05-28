"""Unit tests for trade_intel.normalization.text (P3.2)."""

from __future__ import annotations

import pytest

from trade_intel.normalization import casefold_for_match, normalize_text, strip_noise

# ---------- NFKC ----------


def test_full_width_digits_collapse_to_ascii() -> None:
    # "２５６GB" → "256GB"
    assert normalize_text("１２pm　２５６GB") == "12pm 256GB"


def test_ligature_fi_decomposes() -> None:
    # U+FB01 LATIN SMALL LIGATURE FI → "fi"
    assert normalize_text("ﬁnal price") == "final price"


def test_combining_marks_canonicalize() -> None:
    # "café" composed vs decomposed → same NFKC form
    composed = normalize_text("café")
    decomposed = normalize_text("café")
    assert composed == decomposed == "café"


# ---------- whitespace ----------


def test_nbsp_collapses_to_single_space() -> None:
    assert normalize_text("AED  3200") == "AED 3200"


def test_tabs_and_multiple_spaces_collapse() -> None:
    assert normalize_text("a\t\t  b   c") == "a b c"


def test_em_space_and_ideographic_space_collapse() -> None:
    # U+2003 EM SPACE, U+3000 IDEOGRAPHIC SPACE
    assert normalize_text("a b　c") == "a b c"


def test_newlines_preserved_by_default() -> None:
    assert normalize_text("line1\nline2") == "line1\nline2"


def test_repeated_newlines_collapse_to_one() -> None:
    assert normalize_text("line1\n\n\n\nline2") == "line1\nline2"


def test_flatten_collapses_newlines_into_spaces() -> None:
    assert (
        normalize_text("line1\n\nline2\nline3", flatten=True)
        == "line1 line2 line3"
    )


def test_leading_trailing_blank_lines_dropped() -> None:
    assert normalize_text("\n\nhello\n\n") == "hello"


# ---------- noise stripping ----------


def test_bidi_marks_stripped() -> None:
    # U+200E LRM, U+200F RLM, U+2066 LRI, U+2069 PDI
    assert normalize_text("‎a‏b‪c‬d⁦e⁩f") == "abcdef"


def test_bom_stripped() -> None:
    assert normalize_text("﻿hello") == "hello"


def test_soft_hyphen_stripped() -> None:
    assert normalize_text("co­moperate") == "comoperate"


def test_zero_width_space_stripped() -> None:
    assert normalize_text("foo​bar") == "foobar"


# ---------- emoji safety ----------


def test_single_emoji_preserved() -> None:
    assert "🚀" in normalize_text("ready 🚀")


def test_zwj_emoji_sequence_preserved() -> None:
    # 👨‍👩‍👧 = MAN + ZWJ + WOMAN + ZWJ + GIRL — must NOT be split.
    family = "\U0001f468‍\U0001f469‍\U0001f467"
    out = normalize_text(f"hi {family} bye")
    assert family in out, "ZWJ sequence must survive normalization"


def test_variation_selector_preserved() -> None:
    # 1️⃣ = "1" + VS16 (U+FE0F) + COMBINING ENCLOSING KEYCAP
    keycap_one = "1️⃣"
    out = normalize_text(f"first {keycap_one}!")
    assert keycap_one in out


# ---------- casefold ----------


def test_casefold_lowercases_ascii() -> None:
    assert normalize_text("Hello WORLD", casefold=True) == "hello world"


def test_casefold_handles_german_eszett() -> None:
    # Unicode casefold turns ß into "ss"; .lower() leaves it as "ß".
    assert normalize_text("STRAßE", casefold=True) == "strasse"


def test_casefold_preserves_emoji() -> None:
    assert "🚀" in normalize_text("Launch 🚀 NOW", casefold=True)


def test_casefold_for_match_flattens_and_lowercases() -> None:
    out = casefold_for_match("Hello\n\nWORLD 🚀")
    assert out == "hello world 🚀"


# ---------- edge cases ----------


def test_empty_input_returns_empty() -> None:
    assert normalize_text("") == ""


def test_whitespace_only_input_returns_empty() -> None:
    assert normalize_text("   \n\t\n  ") == ""


def test_only_noise_chars_returns_empty() -> None:
    assert normalize_text("‎‏‪‬") == ""


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("simple", "simple"),
        ("  leading and trailing  ", "leading and trailing"),
        ("emoji 😀 ok", "emoji 😀 ok"),
        ("ARABIC مرحبا  ك", "ARABIC مرحبا ك"),
    ],
)
def test_normalize_matrix(raw: str, expected: str) -> None:
    assert normalize_text(raw) == expected


def test_strip_noise_is_standalone() -> None:
    assert strip_noise("a‎b") == "ab"
    assert strip_noise("plain") == "plain"
