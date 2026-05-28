"""Unit tests for the CSV inventory-sheet parser (P3.8)."""

from __future__ import annotations

from pathlib import Path

import pytest

from trade_intel.parsers import parse_csv
from trade_intel.parsers.tabular import HEADER_SYNONYMS, map_headers, parse_date_cell

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "tabular"


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


# ---------- header mapping ----------


def test_map_headers_basic() -> None:
    m = map_headers(["Seller", "Product", "Qty", "Price"])
    assert m == {"sender_name": 0, "product": 1, "qty": 2, "price": 3}


def test_map_headers_case_insensitive() -> None:
    m = map_headers(["SELLER", "product", "QTY"])
    assert m == {"sender_name": 0, "product": 1, "qty": 2}


def test_map_headers_synonyms_first_wins() -> None:
    m = map_headers(["Supplier", "Seller", "Product"])
    assert m["sender_name"] == 0  # 'Supplier' claimed first


def test_map_headers_unknown_columns_ignored() -> None:
    m = map_headers(["Product", "ZZZ_unknown_col", "Price"])
    assert "product" in m
    assert "price" in m
    assert len(m) == 2


def test_map_headers_handles_none_and_empty() -> None:
    m = map_headers(["Product", None, "", "Price"])
    assert m == {"product": 0, "price": 3}


def test_header_synonym_table_covers_common_uae_terms() -> None:
    for term in (
        "seller", "supplier", "vendor",
        "phone", "contact", "whatsapp",
        "product", "item", "model",
        "qty", "quantity", "stock",
        "price", "lp", "last price",
        "storage", "color", "condition", "location",
    ):
        assert term in HEADER_SYNONYMS, f"missing synonym for {term!r}"


# ---------- date parsing ----------


@pytest.mark.parametrize(
    "raw",
    [
        "2026-05-28",
        "2026-05-28 09:00:00",
        "2026-05-28T09:00:00",
        "28/05/2026",
        "28/05/2026 09:00",
        "28-05-2026",
        "2026/05/28",
    ],
)
def test_parse_date_cell_known_formats(raw: str) -> None:
    dt = parse_date_cell(raw)
    assert dt is not None
    assert dt.year == 2026 and dt.month == 5 and dt.day == 28


def test_parse_date_cell_unknown_returns_none() -> None:
    assert parse_date_cell("bananas") is None


def test_parse_date_cell_empty_returns_none() -> None:
    assert parse_date_cell("") is None
    assert parse_date_cell("   ") is None
    assert parse_date_cell(None) is None


def test_parse_date_cell_passes_through_datetime() -> None:
    from datetime import datetime

    dt = datetime(2026, 5, 28, 9, 0)
    assert parse_date_cell(dt) is dt


def test_parse_date_cell_promotes_date_to_datetime() -> None:
    from datetime import date

    d = date(2026, 5, 28)
    out = parse_date_cell(d)
    assert out is not None
    assert out.year == 2026 and out.month == 5 and out.day == 28


# ---------- parse_csv: basic ----------


def test_parse_csv_empty_file_yields_nothing(tmp_path: Path) -> None:
    p = _write(tmp_path, "empty.csv", "")
    assert list(parse_csv(p)) == []


def test_parse_csv_header_only_yields_nothing(tmp_path: Path) -> None:
    p = _write(tmp_path, "headers.csv", "Seller,Product,Qty\n")
    assert list(parse_csv(p)) == []


def test_parse_csv_single_row(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "one.csv",
        "Seller,Phone,Product,Qty,Price\nAli Mobiles,+971501234567,iPhone 12 Pro Max,5,3200\n",
    )
    msgs = list(parse_csv(p))
    assert len(msgs) == 1
    m = msgs[0]
    assert m.sender_name == "Ali Mobiles"
    assert m.sender_phone == "+971501234567"
    assert "qty 5" in m.raw_message
    assert "iPhone 12 Pro Max" in m.raw_message
    assert "lp 3200" in m.raw_message


def test_parse_csv_skips_rows_with_no_product(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "blank.csv",
        "Seller,Product,Qty\nAli,,5\nSara,iPad Mini 5,3\n",
    )
    msgs = list(parse_csv(p))
    assert len(msgs) == 1
    assert msgs[0].sender_name == "Sara"


def test_parse_csv_no_mapped_headers_yields_nothing(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "unknown.csv",
        "Foo,Bar,Baz\n1,2,3\n",
    )
    assert list(parse_csv(p)) == []


def test_parse_csv_handles_bom(tmp_path: Path) -> None:
    p = tmp_path / "bom.csv"
    body = "Seller,Product\nAli Mobiles,iPhone 12 Pro Max\n"
    p.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))
    msgs = list(parse_csv(p))
    assert len(msgs) == 1
    assert msgs[0].sender_name == "Ali Mobiles"


def test_parse_csv_custom_delimiter(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "tsv.csv",
        "Seller\tProduct\tQty\nAli\tiPhone 12 Pro Max\t5\n",
    )
    msgs = list(parse_csv(p, delimiter="\t"))
    assert len(msgs) == 1


def test_parse_csv_custom_column_mapping_overrides_headers(
    tmp_path: Path,
) -> None:
    # File has NO header row; mapping is given explicitly.
    p = _write(
        tmp_path,
        "headerless.csv",
        "Ali Mobiles,iPhone 12 Pro Max,5\nSara,iPad Mini 5,3\n",
    )
    msgs = list(
        parse_csv(
            p,
            column_mapping={"sender_name": 0, "product": 1, "qty": 2},
        )
    )
    assert len(msgs) == 2
    assert msgs[0].sender_name == "Ali Mobiles"


def test_parse_csv_default_sender_used_when_column_blank(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "default.csv",
        "Product,Qty\niPhone 12 Pro Max,5\n",
    )
    msgs = list(parse_csv(p, default_sender="Sheet Owner"))
    assert len(msgs) == 1
    assert msgs[0].sender_name == "Sheet Owner"


# ---------- parse_csv: identity + phone ----------


def test_phone_column_folds_into_identity(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "phone.csv",
        "Seller,Phone,Product\nAli Mobiles,+971501234567,iPhone 12 Pro Max\n",
    )
    m = next(iter(parse_csv(p)))
    assert m.sender_phone == "+971501234567"
    assert m.sender_identity is not None
    # Identity carries the phone too so downstream phone-first lookup works.
    assert m.sender_identity.phone == "+971501234567"
    assert "+971501234567" in m.sender_identity.aliases


def test_phone_in_name_column_still_extracted(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "namephone.csv",
        "Seller,Product\nAli Mobiles +971501234567,iPhone 12 Pro Max\n",
    )
    m = next(iter(parse_csv(p)))
    assert m.sender_phone == "+971501234567"


# ---------- parse_csv: synthesized body shape ----------


def test_synthesized_body_includes_available_prefix(tmp_path: Path) -> None:
    """Inventory sheets are seller-side by default — 'available' prefix
    ensures the intent classifier tags as SELLER."""
    p = _write(
        tmp_path,
        "ok.csv",
        "Seller,Product\nAli,iPhone 12 Pro Max\n",
    )
    m = next(iter(parse_csv(p)))
    assert m.raw_message.startswith("available")


def test_synthesized_body_buyer_intent_hint_skips_available_prefix(
    tmp_path: Path,
) -> None:
    p = _write(
        tmp_path,
        "buyer.csv",
        "Seller,Product,Intent\nSara,iPhone 12 Pro Max,need urgent\n",
    )
    m = next(iter(parse_csv(p)))
    assert not m.raw_message.startswith("available")
    assert "need" in m.raw_message


def test_synthesized_body_skips_blank_segments(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "sparse.csv",
        "Seller,Product,Qty,Storage,Price\nAli,iPhone 12 Pro Max,,256GB,\n",
    )
    m = next(iter(parse_csv(p)))
    body = m.raw_message
    assert "qty" not in body  # blank qty cell
    assert "lp" not in body  # blank price cell
    assert "256GB" in body


# ---------- parse_csv: idempotency ----------


def test_parse_csv_hashes_are_deterministic(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "ok.csv",
        "Seller,Product,Date\nAli,iPhone 12 Pro Max,2026-05-28\n",
    )
    h1 = next(iter(parse_csv(p))).message_hash
    h2 = next(iter(parse_csv(p))).message_hash
    assert h1 == h2


def test_parse_csv_ts_less_rows_get_distinct_hashes_by_row_no(
    tmp_path: Path,
) -> None:
    p = _write(
        tmp_path,
        "no-date.csv",
        "Seller,Product\nAli,iPhone 12 Pro Max\nAli,iPhone 12 Pro Max\n",
    )
    msgs = list(parse_csv(p))
    assert len(msgs) == 2
    # Identical rows but different row_no → different hashes (orphan fold)
    assert msgs[0].message_hash != msgs[1].message_hash


# ---------- fixture round-trip ----------


def test_inventory_sample_fixture_round_trip() -> None:
    msgs = list(parse_csv(FIXTURES / "inventory_sample.csv"))
    assert len(msgs) == 6
    assert msgs[0].sender_name == "Ali Mobiles"
    assert msgs[0].sender_phone == "+971501234567"
    assert "iPhone 12 Pro Max" in msgs[0].raw_message
    # Repeat seller row exists
    senders = [m.sender_name for m in msgs]
    assert senders.count("Ali Mobiles") == 2
