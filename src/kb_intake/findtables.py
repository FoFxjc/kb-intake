"""PyMuPDF find_tables, the rule-based table finder (intake-design-v2 §9). It never decides what a region is: the label
stage uses it for `table_suspected` hints and as the table layer when Docling fails; review uses it to extract the
structure of a region the user reclassified as a table.

A table is {"bbox", "n_rows", "n_cols", "rows"}: `rows[r][c]` is the grid box of that cell, or None where a merged
neighbour covers the position (find_tables has no spans). Boxes are TOPLEFT PDF points, full precision. No text is
returned: cell text is read from the text layer (kb_intake.textlayer).

Both functions let PyMuPDF's exceptions through; callers record them as `extractor_degraded`.
"""

import pymupdf

from kb_intake.geometry import area

METHOD = "find_tables"
# find_tables prints a one-off recommendation to stdout; the CLI's stdout must stay pure JSON
pymupdf.no_recommend_layout()


def _box(b) -> list[float] | None:
    return None if b is None else [float(v) for v in b]


def _shape(t) -> dict:
    return {"bbox": _box(t.bbox), "n_rows": t.row_count, "n_cols": t.col_count,
            "rows": [[_box(c) for c in row.cells] for row in t.rows]}


def tables_on(page: pymupdf.Page) -> list[dict]:
    """Every table find_tables finds on the page, in its detection order."""
    return [_shape(t) for t in page.find_tables().tables]


def table_in(page: pymupdf.Page, bbox) -> dict | None:
    """The largest table find_tables finds inside `bbox` (clip), or None; ties keep the first found."""
    found = [_shape(t) for t in page.find_tables(clip=pymupdf.Rect(bbox)).tables]
    if not found:
        return None
    return max(enumerate(found), key=lambda it: (area(it[1]["bbox"]), -it[0]))[1]
