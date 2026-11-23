"""PDF handling for OCR import, via PyMuPDF (pip-only, no poppler / sudo).

A PDF page is handled in the cheapest way that works:

* If the page has an embedded text layer (digital price sheet / exported
  Excel-to-PDF), read it directly — no OCR, near-zero cost.
* Otherwise (scanned image / photo PDF) render the page to a bitmap and hand
  it to the OCR engine.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PdfPage:
    """One PDF page: either extracted text, or a rendered image for OCR."""

    index: int
    text: str | None  # set when the page has a usable text layer
    image: object | None  # numpy RGB array when the page must be OCR'd

    @property
    def needs_ocr(self) -> bool:
        return self.text is None


#: Below this many text-layer chars a page is treated as scanned → OCR.
_MIN_TEXT_CHARS = 20
#: Render DPI for scanned pages (150 is a good speed/accuracy tradeoff on CPU).
_RENDER_DPI = 150


def load_pdf_pages(path: str) -> list[PdfPage]:
    """Return one :class:`PdfPage` per page. Pages with a real text layer carry
    ``text``; scanned pages carry a rendered ``image`` (numpy array) for OCR."""
    import fitz  # PyMuPDF
    import numpy as np

    pages: list[PdfPage] = []
    with fitz.open(path) as doc:
        for i, page in enumerate(doc):
            text = page.get_text("text") or ""
            if len(text.strip()) >= _MIN_TEXT_CHARS:
                pages.append(PdfPage(index=i, text=text, image=None))
                continue
            # Scanned / image page → render to an RGB numpy array for OCR.
            pix = page.get_pixmap(dpi=_RENDER_DPI)
            arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, pix.n
            )
            if pix.n == 4:  # RGBA → RGB
                arr = arr[:, :, :3]
            pages.append(PdfPage(index=i, text=None, image=arr.copy()))
    return pages


__all__ = ["PdfPage", "load_pdf_pages"]
