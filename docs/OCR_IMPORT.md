# OCR Stock Import (Phase 2)

Dealers send stock as WhatsApp screenshots, photos, PDFs, or Excel sheets. This
feature auto-extracts structured inventory from them — local OCR only, no paid
APIs, VPS-friendly.

## Usage

```bash
pip install -e ".[ocr]"          # rapidocr-onnxruntime + pymupdf + pillow
trade-intel ocr <file> [--seller "Dealer Name"] [--engine ENGINE] [--mode MODE] [--json]
```

| Input | Handling |
|---|---|
| Stock image / WhatsApp screenshot (`.jpg/.png/...`) | OCR → text lines |
| PDF | text layer read directly; scanned pages rendered (PyMuPDF) → OCR |
| Excel / CSV | parsed structurally (no OCR needed) |

Extracted per row: **product, quantity, LP/price, condition, storage, location,
MOQ** — reusing the same normalization layer as WhatsApp/CSV/Excel ingest, so
results are consistent across sources. Approved rows are inserted as
`SourceType.MANUAL` inventory attributed to `--seller`.

## OCR engines (preference order)

1. **PaddleOCR** — spec's preferred engine; heaviest (~1.5–2 GB RAM).
2. **RapidOCR** (`rapidocr-onnxruntime`) — PaddleOCR's PP-OCRv4 models via ONNX,
   **~300–500 MB RAM, CPU-only, pip-only (no sudo)**. The default on a cheap VPS.
3. **Tesseract** (`pytesseract` + system binary) — lightweight fallback.

The engine abstraction auto-selects the first installed; `--engine` forces one.
A light post-OCR normalizer fixes common noise (the `lp`↔`Ip` confusion,
tokens glued on tight fonts like `128gblp`/`2900qty`) before field extraction.

## Confidence + human review

Each row is scored **High / Medium / Low**:

- **High** — product recognized + (price or qty) + clean OCR (≥0.85).
- **Medium** — product recognized (thinner data or shakier OCR).
- **Low** — no product recognized, or OCR too noisy → **held for human review**.

Modes: `--mode auto` (default — insert High+Medium, hold Low), `--mode review`
(insert nothing, preview), `--mode all` (approve everything). After an insert
the broker cache is invalidated (Phase 1).

## Validated (real UAE dealer images + synthetic price sheet)

- Synthetic UAE price sheet (PNG + PDF) → 6/6 rows High confidence, correct
  product/price/storage/qty/MOQ/location, inserted into inventory; MOQ kept
  distinct from stock qty (e.g. `Samsung S24 Ultra qty 50 lp 2800` → qty 50,
  price 2800).
- Real dealer images (`data/raw/hot_deal_market/`): OCR confidence 0.95+;
  products extracted where present (e.g. "Galaxy S21 5G" → Samsung, Medium);
  marketing messages correctly yield 0 stock rows — no fabrication.

## Notes / limits

- MOQ is extracted and surfaced for review but not persisted (Inventory has no
  MOQ column — a one-line migration would add it).
- OCR (~300–500 MB) and the LLM model (~2 GB) shouldn't run heavy concurrently
  on an 8 GB box; OCR import is a batch op. RapidOCR loads models lazily.
- `data/samples/ocr_pricesheet.{png,pdf}` are generated test fixtures.
