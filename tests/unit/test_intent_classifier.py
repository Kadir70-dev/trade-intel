"""Unit tests for the rule-based intent classifier (P3.6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from trade_intel.core.intent import (
    LEXICON,
    IntentResult,
    IntentSignal,
    classify_intent,
)
from trade_intel.db.models.enums import IntentType, Urgency
from trade_intel.parsers import parse_file

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "whatsapp"


# ---------- empty / noise / defensive ----------


def test_none_input_returns_empty_unknown() -> None:
    r = classify_intent(None)
    assert r.intent is IntentType.UNKNOWN
    assert r.urgency is Urgency.MEDIUM
    assert r.confidence == 0.0
    assert r.is_price_inquiry is False
    assert r.signals == ()


def test_empty_string_returns_unknown() -> None:
    r = classify_intent("")
    assert r.intent is IntentType.UNKNOWN
    assert r.signals == ()


def test_whitespace_only_returns_unknown() -> None:
    r = classify_intent("   \t\n  ")
    assert r.intent is IntentType.UNKNOWN


def test_pure_punctuation_returns_unknown() -> None:
    r = classify_intent("!!!???...")
    assert r.intent is IntentType.UNKNOWN


def test_noise_message_returns_unknown() -> None:
    r = classify_intent("ok thx bro")
    assert r.intent is IntentType.UNKNOWN
    assert r.confidence == 0.0


def test_classify_intent_never_raises() -> None:
    for s in [
        "\x00\x01\x02",
        "🚀" * 100,
        "a" * 5000,
        "\n\n\n",
        ":::-----",
        "+",
    ]:
        r = classify_intent(s)
        assert isinstance(r, IntentResult)


# ---------- BUYER ----------


@pytest.mark.parametrize(
    "text",
    [
        "need 5 12pm",
        "needed urgently",
        "needs 5 mini 5",
        "req 3 air 5",
        "required 256gb",
        "want 10 12pm 256gb",
        "wanted to buy 14 pro",
        "wtb iphone 15 pro max",
        "looking for 12pm",
        "lookin for 12pm",
        "searching for mini 5",
        "anyone has 15pm",
        "anyone got tabs",
    ],
)
def test_buyer_keywords_classify_as_buyer(text: str) -> None:
    r = classify_intent(text)
    assert r.intent is IntentType.BUYER, f"{text!r} should be BUYER, got {r}"
    assert r.buyer_score > 0
    assert r.seller_score == 0
    assert r.broker_score == 0


def test_strong_buyer_keyword_gives_full_confidence() -> None:
    r = classify_intent("need 5 12pm")
    assert r.confidence == 1.0


# ---------- SELLER ----------


@pytest.mark.parametrize(
    "text",
    [
        "available 5 12pm",
        "avail mini 5 256gb",
        "stock ready",
        "in stock 10 air 5",
        "have stock 12pm",
        "for sale 5 15pm fresh",
        "wts 14 pro max",
        "fresh stock 256gb",
        "new lot 12pm seal",
        "in hand 3 mini 5",
        "qty 5 12pm 256gb fresh",
        "selling 10 mini 5",
        "offer 5 12pm",
    ],
)
def test_seller_keywords_classify_as_seller(text: str) -> None:
    r = classify_intent(text)
    assert r.intent is IntentType.SELLER, f"{text!r} should be SELLER, got {r}"
    assert r.seller_score > 0
    assert r.buyer_score == 0


def test_strong_seller_keyword_gives_full_confidence() -> None:
    r = classify_intent("available stock ready 12pm fresh")
    assert r.intent is IntentType.SELLER
    assert r.confidence == 1.0


# ---------- BROKER ----------


@pytest.mark.parametrize(
    "text",
    [
        "looking buyer for 12pm",
        "buyer needed for 15pm",
        "buyer required for mini 5",
        "looking for buyer 12pm",
        "looking seller for 256gb",
        "seller needed for tabs",
        "have buyer for 12pm",
        "have seller for air 5",
        "bridge 5 12pm",
        "middleman commission",
    ],
)
def test_broker_phrases_classify_as_broker(text: str) -> None:
    r = classify_intent(text)
    assert r.intent is IntentType.BROKER, f"{text!r} should be BROKER, got {r}"
    assert r.broker_score > 0


def test_broker_phrase_beats_constituent_buyer_seller_keywords() -> None:
    """'looking buyer for' (broker, weight 3) should win over the
    'looking' + 'buyer' breakdown that would otherwise score buyer."""
    r = classify_intent("looking buyer for 12pm")
    assert r.intent is IntentType.BROKER
    # The matched span is consumed as one phrase; no spurious buyer signal.
    kinds = {s.kind for s in r.signals}
    assert "buyer" not in kinds
    assert "broker" in kinds


def test_buyer_needed_for_beats_buyer_keyword() -> None:
    r = classify_intent("buyer needed for 15pm asap")
    assert r.intent is IntentType.BROKER
    # 'asap' still emits buyer + urgency_high — but broker (3) outscores buyer (1).
    assert r.urgency is Urgency.HIGH


# ---------- urgency ----------


@pytest.mark.parametrize(
    "text",
    [
        "need 12pm urgent",
        "need 12pm urgently",
        "need 12pm asap",
        "need immediate 12pm",
        "need 12pm right now",
        "need 12pm today",
    ],
)
def test_high_urgency_keywords(text: str) -> None:
    r = classify_intent(text)
    assert r.urgency is Urgency.HIGH


@pytest.mark.parametrize(
    "text",
    [
        "need 12pm no rush",
        "need 12pm tomorrow",
        "need 12pm later",
        "need 12pm when ready",
        "need 12pm anytime",
        "need 12pm no hurry",
    ],
)
def test_low_urgency_keywords(text: str) -> None:
    r = classify_intent(text)
    assert r.urgency is Urgency.LOW


def test_high_urgency_wins_over_low() -> None:
    r = classify_intent("need 12pm urgent but tomorrow ok")
    assert r.urgency is Urgency.HIGH


def test_default_urgency_is_medium() -> None:
    r = classify_intent("need 12pm")
    assert r.urgency is Urgency.MEDIUM


def test_urgency_works_without_intent_keywords() -> None:
    """Pure urgency keywords without intent terms → UNKNOWN + LOW."""
    r = classify_intent("no rush mini 5 later")
    assert r.intent is IntentType.UNKNOWN
    assert r.urgency is Urgency.LOW


# ---------- price inquiry ----------


@pytest.mark.parametrize(
    "text",
    [
        "lp",
        "last price?",
        "pm",
        "pm me",
        "price message",
        "best rate",
        "best price",
        "send price",
        "quote",
        "ur price",
    ],
)
def test_price_inquiry_keywords_set_flag(text: str) -> None:
    assert classify_intent(text).is_price_inquiry is True


def test_price_only_message_is_unknown_intent() -> None:
    r = classify_intent("lp")
    assert r.intent is IntentType.UNKNOWN
    assert r.is_price_inquiry is True


def test_price_inquiry_orthogonal_to_buyer() -> None:
    r = classify_intent("need 12pm lp")
    assert r.intent is IntentType.BUYER
    assert r.is_price_inquiry is True


def test_price_inquiry_orthogonal_to_seller() -> None:
    r = classify_intent("available 5 12pm pm me best rate")
    assert r.intent is IntentType.SELLER
    assert r.is_price_inquiry is True


def test_no_price_default() -> None:
    assert classify_intent("need 12pm").is_price_inquiry is False


# ---------- ties → UNKNOWN ----------


def test_tied_buyer_seller_scores_yield_unknown() -> None:
    r = classify_intent("need available")  # 2 vs 2
    assert r.intent is IntentType.UNKNOWN
    assert r.confidence == 0.0
    assert r.buyer_score == r.seller_score > 0


def test_higher_score_wins_with_confidence_proportion() -> None:
    """3 seller signal weight vs 2 buyer → SELLER, conf = 3/5 = 0.6."""
    r = classify_intent("need available qty")
    assert r.intent is IntentType.SELLER
    assert r.confidence == pytest.approx(3 / 5)


def test_confidence_one_when_all_signals_one_intent() -> None:
    r = classify_intent("need need need")
    assert r.confidence == 1.0


# ---------- longest-match preference within combined regex ----------


def test_need_qty_matches_as_buyer_not_seller_qty() -> None:
    """'need qty' is registered as one buyer phrase (weight 2). It must
    NOT also fire 'qty' (seller weight 1) within the same span."""
    r = classify_intent("need qty 5 12pm")
    assert r.intent is IntentType.BUYER
    assert r.seller_score == 0
    # Just the 'need qty' signal.
    assert len(r.signals) == 1
    assert r.signals[0].keyword == "need qty"


def test_stock_ready_matches_as_one_phrase() -> None:
    r = classify_intent("stock ready 5 12pm")
    # Single phrase 'stock ready' (weight 2), not 'stock' (1) + 'ready' (1).
    assert r.seller_score == 2
    assert len(r.signals) == 1


def test_last_price_matches_as_one_phrase() -> None:
    r = classify_intent("last price please")
    # Just one price signal, not two.
    price_signals = [s for s in r.signals if s.kind == "price"]
    assert len(price_signals) == 1
    assert price_signals[0].keyword == "last price"


# ---------- multi-kind keywords ----------


def test_urgent_emits_buyer_and_urgency_signals() -> None:
    r = classify_intent("urgent")
    kinds = {s.kind for s in r.signals}
    assert "buyer" in kinds
    assert "urgency_high" in kinds
    assert r.urgency is Urgency.HIGH


def test_asap_emits_buyer_and_urgency_signals() -> None:
    r = classify_intent("asap")
    kinds = {s.kind for s in r.signals}
    assert "buyer" in kinds
    assert "urgency_high" in kinds


# ---------- word boundary safety ----------


@pytest.mark.parametrize(
    "text",
    [
        "needle and thread",       # 'need' inside 'needle'
        "stable supply",            # 'stab'/'stock'... actually 'stable' has no keyword
        "helpline 555 today asap",  # 'lp' inside 'helpline' — but 'today'/'asap' would fire
    ],
)
def test_word_boundary_blocks_false_positives_inside_words(text: str) -> None:
    r = classify_intent(text)
    # We're testing that 'lp' / 'need' don't fire INSIDE other words.
    # 'helpline' shouldn't yield is_price_inquiry, but 'today' + 'asap' will
    # still set urgency high and fire buyer signals — that's expected.
    for s in r.signals:
        assert s.keyword != "lp"
        # 'need' shouldn't appear from 'needle'
        if "needle" in text:
            assert s.keyword != "need"


def test_lp_inside_helpline_does_not_fire_price() -> None:
    r = classify_intent("helpline")
    assert r.is_price_inquiry is False


def test_need_inside_needle_does_not_fire_buyer() -> None:
    r = classify_intent("needle")
    assert r.buyer_score == 0


def test_keyword_with_trailing_punctuation_still_matches() -> None:
    r = classify_intent("need.")
    assert r.intent is IntentType.BUYER


def test_keyword_after_parenthesis_matches() -> None:
    r = classify_intent("(urgent) 12pm need")
    assert r.intent is IntentType.BUYER
    assert r.urgency is Urgency.HIGH


# ---------- case / unicode ----------


@pytest.mark.parametrize("text", ["NEED 12PM", "Need 12pm", "nEeD"])
def test_case_insensitive_matching(text: str) -> None:
    assert classify_intent(text).intent is IntentType.BUYER


def test_full_width_digits_in_message_do_not_break_classifier() -> None:
    r = classify_intent("need １２ｐｍ urgent")
    assert r.intent is IntentType.BUYER
    assert r.urgency is Urgency.HIGH


def test_arabic_text_returns_unknown_without_crashing() -> None:
    """ASCII-only lexicon — Arabic intent terms aren't recognized."""
    r = classify_intent("متوفر iPhone 15pm")
    assert r.intent is IntentType.UNKNOWN
    assert r.signals == ()


def test_emoji_in_message_does_not_break_classifier() -> None:
    r = classify_intent("need 12pm 🚀 asap")
    assert r.intent is IntentType.BUYER
    assert r.urgency is Urgency.HIGH


# ---------- signal ordering + structure ----------


def test_signals_are_in_source_order() -> None:
    r = classify_intent("need 5 12pm asap")
    starts = [s.start for s in r.signals]
    assert starts == sorted(starts)


def test_signals_carry_offset_and_weight() -> None:
    r = classify_intent("need 12pm")
    sig = next(s for s in r.signals if s.kind == "buyer")
    assert sig.keyword == "need"
    assert sig.weight == 2.0
    assert sig.start == 0
    assert sig.end == 4


def test_intent_signal_is_frozen() -> None:
    s = IntentSignal(keyword="x", kind="buyer", weight=1.0, start=0, end=1)
    with pytest.raises((AttributeError, Exception)):
        s.weight = 99.0  # type: ignore[misc]


def test_intent_result_is_frozen() -> None:
    r = classify_intent("need")
    with pytest.raises((AttributeError, Exception)):
        r.confidence = 0.5  # type: ignore[misc]


# ---------- lexicon exposure ----------


def test_lexicon_is_loaded() -> None:
    assert len(LEXICON) > 50


def test_lexicon_is_readonly_view() -> None:
    with pytest.raises(TypeError):
        LEXICON["new"] = [("buyer", 1.0)]  # type: ignore[index]


def test_lexicon_keys_are_casefolded() -> None:
    from trade_intel.normalization.text import casefold_for_match

    for key in LEXICON:
        assert key == casefold_for_match(key)


def test_lexicon_seed_entries_present() -> None:
    """CLAUDE.md baseline keywords are all in the lexicon."""
    for kw in (
        "need", "req", "required", "want", "wanted", "looking for",
        "urgent", "asap",
        "avail", "available", "fresh", "stock ready", "qty", "have", "ready",
        "bridge", "intro",
        "lp", "last price", "pm", "best rate",
    ):
        assert kw in LEXICON, f"seed keyword {kw!r} missing from lexicon"


# ---------- realistic trader messages ----------


def test_full_seller_post_realistic() -> None:
    r = classify_intent("av 5 15pm 256gb fresh seal dxb lp 3200")
    assert r.intent is IntentType.SELLER
    assert r.is_price_inquiry is True


def test_full_buyer_post_realistic() -> None:
    r = classify_intent("urgent need 10 12pm 256gb best rate pls")
    assert r.intent is IntentType.BUYER
    assert r.urgency is Urgency.HIGH
    assert r.is_price_inquiry is True


def test_broker_post_realistic() -> None:
    r = classify_intent("have buyer for 50 mini 5 cellular, kindly send LP")
    assert r.intent is IntentType.BROKER
    assert r.is_price_inquiry is True


# ---------- parser integration smoke ----------


def test_classify_runs_cleanly_on_real_fixture() -> None:
    """Verify the classifier doesn't crash on any real parsed message body."""
    msgs = list(parse_file(FIXTURES / "android_sample.txt"))
    for m in msgs:
        if not m.normalized_message:
            continue
        r = classify_intent(m.normalized_message)
        assert isinstance(r, IntentResult)
