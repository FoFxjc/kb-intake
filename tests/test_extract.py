"""The extract stage (kb_intake.extract, intake-design-v2 §2 step 6, §3, §5.4, §5.5, §5.7).

Docling only gives structure; every text is read from the PDF text layer. The tests build synthetic PDFs with
pymupdf, write the label-stage work files by hand (the shapes of the plan's "Work directory" section) and run the
extract-stage Docling through the fake engine (tests/fakes.py) in the runner's child process.
"""

import json
import sys
import types

import pymupdf
import pytest
import yaml
from fakes import H, W, fake_engine

import kb_intake
from kb_intake import contract, extract
from kb_intake.config import Config
from kb_intake.jobs import Jobs
from kb_intake.package import read_json, read_jsonl, write_jsonl

DOC = "demo-ar-2025-en"
TABLE_BOX = [72, 200, 400, 300]           # fakes.document's default table box; a 2 x 2 grid
CELL_WORDS = [("Item", 80, 230), ("2025", 244, 230), ("Loans", 80, 280), ("1,234", 244, 280)]
WORKFLOW = """name: default
version: 1
handlers:
  title: text
  heading: text
  text: text
  list: text
  caption: text
  footnote: text
  formula: text
  toc: text
  table: table_structure
  chart: text
  diagram: text
  picture: discard
  logo: discard
  icon: discard
  signature: record
  stamp: record
  page_header: discard
  page_footer: discard
  unknown: unhandled
exclude_pages: []
"""


# ---------- fixtures: workflow / find_tables stand-ins until Task A lands ----------

@pytest.fixture(autouse=True)
def workflow_module(monkeypatch):
    """kb_intake.workflow (Task A) may not exist yet: use it when it does, else a stand-in with the same calls."""
    try:
        from kb_intake import workflow  # noqa: F401
        return
    except ImportError:
        pass
    stub = types.ModuleType("kb_intake.workflow")
    stub.parse = lambda text: yaml.safe_load(text)
    stub.handler_for = lambda wf, region_type: wf["handlers"][region_type]
    monkeypatch.setitem(sys.modules, "kb_intake.workflow", stub)
    monkeypatch.setattr(kb_intake, "workflow", stub, raising=False)


@pytest.fixture
def table_in(monkeypatch):
    """findtables.table_in (Task A) replaced by a recorder that returns `result` (a find_tables table or None)."""
    calls = []
    state = {"result": None}

    def fake(page, bbox):
        calls.append((page.number + 1, list(bbox)))
        return state["result"]
    try:
        from kb_intake import findtables
    except ImportError:
        findtables = types.ModuleType("kb_intake.findtables")
        monkeypatch.setitem(sys.modules, "kb_intake.findtables", findtables)
        monkeypatch.setattr(kb_intake, "findtables", findtables, raising=False)
    monkeypatch.setattr(findtables, "table_in", fake, raising=False)
    return types.SimpleNamespace(calls=calls, state=state)


# ---------- synthetic sources ----------

def write(page, x, y, text, size=11):
    tw = pymupdf.TextWriter(page.rect)
    tw.append((x, y), text, font=pymupdf.Font("helv"), fontsize=size)
    tw.write_text(page)


def make_pdf(path, pages=2, table_pages=(1,), extra_words=()):
    """Each page: a heading in (72, 68, 300, 84), a text in (72, 100, 500, 140); on `table_pages` a ruled 2 x 2 table
    in TABLE_BOX with one word per cell. `extra_words` are (page, x, y, text)."""
    doc = pymupdf.open()
    for p in range(1, pages + 1):
        page = doc.new_page(width=W, height=H)
        write(page, 72, 80, "Financial Review", 12)
        write(page, 72, 120, "Revenue – 2025 grew\x07 strongly")
        if p in table_pages:
            x0, y0, x1, y1 = TABLE_BOX
            for x in (x0, (x0 + x1) / 2, x1):
                page.draw_line((x, y0), (x, y1))
            for y in (y0, (y0 + y1) / 2, y1):
                page.draw_line((x0, y), (x1, y))
            for text, x, y in CELL_WORDS:
                write(page, x, y, text)
    for p, x, y, text in extra_words:
        write(doc[p - 1], x, y, text)
    doc.save(path)
    return path


def region(page, n, bbox, rtype="text", order=0, docling_label=None, label_source="model", score=0.9,
           layer="body", subtype=None):
    """A label/regions.jsonl row (plan, Work directory)."""
    return {"region_id": f"{DOC}:p{page:04d}:r{n:03d}", "page_no": page, "bbox": list(bbox), "type": rtype,
            "subtype": subtype, "score": score, "label_source": label_source, "layer": layer,
            "reading_order": order, "font": None, "docling_label": docling_label, "classes": None}


def standard_regions(pages=2, table_pages=(1,)):
    out = []
    for p in range(1, pages + 1):
        n = 0
        for bbox, rtype, label in (([72, 68, 300, 84], "heading", "section_header"),
                                   ([72, 100, 500, 140], "text", "text")):
            n += 1
            out.append(region(p, n, bbox, rtype, len(out), label))
        if p in table_pages:
            n += 1
            out.append(region(p, n, TABLE_BOX, "table", len(out), "table", score=0.95))
    return out


def cfg_for(tmp_path) -> Config:
    return Config(models_dir=tmp_path / "models", cache_dir=tmp_path / "cache", work_dir=tmp_path / "work",
                  delivery_dir=tmp_path / "delivery")


def approved_job(tmp_path, regions, workflow_text=WORKFLOW, **pdf_kw):
    """A job in state `approved` with hand-written label work files."""
    cfg = cfg_for(tmp_path)
    jobs = Jobs(cfg.work_dir)
    src = make_pdf(tmp_path / "src.pdf", **pdf_kw)
    job, _ = jobs.create(src, {"doc_id": DOC})
    with jobs.running(job.id, "label"):
        write_jsonl(job.dir / "label" / "regions.jsonl", regions)
        jobs.advance(job.id, "labeled", op="label")
        jobs.save_workflow(job.id, workflow_text, op="label")
        jobs.advance(job.id, "planned", op="label")
    jobs.approve(job.id, "peter")
    return jobs, job, cfg


def run(tmp_path, regions, behaviour="ok", workflow_text=WORKFLOW, doc_kw=None, **pdf_kw):
    jobs, job, cfg = approved_job(tmp_path, regions, workflow_text, **pdf_kw)
    engine = fake_engine(tmp_path, behaviour, stage="extract", **(doc_kw or {}))
    summary = extract.run(jobs, job.id, cfg, engine=engine)
    d = job.dir / "extract"
    return types.SimpleNamespace(jobs=jobs, job=jobs.get(job.id), summary=summary, dir=d,
                                 regions=read_jsonl(d / "regions.jsonl"), tables=read_jsonl(d / "tables.jsonl"),
                                 gaps=read_jsonl(d / "gaps.jsonl"), run=read_json(d / "run.json"))


def context(tmp_path, regions, tables=None, excluded=(), failed=(), **pdf_kw):
    pdf = pymupdf.open(make_pdf(tmp_path / "ctx.pdf", **pdf_kw))
    return extract.Context(DOC, pdf, regions, tables, failed_pages=failed, excluded_pages=excluded)


def docling_table(page=1, bbox=TABLE_BOX, rows=2, cols=2, cells=None):
    """An extract-stage table as engines.docling.extract_tables returns it."""
    x0, y0, x1, y1 = bbox
    xs = [x0 + (x1 - x0) * i / cols for i in range(cols + 1)]
    ys = [y0 + (y1 - y0) * i / rows for i in range(rows + 1)]
    if cells is None:
        cells = [{"r": r, "c": c, "rs": 1, "cs": 1, "bbox": [xs[c] + 1, ys[r] + 1, xs[c + 1] - 1, ys[r + 1] - 1],
                  "is_header": r == 0} for r in range(rows) for c in range(cols)]
    return {"engine_ref": "#/tables/0", "page_no": page, "bbox": list(bbox), "n_rows": rows, "n_cols": cols,
            "cells": cells}


def find_tables_table(bbox=TABLE_BOX):
    x0, y0, x1, y1 = bbox
    xm, ym = (x0 + x1) / 2, (y0 + y1) / 2
    return {"bbox": list(bbox), "n_rows": 2, "n_cols": 2,
            "rows": [[[x0, y0, xm, ym], [xm, y0, x1, ym]], [[x0, ym, xm, y1], [xm, ym, x1, y1]]]}


def codes(gaps):
    return sorted(g["code"] for g in gaps)


# ---------- text ----------

def test_text_regions_take_text_layer_words_not_docling_text(tmp_path):
    out = run(tmp_path, standard_regions())
    text = next(r for r in out.regions if r["region_id"] == f"{DOC}:p0001:r002")
    assert text["text"] == "Revenue – 2025 grew strongly"            # dash kept, \x07 removed
    assert "docling" not in json.dumps(out.regions) and "\x07" not in json.dumps(out.regions)
    assert text["status"] == "extracted" and text["handler"] == "text" and text["text_source"] == "native"
    heading = next(r for r in out.regions if r["region_id"] == f"{DOC}:p0001:r001")
    assert heading["text"] == "Financial Review"
    assert out.job.status == "in_review"


def test_regions_keep_label_ids_order_and_full_precision_boxes(tmp_path):
    regs = standard_regions()
    regs[1]["bbox"] = [72.123456789, 100.000001, 500.5, 140.25]
    out = run(tmp_path, regs)
    assert [r["region_id"] for r in out.regions] == [r["region_id"] for r in regs]
    assert [r["reading_order"] for r in out.regions] == list(range(len(regs)))
    assert out.regions[1]["bbox"] == [72.123456789, 100.000001, 500.5, 140.25]
    assert all("docling_label" not in r and "classes" not in r for r in out.regions)


# ---------- tables ----------

def test_table_cells_read_from_the_text_layer(tmp_path):
    out = run(tmp_path, standard_regions())
    [t] = out.tables
    assert t["table_id"] == f"{DOC}:t0001_01" and t["region_id"] == f"{DOC}:p0001:r003"
    assert t["extract_method"] == "docling" and (t["n_rows"], t["n_cols"]) == (2, 2)
    assert [(c["r"], c["c"], c["text"]) for c in t["cells"]] == [
        (0, 0, "Item"), (0, 1, "2025"), (1, 0, "Loans"), (1, 1, "1,234")]
    assert all(c["bbox_source"] == "text" for c in t["cells"]) and t["cells"][0]["is_header"]
    assert t["markdown"] == "|Item|2025|\n|---|---|\n|Loans|1,234|"
    reg = next(r for r in out.regions if r["type"] == "table")
    assert reg["table_id"] == t["table_id"] and reg["status"] == "extracted" and reg["handler"] == "table_structure"
    assert not [g for g in out.gaps if g["code"] == "table_words_unassigned"]


def test_table_cells_read_from_the_text_layer_and_unassigned_words_are_reported(tmp_path):
    regs = standard_regions()
    only_first_row = docling_table(cells=[{"r": 0, "c": c, "rs": 1, "cs": 1, "bbox": box, "is_header": True}
                                          for c, box in enumerate([[73, 201, 235, 249], [237, 201, 399, 249]])])
    ctx = context(tmp_path, regs, [only_first_row])
    row, table, gaps = extract.extract_region(regs[2], "table_structure", ctx)
    assert [c["text"] for c in table["cells"]] == ["Item", "2025"]
    [gap] = gaps
    assert gap["code"] == "table_words_unassigned" and gap["count"] == 2 and gap["region_id"] == regs[2]["region_id"]
    assert "Loans" in gap["detail"] and "1,234" in gap["detail"]
    assert row["status"] == "extracted"


def test_table_gaps_get_the_table_id_of_their_region():
    tables = [{"table_id": f"{DOC}:t0001_01", "region_id": f"{DOC}:p0001:r003"}]
    gaps = [{"code": "table_words_unassigned", "page_no": 1, "region_id": f"{DOC}:p0001:r003", "count": 2,
             "detail": "x"},
            {"code": "no_text_layer", "page_no": 1, "region_id": f"{DOC}:p0001:r001", "detail": "y"}]
    out = extract.tag_table_gaps(gaps, tables)
    assert out[0]["table_id"] == f"{DOC}:t0001_01" and "table_id" not in out[1]
    assert "table_id" not in gaps[0]                                      # the input is not changed


def test_empty_cells_have_null_text_and_box(tmp_path):
    regs = standard_regions(table_pages=())
    regs.append(region(1, 3, TABLE_BOX, "table", 4, "table"))
    ctx = context(tmp_path, regs, [docling_table()], table_pages=())
    _, table, _ = extract.extract_region(regs[-1], "table_structure", ctx)
    assert all(c["text"] is None and c["bbox"] is None and c["bbox_source"] is None for c in table["cells"])


def test_label_table_without_extract_match_is_unhandled_with_table_unmatched_label(tmp_path):
    regs = standard_regions(table_pages=(1, 2))
    out = run(tmp_path, regs, table_pages=(1, 2))           # the fake Docling only has the table of page 1
    reg = next(r for r in out.regions if r["region_id"] == f"{DOC}:p0002:r003")
    assert reg["status"] == "unhandled" and reg["table_id"] is None and reg["text"] is None
    [gap] = [g for g in out.gaps if g["code"] == "table_unmatched"]
    assert gap["side"] == "label" and gap["region_id"] == reg["region_id"] and gap["page_no"] == 2
    assert [t["page_no"] for t in out.tables] == [1]


def test_extra_extract_table_is_reported_side_extract_and_not_delivered(tmp_path):
    regs = standard_regions(table_pages=())
    out = run(tmp_path, regs)                                # the fake Docling finds a table on page 1
    assert out.tables == [] and all(r["table_id"] is None for r in out.regions)
    [gap] = [g for g in out.gaps if g["code"] == "table_unmatched"]
    assert gap["side"] == "extract" and gap["page_no"] == 1 and gap["bbox"] == TABLE_BOX and "region_id" not in gap
    assert out.run["unmatched_extract_tables"] == 1 and out.summary["unmatched_extract_tables"] == 1


def test_matching_needs_iou_of_one_half(tmp_path):
    regs = standard_regions(table_pages=())
    regs.append(region(1, 3, [72, 200, 400, 245], "table", 2, "table"))     # IoU with the Docling box 0.45
    ctx = context(tmp_path, regs, [docling_table()])
    assert ctx.docling_tables(1) == {} and len(ctx.unmatched) == 1
    regs[-1]["bbox"] = [72, 200, 400, 260]                                  # IoU 0.6
    ctx = context(tmp_path, regs, [docling_table()])
    assert list(ctx.docling_tables(1)) == [regs[-1]["region_id"]] and ctx.unmatched == []


# ---------- handlers ----------

@pytest.mark.parametrize("handler,status", [("record", "recorded"), ("discard", "discarded"),
                                            ("unhandled", "unhandled"), ("ocr", "unhandled")])
def test_handlers_set_status(tmp_path, handler, status):
    regs = standard_regions()
    ctx = context(tmp_path, regs, [docling_table()])
    row, table, gaps = extract.extract_region(regs[1], handler, ctx)
    assert row["status"] == status and row["text"] is None and row["text_source"] is None and table is None
    assert row["handler"] == handler
    if status == "unhandled":
        [gap] = gaps
        assert gap["code"] == "unhandled" and gap["region_id"] == regs[1]["region_id"] and gap["page_no"] == 1
    else:
        assert gaps == []


def test_excluded_pages_are_excluded(tmp_path):
    wf = WORKFLOW.replace("exclude_pages: []", "exclude_pages: [2]")
    out = run(tmp_path, standard_regions(table_pages=(1, 2)), workflow_text=wf, table_pages=(1, 2))
    page2 = [r for r in out.regions if r["page_no"] == 2]
    assert page2 and all(r["status"] == "excluded" and r["text"] is None and r["table_id"] is None for r in page2)
    assert not [g for g in out.gaps if g["page_no"] == 2]          # no table_unmatched for an excluded page
    assert [t["page_no"] for t in out.tables] == [1]


def test_workflow_handlers_drive_each_type(tmp_path):
    regs = standard_regions()
    regs.append(region(1, 4, [400, 400, 500, 500], "picture", 3, "picture"))
    regs.append(region(1, 5, [72, 800, 500, 830], "page_footer", 4, "page_footer", layer="furniture"))
    out = run(tmp_path, regs, workflow_text=WORKFLOW.replace("  heading: text", "  heading: record"))
    status = {r["type"]: (r["handler"], r["status"]) for r in out.regions if r["page_no"] == 1}
    assert status == {"heading": ("record", "recorded"), "text": ("text", "extracted"),
                      "table": ("table_structure", "extracted"), "picture": ("discard", "discarded"),
                      "page_footer": ("discard", "discarded")}


def test_text_region_without_words_on_a_page_with_text(tmp_path):
    regs = standard_regions()
    regs.append(region(1, 4, [400, 600, 500, 700], "chart", 3, "picture", subtype="bar"))
    ctx = context(tmp_path, regs, [docling_table()])
    row, _, gaps = extract.extract_region(regs[-1], "text", ctx)
    assert row["text"] is None and row["text_source"] is None and row["status"] == "extracted"
    [gap] = gaps
    assert gap["code"] == "no_text_layer" and gap["region_id"] == regs[-1]["region_id"]


def test_page_without_text_layer_gives_null_text_and_no_region_gap(tmp_path):
    doc = pymupdf.open()
    doc.new_page(width=W, height=H)                         # a scanned page: no words at all
    regs = [region(1, 1, [72, 100, 500, 140], "text", 0, "text")]
    ctx = extract.Context(DOC, doc, regs, [], failed_pages=(), excluded_pages=())
    row, table, gaps = extract.extract_region(regs[0], "text", ctx)
    assert row["text"] is None and row["status"] == "extracted" and gaps == []       # label gives the page gap


# ---------- degradation ----------

def test_docling_failure_falls_back_to_find_tables_in_the_region(tmp_path, table_in):
    table_in.state["result"] = find_tables_table()
    out = run(tmp_path, standard_regions(), behaviour="raise")
    [t] = out.tables
    assert t["extract_method"] == "find_tables" and [c["text"] for c in t["cells"]] == ["Item", "2025", "Loans",
                                                                                        "1,234"]
    assert not any(c["is_header"] for c in t["cells"])
    assert table_in.calls == [(1, TABLE_BOX)]
    [deg] = [g for g in out.gaps if g["code"] == "extractor_degraded"]
    assert deg["extractor"] == "docling" and deg["page_no"] is None
    assert out.job.status == "in_review" and out.run["engine"]["status"] == "error"
    assert not (out.dir / "docling.extract.json").exists()


def test_docling_failure_and_no_find_tables_table_is_unhandled(tmp_path, table_in):
    out = run(tmp_path, standard_regions(), behaviour="raise")
    reg = next(r for r in out.regions if r["type"] == "table")
    assert reg["status"] == "unhandled" and out.tables == []
    [gap] = [g for g in out.gaps if g["code"] == "unhandled"]
    assert gap["detail"] == "find_tables found no table in the region" and gap["region_id"] == reg["region_id"]


def test_failed_page_falls_back_only_on_that_page(tmp_path, table_in):
    table_in.state["result"] = find_tables_table()
    regs = standard_regions(table_pages=(1, 2))
    out = run(tmp_path, regs, behaviour="failed_page", table_pages=(1, 2))    # the fake fails page 2
    assert [(t["page_no"], t["extract_method"]) for t in out.tables] == [(1, "docling"), (2, "find_tables")]
    assert table_in.calls == [(2, TABLE_BOX)]
    [deg] = [g for g in out.gaps if g["code"] == "extractor_degraded"]
    assert deg["pages"] == [2]


def test_user_labelled_table_uses_find_tables(tmp_path, table_in):
    table_in.state["result"] = find_tables_table()
    regs = standard_regions(table_pages=())
    regs.append({**region(1, 3, TABLE_BOX, "table", 2, "text", label_source="user"), "score": None})
    ctx = context(tmp_path, regs, [])
    row, table, gaps = extract.extract_region(regs[-1], "table_structure", ctx)
    assert table["extract_method"] == "find_tables" and row["status"] == "extracted" and gaps == []


def test_unreadable_docling_output_degrades_and_is_not_kept(tmp_path, table_in):
    table_in.state["result"] = find_tables_table()
    out = run(tmp_path, standard_regions(), behaviour="unreadable")
    [t] = out.tables
    assert t["extract_method"] == "find_tables" and out.job.status == "in_review"
    [deg] = [g for g in out.gaps if g["code"] == "extractor_degraded"]
    assert deg["extractor"] == "docling" and "unreadable Docling output" in deg["detail"]
    assert out.run["engine"]["status"] == "error" and out.summary["engine_status"] == "error"
    assert any("unreadable Docling output" in e for e in out.run["engine"]["errors"])
    assert not (out.dir / "docling.extract.json").exists()
    with extract.Context.load(out.jobs, out.job.id) as ctx:      # review re-extraction rebuilds the context
        assert not ctx.docling_ok
        row, table, _ = extract.extract_region(next(r for r in standard_regions() if r["type"] == "table"),
                                               "table_structure", ctx)
        assert table["extract_method"] == "find_tables" and row["status"] == "extracted"


@pytest.mark.parametrize("behaviour, status", [("hang", "timeout"), ("crash", "crashed")])
def test_docling_timeout_or_crash_falls_back_to_find_tables(tmp_path, table_in, behaviour, status):
    from dataclasses import replace
    table_in.state["result"] = find_tables_table()
    jobs, job, cfg = approved_job(tmp_path, standard_regions())
    cfg = replace(cfg, docling=replace(cfg.docling, wall_timeout=3.0))
    extract.run(jobs, job.id, cfg, engine=fake_engine(tmp_path, behaviour, stage="extract"))
    d = job.dir / "extract"
    [t] = read_jsonl(d / "tables.jsonl")
    assert t["extract_method"] == "find_tables" and table_in.calls == [(1, TABLE_BOX)]
    [deg] = [g for g in read_jsonl(d / "gaps.jsonl") if g["code"] == "extractor_degraded"]
    assert deg["extractor"] == "docling" and deg["page_no"] is None
    assert read_json(d / "run.json")["engine"]["status"] == status and jobs.get(job.id).status == "in_review"


def test_layout_disabled_degrades(tmp_path, table_in):
    from dataclasses import replace
    jobs, job, cfg = approved_job(tmp_path, standard_regions())
    extract.run(jobs, job.id, replace(cfg, layout_enabled=False))
    gaps = read_jsonl(job.dir / "extract" / "gaps.jsonl")
    assert "extractor_degraded" in codes(gaps) and jobs.get(job.id).status == "in_review"


# ---------- table ids and continued hints ----------

def _row(page, n, rtype, bbox, order, layer="body"):
    return {"region_id": f"{DOC}:p{page:04d}:r{n:03d}", "page_no": page, "bbox": bbox, "type": rtype,
            "subtype": None, "score": 0.9, "label_source": "model", "layer": layer, "reading_order": order,
            "text": None, "text_source": None, "font": None, "status": "extracted", "handler": "text",
            "table_id": None}


def _table(reg, n_cols=3):
    return {"region_id": reg["region_id"], "page_no": reg["page_no"], "bbox": reg["bbox"], "n_rows": 1,
            "n_cols": n_cols, "cells": [], "markdown": "", "extract_method": "docling"}


def test_continued_hint_links_last_table_to_first_table_of_next_page():
    regs = [_row(1, 1, "heading", [72, 60, 500, 80], 0), _row(1, 2, "table", [72, 100, 500, 300], 1),
            _row(1, 3, "table", [72, 400, 500, 780], 2), _row(1, 4, "page_footer", [72, 800, 500, 820], 3,
                                                             "furniture"),
            _row(2, 1, "heading", [72, 60, 500, 80], 4), _row(2, 2, "table", [72, 100, 500, 500], 5),
            _row(2, 3, "text", [72, 600, 500, 700], 6)]
    tables = [_table(regs[5]), _table(regs[2]), _table(regs[1])]        # any order in, reading order out
    regs2, tables2 = extract.finish_tables(regs, tables)
    assert [t["table_id"] for t in tables2] == [f"{DOC}:t0001_01", f"{DOC}:t0001_02", f"{DOC}:t0002_01"]
    assert [t["continued_hint"] for t in tables2] == [None, None, f"{DOC}:t0001_02"]
    assert [r["table_id"] for r in regs2] == [None, f"{DOC}:t0001_01", f"{DOC}:t0001_02", None, None,
                                              f"{DOC}:t0002_01", None]
    assert list(tables2[0])[0] == "table_id" and list(tables2[0])[-1] == "continued_hint"
    assert regs[1]["table_id"] is None                                  # inputs are not changed


@pytest.mark.parametrize("case", ["text_above_next", "text_below_last", "other_columns", "skip_page"])
def test_continued_hint_negative_cases(case):
    p2 = 3 if case == "skip_page" else 2
    regs = [_row(1, 1, "table", [72, 400, 500, 780], 0), _row(p2, 1, "table", [72, 100, 500, 500], 1)]
    if case == "text_above_next":
        regs.insert(1, _row(p2, 2, "text", [72, 40, 500, 90], 1))
    if case == "text_below_last":
        regs.insert(1, _row(1, 2, "list", [72, 790, 500, 800], 1))
    for i, r in enumerate(regs):
        r["reading_order"] = i
    tables = [_table(r, n_cols=4 if case == "other_columns" and r["page_no"] == p2 else 3)
              for r in regs if r["type"] == "table"]
    _, tables2 = extract.finish_tables(regs, tables)
    assert [t["continued_hint"] for t in tables2] == [None, None]


def test_markdown_escapes_pipes_and_newlines_and_fills_spans():
    cells = [{"r": 0, "c": 0, "rs": 1, "cs": 2, "text": "Group\nTotal"},
             {"r": 1, "c": 0, "rs": 1, "cs": 1, "text": "a|b"}, {"r": 1, "c": 1, "rs": 1, "cs": 1, "text": None}]
    assert extract.markdown(2, 2, cells) == "|Group Total|Group Total|\n|---|---|\n|a\\|b||"
    assert extract.markdown(0, 0, []) == ""


# ---------- contract and determinism ----------

def test_rows_validate_against_contract_v2(tmp_path, table_in):
    regs = standard_regions(table_pages=(1, 2))
    regs.append(region(2, 4, [400, 600, 500, 700], "picture", len(regs), "picture"))
    regs.append(region(2, 5, [400, 710, 500, 760], "unknown", len(regs), "form"))
    out = run(tmp_path, regs, table_pages=(1, 2))
    assert out.tables and out.gaps
    for kind, rows in (("region", out.regions), ("table", out.tables), ("gap", out.gaps)):
        for i, row in enumerate(rows):
            assert contract.schema_errors(kind, row, f"{kind}:{i}") == []
    keys = [(g["page_no"] or 0, g["code"], g.get("region_id") or "", g.get("table_id") or "", g["detail"])
            for g in out.gaps]
    assert keys == sorted(keys)


def test_two_runs_write_identical_rows(tmp_path):
    a = run(tmp_path / "a", standard_regions(table_pages=(1, 2)), table_pages=(1, 2))
    b = run(tmp_path / "b", standard_regions(table_pages=(1, 2)), table_pages=(1, 2))
    for f in ("regions.jsonl", "tables.jsonl", "gaps.jsonl", "docling.extract.json"):
        assert (a.dir / f).read_bytes() == (b.dir / f).read_bytes(), f


def test_run_records_engine_and_timings(tmp_path):
    out = run(tmp_path, standard_regions())
    eng = out.run["engine"]
    assert eng["status"] == "success" and not eng["cached"] and eng["cache_key"]
    assert out.run["params"]["extract"]["stage"] == "extract" and out.run["seconds"] >= 0
    assert set(out.run["cpu_seconds"]) == {"self", "children"}
    assert (out.dir / "docling.extract.json").is_file()


def test_context_load_rebuilds_the_extract_context_from_work_files(tmp_path):
    out = run(tmp_path, standard_regions(table_pages=(1, 2)), table_pages=(1, 2))
    with extract.Context.load(out.jobs, out.job.id) as ctx:
        assert ctx.doc_id == DOC and ctx.excluded_pages == frozenset()
        assert list(ctx.docling_tables(1)) == [f"{DOC}:p0001:r003"] and ctx.docling_tables(2) == {}
        assert ctx.words(1) and ctx.spans(1)


def test_extract_needs_an_approved_job(tmp_path):
    jobs, job, cfg = approved_job(tmp_path, standard_regions())
    extract.run(jobs, job.id, cfg, engine=fake_engine(tmp_path, stage="extract"))
    from kb_intake.jobs import WrongState
    with pytest.raises(WrongState):
        extract.run(jobs, job.id, cfg, engine=fake_engine(tmp_path, stage="extract"))
