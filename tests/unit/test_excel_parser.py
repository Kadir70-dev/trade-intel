"""Unit tests for the Excel (.xlsx) inventory-sheet parser (P3.8)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from openpyxl import Workbook

from trade_intel.parsers import parse_excel


def _make_xlsx(tmp_path: Path, rows: list[list], sheet_title: str | None = None) -> Path:
    """Write a fresh .xlsx with the given rows; return the path."""
    wb = Workbook()
    ws = wb.active
    if sheet_title is not None:
        ws.title = sheet_title
    for row in rows:
        ws.append(row)
    out = tmp_path / "test.xlsx"
    wb.save(out)
    return out


# ---------- basic ----------


def test_parse_excel_single_row(tmp_path: Path) -> None:
    p = _make_xlsx(
        tmp_path,
        [
            ["Seller", "Phone", "Product", "Qty", "Price"],
            ["Ali Mobiles", "+971501234567", "iPhone 12 Pro Max", 5, 3200],
        ],
    )
    msgs = list(parse_excel(p))
    assert len(msgs) == 1
    m = msgs[0]
    assert m.sender_name == "Ali Mobiles"
    assert m.sender_phone == "+971501234567"
    assert "iPhone 12 Pro Max" in m.raw_message
    assert "qty 5" in m.raw_message


def test_parse_excel_header_only_yields_nothing(tmp_path: Path) -> None:
    p = _make_xlsx(tmp_path, [["Seller", "Product"]])
    assert list(parse_excel(p)) == []


def test_parse_excel_multiple_rows(tmp_path: Path) -> None:
    p = _make_xlsx(
        tmp_path,
        [
            ["Supplier", "Item", "Stock"],
            ["Ali", "iPhone 12 Pro Max", 5],
            ["Sara", "iPad Mini 5", 3],
            ["Khalid", "iPad Air 5", 10],
        ],
    )
    msgs = list(parse_excel(p))
    assert len(msgs) == 3
    assert [m.sender_name for m in msgs] == ["Ali", "Sara", "Khalid"]


def test_parse_excel_skips_blank_product_rows(tmp_path: Path) -> None:
    p = _make_xlsx(
        tmp_path,
        [
            ["Seller", "Product"],
            ["Ali", "iPhone 12 Pro Max"],
            ["Sara", None],
            ["", ""],
            ["Khalid", "iPad Mini 5"],
        ],
    )
    msgs = list(parse_excel(p))
    assert [m.sender_name for m in msgs] == ["Ali", "Khalid"]


def test_parse_excel_no_mapped_headers_yields_nothing(tmp_path: Path) -> None:
    p = _make_xlsx(
        tmp_path,
        [
            ["Foo", "Bar", "Baz"],
            ["x", "y", "z"],
        ],
    )
    assert list(parse_excel(p)) == []


def test_parse_excel_native_datetime_cell_preserved(tmp_path: Path) -> None:
    """Excel can hand back native :class:`datetime` from date-typed cells."""
    ts = datetime(2026, 5, 28, 9, 0)
    p = _make_xlsx(
        tmp_path,
        [
            ["Seller", "Product", "Date"],
            ["Ali", "iPhone 12 Pro Max", ts],
        ],
    )
    m = next(iter(parse_excel(p)))
    assert m.timestamp is not None
    assert m.timestamp.year == 2026 and m.timestamp.month == 5 and m.timestamp.day == 28


def test_parse_excel_string_date_cell_parsed(tmp_path: Path) -> None:
    p = _make_xlsx(
        tmp_path,
        [
            ["Seller", "Product", "Date"],
            ["Ali", "iPhone 12 Pro Max", "2026-05-28"],
        ],
    )
    m = next(iter(parse_excel(p)))
    assert m.timestamp is not None
    assert m.timestamp.day == 28


def test_parse_excel_numeric_qty_cell(tmp_path: Path) -> None:
    """Numeric cells should stringify into the synthesized body."""
    p = _make_xlsx(
        tmp_path,
        [
            ["Seller", "Product", "Qty", "Price"],
            ["Ali", "iPhone 12 Pro Max", 5, 3200.50],
        ],
    )
    m = next(iter(parse_excel(p)))
    assert "qty 5" in m.raw_message
    assert "lp 3200" in m.raw_message  # may include decimals


# ---------- sheet selection ----------


def test_parse_excel_sheet_name_by_string(tmp_path: Path) -> None:
    wb = Workbook()
    wb.active.title = "First"
    wb.active.append(["Seller", "Product"])
    wb.active.append(["Ali", "iPhone 12 Pro Max"])
    sheet2 = wb.create_sheet("Second")
    sheet2.append(["Seller", "Product"])
    sheet2.append(["Sara", "iPad Mini 5"])
    p = tmp_path / "two_sheets.xlsx"
    wb.save(p)

    msgs_first = list(parse_excel(p, sheet_name="First"))
    msgs_second = list(parse_excel(p, sheet_name="Second"))
    assert msgs_first[0].sender_name == "Ali"
    assert msgs_second[0].sender_name == "Sara"


def test_parse_excel_sheet_index(tmp_path: Path) -> None:
    wb = Workbook()
    wb.active.append(["Seller", "Product"])
    wb.active.append(["Ali", "iPhone 12 Pro Max"])
    sheet2 = wb.create_sheet("Other")
    sheet2.append(["Seller", "Product"])
    sheet2.append(["Sara", "iPad Mini 5"])
    p = tmp_path / "two.xlsx"
    wb.save(p)

    assert next(iter(parse_excel(p, sheet_name=0))).sender_name == "Ali"
    assert next(iter(parse_excel(p, sheet_name=1))).sender_name == "Sara"


def test_parse_excel_missing_sheet_yields_nothing(tmp_path: Path) -> None:
    p = _make_xlsx(tmp_path, [["Seller", "Product"], ["Ali", "iPhone 12 Pro Max"]])
    assert list(parse_excel(p, sheet_name="does-not-exist")) == []


def test_parse_excel_sheet_index_out_of_range_yields_nothing(
    tmp_path: Path,
) -> None:
    p = _make_xlsx(tmp_path, [["Seller", "Product"], ["Ali", "iPhone 12 Pro Max"]])
    assert list(parse_excel(p, sheet_name=99)) == []


# ---------- identity + phone ----------


def test_phone_column_folds_into_identity(tmp_path: Path) -> None:
    p = _make_xlsx(
        tmp_path,
        [
            ["Seller", "Phone", "Product"],
            ["Ali Mobiles", "+971501234567", "iPhone 12 Pro Max"],
        ],
    )
    m = next(iter(parse_excel(p)))
    assert m.sender_phone == "+971501234567"
    assert m.sender_identity is not None
    assert m.sender_identity.phone == "+971501234567"


# ---------- group name / default sender ----------


def test_parse_excel_uses_sheet_title_as_group_name(tmp_path: Path) -> None:
    p = _make_xlsx(
        tmp_path,
        [["Seller", "Product"], ["Ali", "iPhone 12 Pro Max"]],
        sheet_title="UAE Wholesale",
    )
    m = next(iter(parse_excel(p)))
    assert m.group_name == "UAE Wholesale"


def test_parse_excel_default_sender(tmp_path: Path) -> None:
    p = _make_xlsx(
        tmp_path,
        [["Product"], ["iPhone 12 Pro Max"]],
    )
    m = next(iter(parse_excel(p, default_sender="Sheet Owner")))
    assert m.sender_name == "Sheet Owner"


# ---------- hash idempotency ----------


def test_parse_excel_hash_stable_across_runs(tmp_path: Path) -> None:
    p = _make_xlsx(
        tmp_path,
        [
            ["Seller", "Product", "Date"],
            ["Ali", "iPhone 12 Pro Max", "2026-05-28"],
        ],
    )
    h1 = next(iter(parse_excel(p))).message_hash
    h2 = next(iter(parse_excel(p))).message_hash
    assert h1 == h2


# ---------- defensive ----------


def test_parse_excel_emoji_cell_round_trips(tmp_path: Path) -> None:
    p = _make_xlsx(
        tmp_path,
        [
            ["Seller", "Product"],
            ["📱 Mobile Hub", "iPhone 12 Pro Max"],
        ],
    )
    m = next(iter(parse_excel(p)))
    assert m.sender_name == "📱 Mobile Hub"
    assert m.sender_identity is not None
    assert m.sender_identity.has_emoji is True


def test_parse_excel_arabic_sender(tmp_path: Path) -> None:
    p = _make_xlsx(
        tmp_path,
        [
            ["Seller", "Product"],
            ["محمد عبدالله", "iPhone 15 Pro Max"],
        ],
    )
    m = next(iter(parse_excel(p)))
    assert m.sender_name == "محمد عبدالله"


def test_parse_excel_unknown_path_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        list(parse_excel(tmp_path / "missing.xlsx"))
