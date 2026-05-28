"""Unit tests for date-format auto-detection in the WhatsApp parser (P3.2)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from trade_intel.parsers import (
    DateDetection,
    ParseStatus,
    detect_date_order,
    parse_file,
    parse_lines,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "whatsapp"


# ---------- detect_date_order ----------


def test_pure_dmy_signal_picks_dmy() -> None:
    # Day=28 > 12 → must be day-first.
    lines = ["28/05/26, 09:00 - Ali: hi", "29/05/26, 09:01 - Ali: ok"]
    d = detect_date_order(lines)
    assert d.order == "DMY"
    assert d.confidence == 1.0
    assert d.evidence["dmy"] == 2
    assert d.evidence["mdy"] == 0


def test_pure_mdy_signal_picks_mdy() -> None:
    # Second field=28 > 12 → first must be month → MDY.
    lines = ["5/28/26, 09:00 - Ali: hi", "5/29/26, 09:01 - Ali: ok"]
    d = detect_date_order(lines)
    assert d.order == "MDY"
    assert d.confidence == 1.0


def test_pure_ymd_signal_picks_ymd() -> None:
    # 4-digit year in first field → YMD.
    lines = ["2026/05/28, 09:00 - Ali: hi", "2026/05/29, 09:01 - Ali: ok"]
    d = detect_date_order(lines)
    assert d.order == "YMD"
    assert d.evidence["ymd"] == 2


def test_ambiguous_all_low_falls_back_to_dmy_with_zero_confidence() -> None:
    # Every field 1..12 → no signal; default to DMY (UAE locale bias).
    lines = ["05/06/26, 09:00 - Ali: hi", "07/08/26, 09:01 - Ali: ok"]
    d = detect_date_order(lines)
    assert d.order == "DMY"
    assert d.confidence == 0.0
    assert d.evidence["ambiguous"] == 2


def test_mixed_evidence_picks_majority_with_partial_confidence() -> None:
    # 3 DMY signals, 1 MDY signal → 75% confidence DMY.
    lines = [
        "28/05/26, 09:00 - Ali: dmy",
        "29/05/26, 09:01 - Ali: dmy",
        "30/05/26, 09:02 - Ali: dmy",
        "5/28/26, 09:03 - Ali: mdy",
    ]
    d = detect_date_order(lines)
    assert d.order == "DMY"
    assert d.confidence == pytest.approx(0.75)


def test_no_headers_returns_default() -> None:
    d = detect_date_order(["garbage", "more garbage", ""])
    assert d.order == "DMY"
    assert d.confidence == 0.0
    assert d.samples_seen == 0


def test_iphone_brackets_count_as_headers() -> None:
    lines = ["[28/05/26, 10:16:09] Faisal: req 15pm"]
    d = detect_date_order(lines)
    assert d.order == "DMY"
    assert d.samples_seen == 1


def test_malformed_dates_tally_separately_and_dont_decide() -> None:
    # Both fields > 12 → impossible date.
    lines = ["13/13/26, 09:00 - Ali: bad", "28/05/26, 09:00 - Ali: dmy"]
    d = detect_date_order(lines)
    assert d.evidence["malformed"] == 1
    assert d.order == "DMY"
    assert d.confidence == 1.0  # malformed are excluded from the denominator


def test_max_samples_is_respected() -> None:
    lines = [f"{n:02d}/05/26, 09:00 - Ali: msg" for n in range(1, 50)]
    d = detect_date_order(lines, max_samples=5)
    assert d.samples_seen == 5


# ---------- parse_lines honors a forced date_order ----------


def test_forced_mdy_parses_us_format_dates() -> None:
    msgs = list(
        parse_lines(
            ["5/28/26, 8:42 AM - Ali: hi"],
            source_file="t.txt",
            group_name="g",
            date_order="MDY",
        )
    )
    assert msgs[0].timestamp == datetime(2026, 5, 28, 8, 42, 0)


def test_forced_ymd_parses_iso_like_dates() -> None:
    msgs = list(
        parse_lines(
            ["2026/05/28, 14:33 - Ali: hi"],
            source_file="t.txt",
            group_name="g",
            date_order="YMD",
        )
    )
    assert msgs[0].timestamp == datetime(2026, 5, 28, 14, 33, 0)


def test_tz_param_attaches_zone_to_timestamps() -> None:
    dxb = ZoneInfo("Asia/Dubai")
    msgs = list(
        parse_lines(
            ["28/05/26, 09:00 - Ali: hi"],
            source_file="t.txt",
            group_name="g",
            tz=dxb,
        )
    )
    ts = msgs[0].timestamp
    assert ts is not None
    assert ts.tzinfo is dxb
    # 09:00 in Asia/Dubai is 05:00 UTC.
    assert ts.astimezone(UTC).hour == 5


# ---------- parse_file auto-detects per file ----------


def test_parse_file_auto_picks_dmy_on_android_fixture() -> None:
    # Android fixture uses 27/05/26 etc. → day > 12 evidence is decisive.
    msgs = list(parse_file(FIXTURES / "android_sample.txt"))
    ali = next(m for m in msgs if m.sender_name == "Ali Mobiles")
    assert ali.timestamp == datetime(2026, 5, 28, 8, 42, 0)


def test_parse_file_auto_picks_mdy_on_us_fixture() -> None:
    # In iphone_mdy_sample, "5/28/26" → second field 28 > 12 ⇒ MDY.
    msgs = list(parse_file(FIXTURES / "iphone_mdy_sample.txt"))
    faisal = next(m for m in msgs if m.sender_name == "Faisal")
    # If MDY misidentified as DMY, day would be 5 not 28.
    assert faisal.timestamp == datetime(2026, 5, 28, 10, 15, 42)
    # PM time too.
    mariam_pm = [m for m in msgs if m.sender_name == "Mariam"][-1]
    assert mariam_pm.timestamp == datetime(2026, 5, 28, 13, 19, 0)


def test_parse_file_forced_order_overrides_auto() -> None:
    # Force MDY on a DMY file and confirm the timestamps come out shifted.
    msgs = list(parse_file(FIXTURES / "android_sample.txt", date_order="MDY"))
    ali = next(m for m in msgs if m.sender_name == "Ali Mobiles")
    # In MDY, "28/05/26" means month=28, which is invalid → ts is None.
    assert ali.timestamp is None


def test_normalized_message_is_casefolded_and_flat() -> None:
    msgs = list(
        parse_lines(
            ["28/05/26, 09:15 - Ali: 12pm  256GB  FRESH"],
            source_file="t.txt",
            group_name="g",
        )
    )
    assert msgs[0].raw_message == "12pm  256GB  FRESH"
    assert msgs[0].normalized_message == "12pm 256gb fresh"


def test_normalized_message_preserves_emoji_under_casefold() -> None:
    msgs = list(
        parse_lines(
            ["28/05/26, 09:15 - Ali: Launch 🚀 NOW"],
            source_file="t.txt",
            group_name="g",
        )
    )
    assert msgs[0].normalized_message == "launch 🚀 now"


def test_full_width_digits_normalize_in_message_body() -> None:
    msgs = list(
        parse_lines(
            ["28/05/26, 09:15 - Ali: ２５６GB fresh"],
            source_file="t.txt",
            group_name="g",
        )
    )
    assert msgs[0].normalized_message == "256gb fresh"
    # raw stays as-original.
    assert "２５６" in msgs[0].raw_message


def test_detection_result_is_a_dataclass_with_evidence_dict() -> None:
    d = detect_date_order(["28/05/26, 09:00 - Ali: hi"])
    assert isinstance(d, DateDetection)
    assert set(d.evidence) == {"dmy", "mdy", "ymd", "ambiguous", "malformed"}
    assert d.samples_seen == 1


def test_parse_lines_with_ymd_does_not_crash_on_dmy_input() -> None:
    # Wrong order forced → ts may be None for some lines, but parser keeps going.
    msgs = list(
        parse_lines(
            ["28/05/26, 09:00 - Ali: hi", "29/05/26, 09:01 - Ali: ok"],
            source_file="t.txt",
            group_name="g",
            date_order="YMD",
        )
    )
    assert len(msgs) == 2
    # In YMD, "28" parses as year → 2028; then month=05, day=26 is valid.
    assert msgs[0].timestamp == datetime(2028, 5, 26, 9, 0, 0)
    # Status is still OK; ts oddity is up to the caller to flag.
    assert msgs[0].parse_status is ParseStatus.OK


def test_mdy_fixture_round_trip_preserves_multi_line_body() -> None:
    msgs = list(parse_file(FIXTURES / "iphone_mdy_sample.txt"))
    multi = next(m for m in msgs if m.raw_message.startswith("have stock ready"))
    assert "mini 5 wifi 64gb qty 25 fresh" in multi.raw_message
    assert multi.normalized_message == "have stock ready mini 5 wifi 64gb qty 25 fresh"
