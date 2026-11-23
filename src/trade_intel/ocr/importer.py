"""OCR stock import orchestrator.

One pipeline for every input type:

    image / scanned-PDF  --OCR-->  lines (text + confidence)
    text-PDF / Excel / CSV -------> lines (confidence 1.0, already structured)
                                       |
                                       v
                          extract structured rows + score confidence
                                       |
                     High / Medium  ---+---  Low (held for human review)
                          |
                          v
                  ingest into inventory DB (SourceType.MANUAL)

Confidence gating implements the review mode: by default High + Medium rows are
auto-inserted and Low rows are returned for approval; ``mode="review"`` inserts
nothing (preview) and ``mode="all"`` approves everything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from trade_intel.db.models import SourceType
from trade_intel.normalization import normalize_text
from trade_intel.normalization.identity import extract_identity
from trade_intel.ocr.engine import OcrError, OcrLine, get_engine
from trade_intel.ocr.extract import ExtractedRow, extract_rows
from trade_intel.ocr.pdf import load_pdf_pages
from trade_intel.parsers import ParsedMessage, ParseStatus, hash_for
from trade_intel.persistence import ingest_messages

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


@dataclass(slots=True)
class OcrImportResult:
    source: str
    file_type: str
    engine: str | None
    lines_total: int
    rows_extracted: int
    counts: dict[str, int] = field(default_factory=dict)
    inserted: int = 0
    pending_review: list[ExtractedRow] = field(default_factory=list)
    rows: list[ExtractedRow] = field(default_factory=list)
    ingest_stats: dict | None = None

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "file_type": self.file_type,
            "engine": self.engine,
            "lines_total": self.lines_total,
            "rows_extracted": self.rows_extracted,
            "counts": self.counts,
            "inserted": self.inserted,
            "pending_review": [r.as_dict() for r in self.pending_review],
            "rows": [r.as_dict() for r in self.rows],
            "ingest_stats": self.ingest_stats,
        }


def _lines_for(path: Path, prefer_engine: str | None) -> tuple[list[OcrLine], str, str]:
    """Produce (lines, engine_name, file_type) for any supported input."""
    ext = path.suffix.lower()

    if ext in _IMAGE_EXTS:
        engine = get_engine(prefer_engine)
        return engine.recognize(str(path)), engine.name, "image"

    if ext == ".pdf":
        pages = load_pdf_pages(str(path))
        lines: list[OcrLine] = []
        engine_name = "pdf-text"
        engine = None
        for pg in pages:
            if pg.needs_ocr:
                if engine is None:
                    engine = get_engine(prefer_engine)
                    engine_name = engine.name
                lines.extend(engine.recognize(pg.image))
            else:
                for raw in (pg.text or "").splitlines():
                    if raw.strip():
                        lines.append(OcrLine(raw.strip(), 1.0))
        return lines, engine_name, "pdf"

    if ext in (".xlsx", ".xls"):
        from trade_intel.parsers import parse_excel

        return _lines_from_messages(parse_excel(str(path))), "excel", "excel"

    if ext == ".csv":
        from trade_intel.parsers import parse_csv

        return _lines_from_messages(parse_csv(str(path))), "csv", "csv"

    raise OcrError(f"unsupported file type {ext!r} (image / pdf / xlsx / csv)")


def _lines_from_messages(messages) -> list[OcrLine]:
    """Structured parsers already give clean text — treat each as a 1.0-conf line."""
    out: list[OcrLine] = []
    for m in messages:
        if m.parse_status is ParseStatus.OK and m.normalized_message:
            out.append(OcrLine(m.normalized_message, 1.0))
    return out


def _approved(rows: list[ExtractedRow], mode: str) -> list[ExtractedRow]:
    if mode == "review":
        return []
    if mode == "all":
        return list(rows)
    # auto: High + Medium; Low is held for review.
    return [r for r in rows if r.confidence in ("high", "medium")]


def _synthesize_body(row: ExtractedRow) -> str:
    """Build a clean SELLER stock line from the VALIDATED extracted fields (not
    the raw OCR text). This makes the ingest re-extraction reproduce exactly
    what we scored — no OCR-merge artifacts, and MOQ never contaminates qty
    (Inventory has no MOQ column; MOQ is surfaced in the review output only).
    'available' biases the intent classifier to SELLER."""
    parts: list[str] = ["available"]
    if row.product:
        parts.append(row.product)
    if row.storage:
        parts.append(row.storage)
    if row.condition:
        parts.append(row.condition)
    if row.quantity is not None:
        parts.append(f"qty {row.quantity}")
    if row.price_aed is not None:
        parts.append(f"lp {row.price_aed:g}")
    if row.location:
        parts.append(row.location)
    # Fall back to raw text if we somehow extracted nothing structured.
    if len(parts) == 1:
        parts.append(row.raw_text)
    return " ".join(parts)


def _to_message(
    row: ExtractedRow, *, seller: str, source_file: str, line_no: int, now: datetime
) -> ParsedMessage:
    """Synthesize a SELLER ParsedMessage from an approved row so the standard
    ingest path creates the Inventory record (seller get-or-create, dedup,
    idempotency all reused)."""
    body = _synthesize_body(row)
    identity = extract_identity(seller)
    msg = ParsedMessage(
        timestamp=now,
        sender_name=seller,
        raw_message=body,
        normalized_message=normalize_text(body, casefold=True, flatten=True),
        group_name=None,
        message_hash="",
        source_file=source_file,
        is_media=False,
        parse_status=ParseStatus.OK,
        line_no=line_no,
        sender_phone=identity.phone if identity else None,
        sender_identity=identity,
    )
    msg.message_hash = hash_for(msg)
    return msg


def import_stock(
    path: str | Path,
    *,
    session: Session,
    seller: str | None = None,
    prefer_engine: str | None = None,
    mode: str = "auto",
    now: datetime | None = None,
) -> OcrImportResult:
    """OCR/parse a stock file, score confidence, and import approved rows.

    Args:
        path: image / pdf / xlsx / csv.
        seller: dealer the stock is attributed to (defaults to the file stem).
        prefer_engine: 'paddleocr' | 'rapidocr' | 'tesseract' (else auto).
        mode: 'auto' (insert High+Medium, hold Low), 'review' (insert nothing),
            'all' (insert everything).
    """
    p = Path(path)
    now = now or datetime.now(UTC).replace(tzinfo=None)
    seller = seller or f"ocr:{p.stem}"

    lines, engine_name, file_type = _lines_for(p, prefer_engine)
    rows = extract_rows(lines)
    counts = {"high": 0, "medium": 0, "low": 0}
    for r in rows:
        counts[r.confidence] = counts.get(r.confidence, 0) + 1

    result = OcrImportResult(
        source=str(p),
        file_type=file_type,
        engine=engine_name,
        lines_total=len(lines),
        rows_extracted=len(rows),
        counts=counts,
        rows=rows,
        pending_review=[r for r in rows if r.confidence == "low"],
    )

    approved = _approved(rows, mode)
    if approved:
        messages = [
            _to_message(r, seller=seller, source_file=str(p), line_no=i, now=now)
            for i, r in enumerate(approved)
        ]
        stats = ingest_messages(
            messages,
            session=session,
            source_type=SourceType.MANUAL,
            group_name=f"ocr-import:{p.name}",
            source_path=str(p),
        )
        result.ingest_stats = stats.as_dict()
        result.inserted = stats.inventory_inserted
    return result


__all__ = ["OcrImportResult", "import_stock"]
