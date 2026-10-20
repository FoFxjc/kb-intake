"""Flow A (native PDF): text layer, word coordinates and table geometry, straight from PyMuPDF.

Moved from kb `pipeline/build.py` in the D49 split: the layout writer and the PDF-only half of `tables_of()`
(find tables, cells, cell boxes). The business reading of a table (`structure`, unit hints, entity words) needs kb's
type configuration and stays in kb, which now reads the cells and words from the package.

Coordinates are PDF points with a TOPLEFT origin and are kept at full precision (intake-design §4).
"""

import re

import pymupdf

TABLE_METHOD = "find_tables"
_NUM = re.compile(r"\(?[-+]?\d[\d,]*(?:\.\d+)?\)?")
_NIL = {"-", "–", "—"}          # hyphen, en dash, em dash: "nil" in financial tables, not a sign


def parse_value(text: str | None) -> tuple[float | int | None, bool]:
    """(value, nil) of one cell. Thousands separators dropped, parentheses mean negative, a lone dash means nil."""
    if text is None:
        return None, False
    t = text.replace("\x07", "").replace("\x03", "").strip()
    if t in _NIL:
        return None, True
    t = t.replace(" ", "")
    if not _NUM.fullmatch(t) or (t.startswith("(") != t.endswith(")")):
        return None, False
    neg = t.startswith("(")
    t = t.strip("()").replace(",", "")
    v = float(t) if "." in t else int(t)
    return (-v if neg else v), False


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
    out = []
    for i, t in enumerate(found.tables):
        raw = t.extract()
        if len(raw) != len(t.rows) or any(len(r.cells) != t.col_count for r in t.rows):
            raise ValueError(f"p{page_no} table {i + 1}: irregular find_tables grid")   # contract assumes a full grid
        cells = []
        for r, row in enumerate(t.rows):
            for c, box in enumerate(row.cells):
                if box is None:          # covered by a merged neighbour: no cell of its own
                    continue
                value, nil = parse_value(raw[r][c])
                cells.append({"r": r, "c": c, "rs": 1, "cs": 1, "text": raw[r][c], "value": value, "nil": nil,
                              "bbox": list(box), "is_header": False})
        md = t.to_markdown(clean=False).strip()
        out.append({"table_id": f"{doc_id}:t{page_no:04d}_{i + 1:02d}", "region_ids": [], "pages": [page_no],
                    "bboxes": [list(t.bbox)], "n_rows": len(t.rows), "n_cols": t.col_count, "cells": cells,
                    "header_rows": None, "markdown": md, "extract_method": TABLE_METHOD, "continued_from": None,
                    "checks": {}})
    return out, []
