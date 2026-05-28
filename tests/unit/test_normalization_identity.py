"""Unit tests for sender-identity extraction (P3.3)."""

from __future__ import annotations

import pytest

from trade_intel.normalization import (
    BUSINESS_KEYWORDS,
    LOCATION_TOKENS,
    SenderIdentity,
    extract_identity,
    normalize_sender_name,
)

# ---------- basic personal / business / location ----------


def test_plain_personal_name() -> None:
    i = extract_identity("Ali")
    assert i.display_name == "Ali"
    assert i.normalized_name == "ali"
    assert i.phone is None
    assert i.is_business is False
    assert i.is_phone_only is False
    assert i.probable_business_name is None
    assert i.aliases == ("ali",)
    assert i.has_emoji is False


def test_multi_word_personal_name() -> None:
    i = extract_identity("Ali Mohammed")
    assert i.normalized_name == "ali mohammed"
    assert i.is_business is False


def test_business_name_with_mobiles_keyword() -> None:
    i = extract_identity("Ali Mobiles")
    assert i.is_business is True
    assert i.probable_business_name == "Ali Mobiles"
    assert i.normalized_name == "ali mobiles"


def test_business_name_with_llc_suffix() -> None:
    i = extract_identity("Khalid Trading LLC")
    assert i.is_business is True
    assert i.probable_business_name == "Khalid Trading LLC"
    assert "khalid trading llc" in i.aliases


def test_business_name_with_multiple_keywords() -> None:
    i = extract_identity("Dubai Electronics Hub")
    assert i.is_business is True
    assert i.probable_business_name is not None


def test_location_suffix_dxb_stripped_into_alias() -> None:
    i = extract_identity("Sara DXB")
    assert i.normalized_name == "sara dxb"
    assert "sara" in i.aliases  # location-stripped root
    assert "sara dxb" in i.aliases


def test_parenthesized_location_stripped_into_alias() -> None:
    i = extract_identity("Ahmed (Sharjah)")
    # parens squashed in normalized form
    assert i.normalized_name == "ahmed sharjah"
    assert "ahmed" in i.aliases


def test_bracketed_location_stripped_into_alias() -> None:
    i = extract_identity("Faisal [Dubai]")
    assert "faisal" in i.aliases


def test_chained_location_tokens_strip_recursively() -> None:
    i = extract_identity("Ali UAE GCC")
    assert "ali" in i.aliases


# ---------- phone-only / phone-mixed ----------


def test_international_phone_with_plus_canonicalizes() -> None:
    i = extract_identity("+971 50 555 1234")
    assert i.phone == "+971505551234"
    assert i.is_phone_only is True
    assert i.normalized_name == "+971505551234"
    assert i.display_name == "+971 50 555 1234"


def test_phone_with_hyphens_canonicalizes() -> None:
    i = extract_identity("+971-50-555-1234")
    assert i.phone == "+971505551234"
    assert i.is_phone_only is True


def test_phone_with_parens_canonicalizes() -> None:
    i = extract_identity("+971 (50) 555-1234")
    assert i.phone == "+971505551234"


def test_bare_digits_phone_accepted_when_long_enough() -> None:
    i = extract_identity("971555551234")
    assert i.phone == "971555551234"
    assert i.is_phone_only is True


def test_short_digit_run_is_not_a_phone() -> None:
    # 6 digits, no plus → not enough confidence to call it a phone.
    i = extract_identity("123456")
    assert i.phone is None


def test_phone_alternate_formats_share_canonical_phone() -> None:
    a = extract_identity("+971-50-555-1234")
    b = extract_identity("+971 50 555 1234")
    c = extract_identity("+971(50)5551234")
    assert a.phone == b.phone == c.phone == "+971505551234"


def test_business_name_with_embedded_phone_extracts_both() -> None:
    i = extract_identity("Ali Mobiles +971505551234")
    assert i.phone == "+971505551234"
    assert i.is_phone_only is False
    assert i.is_business is True


# ---------- emoji handling ----------


def test_trailing_emoji_stripped_from_normalized_but_kept_in_display() -> None:
    i = extract_identity("Ali 🚀")
    assert i.has_emoji is True
    assert i.display_name == "Ali 🚀"
    assert i.normalized_name == "ali"
    assert "ali" in i.aliases


def test_leading_emoji_business() -> None:
    i = extract_identity("📱 Mobile Hub")
    assert i.has_emoji is True
    assert i.is_business is True
    assert i.probable_business_name == "Mobile Hub"
    assert i.normalized_name == "mobile hub"


def test_pure_emoji_handle_yields_empty_normalized() -> None:
    i = extract_identity("🚀🚀")
    assert i.has_emoji is True
    assert i.display_name == "🚀🚀"
    assert i.normalized_name == ""
    # Emoji is preserved as an alias so downstream can still match it.
    assert "🚀🚀" in i.aliases


def test_zwj_family_emoji_strips_as_a_unit() -> None:
    family = "\U0001f468‍\U0001f469‍\U0001f467"
    i = extract_identity(f"Trader {family}")
    # Emoji block stripped; "Trader" left clean.
    assert i.normalized_name == "trader"
    assert i.has_emoji is True


# ---------- unicode / non-Latin ----------


def test_arabic_name_round_trips() -> None:
    i = extract_identity("محمد عبدالله")
    assert i.display_name == "محمد عبدالله"
    assert i.normalized_name == "محمد عبدالله"  # casefold is no-op for Arabic
    assert "محمد عبدالله" in i.aliases


def test_arabic_business_keyword_not_recognized_as_business() -> None:
    # Business keyword list is ASCII-only by design (P3.5 will expand).
    i = extract_identity("علي موبايلز")
    assert i.is_business is False


def test_full_width_digits_normalize_to_ascii_in_phone() -> None:
    # NFKC turns U+FF11 etc. into ASCII; phone detector then matches.
    i = extract_identity("＋９７１ ５０ ５５５ １２３４")
    assert i.phone == "+971505551234"


# ---------- case-insensitive matching prep ----------


def test_same_name_different_case_yields_same_normalized_key() -> None:
    a = extract_identity("Ali Mobiles")
    b = extract_identity("ali mobiles")
    c = extract_identity("ALI MOBILES")
    assert a.normalized_name == b.normalized_name == c.normalized_name


def test_normalized_form_is_stable_under_whitespace_noise() -> None:
    a = extract_identity("Ali  Mobiles")
    b = extract_identity("Ali\tMobiles")
    c = extract_identity("Ali Mobiles")  # NBSP
    assert a.normalized_name == b.normalized_name == c.normalized_name


def test_punctuation_only_difference_collapses_via_alias() -> None:
    a = extract_identity("Khalid Trading L.L.C")
    b = extract_identity("Khalid Trading LLC")
    # Both produce a punctuation-stripped alias that matches.
    a_aliases = set(a.aliases)
    b_aliases = set(b.aliases)
    assert a_aliases & b_aliases


# ---------- malformed / edge cases ----------


def test_none_input_returns_empty_identity() -> None:
    i = extract_identity(None)
    assert i.display_name is None
    assert i.normalized_name == ""
    assert i.phone is None
    assert i.aliases == ()


def test_empty_string_returns_empty_identity() -> None:
    i = extract_identity("")
    assert i.display_name is None
    assert i.normalized_name == ""


def test_whitespace_only_returns_empty_identity() -> None:
    i = extract_identity("   \t  ")
    assert i.display_name is None
    assert i.normalized_name == ""


def test_only_punctuation_strips_to_empty() -> None:
    i = extract_identity(":::---")
    assert i.normalized_name == ""


def test_extract_identity_never_raises() -> None:
    # Defensive: throw a soup of weird input and make sure nothing blows up.
    for s in [
        "\x00\x01\x02",
        "()()()",
        "+",
        "1",
        "+1",
        "Ali::Mobiles::DXB",
        "🚀" * 100,
        "a" * 1000,
        "\n\n\n",
    ]:
        i = extract_identity(s)
        assert isinstance(i, SenderIdentity)


# ---------- duplicate-aware / merge-safe ----------


def test_same_trader_two_handles_share_an_alias() -> None:
    """'Ali Mobiles' and 'Ali Mobiles DXB' should rendezvous on a shared alias."""
    a = extract_identity("Ali Mobiles")
    b = extract_identity("Ali Mobiles DXB")
    assert "ali mobiles" in a.aliases
    assert "ali mobiles" in b.aliases


def test_same_phone_two_handles_share_phone() -> None:
    a = extract_identity("Ali Mobiles +971505551234")
    b = extract_identity("Different Name +971505551234")
    assert a.phone == b.phone


def test_phone_only_handle_uses_phone_as_normalized_key() -> None:
    i = extract_identity("+971 50 555 1234")
    assert i.normalized_name == i.phone


# ---------- normalize_sender_name shortcut ----------


def test_normalize_sender_name_matches_extract_identity_field() -> None:
    assert normalize_sender_name("Ali 🚀") == extract_identity("Ali 🚀").normalized_name
    assert normalize_sender_name("Ali Mobiles") == "ali mobiles"
    assert normalize_sender_name(None) == ""
    assert normalize_sender_name("") == ""


# ---------- vocabularies are sensible ----------


def test_business_keywords_cover_uae_chat_basics() -> None:
    for kw in ("trading", "mobiles", "llc", "wholesale", "electronics"):
        assert kw in BUSINESS_KEYWORDS


def test_location_tokens_cover_uae_cities() -> None:
    for tok in ("dxb", "dubai", "auh", "sharjah", "ajman"):
        assert tok in LOCATION_TOKENS


@pytest.mark.parametrize(
    "raw,expected_business",
    [
        ("Tablet World", True),
        ("Watch Center", True),
        ("Gadgets International", True),
        ("Just Ali", False),
        ("Sara", False),
    ],
)
def test_business_detection_matrix(raw: str, expected_business: bool) -> None:
    assert extract_identity(raw).is_business is expected_business
