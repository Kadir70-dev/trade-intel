"""Excel (.xlsx) inventory-sheet parser (P3.8).

Streams an Excel workbook row-by-row into :class:`ParsedMessage`
records via the shared synthesis layer. Uses :mod:`openpyxl` in
read-only mode so multi-MB workbooks don't have to fit in memory.

Only ``.xlsx`` (Open XML) is supported. Legacy ``.xls`` would need
``xlrd`` and is out of scope for v1.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from openpyxl import load_workbook

from trade_intel.core.logging import get_logger
from trade_intel.parsers.tabular import map_headers, row_to_parsed_message
from trade_intel.parsers.whatsapp import ParsedMessage

log = get_logger(__name__, component="parser.excel")


def parse_excel(
    path: str | Path,
    *,
    sheet_name: str | int = 0,
    group_name: str | None = None,
    column_mapping: dict[str, int] | None = None,
    default_sender: str | None = None,
) -> Iterator[ParsedMessage]:
    """Stream-parse an Excel inventory sheet.

    Args:
        path: file path (``.xlsx``).
        sheet_name: sheet selector — string name or 0-based int index
            (default ``0`` → first sheet).
        group_name: chat / sheet display name. Defaults to the sheet
            title (or file stem when no sheet title).
        column_mapping: optional override (see
            :func:`trade_intel.parsers.csv_parser.parse_csv`).
        default_sender: fallback sender name when the sender cell is
            blank.

    Yields:
        :class:`ParsedMessage` per non-empty data row.
    """
    p = Path(path)
    log.info("excel_parse_started", source_file=p.name)

    wb = load_workbook(p, read_only=True, data_only=True)
    try:
        if isinstance(sheet_name, int):
            sheets = wb.worksheets
            if not sheets or sheet_name >= len(sheets):
                log.warning(
                    "excel_sheet_not_found",
                    source_file=p.name,
                    sheet_index=sheet_name,
                )
                return
            ws = sheets[sheet_name]
        else:
            if sheet_name not in wb.sheetnames:
                log.warning(
                    "excel_sheet_not_found",
                    source_file=p.name,
                    sheet_name=sheet_name,
                )
                return
            ws = wb[sheet_name]

        chat_name = group_name or ws.title or p.stem
        rows_seen = 0
        emitted = 0
        skipped_empty = 0

        rows_iter = ws.iter_rows(values_only=True)
        try:
            first_row = next(rows_iter)
        except StopIteration:
            log.info("excel_parse_completed", source_file=p.name, emitted=0)
            return

        if column_mapping is not None:
            mapping = column_mapping
            data_rows: Iterator[tuple] = iter([first_row, *rows_iter])
        else:
            mapping = map_headers(first_row)
            data_rows = rows_iter
            if not mapping:
                log.warning(
                    "excel_no_mapped_headers",
                    source_file=p.name,
                    headers=list(first_row),
                )
                return

        for row_no, row in enumerate(data_rows, start=2):
            rows_seen += 1
            msg = row_to_parsed_message(
                row,
                mapping=mapping,
                source_file=p.name,
                group_name=chat_name,
                row_no=row_no,
                default_sender=default_sender,
            )
            if msg is None:
                skipped_empty += 1
                continue
            yield msg
            emitted += 1

        log.info(
            "excel_parse_completed",
            source_file=p.name,
            sheet=ws.title,
            rows_seen=rows_seen,
            emitted=emitted,
            skipped_empty=skipped_empty,
        )
    finally:
        wb.close()


__all__ = ["parse_excel"]
