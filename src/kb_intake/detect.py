"""Scan and classify a file: magic bytes, text layer, language, page labels, per-page kinds; pick the flow.

The PDF part comes from kb `pipeline/ingest.detect()` (including kb #29's per-page classification), moved in the
D49 split. Rejection is not decided here: detection results go into the manifest and gaps, and kb decides at import.
"""

import pymupdf

from kb_intake import pages
from kb_intake.labels import page_labels

MAGIC = {b"%PDF-": "pdf"}
FLOWS = {"pdf": "A"}            # only flow A (native PDF) is implemented; others come later (intake-design §2)


def sniff(data: bytes) -> str | None:
    return next((kind for magic, kind in MAGIC.items() if data.startswith(magic)), None)


def detect_pdf(path: str, lang: str) -> tuple[dict, list[dict]]:
    """(detection summary, per-page classification rows)."""
    pdf = pymupdf.open(path)
    text = "".join(p.get_text() for p in pdf)
    chars = [c for c in text if not c.isspace()]
    cjk = sum(1 for c in chars if "\u4e00" <= c <= "\u9fff") / max(1, len(chars))
    detected_lang = "zh" if cjk > 0.2 else "en"
    labels, method = page_labels(path)
    out = {"page_count": len(pdf), "has_text_layer": bool(chars), "cjk_ratio": round(cjk, 4),
           "detected_lang": detected_lang, "lang_matches": detected_lang == lang, "page_label_method": method}
    rows: list[dict] = []
    try:      # classification is a record only; on error degrade and keep the error (kb review M4)
        rows = [{"page_no": i + 1, **pages.classify(p)} for i, p in enumerate(pdf)]
        out.update(pages.summary(rows))
    except Exception as e:  # noqa: BLE001
        rows = []
        out["page_kinds_error"] = f"{type(e).__name__}: {e}"[:300]
    out["page_labels"] = labels
    return out, rows
