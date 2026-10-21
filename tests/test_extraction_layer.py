"""The extraction layer's rules (intake-design §9): weights and dependency pins, post-processing, checks, headings.
Unit tests on synthetic inputs; the pipeline paths (engine in a child process, cache, fallbacks) are in
test_pipeline.py, the real Docling run in tests/regression/."""

import json
from pathlib import Path

import pymupdf
import pytest

from kb_intake import checks, deps, headings, models
from kb_intake import postprocess as post
from kb_intake.engines import docling as dl


# ---------- pinned weights ----------

def _fake_lock(monkeypatch, root: Path) -> None:
    specs = []
    for repo in ("org/layout", "org/tables"):
        d = root / models.folder(repo)
        (d / "sub").mkdir(parents=True)
        (d / "sub" / "w.bin").write_bytes(repo.encode())
        specs.append({"repo_id": repo, "revision": "a" * 40, "role": "x", "files": {"sub/w.bin": models.sha_file(d / "sub" / "w.bin")}})
    monkeypatch.setattr(models, "lock", lambda: tuple(specs))


def test_models_must_match_the_shipped_lock(tmp_path, monkeypatch):
    _fake_lock(monkeypatch, tmp_path)
    assert [m["repo_id"] for m in models.verify(tmp_path)] == ["org/layout", "org/tables"]
    (tmp_path / "org--layout" / "sub" / "w.bin").write_bytes(b"changed")
    with pytest.raises(models.ModelsError, match="does not match the lock"):
        models.verify(tmp_path)
    (tmp_path / "org--layout" / "sub" / "w.bin").unlink()
    with pytest.raises(models.ModelsError, match="missing"):
        models.verify(tmp_path)


def test_the_shipped_lock_pins_revisions_and_every_file():
    for m in models.lock():
        assert len(m["revision"]) == 40 and m["files"] and all(len(h) == 64 for h in m["files"].values())


def test_dependency_versions_are_checked_against_constraints(monkeypatch):
    assert deps.problems() == []                     # this environment is installed from uv.lock
    want = dict(deps.expected())
    want["numpy"] = "0.0.1"
    monkeypatch.setattr(deps, "expected", lambda: want)
    assert any(p.startswith("numpy ") and "pins 0.0.1" in p for p in deps.problems())


def test_closure_follows_extras_to_the_inference_libraries():
    c = deps.closure()
    assert {"docling", "pymupdf", "torch", "transformers", "pillow", "numpy", "torchvision"} <= set(c)


def test_local_versions_compare_by_public_part():
    assert deps._public("2.14.1+cpu") == "2.14.1"


# ---------- Docling: failed pages, coordinates ----------

def test_failed_pages_come_from_the_completed_pages_not_the_document():
    """Docling 2.140 puts failed pages back into document.pages; only ConversionResult.pages and errors tell."""
    from types import SimpleNamespace as NS
    res = NS(input=NS(page_count=5), pages=[NS(page_no=p) for p in (1, 2, 4, 5)],
             errors=[NS(page_no=5, error_message="x"), NS(page_no=None, error_message="timeout")],
             document=NS(pages={p: None for p in range(1, 6)}))
    assert dl.failed_pages(res) == [3, 5]


def test_coordinates_become_topleft():
    assert dl.topleft({"l": 10, "t": 700, "r": 50, "b": 680, "coord_origin": "BOTTOMLEFT"}, 800) == [10, 100, 50, 120]
    assert dl.topleft({"l": 1, "t": 2, "r": 3, "b": 4, "coord_origin": "TOPLEFT"}, 800) == [1, 2, 3, 4]


# ---------- post-processing rules ----------

def test_boxes_match_by_iou_or_containment():
    assert post.same_box([0, 0, 10, 10], [1, 1, 10, 10])
    assert post.same_box([0, 0, 100, 100], [10, 10, 20, 20])          # one inside the other
    assert not post.same_box([0, 0, 10, 10], [8, 8, 30, 30])


def test_numbers_are_normalised():
    assert post.numbers("(1,234) 12.5% \x07 7,422 Net 2025:") == ["1,234", "12.5", "7,422", "2025"]
    assert post.numbers("- – n/a") == []
    assert post.numbers("+100 basis points") == post.numbers("-100") == post.numbers("\u2013100") == ["100"]
    assert post.numbers("\u606f\u5dee\u2013100") == post.numbers("\u606f\u5dee-100") == ["100"]   # CJK label + dash
    assert post.numbers("2022(1)") == post.numbers("2022 (1)") == ["2022", "1"]


def test_header_text_rule():
    assert post.same_text("2022 (1)", "2022(1)") and post.same_text("Directors’", "Directors'")
    assert not post.same_text("capital and other equity", "Share capital and other equity")


def _table(page, box, cells, header=1, n_cols=2):
    return {"engine_ref": f"#/tables/{page}", "pages": [page], "bboxes": [box], "n_rows": 1 + len(cells) // n_cols,
            "n_cols": n_cols, "header_rows": header, "extract_method": "docling", "checks": {},
            "cells": [{"r": 0, "c": 0, "rs": 1, "cs": 1, "text": "Item", "is_header": True, "bbox": box, "page_no": page},
                      {"r": 0, "c": 1, "rs": 1, "cs": 1, "text": "2025", "is_header": True, "bbox": box, "page_no": page}]
                     + cells}


def test_continued_table_is_merged_only_after_a_continued_heading_or_none():
    a = _table(216, [0, 500, 100, 700], [])
    b = _table(217, [0, 150, 100, 300], [])
    ctx = {id(a): (None, "38.3 Market Risk", False), id(b): ("38.3 Market Risk (continued)", "38.3 Market Risk (continued)", False)}
    merged = post.merge_continued([a, b], lambda t: ctx[id(t)])
    assert len(merged) == 1 and merged[0]["pages"] == [216, 217] and merged[0]["extract_method"] == "merged"
    a2, b2 = _table(156, [0, 500, 100, 700], []), _table(157, [0, 150, 100, 300], [])
    ctx = {id(a2): (None, "3. Net Interest Income", False), id(b2): ("4. Insurance Results (continued)", "4.", False)}
    assert len(post.merge_continued([a2, b2], lambda t: ctx[id(t)])) == 2       # another note: not merged
    a3, b3 = _table(156, [0, 500, 100, 700], []), _table(157, [0, 150, 100, 300], [])
    ctx = {id(a3): (None, "4. X", False), id(b3): ("4. X (continued)", "4. X (continued)", True)}
    assert len(post.merge_continued([a3, b3], lambda t: ctx[id(t)])) == 2       # a subtitle stands between
    a4, b4 = _table(216, [0, 500, 100, 700], []), _table(217, [0, 40, 100, 300], [])
    ctx = {id(a4): (None, "38.3 Market Risk", False), id(b4): (None, "38.3 Market Risk", False)}
    assert len(post.merge_continued([a4, b4], lambda t: ctx[id(t)])) == 1       # page top, no heading: continues
    b5 = _table(217, [0, 40, 100, 300], [])
    b5["cells"][1]["text"] = "2024"
    ctx = {id(a4): (None, "38.3", False), id(b5): (None, "38.3", False)}
    a4 = _table(216, [0, 500, 100, 700], [])
    ctx[id(a4)] = (None, "38.3", False)
    assert len(post.merge_continued([a4, b5], lambda t: ctx[id(t)])) == 2       # other column headers


def test_text_conservation_finds_missing_and_extra_numbers():
    t = _table(1, [0, 0, 200, 100], [{"r": 1, "c": 0, "rs": 1, "cs": 1, "text": "Net profit", "is_header": False,
                                       "bbox": [0, 20, 50, 30], "page_no": 1},
                                      {"r": 1, "c": 1, "rs": 1, "cs": 1, "text": "7,422", "is_header": False,
                                       "bbox": [60, 20, 90, 30], "page_no": 1},
                                      {"r": 2, "c": 1, "rs": 1, "cs": 1, "text": "999", "is_header": False,
                                       "bbox": [60, 40, 90, 50], "page_no": 1}])
    words = {1: [[60, 5, 80, 12, "2025"], [60, 20, 90, 30, "7,422"], [60, 60, 90, 70, "(1,190)"], [300, 5, 320, 12, "17"]]}
    missing, extra = checks.conservation(t, words)
    assert [w[4] for w in missing] == ["(1,190)"] and extra == ["999"]


# ---------- headings ----------

def _heading_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    for i in range(3):
        p = doc.new_page(width=595, height=842)
        p.insert_text((40, 60), "Notes to the Financial Statements", fontsize=20, fontname="hebo")
        if i == 0:
            p.insert_text((40, 120), "21.", fontsize=13, fontname="hebo")
            p.insert_text((70, 120), "Debt Issued", fontsize=13, fontname="hebo")
            p.insert_text((40, 160), "21.1 Subordinated Debt", fontsize=10, fontname="hebo")
        else:
            p.insert_text((40, 120), "21. Debt Issued (continued)", fontsize=13, fontname="hebo")
        p.insert_text((40, 300), "Body text " * 10, fontsize=9)
    doc.save(path)
    return path


PROFILE = {"name": "test", "version": 1, "match": {"fonts_all": ["Helvetica-Bold"]}, "levels": [
    {"level": 1, "kind": "chapter", "font": "Helvetica-Bold", "size": 20},
    {"level": 2, "kind": "note", "font": "Helvetica-Bold", "size": 13},
    {"level": 3, "kind": "subnote", "font": "Helvetica-Bold", "size": 10, "numbering": r"^\d{1,3}\.\d{1,3}\s"}]}


def test_headings_levels_numbering_and_furniture(tmp_path):
    pdf = pymupdf.open(_heading_pdf(tmp_path / "h.pdf"))
    rows, summary = headings.headings(headings.lines(pdf), [], [PROFILE])
    real = [(r["level"], r["text"], r["numbering"]) for r in rows if not r["furniture"]]
    assert real == [(1, "Notes to the Financial Statements", None), (2, "21. Debt Issued", "21."),
                    (3, "21.1 Subordinated Debt", "21.1")]
    fur = [r["text"] for r in rows if r["furniture"]]
    assert fur.count("Notes to the Financial Statements") == 2 and fur.count("21. Debt Issued (continued)") == 2
    assert summary["source"] == "profile" and summary["by_level"] == {1: 1, 2: 1, 3: 1}


def test_without_a_profile_levels_are_inferred(tmp_path):
    pdf = pymupdf.open(_heading_pdf(tmp_path / "h.pdf"))
    rows, summary = headings.headings(headings.lines(pdf), [], [])
    assert summary["source"] == "auto" and any(r["level"] == 3 and r["numbering"] == "21.1" for r in rows)


def test_profiles_are_read_only_from_the_configured_directories(tmp_path):
    assert headings.load_profiles([]) == []
    (tmp_path / "p.yaml").write_text("name: x\nversion: 1\nmatch: {fonts_all: [A]}\nlevels: []\n")
    assert [p["name"] for p in headings.load_profiles([tmp_path])] == ["x"]


# ---------- post-processing of engine tables ----------

def _engine_table(cells, box=(100, 100, 300, 160)):
    return {"engine_ref": "#/tables/0", "pages": [1], "bboxes": [list(box)], "n_rows": 2, "n_cols": 2,
            "cells": [{"r": r, "c": c, "rs": 1, "cs": 1, "text": t, "engine_bbox": b, "is_header": r == 0, "page_no": 1}
                      for r, c, t, b in cells]}


def _w(x0, y0, x1, y1, text, block=0, line=0, n=0):
    return [x0, y0, x1, y1, text, block, line, n]


def test_header_text_layer_wins_only_when_it_holds_all_the_engine_read():
    words = [_w(102, 102, 130, 110, "Share", 0, 0, 0), _w(132, 102, 170, 110, "capital", 0, 0, 1),
             _w(202, 102, 230, 110, "2025", 1, 0, 0), _w(102, 132, 140, 140, "Equity", 2, 0, 0),
             _w(202, 132, 230, 140, "1,234", 3, 0, 0)]
    t = _engine_table([(0, 0, "capital", [100, 100, 200, 120]), (0, 1, "2025 Total", [200, 100, 300, 120]),
                       (1, 0, "Equity", [100, 130, 200, 150]), (1, 1, "1,234", [200, 130, 300, 150])])
    (out,), gaps = post.tables([t], {1: [w[:5] for w in words]}, lambda p: words)
    by = {(c["r"], c["c"]): c for c in out["cells"]}
    assert by[(0, 0)]["text"] == "Share capital"                 # the text layer restores the dropped word
    assert by[(0, 1)]["text"] == "2025 Total"                    # the text layer lacks "Total": keep the engine's
    assert [(g["r"], g["c"], g["resolution"]) for g in gaps] == [(0, 0, "text_layer"), (0, 1, "kept_engine")]
    assert by[(1, 1)]["value"] == 1234 and out["header_rows"] == 1


def test_cell_bbox_is_the_text_box_and_empty_cells_have_none():
    words = [_w(205, 133, 225, 141, "7")]
    t = _engine_table([(0, 0, "", [100, 100, 200, 120]), (1, 1, "7", [200, 130, 300, 150]),
                       (1, 0, "orphan", None)])
    (out,), gaps = post.tables([t], {1: [w[:5] for w in words]}, lambda p: words)
    by = {(c["r"], c["c"]): c for c in out["cells"]}
    assert by[(0, 0)]["bbox"] is None and by[(1, 1)]["bbox"] == [205, 133, 225, 141]
    assert (1, 0) not in by and gaps[-1]["code"] == "table_cells_dropped"


def test_context_above_takes_the_unit_lines_right_above_the_table():
    lines = [{"bbox": [100, 40, 400, 50], "text": "A long paragraph " * 8},
             {"bbox": [100, 70, 150, 80], "text": "$ million"}, {"bbox": [300, 70, 350, 80], "text": "GROUP"},
             {"bbox": [100, 85, 160, 95], "text": "Note 4"}, {"bbox": [500, 85, 560, 95], "text": "other column"}]
    got = post.context_above([90, 100, 400, 200], lines, [], [])
    assert [c["text"] for c in got] == ["$ million", "GROUP", "Note 4"]
    assert post.context_above([90, 100, 400, 200], lines, [[90, 84, 400, 96]], []) == []   # another table between
    assert post.context_above([90, 200, 400, 300], lines, [], []) == []                   # nothing close enough
    got = post.context_above([90, 100, 400, 200], lines, [], [[95, 68, 160, 82]])          # a heading stops it
    assert [c["text"] for c in got] == ["Note 4"]
    tail = [{"bbox": [100, 85, 160, 95], "text": "risk tolerance.", "block_chars": 400}]
    assert post.context_above([90, 100, 400, 200], tail, [], []) == []                      # end of a paragraph


def test_a_picture_with_native_text_is_a_diagram():
    regs = [{"page_no": 1, "bbox": [0, 0, 100, 100], "type": "picture", "engine_ref": "#/pictures/0",
             "content_layer": "body", "page_area": 1e6},
            {"page_no": 1, "bbox": [0, 200, 100, 300], "type": "picture", "engine_ref": "#/pictures/1",
             "content_layer": "body", "page_area": 1e6}]
    rows, refs = post.regions(regs, "d", lambda p, b: 50 if b[1] == 0 else 0, "m", "v")
    assert [(r["region_id"], r["type"], r["type_source"]) for r in rows] == [
        ("d:p0001:r001", "diagram", "fused"), ("d:p0001:r002", "picture", "model")]
    assert refs == {"#/pictures/0": "d:p0001:r001", "#/pictures/1": "d:p0001:r002"}


# ---------- which table enters ----------

def _ft(page, i, box, cells):
    return {"table_id": f"d:t{page:04d}_{i:02d}", "region_ids": [], "pages": [page], "bboxes": [box], "n_rows": 3,
            "n_cols": 1, "cells": [{"r": r, "c": 0, "rs": 1, "cs": 1, "text": t, "bbox": [box[0], box[1] + 10 * r,
                                                                                      box[2], box[1] + 10 * r + 8],
                                    "is_header": False, "page_no": page} for r, t in enumerate(cells)],
            "header_rows": None, "markdown": "|a|\n|---|\n|b|", "extract_method": "find_tables",
            "continued_from": None, "checks": {}}


def test_combine_routes_candidates_and_renumbers():
    words = {1: [[10, 10, 20, 18, "5"], [10, 20, 20, 28, "6"], [10, 30, 20, 38, "7"]],
             2: [[10, 10, 20, 18, "1"]], 3: [[10, 10, 20, 18, "8"]]}
    eng = {"engine_ref": "#/tables/0", "pages": [1, 2], "bboxes": [[0, 0, 100, 40], [0, 0, 100, 20]], "n_rows": 4,
           "n_cols": 1, "cells": [{"r": r, "c": 0, "rs": 1, "cs": 1, "text": t, "bbox": [10, 10 * r + 10, 20, 10 * r + 18],
                                   "is_header": False, "page_no": 1} for r, t in enumerate(["5", "6", "7"])]
                                 + [{"r": 3, "c": 0, "rs": 1, "cs": 1, "text": "1", "bbox": [10, 10, 20, 18],
                                     "is_header": False, "page_no": 2}],
           "header_rows": 0, "extract_method": "merged", "merged_refs": ["#/tables/0", "#/tables/1"], "checks": {}}
    regions = [{"region_id": "d:p0001:r001", "page_no": 1, "bbox": [0, 0, 100, 40], "type": "table", "ref": None},
               {"region_id": "d:p0002:r001", "page_no": 2, "bbox": [0, 0, 100, 20], "type": "table", "ref": None},
               {"region_id": "d:p0002:r002", "page_no": 2, "bbox": [0, 100, 100, 200], "type": "chart", "ref": None}]
    refs = {"#/tables/0": "d:p0001:r001", "#/tables/1": "d:p0002:r001"}
    ft = [_ft(1, 1, [0, 0, 100, 40], ["5", "6", "7"]),       # matches the engine table
          _ft(2, 2, [0, 100, 100, 200], ["9"]),                # under a chart: misread
          _ft(3, 1, [0, 0, 100, 20], ["8"]),                   # nothing there, conserves: table_missed, entered
          _ft(4, 1, [0, 0, 100, 20], ["3"])]                   # page 4 not converted: as without an engine
    primary, secondary, gaps, summary = checks.combine("d", [eng], ft, {1, 2, 3}, regions, refs, words)
    assert [(t["table_id"], t["extract_method"]) for t in primary] == [
        ("d:t0001_01", "merged"), ("d:t0003_01", "find_tables"), ("d:t0004_01", "find_tables")]
    assert primary[0]["region_ids"] == ["d:p0001:r001", "d:p0002:r001"]
    assert [r["ref"] for r in regions] == ["d:t0001_01", "d:t0001_01", None]      # both parts link to the table
    assert {t["table_id"]: t["checks"].get("misread", "") for t in secondary}["d:f0002_02"].endswith("is chart")
    assert [(g["code"], g.get("resolution")) for g in gaps] == [("table_missed", "find_tables")]
    assert "text_conservation" not in primary[2]["checks"] and summary["fallback_tables"] == 1
