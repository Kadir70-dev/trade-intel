"""Source-format parsers (WhatsApp .txt, CSV, Excel).

Each parser yields `ParsedMessage` records — a thin, format-agnostic
shape consumed by the normalization + persistence layers downstream.
"""

from trade_intel.parsers.csv_parser import parse_csv
from trade_intel.parsers.dedup import DedupSet, DedupStats, dedupe
from trade_intel.parsers.excel_parser import parse_excel
from trade_intel.parsers.tabular import (
    HEADER_SYNONYMS,
    map_headers,
    row_to_parsed_message,
)
from trade_intel.parsers.whatsapp import (
    DateDetection,
    DateOrder,
    DateOrderInput,
    ParsedMessage,
    ParseStatus,
    detect_date_order,
    hash_for,
    parse_file,
    parse_lines,
)

__all__ = [
    "HEADER_SYNONYMS",
    "DateDetection",
    "DateOrder",
    "DateOrderInput",
    "DedupSet",
    "DedupStats",
    "ParseStatus",
    "ParsedMessage",
    "dedupe",
    "detect_date_order",
    "hash_for",
    "map_headers",
    "parse_csv",
    "parse_excel",
    "parse_file",
    "parse_lines",
    "row_to_parsed_message",
]
