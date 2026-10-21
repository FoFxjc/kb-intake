"""Docling post-processing (intake-design §9.2 steps 4-6): regions, tables, and the three checks.

Everything here is a deterministic rule on top of the cached DoclingDocument, the text layer (layout.jsonl words)
and the find_tables candidates. No model is called.

Tables
- Docling (TableFormer) gives the row/column structure; cells keep spans (rs/cs) and TOPLEFT boxes.
- Control characters \\x07 and \\x03 are removed from cell text (they come from the PDF text layer itself).
- A cell holding only a dash is nil (value None, nil True); negatives are parentheses only (native.parse_value).
- Header cells are re-read from the text layer inside the cell box; when Docling's text differs, the text layer
  wins and the cell is reported as `table_header_suspect`.
- A table that continues on the next page (same column headers, and the second part starts at the top of the page
  or follows a "(continued)" title) is merged: extract_method "merged", both pages and boxes kept.
Checks, strongest first
1. Text conservation: every number word of the text layer inside the table box must appear in the cells exactly as
   often, and every number in the cells must be in the text layer. Missing words -> `table_text_unaccounted`
   (with page and word box); numbers only in the cells -> `table_cross_mismatch` (source "text_layer").
2. Docling vs find_tables: tables matched by box (IoU >= 0.5 or one inside the other); different number multisets
   -> `table_cross_mismatch` (source "find_tables"). The find_tables result stays in the package (role secondary).
3. Missed tables: a find_tables table that no Docling table matches is routed by what Docling says is there: a
   picture or chart -> the model wins and the candidate is marked as a misread; text or nothing, and the candidate
   passes text conservation -> it is entered as a table (extract_method find_tables) with `table_missed`;
   otherwise only `table_missed`.
"""

import re
from collections import Counter

from kb_intake.native import parse_value

# Docling labels -> layout-design §2 region types
REGION_TYPE = {
    "title": "title", "section_header": "heading", "text": "text", "paragraph": "text", "reference": "text",
    "list_item": "list", "caption": "caption", "footnote": "footnote", "page_header": "page_header",
    "page_footer": "page_footer", "formula": "formula", "code": "text", "document_index": "toc", "table": "table",
    "picture": "picture", "chart": "chart",
}
DISCARDED = {"picture", "logo", "icon"}
NATIVE_TEXT_MIN, NATIVE_TEXT_DENSITY = 6, 100      # same thresholds as pages.py: picture with native text = diagram
CONTINUED = re.compile(r"\(continued\)|\uff08\u7eed\uff09", re.I)


def clean(text: str | None) -> str | None:
    return None if text is None else text.replace("\x07", "").replace("\x03", "").strip()


def topleft(bbox: dict, page_h: float) -> list[float]:
    if bbox.get("coord_origin") == "BOTTOMLEFT":
        return [bbox["l"], page_h - bbox["t"], bbox["r"], page_h - bbox["b"]]
    return [bbox["l"], bbox["t"], bbox["r"], bbox["b"]]


def _area(b) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _inter(a, b) -> float:
    return _area([max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])])


def same_box(a, b) -> bool:
    """IoU >= 0.5, or one box (almost) inside the other."""
    i = _inter(a, b)
    if not i:
        return False
    return i / (_area(a) + _area(b) - i) >= 0.5 or i / max(1e-9, min(_area(a), _area(b))) >= 0.9


def _center_in(w, box, pad: float = 1.0) -> bool:
    cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
    return box[0] - pad <= cx <= box[2] + pad and box[1] - pad <= cy <= box[3] + pad


_NUMBER_IN = re.compile(r"(?<![\w.,])\(?[-+\u2212\u2013]?\d[\d,]*(?:\.\d+)?\)?%?")
_SIGNS = "+-\u2212\u2013"


def numbers(text: str | None) -> list[str]:
    """Number tokens of a text, normalised to the digits (no parentheses, sign, percent sign or control characters).
    Found anywhere in the string, not by splitting on spaces, so a cell "2022 (1)" and a text-layer word "2022(1)" give
    the same tokens. The sign is dropped like the parentheses: Docling writes a hyphen where the text layer has an
    en dash or a minus sign ("-100" against "\u2013100"), and the check is that the text is there, not its sign."""
    out = []
    for m in _NUMBER_IN.finditer(clean(text) or ""):
        tok = m.group(0).rstrip(",")
        out.append(tok.strip("()%").lstrip(_SIGNS))
    return out


# ---------- the DoclingDocument ----------

def walk(doc: dict):
    """Items in reading order: body first, then furniture; groups (lists, etc.) are entered."""
    index = {}
    for kind in ("texts", "tables", "pictures", "groups", "key_value_items", "form_items"):
        for item in doc.get(kind) or []:
            index[item["self_ref"]] = item

    def visit(node):
        for ref in node.get("children") or []:
            item = index.get(ref["$ref"])
            if item is None:
                continue
            if item["self_ref"].startswith("#/groups/"):
                yield from visit(item)
            else:
                yield item
                yield from visit(item)
    yield from visit(doc["body"])
    yield from visit(doc.get("furniture") or {})


def page_heights(doc: dict) -> dict[int, float]:
    return {int(k): v["size"]["height"] for k, v in doc["pages"].items()}


# ---------- regions ----------

def regions(doc: dict, doc_id: str, page_text, layout_model: str, layout_version: str) -> tuple[list[dict], dict]:
    """regions.jsonl rows and {docling self_ref: region_id}. page_text(page_no, bbox) -> native characters inside."""
    heights = page_heights(doc)
    per_page: dict[int, list[dict]] = {}
    refs = {}
    order = 0
    for item in walk(doc):
        if not item.get("prov"):
            continue
        prov = item["prov"][0]
        page_no = prov["page_no"]
        box = topleft(prov["bbox"], heights[page_no])
        label = item.get("label", "unknown")
        rtype = REGION_TYPE.get(label, "unknown")
        type_source, agree = "model", None
        if rtype == "picture":
            chars = page_text(page_no, box)
            if chars >= max(NATIVE_TEXT_MIN, NATIVE_TEXT_DENSITY * _ratio(box, doc, page_no)):
                rtype, type_source, agree = "diagram", "fused", False      # infographic: native text on the image
        reg = {"page_no": page_no, "bbox": box, "type": rtype, "subtype": None, "score": None,
               "type_source": type_source, "agree": agree, "text_source": "native", "reading_order": order,
               "status": "discarded" if rtype in DISCARDED else "kept", "ref": None,
               "layout_model": layout_model, "layout_version": layout_version, "docling_ref": item["self_ref"],
               "content_layer": item.get("content_layer", "body")}
        order += 1
        per_page.setdefault(page_no, []).append(reg)
    rows = []
    for page_no in sorted(per_page):
        for n, reg in enumerate(per_page[page_no], 1):
            reg["region_id"] = f"{doc_id}:p{page_no:04d}:r{n:03d}"
            refs[reg.pop("docling_ref")] = reg["region_id"]
            rows.append({"region_id": reg.pop("region_id"), **reg})
    return rows, refs


def _ratio(box, doc, page_no) -> float:
    size = doc["pages"][str(page_no)]["size"]
    return _area(box) / max(1e-9, size["width"] * size["height"])


def native_only(rows: list[dict], blocks_by_page: dict[int, list], doc_id: str, start_order: int,
                layout_model: str, layout_version: str) -> list[dict]:
    """Native text blocks that no region covers become `text` regions with type_source native (layout-design §3.2)."""
    out, order = [], start_order
    by_page: dict[int, list] = {}
    for r in rows:
        by_page.setdefault(r["page_no"], []).append(r["bbox"])
    for page_no in sorted(blocks_by_page):
        n = sum(1 for r in rows if r["page_no"] == page_no)
        for b in blocks_by_page[page_no]:
            if any(_center_in(b, box, 2.0) for box in by_page.get(page_no, [])):
                continue
            n += 1
            out.append({"region_id": f"{doc_id}:p{page_no:04d}:r{n:03d}", "page_no": page_no, "bbox": list(b[:4]),
                        "type": "text", "subtype": None, "score": None, "type_source": "native", "agree": False,
                        "text_source": "native", "reading_order": order, "status": "kept", "ref": None,
                        "layout_model": layout_model, "layout_version": layout_version, "content_layer": "body",
                        "native_only": True})
            order += 1
    return out


# ---------- tables ----------

def _markdown(n_rows: int, n_cols: int, cells: list[dict]) -> str:
    grid = [[""] * n_cols for _ in range(n_rows)]
    for c in cells:
        for r in range(c["r"], min(n_rows, c["r"] + c["rs"])):
            for k in range(c["c"], min(n_cols, c["c"] + c["cs"])):
                grid[r][k] = (c["text"] or "").replace("\n", " ").replace("|", "\\|")
    if not grid:
        return ""
    lines = ["|" + "|".join(grid[0]) + "|", "|" + "|".join(["---"] * n_cols) + "|"]
    lines += ["|" + "|".join(row) + "|" for row in grid[1:]]
    return "\n".join(lines)


_FOLD = str.maketrans({"\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-"})


def _fold(x: str | None) -> str:
    return "".join((x or "").translate(_FOLD).split())


def _same_text(a: str | None, b: str | None) -> bool:
    """Equal up to spacing, curly quotes and dash style (superscript markers often lose their space)."""
    return _fold(a) == _fold(b)


def _reading_order(words: list) -> str:
    """PyMuPDF words in the text layer's own order (block, line, word); keeps superscripts where they belong."""
    if words and len(words[0]) >= 8:
        words = sorted(words, key=lambda w: (w[5], w[6], w[7]))
    else:
        words = sorted(words, key=lambda w: (round(w[3]), w[0]))
    return " ".join(w[4] for w in words)


def docling_tables(doc: dict, words_by_page: dict[int, list], page_words=None) -> tuple[list[dict], list[dict]]:
    """Docling tables in reading order with post-processing (header re-read, nil, cleaning). Ids are given later.
    page_words(page_no) -> PyMuPDF words with block / line / word numbers (reading order for the header re-read);
    defaults to words_by_page."""
    page_words = page_words or (lambda p: words_by_page.get(p, []))
    heights = page_heights(doc)
    tables, gaps = [], []
    for item in walk(doc):
        if item.get("label") not in ("table", "document_index") or not item.get("prov") or "data" not in item:
            continue
        prov = item["prov"][0]
        page_no, data = prov["page_no"], item["data"]
        box = topleft(prov["bbox"], heights[page_no])
        cells, seen = [], set()
        for c in data["table_cells"]:
            key = (c["start_row_offset_idx"], c["start_col_offset_idx"])
            if key in seen:
                continue
            seen.add(key)
            text = clean(c.get("text"))
            cbox = topleft(c["bbox"], heights[page_no]) if c.get("bbox") else None
            cell = {"r": key[0], "c": key[1], "rs": max(1, c.get("row_span") or 1), "cs": max(1, c.get("col_span") or 1),
                    "text": text, "value": None, "nil": False, "bbox": cbox, "is_header": bool(c.get("column_header")),
                    "page_no": page_no}
            if cell["is_header"] and cbox:
                inside = [w for w in page_words(page_no) if _inter(w, cbox) >= 0.5 * max(1e-9, _area(w))]
                layer = clean(_reading_order(inside)) if inside else None
                if layer and not _same_text(text, layer):
                    # The text layer wins when it holds everything Docling read (it restores dropped words and the
                    # true order); when it lacks characters the cell box was too small: keep Docling, still report.
                    wins = not (Counter(_fold(text)) - Counter(_fold(layer)))
                    gaps.append({"code": "table_header_suspect", "page_no": page_no, "r": key[0], "c": key[1],
                                 "docling": text, "text_layer": layer, "ref": item["self_ref"],
                                 "resolution": "text_layer" if wins else "kept_docling"})
                    if wins:
                        cell["text"] = layer
            cell["value"], cell["nil"] = parse_value(cell["text"])
            cells.append(cell)
        cells = [c for c in cells if c["bbox"] is not None] + [c for c in cells if c["bbox"] is None and c["text"]]
        header_rows = 0
        while any(c["r"] == header_rows and c["is_header"] for c in cells):
            header_rows += 1
        tables.append({"docling_ref": item["self_ref"], "pages": [page_no], "bboxes": [box],
                       "n_rows": data["num_rows"], "n_cols": data["num_cols"], "cells": cells,
                       "header_rows": header_rows, "extract_method": "docling", "continued_from": None,
                       "role": "primary", "checks": {}})
    # Docling may give a cell without a box (empty cells); the contract wants a box on every cell
    for t in tables:
        missing = [c for c in t["cells"] if c["bbox"] is None]
        if missing:
            t["cells"] = [c for c in t["cells"] if c["bbox"] is not None]
            gaps.append({"code": "table_cells_dropped", "page_no": t["pages"][0], "ref": t["docling_ref"],
                         "detail": f"{len(missing)} cell(s) without a box: {[c['text'] for c in missing][:5]}"})
    return tables, gaps


def _headers(t: dict) -> list[str]:
    hdr = {}
    for c in t["cells"]:
        if c["is_header"]:
            hdr.setdefault(c["c"], []).append(c["text"] or "")
    return [" ".join(hdr.get(k, [])) for k in range(t["n_cols"])]


def heading_key(text: str | None) -> str | None:
    """A heading without "(continued)", control characters, case and spacing: the same heading on two pages."""
    if text is None:
        return None
    return " ".join(CONTINUED.sub("", clean(text) or "").lower().split())


def merge_continued(tables: list[dict], context) -> tuple[list[dict], list[dict]]:
    """Merge a table with its continuation on the next page. context(t) -> (heading above the table on its own
    page or None, heading the table sits under, whether other content stands between that heading and the table).
    The second part continues the first when the column headers are the same, nothing but its heading precedes it
    on its page, and that heading is absent or a "(continued)" of the heading the first part sits under.
    Returns (tables, gaps)."""
    out: list[dict] = []
    gaps: list[dict] = []
    first_on_page = {}
    for t in tables:
        first_on_page.setdefault(t["pages"][0], t)
    for t in tables:
        prev = out[-1] if out else None
        page = t["pages"][0]
        own, _, between = context(t) if prev else (None, None, True)
        if (prev and prev["pages"][-1] == page - 1 and first_on_page.get(page) is t and prev["n_cols"] == t["n_cols"]
                and _headers(prev) == _headers(t) and any(_headers(t)) and not between
                and (own is None or (CONTINUED.search(own) and heading_key(own) == heading_key(context(prev)[1])))):
            hdr_rows = t["header_rows"]
            prev.setdefault("merged_header_cells", []).extend(c for c in t["cells"] if c["r"] < hdr_rows)
            offset = prev["n_rows"] - hdr_rows
            prev["cells"] += [{**c, "r": c["r"] + offset} for c in t["cells"] if c["r"] >= hdr_rows]
            prev["n_rows"] += t["n_rows"] - hdr_rows
            prev["pages"].append(page)
            prev["bboxes"].append(t["bboxes"][0])
            prev["extract_method"] = "merged"
            prev.setdefault("merged_refs", [prev["docling_ref"]]).append(t["docling_ref"])
            continue
        out.append(t)
    return out, gaps


def conservation(t: dict, words_by_page: dict[int, list]) -> tuple[list[list], list[str]]:
    """(text-layer number words not found in the cells, cell numbers not found in the text layer)."""
    layer_words = [w for page, box in zip(t["pages"], t["bboxes"])
                   for w in words_by_page.get(page, []) if _center_in(w, box)]
    layer = Counter()
    by_token: dict[str, list] = {}
    for w in layer_words:
        for tok in numbers(w[4]):
            layer[tok] += 1
            by_token.setdefault(tok, []).append(w)
    cells = Counter(tok for c in t["cells"] + t.get("merged_header_cells", []) for tok in numbers(c["text"]))
    missing = []
    for tok, n in (layer - cells).items():
        missing += by_token[tok][-n:]
    extra = sorted((cells - layer).elements())
    return missing, extra


def cross_compare(a: dict, b: dict, page_no: int) -> tuple[list[str], list[str]]:
    """Numbers only in a / only in b, counted over the cells inside the overlap of the two tables on that page
    (find_tables often leaves the header row outside its box; that is not a disagreement about the numbers)."""
    box_a = next(box for p, box in zip(a["pages"], a["bboxes"]) if p == page_no)
    box_b = b["bboxes"][0]
    both = [max(box_a[0], box_b[0]), max(box_a[1], box_b[1]), min(box_a[2], box_b[2]), min(box_a[3], box_b[3])]

    def nums(t):
        return Counter(tok for c in t["cells"] if c.get("page_no", page_no) == page_no and c["bbox"]
                       and _center_in([*c["bbox"], ""], both) for tok in numbers(c["text"]))
    na, nb = nums(a), nums(b)
    return sorted((na - nb).elements()), sorted((nb - na).elements())


def region_at(regions: list[dict], page_no: int, box) -> dict | None:
    """The region that overlaps the box most (on that page)."""
    best, best_i = None, 0.0
    for r in regions:
        if r["page_no"] != page_no:
            continue
        i = _inter(r["bbox"], box)
        if i > best_i:
            best, best_i = r, i
    return best if best_i / max(1e-9, _area(box)) >= 0.2 else None
