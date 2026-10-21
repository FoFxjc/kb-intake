"""Scan and classify a file: magic bytes, text layer, language, page labels, per-page kinds; pick the flow.

Rejection is not decided here: detection results go into the manifest and gaps, and the consumer decides.

Language detection is deliberately small: Chinese by the share of CJK characters, English by the share of common
English function words. Anything else is "not detected" (None); a declaration without `lang` then fails with a
message asking for it, instead of guessing.
"""

import re

import pymupdf

from kb_intake import pages
from kb_intake.labels import page_labels

MAGIC = {b"%PDF-": "pdf"}
FLOWS = {"pdf": "A"}            # only flow A (native PDF) is implemented; others come later (intake-design §2)
_WORD = re.compile(r"[A-Za-z]+")
_EN = {"the", "and", "of", "to", "in", "for", "is", "on", "with", "by", "as", "at", "from", "are", "that", "this"}


def sniff(data: bytes) -> str | None:
    return next((kind for magic, kind in MAGIC.items() if data.startswith(magic)), None)


def detect_lang(text: str) -> tuple[str | None, float]:
    """(ISO 639-1 code or None, CJK ratio)."""
    chars = [c for c in text if not c.isspace()]
    cjk = sum(1 for c in chars if "\u4e00" <= c <= "\u9fff") / max(1, len(chars))
    if cjk > 0.2:
        return "zh", cjk
    words = [w.lower() for w in _WORD.findall(text)]
    if len(words) >= 50 and sum(w in _EN for w in words) / len(words) >= 0.05:
        return "en", cjk
    return None, cjk


def detect_pdf(path: str, lang: str | None) -> tuple[dict, list[dict]]:
    """(detection summary, per-page classification rows). `lang` is the declared language, if any."""
    with pymupdf.open(path) as pdf:
        return _detect(pdf, path, lang)


def _detect(pdf: pymupdf.Document, path: str, lang: str | None) -> tuple[dict, list[dict]]:
    text = "".join(p.get_text() for p in pdf)
    detected, cjk = detect_lang(text)
    labels, method = page_labels(path)
    out = {"page_count": len(pdf), "has_text_layer": any(not c.isspace() for c in text), "cjk_ratio": round(cjk, 4),
           "detected_lang": detected,
           "lang_matches": None if lang is None or detected is None else detected == lang,
           "page_label_method": method}
    rows: list[dict] = []
    try:      # classification is a record only; on error degrade and keep the error
        rows = [{"page_no": i + 1, **pages.classify(p)} for i, p in enumerate(pdf)]
        out.update(pages.summary(rows))
    except Exception as e:  # noqa: BLE001
        rows = []
        out["page_kinds_error"] = f"{type(e).__name__}: {e}"[:300]
    out["page_labels"] = labels
    return out, rows
