"""Deterministic similar-region groups (intake-ui-llm-design-v1 §3.3) on hand-made rows."""

from kb_intake import clusters
from kb_intake.search import Rows

PAGE = {"width": 600.0, "height": 800.0}


def region(n, page, box, type_="text", text=None, font=("Roboto", 9.0), subtype=None, status="extracted"):
    return {"region_id": f"d:p{page:04d}:r{n:03d}", "page_no": page, "bbox": list(box), "type": type_,
            "subtype": subtype, "score": 0.9, "label_source": "model", "layer": "body", "reading_order": page * 100 + n,
            "status": status, "handler": "text", "table_id": None, "text": text, "gaps": [], "llm": [],
            "font": {"name": font[0], "size": font[1], "bold": False} if font else None}


def build(regions, tables=(), gaps=()):
    pages = {r["page_no"]: PAGE for r in regions}
    return clusters.build(Rows("current", list(regions), list(tables)), pages, list(gaps))


def test_normalise():
    assert clusters.normalise("Page 12 of  276 ") == "page # of #"
    assert clusters.normalise("\uff11\uff12 Notes") == "# notes"          # full-width digits


def test_repeated_header_grouped_across_pages():
    top = (50, 30, 300, 45)                                           # centre y 37.5 / 800 < 0.12
    rows = [region(1, p, top, "heading", "Notes to the Financial Statements", ("Geomanist", 20.0)) for p in (5, 6, 7)]
    rows.append(region(2, 8, (50, 400, 300, 415), "heading", "Notes to the Financial Statements", ("Geomanist", 20.0)))
    rows.append(region(1, 9, top, "heading", "Notes to the Financial Statements", ("Geomanist", 16.0)))
    out = build(rows)
    assert len(out) == 1
    c = out[0]
    assert c["kind"] == "repeated_text" and c["n"] == 3 and c["pages"] == [5, 6, 7]
    assert c["basis"] == {"type": "heading", "text": "notes to the financial statements", "band": "top",
                          "font": {"name": "Geomanist", "size": 20}}
    assert c["label"] == "Notes to the Financial Statements"


def test_page_numbers_group_in_the_bottom_band():
    rows = [region(9, p, (290, 760, 310, 772), "page_footer", str(p)) for p in (10, 11, 12)]
    out = build(rows)
    assert [(c["kind"], c["n"], c["basis"]["band"], c["basis"]["text"]) for c in out] == \
        [("repeated_text", 3, "bottom", "#")]


def test_single_page_repeat_is_not_a_cluster():
    rows = [region(n, 3, (50 + n, 30, 300, 45), "text", "Same") for n in (1, 2)]
    assert build(rows) == []


def test_same_shape_buckets():
    rows = [region(1, 1, (0, 0, 29, 29), "icon", font=None), region(1, 2, (0, 0, 30, 30), "icon", font=None),
            region(2, 2, (0, 0, 64, 31), "icon", font=None), region(3, 2, (0, 0, 29, 29), "logo", font=None),
            region(4, 2, (0, 0, 29, 29), "icon", font=None, status="excluded")]
    out = build(rows)
    assert [(c["kind"], c["n"], c["basis"]) for c in out] == \
        [("same_shape", 2, {"type": "icon", "subtype": None, "size_bucket": [10, 10]})]
    assert out[0]["members"] == ["d:p0001:r001", "d:p0002:r001"]


def table(n, page, first_row, n_cols):
    cells = [{"r": 0, "c": c, "rs": 1, "cs": 1, "text": t, "bbox": None, "is_header": True}
             for c, t in enumerate(first_row)]
    return {"table_id": f"d:t{page:04d}_{n:02d}", "region_id": f"d:p{page:04d}:r{n:03d}", "page_no": page,
            "bbox": [0, 0, 1, 1], "n_rows": 2, "n_cols": n_cols, "cells": cells}


def test_same_table_header():
    tabs = [table(1, 1, ["", "GROUP", "BANK\n", "", ""], 5), table(1, 2, ["", "Group", "Bank", None, ""], 5),
            table(1, 3, ["", "GROUP", "BANK", "", "", ""], 6), table(2, 3, ["", "", ""], 3), table(3, 3, ["", "", ""], 3)]
    rows = [region(1, t["page_no"], (0, 0, 1, 1), "table") if t["region_id"].endswith("r001")
            else region(int(t["region_id"][-3:]), t["page_no"], (0, 0, 1, 1), "table") for t in tabs]
    out = build(rows, tabs)
    assert [(c["kind"], c["n"], c["basis"]) for c in out] == \
        [("same_table_header", 2, {"n_cols": 5, "first_row": " | group | bank |  | "})]
    assert out[0]["label"] == 'Tables headed "GROUP | BANK"'


def test_same_gap_counts_open_gaps_only():
    rows = [region(n, 1, (0, 100 * n, 10, 100 * n + 10), "text", f"t{n}") for n in (1, 2, 3)]
    gaps = [{"code": "low_confidence", "page_no": 1, "region_id": rows[0]["region_id"], "detail": ""},
            {"code": "low_confidence", "page_no": 1, "region_id": rows[1]["region_id"], "detail": ""},
            {"code": "low_confidence", "page_no": 1, "region_id": rows[2]["region_id"], "detail": "",
             "resolved": {"action": "ack", "review_seq": 1}}]
    out = build(rows, gaps=gaps)
    assert [(c["kind"], c["n"], c["label"]) for c in out] == [("same_gap", 2, "Low confidence")]


def test_cluster_ids_are_stable_and_ordered():
    rows = [region(1, p, (50, 30, 300, 45), "heading", "Risk", ("G", 20.0)) for p in (1, 2)]
    rows += [region(2, p, (0, 300, 29, 329), "icon", font=None) for p in (1, 2, 3)]
    a, b = build(rows), build(list(reversed(rows)))
    assert a == b
    assert [c["kind"] for c in a] == ["repeated_text", "same_shape"]
    assert all(c["cluster_id"].startswith(c["kind"] + ":") and len(c["cluster_id"].split(":")[1]) == 12 for c in a)
