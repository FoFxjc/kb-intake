"""The pipeline with a layout engine (intake-design §9.3, §9.4): every fallback path, on a fake engine that runs in
the same child process as the real one. Each path must leave a package that passes the contract, with the text
layer intact and one document-level `extractor_degraded` gap when the engine output could not be used."""

import json
import shutil
from dataclasses import replace
from functools import partial
from pathlib import Path

import pytest

import fakes
from kb_intake import contract, pipeline, scan
from kb_intake import postprocess as post
from kb_intake.config import Config, DoclingParams
from test_package import DECL, make_pdf, read_jsonl

XS, YS = [72, 250, 400, 520], [200, 230, 260, 290]
CELLS = [["Item", "2025", "2024"], ["Net profit", "7,422", "(1,234)"], ["Other", "-", "12.5"]]


@pytest.fixture
def pdf(tmp_path):
    return make_pdf(tmp_path / "ar.pdf")


def _cfg(tmp_path, wall=60.0):
    return Config(models_dir=tmp_path / "models", cache_dir=tmp_path / "cache",
                  docling=DoclingParams(wall_timeout=wall))


def _engine(monkeypatch, tmp_path, mode="ok", skip=()):
    doc = json.dumps(fakes.document(3, 2, CELLS, XS, YS, skip_pages=skip))
    calls = tmp_path / f"calls-{mode}.txt"
    monkeypatch.setitem(pipeline.LAYOUT, "docling", partial(fakes.FakeEngine, mode=mode, doc=doc, calls=str(calls)))
    return calls


def _run(pdf, tmp_path, cfg, out="out"):
    pkg = pipeline.process(pdf, dict(DECL), tmp_path / out, cfg)
    assert contract.check_path(pkg)["ok"], contract.check_path(pkg)["errors"]
    return pkg


def _layout_step(pkg):
    m = json.loads((pkg / "manifest.json").read_text())
    return next(s for s in m["steps"] if s["step"] == "layout"), m


def test_engine_tables_are_primary_and_find_tables_cross_checks(pdf, tmp_path, monkeypatch):
    _engine(monkeypatch, tmp_path)
    pkg = _run(pdf, tmp_path, _cfg(tmp_path))
    tables = read_jsonl(pkg / "tables.jsonl")
    (p,) = [t for t in tables if t["role"] == "primary"]
    (s,) = [t for t in tables if t["role"] == "secondary"]
    assert p["extract_method"] == "docling" and p["table_id"] == "acme_ar_2025_en:t0002_01"
    assert p["checks"]["text_conservation"] == {"missing": 0, "extra": 0}
    assert s["table_id"] == "acme_ar_2025_en:f0002_01" and p["checks"]["matched_find_tables"] == [s["table_id"]]
    regions = [r for r in read_jsonl(pkg / "regions.jsonl") if r["page_no"] == 2]
    assert [(r["type"], r["type_source"]) for r in regions] == [("text", "model"), ("table", "model"),
                                                                ("text", "native")]    # the page number: native only
    assert regions[1]["ref"] == p["table_id"] and p["region_ids"] == [regions[1]["region_id"]]
    assert (pkg / "docling.json").is_file()
    assert not [g for g in read_jsonl(pkg / "gaps.jsonl") if g["code"] == "extractor_degraded"]
    step, m = _layout_step(pkg)
    assert step["status"] == "success" and step["failed_pages"] == [] and m["degraded"] is False


def test_a_failed_page_falls_back_to_find_tables_for_that_page(pdf, tmp_path, monkeypatch):
    _engine(monkeypatch, tmp_path, "failed_page", skip=(2,))
    pkg = _run(pdf, tmp_path, _cfg(tmp_path))
    (t,) = [t for t in read_jsonl(pkg / "tables.jsonl") if t["role"] == "primary"]
    assert t["extract_method"] == "find_tables" and t["table_id"] == "acme_ar_2025_en:t0002_01"
    gaps = [g for g in read_jsonl(pkg / "gaps.jsonl") if g["code"] == "extractor_degraded"]
    assert len(gaps) == 1 and gaps[0]["pages"] == [2] and gaps[0]["status"] == "partial_success"
    assert _layout_step(pkg)[0]["failed_pages"] == [2]


@pytest.mark.parametrize("mode,status", [("hang", "timeout"), ("crash", "crashed"), ("silent_exit", "crashed"),
                                         ("raise", "error"), ("not_ready", "not_ready"), ("info_error", "error")])
def test_engine_failures_degrade_the_whole_file(pdf, tmp_path, monkeypatch, mode, status):
    _engine(monkeypatch, tmp_path, mode)
    pkg = _run(pdf, tmp_path, _cfg(tmp_path, wall=5.0))
    (t,) = read_jsonl(pkg / "tables.jsonl")
    assert t["extract_method"] == "find_tables" and t["role"] == "primary" and t["checks"] == {}
    (g,) = [g for g in read_jsonl(pkg / "gaps.jsonl") if g["code"] == "extractor_degraded"]
    assert g["status"] == status and g["pages"] == "all"
    assert read_jsonl(pkg / "pages.jsonl") and read_jsonl(pkg / "layout.jsonl")       # text layer untouched
    assert not (pkg / "docling.json").exists() and read_jsonl(pkg / "regions.jsonl") == []
    if mode == "raise":
        assert "model blew up" in g["detail"]


def test_a_post_processing_error_degrades_but_keeps_the_engine_output(pdf, tmp_path, monkeypatch):
    _engine(monkeypatch, tmp_path)
    monkeypatch.setattr(post, "tables", lambda *a: (_ for _ in ()).throw(KeyError("bbox")))
    pkg = _run(pdf, tmp_path, _cfg(tmp_path))
    (t,) = read_jsonl(pkg / "tables.jsonl")
    assert t["extract_method"] == "find_tables"
    (g,) = [g for g in read_jsonl(pkg / "gaps.jsonl") if g["code"] == "extractor_degraded"]
    assert g["detail"].startswith("post-processing failed: KeyError") and (pkg / "docling.json").is_file()
    assert _layout_step(pkg)[0]["postprocess_error"].startswith("post-processing failed")


def test_only_complete_successes_are_cached(pdf, tmp_path, monkeypatch):
    calls = _engine(monkeypatch, tmp_path, "failed_page", skip=(2,))
    cfg = _cfg(tmp_path)
    _run(pdf, tmp_path, cfg, "a")
    _run(pdf, tmp_path, cfg, "b")
    assert calls.read_text().count("\n") == 2                    # the partial result was not replayed
    calls = _engine(monkeypatch, tmp_path, "ok")
    _run(pdf, tmp_path, cfg, "c")
    b = _run(pdf, tmp_path, cfg, "d")
    assert calls.read_text().count("\n") == 1 and json.loads((b / "manifest.json").read_text())["run"]["layout"]["cached"]


def test_a_failed_cache_write_does_not_fail_the_file(pdf, tmp_path, monkeypatch):
    _engine(monkeypatch, tmp_path)
    (tmp_path / "blocker").write_text("a file where the cache directory should be")
    cfg = replace(_cfg(tmp_path), cache_dir=tmp_path / "blocker" / "cache")
    pkg = _run(pdf, tmp_path, cfg)
    (t,) = [t for t in read_jsonl(pkg / "tables.jsonl") if t["role"] == "primary"]
    m = json.loads((pkg / "manifest.json").read_text())
    assert t["extract_method"] == "docling" and m["run"]["layout"]["cache_written"] is False and not m["degraded"]


def test_a_forced_run_is_never_cached(pdf, tmp_path, monkeypatch):
    calls = _engine(monkeypatch, tmp_path, "forced")
    cfg = _cfg(tmp_path)
    for out in ("a", "b"):
        pkg = pipeline.process(pdf, dict(DECL), tmp_path / out, cfg, force=True)
        step, m = _layout_step(pkg)
        assert step["forced"] is True and step["status"] == "success" and not m["run"]["layout"]["cache_written"]
    assert calls.read_text().count("\n") == 2 and not (tmp_path / "cache").exists()


def test_header_gaps_carry_the_final_table_id(pdf, tmp_path, monkeypatch):
    cells = [["Item", "2025 restated", "2024"], *CELLS[1:]]           # the text layer says "2025": header suspect
    doc = json.dumps(fakes.document(3, 2, cells, XS, YS))
    monkeypatch.setitem(pipeline.LAYOUT, "docling", partial(fakes.FakeEngine, mode="ok", doc=doc))
    pkg = _run(pdf, tmp_path, _cfg(tmp_path))
    (g,) = [g for g in read_jsonl(pkg / "gaps.jsonl") if g["code"] == "table_header_suspect"]
    assert g["table_id"] == "acme_ar_2025_en:t0002_01" and g["resolution"] == "kept_engine"


def test_a_corrupt_cache_entry_is_a_miss(pdf, tmp_path, monkeypatch):
    calls = _engine(monkeypatch, tmp_path)
    cfg = _cfg(tmp_path)
    _run(pdf, tmp_path, cfg, "a")
    (entry,) = (tmp_path / "cache" / "docling").iterdir()
    for damage in ({"meta.json": "{bad"}, {"output.bin": "changed"}):
        for f, text in damage.items():
            (entry / f).write_text(text)
        _run(pdf, tmp_path, cfg, "b")
        (entry,) = (tmp_path / "cache" / "docling").iterdir()
    assert calls.read_text().count("\n") == 3                    # both damaged entries were re-converted


def _same_except_run(a: Path, b: Path) -> None:
    assert sorted(p.name for p in a.iterdir()) == sorted(p.name for p in b.iterdir())
    for f in sorted(p.name for p in a.iterdir()):
        if f == "manifest.json":
            ma, mb = (json.loads((x / f).read_text()) for x in (a, b))
            ma.pop("run"), mb.pop("run")
            assert ma == mb
        else:
            assert (a / f).read_bytes() == (b / f).read_bytes(), f


def test_first_run_cached_rerun_and_fresh_cache_are_identical(pdf, tmp_path, monkeypatch):
    """§9.4: three-way determinism, everything outside manifest.run byte for byte."""
    _engine(monkeypatch, tmp_path)
    cfg = _cfg(tmp_path)
    first = _run(pdf, tmp_path, cfg, "first")
    cached = _run(pdf, tmp_path, cfg, "cached")
    shutil.rmtree(tmp_path / "cache")
    fresh = _run(pdf, tmp_path, cfg, "fresh")
    runs = [json.loads((p / "manifest.json").read_text())["run"]["layout"]["cached"] for p in (first, cached, fresh)]
    assert runs == [False, True, False]
    _same_except_run(first, cached)
    _same_except_run(first, fresh)


def test_full_degradation_is_the_package_without_an_engine(pdf, tmp_path, monkeypatch):
    _engine(monkeypatch, tmp_path, "not_ready")
    degraded = _run(pdf, tmp_path, _cfg(tmp_path), "a")
    off = _run(pdf, tmp_path, replace(_cfg(tmp_path), layout_enabled=False), "b")
    for f in ("pages.jsonl", "layout.jsonl", "tables.jsonl", "regions.jsonl", "headings.jsonl"):
        assert (degraded / f).read_bytes() == (off / f).read_bytes(), f


# ---------- scan ----------

def test_scan_a_directory_with_defaults_overrides_and_skips(tmp_path, monkeypatch):
    d = tmp_path / "in"
    d.mkdir()
    english = "the report of the group and the bank for the year is set out"      # enough for detection
    make_pdf(d / "Annual Report 2025.pdf", note=english)
    make_pdf(d / "b.pdf", note="other " + english)
    make_pdf(d / "c.pdf", note="third")
    (d / "notes.txt").write_text("not a pdf")
    make_pdf(d / ".hidden.pdf", note="hidden")
    base = {k: v for k, v in DECL.items() if k not in ("doc_id", "lang")}
    raw = {"defaults": {k: v for k, v in base.items() if k != "period_end"},
           "files": {"Annual Report 2025.pdf": {"period_end": "2025-12-31"},
                     "b.pdf": {"doc_id": "acme-b", "period_end": "2024-12-31", "lang": "fr"}}}
    cfg = replace(_cfg(tmp_path), layout_enabled=False)
    out = tmp_path / "out"
    results = {r["file"]: r for r in scan.scan(d, raw, out, cfg)}
    assert set(results) == {"Annual Report 2025.pdf", "b.pdf", "c.pdf"}
    assert results["Annual Report 2025.pdf"]["package"].startswith("annual-report-2025@")      # slug of the name
    assert results["c.pdf"] == {"file": "c.pdf", "status": "skipped_no_declaration", "missing": ["period_end"]}
    b = out / results["b.pdf"]["package"]
    gaps = read_jsonl(b / "gaps.jsonl")
    assert {"code": "lang_mismatch", "declared": "fr", "detected": "en"} in gaps
    m = json.loads((out / results["Annual Report 2025.pdf"]["package"] / "manifest.json").read_text())
    assert m["declaration"]["lang"] == "en" and m["declaration_filled"] == ["lang"]
    again = list(scan.scan(d, raw, out, cfg))
    assert [r["status"] for r in again] == ["skipped_same_sha", "skipped_same_sha", "skipped_no_declaration"]


def test_an_undetectable_language_must_be_declared(pdf, tmp_path):
    from kb_intake import declaration
    with pytest.raises(declaration.DeclarationError, match="could not be detected") as e:
        pipeline.process(pdf, {k: v for k, v in DECL.items() if k != "lang"}, tmp_path / "out",
                         replace(_cfg(tmp_path), layout_enabled=False))
    assert e.value.param == "lang"


def test_doc_ids_may_not_collide_within_a_scan(tmp_path):
    d = tmp_path / "in"
    d.mkdir()
    make_pdf(d / "A b.pdf")
    make_pdf(d / "a-b.pdf", note="other")
    make_pdf(d / "\u534e\u4fa8\u94f6\u884c.pdf", note="cjk name")
    raw = {"defaults": {k: v for k, v in DECL.items() if k != "doc_id"}, "files": {"typo.pdf": {"lang": "en"}}}
    results = list(scan.scan(d, raw, tmp_path / "out", replace(_cfg(tmp_path), layout_enabled=False)))
    by = {r.get("file"): r for r in results}
    assert by["A b.pdf"]["package"].startswith("a-b@")
    assert by["a-b.pdf"]["status"] == "failed" and "already used by A b.pdf" in by["a-b.pdf"]["message"]
    assert by["\u534e\u4fa8\u94f6\u884c.pdf"]["package"].startswith("doc-")
    assert results[-1] == {"status": "unused_overrides", "files": ["typo.pdf"]}


def test_one_failing_file_does_not_stop_the_scan(tmp_path, monkeypatch):
    d = tmp_path / "in"
    d.mkdir()
    make_pdf(d / "a.pdf")
    make_pdf(d / "b.pdf", note="b")
    raw = {"defaults": {k: v for k, v in DECL.items() if k != "doc_id"}}
    real = pipeline.process

    def boom(path, *a, **kw):
        if path.name == "a.pdf":
            raise RuntimeError("disk full")
        return real(path, *a, **kw)
    monkeypatch.setattr(pipeline, "process", boom)
    results = list(scan.scan(d, raw, tmp_path / "out", replace(_cfg(tmp_path), layout_enabled=False)))
    assert [r["status"] for r in results] == ["failed", "ok"] and results[0]["message"] == "disk full"
