"""Flow A (native PDF): text layer, word coordinates and table geometry, straight from PyMuPDF.

Moved from kb `pipeline/build.py` in the D49 split: the layout writer and the PDF-only half of `tables_of()`
(find tables, cells, cell boxes). The business reading of a table (`structure`, unit hints, entity words) needs kb's
type configuration and stays in kb, which now reads the cells and words from the package.

Coordinates are PDF points with a TOPLEFT origin and are kept at full precision (intake-design §4).
"""

import importlib.util
import re

import pymupdf

TABLE_METHOD = "find_tables"
# find_tables prints a one-off recommendation to stdout; the CLI's stdout must stay pure JSON (review S2)
pymupdf.no_recommend_layout()
PYMUPDF_LAYOUT = importlib.util.find_spec("pymupdf.layout") is not None   # would change layout analysis; recorded
# Exactly one number: optional sign, digits with well-formed thousands separators, optional decimals, optionally in
# parentheses (negative). No inner whitespace: "2023 2024" is two numbers, not 20232024 (review B1).
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
    """One pages.jsonl row: text layer as PyMuPDF returns it (no cleaning in flow A, step 2)."""
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


def tables_on(page: pymupdf.Page, doc_id: str, page_no: int) -> tuple[list[dict], list[dict]]:
    """(tables, gaps) of one page. Every find_tables candidate is kept, in detection order, so that table ids and
    the "other tables on this page" that kb's structure needs stay complete; kb applies its own filters."""
    try:
        found = page.find_tables()
    except Exception as e:  # noqa: BLE001 - some layouts make table detection fail; skip the page and say so
        return [], [{"code": "extractor_degraded", "page_no": page_no, "extractor": TABLE_METHOD,
                     "detail": f"{type(e).__name__}: {e}"[:300]}]
    out, gaps = [], []
    for i, t in enumerate(found.tables):
        try:
            out.append(_table(t, doc_id, page_no, i))
        except Exception as e:  # noqa: BLE001 - one bad table degrades, it does not fail the document (§9.3.5)
            gaps.append({"code": "extractor_degraded", "page_no": page_no, "extractor": TABLE_METHOD,
                         "table_index": i + 1, "detail": f"{type(e).__name__}: {e}"[:300]})
    return out, gaps


def _table(t, doc_id: str, page_no: int, i: int) -> dict:
    """One find_tables table. find_tables has no spans: every cell is rs = cs = 1, and grid positions covered by a
    merged neighbour have no cell of their own (the owner's bbox spans them)."""
    raw = t.extract()
    if len(raw) != len(t.rows) or any(len(r.cells) != t.col_count for r in t.rows):
        raise ValueError("irregular find_tables grid")      # the contract describes a full n_rows x n_cols grid
    cells = []
    for r, row in enumerate(t.rows):
        for c, box in enumerate(row.cells):
            if box is None:
                continue
            value, nil = parse_value(raw[r][c])
            cells.append({"r": r, "c": c, "rs": 1, "cs": 1, "text": raw[r][c], "value": value, "nil": nil,
                          "bbox": list(box), "is_header": False, "page_no": page_no})
    return {"table_id": f"{doc_id}:t{page_no:04d}_{i + 1:02d}", "region_ids": [], "pages": [page_no],
            "bboxes": [list(t.bbox)], "n_rows": len(t.rows), "n_cols": t.col_count, "cells": cells,
            "header_rows": None, "markdown": t.to_markdown(clean=False).strip(), "extract_method": TABLE_METHOD,
            "continued_from": None, "checks": {}}
