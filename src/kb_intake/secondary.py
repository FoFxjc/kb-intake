"""Step 5 of the pipeline (intake-design §3.1, §9): the secondary table extractor, PyMuPDF find_tables.

It cross-checks the layout engine's tables, fills in tables the engine missed, and is the whole table layer when the
engine is not available (full degradation). Every candidate is kept in detection order, so table ids are stable.

Cells: `bbox` is the box of the text inside the cell, clipped to the grid box, null for an empty cell (contract §4);
find_tables' own grid box is `grid_bbox`.
find_tables has no spans: every cell is rs = cs = 1, and grid positions covered by a merged neighbour have no cell.
"""

import importlib.util

import pymupdf

from kb_intake.textlayer import parse_value, text_box

METHOD = "find_tables"
# find_tables prints a one-off recommendation to stdout; the CLI's stdout must stay pure JSON
pymupdf.no_recommend_layout()
PYMUPDF_LAYOUT = importlib.util.find_spec("pymupdf.layout") is not None   # would change layout analysis; recorded


def tables_on(page: pymupdf.Page, doc_id: str, page_no: int, words: list) -> tuple[list[dict], list[dict]]:
    """(tables, gaps) of one page. `words` are the page's text-layer words (layout.jsonl order)."""
    try:
        found = page.find_tables()
    except Exception as e:  # noqa: BLE001 - some layouts make table detection fail; skip the page and say so
        return [], [{"code": "extractor_degraded", "page_no": page_no, "extractor": METHOD,
                     "detail": f"{type(e).__name__}: {e}"[:300]}]
    out, gaps = [], []
    for i, t in enumerate(found.tables):
        try:
            out.append(_table(t, doc_id, page_no, i, words))
        except Exception as e:  # noqa: BLE001 - one bad table degrades, it does not fail the document
            gaps.append({"code": "extractor_degraded", "page_no": page_no, "extractor": METHOD,
                         "table_index": i + 1, "detail": f"{type(e).__name__}: {e}"[:300]})
    return out, gaps


def _table(t, doc_id: str, page_no: int, i: int, words: list) -> dict:
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
                          "bbox": text_box(words, box) if (raw[r][c] or "").strip() else None,
                          "grid_bbox": list(box), "is_header": False,
                          "page_no": page_no})
    return {"table_id": f"{doc_id}:t{page_no:04d}_{i + 1:02d}", "region_ids": [], "pages": [page_no],
            "bboxes": [list(t.bbox)], "n_rows": len(t.rows), "n_cols": t.col_count, "cells": cells,
            "header_rows": None, "markdown": t.to_markdown(clean=False).strip(), "extract_method": METHOD,
            "continued_from": None, "checks": {}}
