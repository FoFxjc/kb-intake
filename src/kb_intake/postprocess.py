"""Step 6 of the pipeline (intake-design §3.1, §9.2): deterministic rules on top of the layout engine's output and
the text layer. No model is called.

Regions
- Engine regions get ids `{doc_id}:p{page:04d}:r{n:03d}` in reading order. A picture that carries enough native text
  is a diagram (type_source fused). Native text blocks no region covers become `text` regions (type_source native).
Tables
- Control characters \\x07 and \\x03 are removed from cell text; a cell holding only a dash is nil (textlayer.parse_value).
- Header cells are re-read from the text layer inside the engine's cell box; when the text layer holds everything the
  engine read it wins, otherwise the engine's text is kept. Either way the cell is reported (`table_header_suspect`).
- The cell `bbox` is the box of the text in the cell, with `bbox_source` (text | words | cell, see
  textlayer.text_box); an empty cell has bbox null (contract §4).
- A table that continues on the next page (same column headers; the second part opens its page, after nothing but an
  absent or "(continued)" heading) is merged: extract_method "merged", all pages and boxes kept.
- `context_above`: the short lines right above a table, in its columns (unit lines like "$ million", notes), which the
  engine's table box often leaves out.
"""

import re
from collections import Counter

from kb_intake.textlayer import parse_value, text_box

DISCARDED = {"picture", "logo", "icon"}
NATIVE_TEXT_MIN, NATIVE_TEXT_DENSITY = 6, 100      # same thresholds as pages.py: picture with native text = diagram
CONTINUED = re.compile(r"\(continued\)|\uff08\u7eed\uff09", re.I)     # "(continued)", fullwidth CJK form
CONTEXT_LINES, CONTEXT_CHARS = 3, 80


def clean(text: str | None) -> str | None:
    return None if text is None else text.replace("\x07", "").replace("\x03", "").strip()


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


def center_in(w, box, pad: float = 1.0) -> bool:
    cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
    return box[0] - pad <= cx <= box[2] + pad and box[1] - pad <= cy <= box[3] + pad


_NUMBER_IN = re.compile(r"(?<![\w.,])\(?[-+−–]?\d[\d,]*(?:\.\d+)?\)?%?")
_SIGNS = "+-−–"


def numbers(text: str | None) -> list[str]:
    """Number tokens of a text, normalised to the digits (no parentheses, sign, percent sign or control characters).
    Found anywhere in the string, not by splitting on spaces, so a cell "2022 (1)" and a text-layer word "2022(1)" give
    the same tokens. The sign is dropped like the parentheses: an engine may write a hyphen where the text layer has
    an en dash or a minus sign ("-100" against "–100"), and the check is that the text is there, not its sign."""
    out = []
    for m in _NUMBER_IN.finditer(clean(text) or ""):
        tok = m.group(0).rstrip(",")
        out.append(tok.strip("()%").lstrip(_SIGNS))
    return out


# ---------- regions ----------

def regions(engine_regions: list[dict], doc_id: str, page_text, layout_model: str,
            layout_version: str) -> tuple[list[dict], dict]:
    """regions.jsonl rows and {engine_ref: region_id}. page_text(page_no, bbox) -> native characters inside."""
    per_page: dict[int, list[dict]] = {}
    for order, r in enumerate(engine_regions):
        rtype, type_source, agree = r["type"], "model", None
        if rtype == "picture":
            chars = page_text(r["page_no"], r["bbox"])
            if chars >= max(NATIVE_TEXT_MIN, NATIVE_TEXT_DENSITY * _area(r["bbox"]) / max(1e-9, r["page_area"])):
                rtype, type_source, agree = "diagram", "fused", False      # infographic: native text on the image
        per_page.setdefault(r["page_no"], []).append({
            "page_no": r["page_no"], "bbox": r["bbox"], "type": rtype, "subtype": None, "score": None,
            "type_source": type_source, "agree": agree, "text_source": "native", "reading_order": order,
            "status": "discarded" if rtype in DISCARDED else "kept", "ref": None,
            "layout_model": layout_model, "layout_version": layout_version, "content_layer": r["content_layer"],
            "_engine_ref": r["engine_ref"]})
    rows, refs = [], {}
    for page_no in sorted(per_page):
        for n, reg in enumerate(per_page[page_no], 1):
            rid = f"{doc_id}:p{page_no:04d}:r{n:03d}"
            refs[reg.pop("_engine_ref")] = rid
            rows.append({"region_id": rid, **reg})
    return rows, refs


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
            if any(center_in(b, box, 2.0) for box in by_page.get(page_no, [])):
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

def markdown(n_rows: int, n_cols: int, cells: list[dict]) -> str:
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


_FOLD = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-"})


def _fold(x: str | None) -> str:
    return "".join((x or "").translate(_FOLD).split())


def same_text(a: str | None, b: str | None) -> bool:
    """Equal up to spacing, curly quotes and dash style (superscript markers often lose their space)."""
    return _fold(a) == _fold(b)


def _reading_order(words: list) -> str:
    """PyMuPDF words in the text layer's own order (block, line, word); keeps superscripts where they belong."""
    if words and len(words[0]) >= 8:
        words = sorted(words, key=lambda w: (w[5], w[6], w[7]))
    else:
        words = sorted(words, key=lambda w: (round(w[3]), w[0]))
    return " ".join(w[4] for w in words)


def tables(engine_tables: list[dict], words_by_page: dict[int, list], page_words) -> tuple[list[dict], list[dict]]:
    """Engine tables -> contract tables (ids are given later) and gaps. page_words(page_no) -> PyMuPDF words with
    block / line / word numbers (reading order for the header re-read)."""
    out, gaps = [], []
    for t in engine_tables:
        page_no = t["pages"][0]
        cells, dropped = [], []
        for c in t["cells"]:
            text, ebox = clean(c["text"]), c["engine_bbox"]
            if ebox is None:
                if text:
                    dropped.append(text)
                continue
            cell = {"r": c["r"], "c": c["c"], "rs": c["rs"], "cs": c["cs"], "text": text, "value": None, "nil": False,
                    "bbox": None, "bbox_source": None, "is_header": c["is_header"], "page_no": c["page_no"]}
            if cell["is_header"]:
                inside = [w for w in page_words(page_no) if _inter(w, ebox) >= 0.5 * max(1e-9, _area(w))]
                layer = clean(_reading_order(inside)) if inside else None
                if layer and not same_text(text, layer):
                    # The text layer wins when it holds everything the engine read (it restores dropped words and
                    # the true order); when it lacks characters the cell box was too small: keep the engine's text.
                    wins = not (Counter(_fold(text)) - Counter(_fold(layer)))
                    gaps.append({"code": "table_header_suspect", "page_no": page_no, "r": c["r"], "c": c["c"],
                                 "engine": text, "text_layer": layer, "ref": t["engine_ref"],
                                 "resolution": "text_layer" if wins else "kept_engine"})
                    if wins:
                        cell["text"] = layer
            cell["value"], cell["nil"] = parse_value(cell["text"])
            if cell["text"]:
                cell["bbox"], cell["bbox_source"] = text_box(words_by_page.get(page_no, []), ebox)
            cells.append(cell)
        if dropped:
            gaps.append({"code": "table_cells_dropped", "page_no": page_no, "ref": t["engine_ref"],
                         "detail": f"{len(dropped)} cell(s) without a box: {dropped[:5]}"})
        header_rows = 0
        while any(c["r"] == header_rows and c["is_header"] for c in cells):
            header_rows += 1
        out.append({"engine_ref": t["engine_ref"], "pages": list(t["pages"]), "bboxes": [list(b) for b in t["bboxes"]],
                    "n_rows": t["n_rows"], "n_cols": t["n_cols"], "cells": cells, "header_rows": header_rows,
                    "extract_method": "docling", "continued_from": None, "role": "primary", "checks": {}})
    return out, gaps


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


def merge_continued(tables_: list[dict], context) -> list[dict]:
    """Merge a table with its continuation on the next page. context(t) -> (heading above the table on its own
    page or None, heading the table sits under, whether other content stands between that heading and the table).
    The second part continues the first when the column headers are the same, nothing but its heading precedes it
    on its page, and that heading is absent or a "(continued)" of the heading the first part sits under."""
    out: list[dict] = []
    first_on_page = {}
    for t in tables_:
        first_on_page.setdefault(t["pages"][0], t)
    for t in tables_:
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
            prev.setdefault("merged_refs", [prev["engine_ref"]]).append(t["engine_ref"])
            continue
        out.append(t)
    return out


def text_lines(page_words: list) -> list[dict]:
    """Lines of a page from PyMuPDF words with block / line numbers: {bbox, text, block_chars}, in reading order.
    block_chars is the length of the whole text block the line belongs to (a long block is a paragraph)."""
    lines: dict[tuple, list] = {}
    block_chars: Counter = Counter()
    for w in page_words:
        lines.setdefault((w[5], w[6]), []).append(w)
        block_chars[w[5]] += len(w[4]) + 1
    out = []
    for key in sorted(lines):
        ws = sorted(lines[key], key=lambda w: w[7])
        out.append({"bbox": [min(w[0] for w in ws), min(w[1] for w in ws), max(w[2] for w in ws), max(w[3] for w in ws)],
                    "text": " ".join(w[4] for w in ws), "block_chars": block_chars[key[0]]})
    return out


def context_above(box, lines: list[dict], others: list, stops: list) -> list[dict]:
    """The short lines directly above `box` that overlap its columns, top to bottom: up to CONTEXT_LINES bands (lines
    at the same height, e.g. "$ million" on the left and "GROUP" on the right, form one band) - unit lines, column
    notes, header rows the engine's table box left out. Going up, the search stops at a gap of more than one and a
    half line heights, at a line of a paragraph (its text block is longer than CONTEXT_CHARS), at another table's
    box, and at a line inside one of `stops` (heading candidates, page headers and footers)."""
    above = [ln for ln in lines if ln["bbox"][3] <= box[1] + 1 and ln["bbox"][0] < box[2] and ln["bbox"][2] > box[0]
             and not center_in(ln["bbox"], box, 0)]
    above.sort(key=lambda ln: (-ln["bbox"][3], ln["bbox"][0]))
    bands: list[list[dict]] = []
    for ln in above:
        if bands and abs(bands[-1][0]["bbox"][3] - ln["bbox"][3]) <= 2:
            bands[-1].append(ln)
        else:
            bands.append([ln])
    out, edge = [], box[1]
    for band in bands[:CONTEXT_LINES]:
        h = max(1.0, max(ln["bbox"][3] - ln["bbox"][1] for ln in band))
        bottom = max(ln["bbox"][3] for ln in band)
        if edge - bottom > 1.5 * h:
            break
        if any(len(ln["text"]) > CONTEXT_CHARS or ln.get("block_chars", 0) > CONTEXT_CHARS for ln in band):
            break
        if any(_inter(ln["bbox"], o) > 0 for ln in band for o in others):
            break
        if any(center_in(ln["bbox"], s, 1.0) for ln in band for s in stops):
            break
        out = [{"text": clean(ln["text"]), "bbox": ln["bbox"]} for ln in sorted(band, key=lambda x: x["bbox"][0])] + out
        edge = min(ln["bbox"][1] for ln in band)
    return out
