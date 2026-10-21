"""The 3b extraction layer (intake-design §9): Docling runner guards, post-processing rules, checks, headings.
Unit tests on synthetic inputs; the real Docling run is covered by the reports and scripts/compare_snapshot.py."""

import json
from pathlib import Path

import pymupdf
import pytest

from kb_intake import docling_post as post
from kb_intake import docling_run, headings


# ---------- docling_run: pinned weights, cache key ----------

def _fake_models(root: Path) -> None:
    lock = []
    for spec in docling_run.MODELS:
        d = root / docling_run.folder(spec["repo_id"])
        d.mkdir(parents=True)
        (d / "w.bin").write_bytes(spec["repo_id"].encode())
        lock.append({**spec, "files": {"w.bin": docling_run._sha_file(d / "w.bin")}})
    (root / docling_run.LOCK).write_text(json.dumps({"models": lock}))


def test_models_must_match_the_lock(tmp_path):
    _fake_models(tmp_path)
    assert [m["revision"] for m in docling_run.verify_models(tmp_path)] == [m["revision"] for m in docling_run.MODELS]
    (tmp_path / docling_run.folder(docling_run.MODELS[0]["repo_id"]) / "w.bin").write_bytes(b"changed")
    with pytest.raises(ValueError, match="does not match"):
        docling_run.verify_models(tmp_path)


def test_models_at_another_revision_are_refused(tmp_path):
    _fake_models(tmp_path)
    lock = json.loads((tmp_path / docling_run.LOCK).read_text())
    lock["models"][0]["revision"] = "main"
    (tmp_path / docling_run.LOCK).write_text(json.dumps(lock))
    with pytest.raises(ValueError, match="pinned revision"):
        docling_run.verify_models(tmp_path)


def test_unverified_models_degrade_instead_of_running(tmp_path):
    cfg = docling_run.DoclingConfig(artifacts_path=tmp_path / "none", cache_dir=tmp_path / "cache")
    r = docling_run.run(tmp_path / "x.pdf", "0" * 64, cfg)
    assert not r.ok and r.status == "models_unverified" and r.docling_json is None


def test_cache_key_covers_source_versions_models_and_params(tmp_path):
    cfg = docling_run.DoclingConfig(artifacts_path=tmp_path, cache_dir=tmp_path)
    v = {"docling": "2.140.0"}
    base = docling_run.cache_key("a" * 64, cfg, v)
    assert base == docling_run.cache_key("a" * 64, cfg, v)
    assert base != docling_run.cache_key("b" * 64, cfg, v)
    assert base != docling_run.cache_key("a" * 64, cfg, {"docling": "2.141.0"})
    assert base != docling_run.cache_key("a" * 64, docling_run.DoclingConfig(tmp_path, tmp_path, device="mps"), v)


def test_a_cache_hit_returns_the_stored_bytes(tmp_path):
    _fake_models(tmp_path / "models")
    cfg = docling_run.DoclingConfig(artifacts_path=tmp_path / "models", cache_dir=tmp_path / "cache")
    key = docling_run.cache_key("c" * 64, cfg, docling_run.versions())
    entry = tmp_path / "cache" / "docling" / key
    entry.mkdir(parents=True)
    (entry / "docling.json").write_bytes(b'{"x": 1}')
    (entry / "meta.json").write_text(json.dumps({"status": "success", "seconds": 1.0, "failed_pages": [],
                                                 "errors": [], "confidence_pages": {}}))
    r = docling_run.run(tmp_path / "never-read.pdf", "c" * 64, cfg)
    assert r.cached and r.ok and r.docling_json == b'{"x": 1}'


# ---------- post-processing rules ----------

def test_coordinates_become_topleft():
    assert post.topleft({"l": 10, "t": 700, "r": 50, "b": 680, "coord_origin": "BOTTOMLEFT"}, 800) == [10, 100, 50, 120]
    assert post.topleft({"l": 1, "t": 2, "r": 3, "b": 4, "coord_origin": "TOPLEFT"}, 800) == [1, 2, 3, 4]


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
    assert post._same_text("2022 (1)", "2022(1)") and post._same_text("Directors’", "Directors'")
    assert not post._same_text("capital and other equity", "Share capital and other equity")


def _table(page, box, cells, header=1, n_cols=2):
    return {"docling_ref": f"#/tables/{page}", "pages": [page], "bboxes": [box], "n_rows": 1 + len(cells) // n_cols,
            "n_cols": n_cols, "header_rows": header, "extract_method": "docling", "checks": {},
            "cells": [{"r": 0, "c": 0, "rs": 1, "cs": 1, "text": "Item", "is_header": True, "bbox": box, "page_no": page},
                      {"r": 0, "c": 1, "rs": 1, "cs": 1, "text": "2025", "is_header": True, "bbox": box, "page_no": page}]
                     + cells}


def test_continued_table_is_merged_only_after_a_continued_heading_or_none():
    a = _table(216, [0, 500, 100, 700], [])
    b = _table(217, [0, 150, 100, 300], [])
    ctx = {id(a): (None, "38.3 Market Risk", False), id(b): ("38.3 Market Risk (continued)", "38.3 Market Risk (continued)", False)}
    merged, _ = post.merge_continued([a, b], lambda t: ctx[id(t)])
    assert len(merged) == 1 and merged[0]["pages"] == [216, 217] and merged[0]["extract_method"] == "merged"
    a2, b2 = _table(156, [0, 500, 100, 700], []), _table(157, [0, 150, 100, 300], [])
    ctx = {id(a2): (None, "3. Net Interest Income", False), id(b2): ("4. Insurance Results (continued)", "4.", False)}
    assert len(post.merge_continued([a2, b2], lambda t: ctx[id(t)])[0]) == 2       # another note: not merged
    a3, b3 = _table(156, [0, 500, 100, 700], []), _table(157, [0, 150, 100, 300], [])
    ctx = {id(a3): (None, "4. X", False), id(b3): ("4. X (continued)", "4. X (continued)", True)}
    assert len(post.merge_continued([a3, b3], lambda t: ctx[id(t)])[0]) == 2       # a subtitle stands between


def test_text_conservation_finds_missing_and_extra_numbers():
    t = _table(1, [0, 0, 200, 100], [{"r": 1, "c": 0, "rs": 1, "cs": 1, "text": "Net profit", "is_header": False,
                                       "bbox": [0, 20, 50, 30], "page_no": 1},
                                      {"r": 1, "c": 1, "rs": 1, "cs": 1, "text": "7,422", "is_header": False,
                                       "bbox": [60, 20, 90, 30], "page_no": 1},
                                      {"r": 2, "c": 1, "rs": 1, "cs": 1, "text": "999", "is_header": False,
                                       "bbox": [60, 40, 90, 50], "page_no": 1}])
    words = {1: [[60, 5, 80, 12, "2025"], [60, 20, 90, 30, "7,422"], [60, 60, 90, 70, "(1,190)"], [300, 5, 320, 12, "17"]]}
    missing, extra = post.conservation(t, words)
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
    rows, summary = headings.headings(pdf, [], [PROFILE])
    real = [(r["level"], r["text"], r["numbering"]) for r in rows if not r["furniture"]]
    assert real == [(1, "Notes to the Financial Statements", None), (2, "21. Debt Issued", "21."),
                    (3, "21.1 Subordinated Debt", "21.1")]
    fur = [r["text"] for r in rows if r["furniture"]]
    assert fur.count("Notes to the Financial Statements") == 2 and fur.count("21. Debt Issued (continued)") == 2
    assert summary["source"] == "profile" and summary["by_level"] == {1: 1, 2: 1, 3: 1}


def test_without_a_profile_levels_are_inferred(tmp_path):
    pdf = pymupdf.open(_heading_pdf(tmp_path / "h.pdf"))
    rows, summary = headings.headings(pdf, [], [])
    assert summary["source"] == "auto" and any(r["level"] == 3 and r["numbering"] == "21.1" for r in rows)
