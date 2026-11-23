"""Local OCR engine abstraction (no paid APIs, VPS-friendly).

Engine selection order (first importable wins, override via ``prefer=``):

1. **PaddleOCR** — the spec's preferred engine. Heaviest (paddlepaddle,
   ~1.5-2 GB RAM); use on a roomier box.
2. **RapidOCR** (``rapidocr-onnxruntime``) — runs PaddleOCR's PP-OCRv4 models
   via ONNX Runtime at ~300-500 MB RAM, CPU-only, pip-install-only (no system
   deps / sudo). This is the default on a cheap 8 GB VPS.
3. **Tesseract** (``pytesseract`` + the ``tesseract`` binary) — lightweight
   fallback; needs the system package installed.

All engines return a uniform ``list[OcrLine]`` (text + confidence in [0,1]),
so the extraction layer is engine-agnostic. Imports are lazy so the OCR extra
is optional and a missing engine never breaks the rest of the app.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class OcrLine:
    """One recognized text line with its OCR confidence (0..1)."""

    text: str
    confidence: float


class OcrError(RuntimeError):
    """No OCR engine is available, or recognition failed."""


def _have(mod: str) -> bool:
    return importlib.util.find_spec(mod) is not None


def available_engines() -> list[str]:
    """Names of OCR engines installed on this box, in preference order."""
    found: list[str] = []
    if _have("paddleocr"):
        found.append("paddleocr")
    if _have("rapidocr_onnxruntime"):
        found.append("rapidocr")
    if _have("pytesseract"):
        found.append("tesseract")
    return found


class _PaddleEngine:
    name = "paddleocr"

    def __init__(self) -> None:
        from paddleocr import PaddleOCR

        self._ocr = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)

    def recognize(self, image: Any) -> list[OcrLine]:
        result = self._ocr.ocr(image, cls=True)
        lines: list[OcrLine] = []
        for page in result or []:
            for box in page or []:
                # box = [coords, (text, confidence)]
                text, conf = box[1][0], float(box[1][1])
                if text.strip():
                    lines.append(OcrLine(text.strip(), conf))
        return lines


class _RapidEngine:
    name = "rapidocr (PP-OCRv4)"

    def __init__(self) -> None:
        from rapidocr_onnxruntime import RapidOCR

        self._ocr = RapidOCR()

    def recognize(self, image: Any) -> list[OcrLine]:
        result, _ = self._ocr(image)
        lines: list[OcrLine] = []
        for item in result or []:
            # item = [box, text, confidence]
            text, conf = str(item[1]), float(item[2])
            if text.strip():
                lines.append(OcrLine(text.strip(), conf))
        return lines


class _TesseractEngine:
    name = "tesseract"

    def __init__(self) -> None:
        import pytesseract  # noqa: F401  (import-time check)

    def recognize(self, image: Any) -> list[OcrLine]:
        import pytesseract
        from PIL import Image

        img = image
        if isinstance(image, str):
            img = Image.open(image)
        data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
        # Group words into lines via (block, par, line) and average confidence.
        groups: dict[tuple[int, int, int], list[tuple[str, float]]] = {}
        for i, word in enumerate(data["text"]):
            w = word.strip()
            if not w:
                continue
            conf = float(data["conf"][i])
            if conf < 0:
                continue
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            groups.setdefault(key, []).append((w, conf / 100.0))
        lines: list[OcrLine] = []
        for key in sorted(groups):
            words = groups[key]
            text = " ".join(w for w, _ in words)
            conf = sum(c for _, c in words) / len(words)
            lines.append(OcrLine(text, conf))
        return lines


def get_engine(prefer: str | None = None):
    """Return an initialized OCR engine. ``prefer`` forces a specific engine
    ('paddleocr' | 'rapidocr' | 'tesseract'); otherwise the first available in
    preference order is used."""
    order = ["paddleocr", "rapidocr", "tesseract"]
    if prefer:
        order = [prefer] + [e for e in order if e != prefer]

    factories = {
        "paddleocr": (_have("paddleocr"), _PaddleEngine),
        "rapidocr": (_have("rapidocr_onnxruntime"), _RapidEngine),
        "tesseract": (_have("pytesseract"), _TesseractEngine),
    }
    last_err: Exception | None = None
    for name in order:
        ok, factory = factories[name]
        if not ok:
            continue
        try:
            return factory()
        except Exception as exc:  # engine present but failed to init
            last_err = exc
            continue
    msg = (
        "no OCR engine available — install one of: "
        "`pip install rapidocr-onnxruntime` (light, recommended), "
        "`pip install paddleocr paddlepaddle`, or the tesseract binary + "
        "`pip install pytesseract`."
    )
    if last_err:
        raise OcrError(f"{msg} (last init error: {last_err})")
    raise OcrError(msg)


__all__ = ["OcrError", "OcrLine", "available_engines", "get_engine"]
