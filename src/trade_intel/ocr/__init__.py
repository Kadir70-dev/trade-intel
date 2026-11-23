"""Local OCR stock import (no paid APIs, VPS-friendly).

- :mod:`engine` — PaddleOCR / RapidOCR (PP-OCR) / Tesseract abstraction.
- :mod:`pdf` — PyMuPDF text extraction + page render for scanned PDFs.
- :mod:`extract` — OCR line -> structured, confidence-scored inventory row.
- :mod:`importer` — orchestrate file -> rows -> review gate -> inventory DB.
"""

from __future__ import annotations

from trade_intel.ocr.engine import OcrError, OcrLine, available_engines, get_engine
from trade_intel.ocr.extract import ExtractedRow, extract_row, extract_rows
from trade_intel.ocr.importer import OcrImportResult, import_stock

__all__ = [
    "ExtractedRow",
    "OcrError",
    "OcrImportResult",
    "OcrLine",
    "available_engines",
    "extract_row",
    "extract_rows",
    "get_engine",
    "import_stock",
]
