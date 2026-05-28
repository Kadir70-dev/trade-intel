"""CSV inventory-sheet parser (P3.8).

Streams a CSV file row-by-row into :class:`ParsedMessage` records using
the shared row→message synthesis in :mod:`trade_intel.parsers.tabular`.
The same downstream pipeline (dedup → classify → extract → persist)
applies — call :func:`ingest_messages` with ``source_type=SourceType.CSV``
and the same idempotency / identity-merge guarantees hold.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path

from trade_intel.core.logging import get_logger
from trade_intel.parsers.tabular import map_headers, row_to_parsed_message
from trade_intel.parsers.whatsapp import ParsedMessage

log = get_logger(__name__, component="parser.csv")


def parse_csv(
    path: str | Path,
    *,
    group_name: str | None = None,
    encoding: str = "utf-8-sig",  # 'utf-8-sig' transparently strips a BOM
    delimiter: str = ",",
    column_mapping: dict[str, int] | None = None,
    default_sender: str | None = None,
) -> Iterator[ParsedMessage]:
    """Stream-parse a CSV inventory sheet.

    Args:
        path: file path.
        group_name: chat / sheet display name. Defaults to file stem.
        encoding: source encoding. ``utf-8-sig`` (default) silently
            strips a leading BOM if present.
        delimiter: column delimiter (default ``,``).
        column_mapping: optional override for header-to-canonical
            mapping. Use when the header row is missing or
            non-standard. Keys are canonical fields (see
            :data:`HEADER_SYNONYMS`); values are 0-based column indexes.
        default_sender: fallback sender name for rows where the sender
            cell is blank (or no sender column exists).

    Yields:
        :class:`ParsedMessage` per non-empty data row. Rows with no
        ``product``-like cell are silently skipped.
    """
    p = Path(path)
    chat_name = group_name or p.stem
    log.info("csv_parse_started", source_file=p.name, group=chat_name)

    rows_seen = 0
    emitted = 0
    skipped_empty = 0

    with p.open("r", encoding=encoding, newline="") as fh:
        reader = csv.reader(fh, delimiter=delimiter)
        try:
            first_row = next(reader)
        except StopIteration:
            log.info("csv_parse_completed", source_file=p.name, emitted=0)
            return

        if column_mapping is not None:
            mapping = column_mapping
            # No header row — treat first_row as data.
            data_rows: Iterator[list[str]] = iter([first_row, *reader])
        else:
            mapping = map_headers(first_row)
            data_rows = reader
            if not mapping:
                log.warning(
                    "csv_no_mapped_headers",
                    source_file=p.name,
                    headers=first_row,
                )
                return

        for row_no, row in enumerate(data_rows, start=2):  # row_no=2 for first data row when headers present
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
        "csv_parse_completed",
        source_file=p.name,
        rows_seen=rows_seen,
        emitted=emitted,
        skipped_empty=skipped_empty,
    )


__all__ = ["parse_csv"]
