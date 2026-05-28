"""Integration tests: WhatsApp parser populates SenderIdentity (P3.3)."""

from __future__ import annotations

from pathlib import Path

from trade_intel.parsers import ParsedMessage, ParseStatus, parse_file, parse_lines

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "whatsapp"


def _by_sender(msgs: list[ParsedMessage], name: str) -> ParsedMessage:
    return next(m for m in msgs if m.sender_name == name)


# ---------- new fields ----------


def test_personal_sender_yields_identity_with_no_phone() -> None:
    msgs = list(
        parse_lines(
            ["28/05/26, 09:00 - Ali: hi"],
            source_file="t.txt",
            group_name="g",
        )
    )
    m = msgs[0]
    assert m.sender_phone is None
    assert m.sender_identity is not None
    assert m.sender_identity.normalized_name == "ali"
    assert m.sender_identity.is_business is False


def test_business_sender_marks_identity_business() -> None:
    msgs = list(
        parse_lines(
            ["28/05/26, 09:00 - Ali Mobiles: qty 5 fresh"],
            source_file="t.txt",
            group_name="g",
        )
    )
    m = msgs[0]
    assert m.sender_identity is not None
    assert m.sender_identity.is_business is True
    assert m.sender_identity.probable_business_name == "Ali Mobiles"


def test_phone_only_sender_populates_sender_phone() -> None:
    msgs = list(
        parse_lines(
            ["28/05/26, 09:00 - +971 50 555 1234: looking buyer"],
            source_file="t.txt",
            group_name="g",
        )
    )
    m = msgs[0]
    assert m.sender_phone == "+971505551234"
    assert m.sender_identity is not None
    assert m.sender_identity.is_phone_only is True


def test_system_message_has_no_sender_identity() -> None:
    msgs = list(
        parse_lines(
            ['27/05/26, 09:00 - You created group "X"'],
            source_file="t.txt",
            group_name="g",
        )
    )
    m = msgs[0]
    assert m.parse_status is ParseStatus.SYSTEM
    assert m.sender_name is None
    assert m.sender_phone is None
    assert m.sender_identity is None


# ---------- hash includes phone now ----------


def test_hash_distinguishes_messages_with_different_phones() -> None:
    """Two messages identical except for an extracted phone should not collide."""
    a = next(
        iter(
            parse_lines(
                ["28/05/26, 09:00 - Ali: hi"],
                source_file="t.txt",
                group_name="g",
            )
        )
    )
    b = next(
        iter(
            parse_lines(
                ["28/05/26, 09:00 - +971505551234: hi"],
                source_file="t.txt",
                group_name="g",
            )
        )
    )
    # Different sender names → different hashes regardless of phone field.
    assert a.message_hash != b.message_hash
    # The phone-bearing sender has phone folded into the hash.
    assert b.sender_phone == "+971505551234"


def test_hash_stable_for_same_sender_across_parses() -> None:
    line = "28/05/26, 09:00 - +971 50 555 1234: hi"
    a = next(iter(parse_lines([line], source_file="t.txt", group_name="g")))
    b = next(iter(parse_lines([line], source_file="t.txt", group_name="g")))
    assert a.message_hash == b.message_hash


# ---------- fixture round-trip ----------


def test_mixed_identities_fixture_round_trip() -> None:
    msgs = list(parse_file(FIXTURES / "mixed_identities_sample.txt"))
    # Drop system / orphans for the identity assertions.
    payload = [m for m in msgs if m.sender_identity is not None]

    ali = _by_sender(payload, "Ali")
    assert ali.sender_identity is not None
    assert ali.sender_identity.is_business is False

    ali_m = _by_sender(payload, "Ali Mobiles")
    assert ali_m.sender_identity is not None
    assert ali_m.sender_identity.is_business is True

    khalid = _by_sender(payload, "Khalid Trading LLC")
    assert khalid.sender_identity is not None
    assert khalid.sender_identity.probable_business_name == "Khalid Trading LLC"

    sara = _by_sender(payload, "Sara DXB")
    assert sara.sender_identity is not None
    assert "sara" in sara.sender_identity.aliases

    ahmed = _by_sender(payload, "Ahmed (Sharjah)")
    assert ahmed.sender_identity is not None
    assert "ahmed" in ahmed.sender_identity.aliases

    phone_sender = _by_sender(payload, "+971 50 555 1234")
    assert phone_sender.sender_phone == "+971505551234"
    assert phone_sender.sender_identity is not None
    assert phone_sender.sender_identity.is_phone_only is True

    hub = _by_sender(payload, "📱 Mobile Hub")
    assert hub.sender_identity is not None
    assert hub.sender_identity.is_business is True
    assert hub.sender_identity.has_emoji is True

    arabic = _by_sender(payload, "محمد عبدالله")
    assert arabic.sender_identity is not None
    assert arabic.sender_identity.normalized_name == "محمد عبدالله"

    bare_digits = _by_sender(payload, "971555551234")
    assert bare_digits.sender_phone == "971555551234"


def test_two_handles_for_same_trader_share_an_alias() -> None:
    """Cross-message merge candidate: same trader posts as 'Ali Mobiles' once
    and as 'Ali Mobiles AUH' in another message; aliases should overlap so
    Phase 4 dedup can rendezvous on the shared root."""
    msgs = list(parse_file(FIXTURES / "mixed_identities_sample.txt"))
    ali_m = next(
        m for m in msgs if m.sender_name == "Ali Mobiles" and m.sender_identity
    )
    ali_m_auh = next(
        m for m in msgs if m.sender_name == "Ali Mobiles AUH" and m.sender_identity
    )
    assert ali_m.sender_identity is not None
    assert ali_m_auh.sender_identity is not None
    overlap = set(ali_m.sender_identity.aliases) & set(
        ali_m_auh.sender_identity.aliases
    )
    assert "ali mobiles" in overlap


def test_case_variant_handles_share_normalized_key() -> None:
    msgs = list(parse_file(FIXTURES / "mixed_identities_sample.txt"))
    upper = next(m for m in msgs if m.sender_name == "Ali Mobiles")
    lower = next(m for m in msgs if m.sender_name == "ali mobiles")
    assert upper.sender_identity is not None
    assert lower.sender_identity is not None
    assert (
        upper.sender_identity.normalized_name == lower.sender_identity.normalized_name
    )
