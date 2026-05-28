"""Unit tests for the WhatsApp parser (P3.1)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest

from trade_intel.parsers.whatsapp import (
    ParsedMessage,
    ParseStatus,
    parse_file,
    parse_lines,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "whatsapp"


def _parse(text: str, **kwargs: str) -> list[ParsedMessage]:
    """Helper: feed a string through parse_lines and materialize the iterator."""
    return list(
        parse_lines(
            text.splitlines(),
            source_file=kwargs.get("source_file", "test.txt"),
            group_name=kwargs.get("group_name", "Test Group"),
        )
    )


# ---------- format detection ----------


def test_android_simple_message_parses_sender_body_and_ts() -> None:
    msgs = _parse("28/05/26, 08:42 - Ali Mobiles: 12pm 256gb fresh qty 8")
    assert len(msgs) == 1
    m = msgs[0]
    assert m.parse_status is ParseStatus.OK
    assert m.sender_name == "Ali Mobiles"
    assert m.raw_message == "12pm 256gb fresh qty 8"
    assert m.timestamp == datetime(2026, 5, 28, 8, 42, 0)
    assert m.group_name == "Test Group"
    assert m.is_media is False
    assert len(m.message_hash) == 64  # sha-256 hex


def test_iphone_bracketed_format_parses() -> None:
    msgs = _parse("[28/05/26, 10:16:09] Faisal: req 15pm 256gb sealed qty 50 LP pls")
    assert len(msgs) == 1
    m = msgs[0]
    assert m.parse_status is ParseStatus.OK
    assert m.sender_name == "Faisal"
    assert m.timestamp == datetime(2026, 5, 28, 10, 16, 9)


def test_iphone_lrm_marks_are_stripped() -> None:
    # iOS prefixes the body with U+200E (LRM); make sure it's gone after parse.
    msgs = _parse("[28/05/26, 10:15:42] ‎Faisal: ‎image omitted")
    assert len(msgs) == 1
    m = msgs[0]
    assert m.sender_name == "Faisal"
    assert "‎" not in m.raw_message
    assert m.parse_status is ParseStatus.MEDIA
    assert m.is_media is True


def test_twelve_hour_am_pm_normalizes_to_24h() -> None:
    am = _parse("28/05/26, 12:30 AM - Ali: midnight thirty")
    pm = _parse("28/05/26, 1:05 PM - Ali: afternoon")
    assert am[0].timestamp == datetime(2026, 5, 28, 0, 30, 0)
    assert pm[0].timestamp == datetime(2026, 5, 28, 13, 5, 0)


def test_four_digit_year_supported() -> None:
    msgs = _parse("28/05/2026, 09:15 - Ali: hi")
    assert msgs[0].timestamp == datetime(2026, 5, 28, 9, 15, 0)


# ---------- multiline ----------


def test_multiline_message_coalesces_continuation_lines() -> None:
    msgs = _parse(
        "28/05/26, 08:43 - Buyer: Need urgent 15pm 1tb sealed any qty\n"
        "shj pickup ok\n"
        "ASAP"
    )
    assert len(msgs) == 1
    m = msgs[0]
    assert m.raw_message == "Need urgent 15pm 1tb sealed any qty\nshj pickup ok\nASAP"
    # normalized_message is casefolded + whitespace-flattened (matching form);
    # raw_message preserves the original case + line structure.
    assert m.normalized_message == "need urgent 15pm 1tb sealed any qty shj pickup ok asap"


def test_blank_line_within_message_is_preserved_as_empty_line() -> None:
    msgs = _parse(
        "28/05/26, 08:43 - Buyer: line1\n"
        "\n"
        "line3"
    )
    assert len(msgs) == 1
    assert msgs[0].raw_message == "line1\n\nline3"


# ---------- system + media + emoji ----------


def test_system_messages_are_tagged_not_dropped() -> None:
    msgs = _parse('27/05/26, 09:00 - You created group "UAE Wholesale"')
    assert len(msgs) == 1
    m = msgs[0]
    assert m.parse_status is ParseStatus.SYSTEM
    assert m.sender_name is None
    assert "created group" in m.raw_message


def test_media_omitted_is_recognised() -> None:
    msgs = _parse("28/05/26, 08:45 - Khalid Trading: <Media omitted>")
    assert msgs[0].parse_status is ParseStatus.MEDIA
    assert msgs[0].is_media is True


def test_emoji_preserved_through_utf8() -> None:
    msgs = _parse("28/05/26, 08:48 - Sara DXB: pm best rate for air 5 🚀")
    assert msgs[0].raw_message.endswith("🚀")
    assert "🚀" in msgs[0].normalized_message


# ---------- malformed / orphan ----------


def test_orphan_line_at_start_emitted_with_orphan_status() -> None:
    msgs = _parse("garbage with no header\n28/05/26, 11:00 - Ali: hi")
    assert msgs[0].parse_status is ParseStatus.ORPHAN_CONTINUATION
    assert msgs[0].sender_name is None
    assert msgs[1].parse_status is ParseStatus.OK
    assert msgs[1].sender_name == "Ali"


def test_invalid_clock_time_yields_null_timestamp_not_crash() -> None:
    msgs = _parse("28/05/26, 99:99 - Bad: oops")
    assert len(msgs) == 1
    assert msgs[0].timestamp is None
    assert msgs[0].sender_name == "Bad"
    assert msgs[0].parse_status is ParseStatus.OK


def test_invalid_date_yields_null_timestamp_not_crash() -> None:
    msgs = _parse("31/02/26, 10:00 - Bad: impossible date")
    assert msgs[0].timestamp is None
    assert msgs[0].sender_name == "Bad"


# ---------- hashing / idempotency ----------


def test_hash_is_deterministic_across_runs() -> None:
    line = "28/05/26, 08:42 - Ali: 12pm qty 8"
    a = _parse(line)[0]
    b = _parse(line)[0]
    assert a.message_hash == b.message_hash


def test_hash_differs_when_sender_differs() -> None:
    a = _parse("28/05/26, 08:42 - Ali: same body")[0]
    b = _parse("28/05/26, 08:42 - Khalid: same body")[0]
    assert a.message_hash != b.message_hash


def test_hash_differs_when_timestamp_differs() -> None:
    a = _parse("28/05/26, 08:42 - Ali: same body")[0]
    b = _parse("28/05/26, 08:43 - Ali: same body")[0]
    assert a.message_hash != b.message_hash


def test_hash_differs_when_body_differs() -> None:
    a = _parse("28/05/26, 08:42 - Ali: body one")[0]
    b = _parse("28/05/26, 08:42 - Ali: body two")[0]
    assert a.message_hash != b.message_hash


def test_orphan_hash_includes_line_no_so_repeat_text_does_not_collide() -> None:
    msgs = _parse("garbage\ngarbage\n28/05/26, 09:00 - Ali: hi")
    orphans = [m for m in msgs if m.parse_status is ParseStatus.ORPHAN_CONTINUATION]
    assert len(orphans) == 2
    assert orphans[0].message_hash != orphans[1].message_hash


# ---------- streaming / file-based ----------


def test_parse_lines_accepts_a_generator_not_a_list() -> None:
    def gen() -> Iterator[str]:
        yield "28/05/26, 08:42 - Ali: line a"
        yield "28/05/26, 08:43 - Ali: line b"

    msgs = list(parse_lines(gen(), source_file="g.txt", group_name="g"))
    assert [m.sender_name for m in msgs] == ["Ali", "Ali"]


def test_android_fixture_round_trip() -> None:
    msgs = list(parse_file(FIXTURES / "android_sample.txt", group_name="UAE Wholesale"))
    statuses = [m.parse_status for m in msgs]
    # Expect at least: 2 system (encryption + create group), 1 OK seller,
    # 1 multi-line buyer, 1 media, 1 OK avail, 1 system (join), 1 OK pm.
    assert statuses.count(ParseStatus.SYSTEM) >= 3
    assert statuses.count(ParseStatus.MEDIA) >= 1
    assert any(m.sender_name == "Ali Mobiles" for m in msgs)
    # Multi-line buyer message should have its second line coalesced.
    buyer = next(m for m in msgs if m.raw_message.startswith("Need urgent"))
    assert "shj pickup ok" in buyer.raw_message


def test_iphone_fixture_round_trip() -> None:
    msgs = list(parse_file(FIXTURES / "iphone_sample.txt", group_name="GCC Hub"))
    assert any(m.is_media for m in msgs)
    assert all(
        "‎" not in m.raw_message for m in msgs
    ), "LRM marks must not survive the parser"
    multi = next(m for m in msgs if m.raw_message.startswith("have stock ready"))
    assert "mini 5 wifi" in multi.raw_message
    assert "auh warehouse" in multi.raw_message


def test_malformed_fixture_yields_orphans_and_continues() -> None:
    msgs = list(parse_file(FIXTURES / "malformed_sample.txt"))
    # First record should be orphan; subsequent real headers should still parse.
    assert msgs[0].parse_status is ParseStatus.ORPHAN_CONTINUATION
    senders = [m.sender_name for m in msgs if m.sender_name == "Ali"]
    assert len(senders) >= 2  # the two Ali messages still come through


def test_reimport_same_file_produces_identical_hashes() -> None:
    a = [m.message_hash for m in parse_file(FIXTURES / "android_sample.txt")]
    b = [m.message_hash for m in parse_file(FIXTURES / "android_sample.txt")]
    assert a == b
    # And: every emitted hash is unique within a single parse pass.
    assert len(set(a)) == len(a), "duplicate hash within one file would break unique index"


def test_utf8_decode_failure_does_not_crash() -> None:
    # Synthesize a file with an invalid UTF-8 byte and confirm parser
    # survives via the replacement strategy.
    p = FIXTURES.parent / "_tmp_bad_utf8.txt"
    p.write_bytes(
        b"28/05/26, 08:42 - Ali: hello \xff world\n"
        b"28/05/26, 08:43 - Ali: second line\n"
    )
    try:
        msgs = list(parse_file(p))
        assert len(msgs) == 2
        assert msgs[0].sender_name == "Ali"
    finally:
        p.unlink(missing_ok=True)


@pytest.mark.parametrize(
    "line,expected_status",
    [
        ("28/05/26, 08:42 - Ali: hi", ParseStatus.OK),
        ("28/05/26, 08:42 - Ali: <Media omitted>", ParseStatus.MEDIA),
        ('27/05/26, 09:00 - You created group "X"', ParseStatus.SYSTEM),
    ],
)
def test_status_dispatch_matrix(line: str, expected_status: ParseStatus) -> None:
    assert _parse(line)[0].parse_status is expected_status
