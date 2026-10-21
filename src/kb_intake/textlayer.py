"""Step 2 of the pipeline (intake-design §3.1): the text layer and word coordinates of a native PDF, from PyMuPDF.

Coordinates are PDF points with a TOPLEFT origin and are kept at full precision (intake-design §4).
Also here: `parse_value`, the number rule every table cell goes through, whichever extractor found the table.
"""

import re

import pymupdf

# Exactly one number: optional sign, digits with well-formed thousands separators, optional decimals, optionally in
# parentheses (negative). No inner whitespace: "2023 2024" is two numbers, not 20232024.
_NUM = re.compile(r"(?P<open>\()?(?P<sign>[-+−])?(?P<int>\d{1,3}(?:,\d{3})+|\d+)(?P<dec>\.\d+)?(?(open)\))")
_NIL = {"-", "–", "—"}          # hyphen, en dash, em dash: "nil" in financial tables, not a sign


def parse_value(text: str | None) -> tuple[float | int | None, bool]:
    """(value, nil) of one cell. Thousands separators dropped, parentheses mean negative, a lone dash means nil.
    Anything that is not exactly one number (two numbers, words, a sign inside parentheses) gives no value."""
    if text is None:
        return None, False
    t = text.replace("\x07", "").replace("\x03", "").strip()
    if t in _NIL:
        return None, True
    m = _NUM.fullmatch(t)
    if not m or (m["open"] and m["sign"]):
        return None, False
    digits = m["int"].replace(",", "") + (m["dec"] or "")
    v = float(digits) if m["dec"] else int(digits)
    return (-v if m["open"] or m["sign"] in ("-", "−") else v), False


def page_record(page: pymupdf.Page, page_no: int, label: str | None, kind: dict | None) -> dict:
    """One pages.jsonl row: the text layer as PyMuPDF returns it (no cleaning)."""
    rec = {"page_no": page_no, "page_label": label, "kind": kind["kind"] if kind else None,
           "text": page.get_text(), "text_source": "native", "ocr_confidence": None,
           "width": page.rect.width, "height": page.rect.height, "docling_confidence": None}
    if kind:
        rec.update(chars=kind["chars"], image_regions=kind["regions"], images_discarded=kind["discarded"])
    return rec


def layout_record(page: pymupdf.Page, page_no: int) -> dict:
    """One layout.jsonl row: every word in PyMuPDF order, [x0, y0, x1, y1, text, source, conf]."""
    return {"page_no": page_no, "width": page.rect.width, "height": page.rect.height,
            "words": [[w[0], w[1], w[2], w[3], w[4], "native", None] for w in page.get_text("words")]}


def text_box(words: list, region) -> list[float] | None:
    """The contract's cell bbox: the box of the text in the cell, clipped to the cell's own box `region` (the grid
    box of find_tables, the engine's cell box). Words are those whose centre lies in the region, else those that
    overlap it; when none does, the region itself. Callers give None for an empty cell."""
    def centred(w):
        return region[0] <= (w[0] + w[2]) / 2 <= region[2] and region[1] <= (w[1] + w[3]) / 2 <= region[3]

    def overlaps(w):
        return w[0] < region[2] and w[2] > region[0] and w[1] < region[3] and w[3] > region[1]
    inside = [w for w in words if centred(w)] or [w for w in words if overlaps(w)]
    if not inside:
        return list(region)
    return [max(region[0], min(w[0] for w in inside)), max(region[1], min(w[1] for w in inside)),
            min(region[2], max(w[2] for w in inside)), min(region[3], max(w[3] for w in inside))]
