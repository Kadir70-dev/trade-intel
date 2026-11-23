"""Turn OCR text lines into structured, confidence-scored inventory rows.

Reuses the existing, tested normalization layer — :func:`extract_products`
(canonical product) and :func:`extract_structured` (storage / qty / price /
condition / location) — so OCR import stays consistent with WhatsApp / CSV /
Excel ingest. The only OCR-specific additions are MOQ extraction and the
High / Medium / Low confidence score that drives human review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from trade_intel.normalization import extract_products
from trade_intel.normalization.structured import extract_structured
from trade_intel.ocr.engine import OcrLine

# MOQ: "MOQ 20", "moq:20", "min order 50", "minimum qty 10".
_MOQ_RE = re.compile(
    r"\b(?:moq|min(?:imum)?(?:\s*(?:order|qty|quantity))?)\s*[:\-]?\s*(\d{1,5})\b",
    re.IGNORECASE,
)

# ---- post-OCR normalization (fix common OCR noise before extraction) ----
# OCR confuses the price marker "lp" with "Ip" (lowercase-L vs capital-I) when
# it stands alone before a number. iPhone is safe ("ip" has no word boundary
# inside "iphone").
_OCR_IP_TO_LP = re.compile(r"(?i)\bip\b(?=\s*\d)")
# Markers that OCR often glues to neighbouring tokens on tight fonts.
_OCR_MARKER_BEFORE = re.compile(r"(?i)(?<=[a-z0-9])(lp|aed|qty|moq|pcs)\b")
_OCR_MARKER_NUM = re.compile(r"(?i)\b(lp|aed|qty|moq)(\d)")
_OCR_NUM_WORD = re.compile(r"(?i)(\d)(lp|qty|moq|used|new|sealed|fresh)\b")


def _normalize_ocr(text: str) -> str:
    """De-noise an OCR line before field extraction: fix the l/I price-marker
    confusion and split markers OCR glued to neighbours (e.g. '128gblp',
    '2900qty', 'lp2400', '2400used'). Conservative — only known trade markers."""
    t = f" {text} "
    t = _OCR_IP_TO_LP.sub("lp", t)
    t = _OCR_MARKER_NUM.sub(r"\1 \2", t)
    t = _OCR_NUM_WORD.sub(r"\1 \2", t)
    t = _OCR_MARKER_BEFORE.sub(r" \1", t)
    return re.sub(r"\s+", " ", t).strip()


_MOQ_MARKER_RE = re.compile(r"(?i)^(?:moq|min)")

# Confidence thresholds on the OCR engine's own line confidence.
_OCR_HIGH = 0.85
_OCR_OK = 0.60


@dataclass(slots=True)
class ExtractedRow:
    """One candidate inventory row extracted from an OCR line."""

    raw_text: str
    ocr_confidence: float
    product: str | None = None
    quantity: int | None = None
    price_aed: float | None = None
    condition: str | None = None
    storage: str | None = None
    location: str | None = None
    moq: int | None = None
    confidence: str = "low"  # high | medium | low
    reasons: list[str] = field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        return self.confidence == "low"

    def as_dict(self) -> dict:
        return {
            "raw_text": self.raw_text,
            "ocr_confidence": round(self.ocr_confidence, 3),
            "product": self.product,
            "quantity": self.quantity,
            "price_aed": self.price_aed,
            "condition": self.condition,
            "storage": self.storage,
            "location": self.location,
            "moq": self.moq,
            "confidence": self.confidence,
            "reasons": self.reasons,
        }


def _extract_moq(text: str) -> int | None:
    m = _MOQ_RE.search(text)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _score(product: str | None, price, qty, ocr_conf: float) -> tuple[str, list[str]]:
    """High / Medium / Low confidence + human-readable reasons.

    High   = product recognized + (price or qty) + clean OCR.
    Medium = product recognized (thinner data, or shakier OCR).
    Low    = no product recognized, or OCR too noisy → needs human review.
    """
    reasons: list[str] = []
    if product:
        reasons.append("product recognized")
    else:
        reasons.append("no known product")
    has_data = price is not None or qty is not None
    if has_data:
        reasons.append("price/qty present")
    if ocr_conf >= _OCR_HIGH:
        reasons.append(f"clean OCR ({ocr_conf:.2f})")
    elif ocr_conf >= _OCR_OK:
        reasons.append(f"ok OCR ({ocr_conf:.2f})")
    else:
        reasons.append(f"noisy OCR ({ocr_conf:.2f})")

    if not product or ocr_conf < _OCR_OK:
        return "low", reasons
    if has_data and ocr_conf >= _OCR_HIGH:
        return "high", reasons
    return "medium", reasons


def extract_row(line: OcrLine) -> ExtractedRow | None:
    """Extract one structured row from an OCR line. Returns None when the line
    has no product-like content (skip silently)."""
    text = _normalize_ocr(line.text)
    products = extract_products(text)
    sf = extract_structured(text)
    moq = _extract_moq(text)

    # MOQ leaks into quantity ("moq 10" reads as qty 10) — keep them distinct:
    # a quantity sourced from a moq/min marker is the MOQ, not stock on hand.
    quantity = sf.quantity
    if sf.quantity_raw and _MOQ_MARKER_RE.match(sf.quantity_raw.strip()):
        if moq is None:
            moq = sf.quantity
        quantity = None

    product = products[0].canonical.canonical_name if products else None
    # Skip pure noise: no product AND no price AND no qty — nothing to import.
    if product is None and sf.price_aed is None and quantity is None:
        return None

    conf, reasons = _score(product, sf.price_aed, quantity, line.confidence)
    return ExtractedRow(
        raw_text=text,
        ocr_confidence=line.confidence,
        product=product,
        quantity=quantity,
        price_aed=sf.price_aed,
        condition=sf.condition.value if sf.condition else None,
        storage=sf.storage,
        location=sf.location,
        moq=moq,
        confidence=conf,
        reasons=reasons,
    )


def extract_rows(lines: list[OcrLine]) -> list[ExtractedRow]:
    """Extract structured rows from OCR lines, dropping content-free lines."""
    rows: list[ExtractedRow] = []
    for line in lines:
        row = extract_row(line)
        if row is not None:
            rows.append(row)
    return rows


__all__ = ["ExtractedRow", "extract_row", "extract_rows"]
