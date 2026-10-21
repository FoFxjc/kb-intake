"""The label stage (intake-design-v2 §2 steps 2-4): regions from a fake Docling engine plus the text layer, gaps,
stats and the suggested workflow. Synthetic PDFs built with PyMuPDF; Docling itself never runs here."""

import io
import json
from collections import Counter

import pymupdf
import pytest
from fakes import H, W, FakeEngine, bl, document, fake_engine
from PIL import Image

from kb_intake import contract, findtables, label, runner, textlayer, workflow
from kb_intake.config import Config, DoclingParams
from kb_intake.jobs import Jobs, WrongState
from kb_intake.package import read_json, read_jsonl

DOC = "demo-ar-2025-en"
DECL = {"doc_id": DOC, "issuer_id": "demo", "doc_type": "annual_report", "period_end": "2025-12-31",
        "source_url": "https://example.com/demo.pdf", "completeness": {"full": True}, "lang": "en"}
BODY = ("The Group reported a net profit for the year and the board is pleased with the results of this year. "
        "It is the view of the board that the bank is in a strong position for the next year.")
TABLE_BOX = (72, 200, 400, 300)


# ---------- synthetic PDFs ----------

def draw_table(page, box=TABLE_BOX, rows=2, cols=2):
    x0, y0, x1, y1 = box
    xs = [x0 + (x1 - x0) * i / cols for i in range(cols + 1)]
    ys = [y0 + (y1 - y0) * i / rows for i in range(rows + 1)]
    for r in range(rows):
        for c in range(cols):
            page.draw_rect(pymupdf.Rect(xs[c], ys[r], xs[c + 1], ys[r + 1]), color=(0, 0, 0), width=0.8)
            page.insert_text((xs[c] + 5, ys[r] + 30), f"cell {r}{c}", fontsize=10)


def png(w: int, h: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (40, 90, 160)).save(buf, format="PNG")
    return buf.getvalue()


def build_pdf(path, pages=2, table_page=1, extra=None, scanned=()):
    """Each native page: a bold heading at y 67-84, body text in 100-140; `table_page` has a ruled 2 x 2 table in
    TABLE_BOX (the boxes of fakes.document). `extra[page_no](page)` adds more; `scanned` pages are one image."""
    doc = pymupdf.open()
    for n in range(1, pages + 1):
        p = doc.new_page(width=W, height=H)
        if n in scanned:
            p.insert_image(p.rect, stream=png(200, 280))
            continue
        p.insert_text((72, 80), f"Section {n}", fontsize=12, fontname="hebo")
        p.insert_textbox(pymupdf.Rect(72, 100, 500, 140), BODY, fontsize=9)
        if n == table_page:
            draw_table(p)
        if extra and n in extra:
            extra[n](p)
    doc.save(path)
    return path


def add_item(doc, clusters, label_, page, box, score=0.9, layer="body"):
    """Add a clustered text-like item to a fakes.document."""
    ref = f"#/texts/{len(doc['texts'])}"
    doc["texts"].append({"self_ref": ref, "label": label_, "content_layer": layer, "text": "docling text (ignored)",
                         "prov": [{"page_no": page, "bbox": bl(*box)}], "children": [],
                         "parent": {"$ref": "#/body"}})
    (doc["furniture"] if layer == "furniture" else doc["body"])["children"].append({"$ref": ref})
    clusters.setdefault(str(page), []).append([*box, label_, score])


def engine_of(tmp_path, doc, clusters, behaviour="ok"):
    return FakeEngine(models_dir=tmp_path / "models", params=DoclingParams(), stage="label", behaviour=behaviour,
                      doc=json.dumps([doc, clusters]), calls=str(tmp_path / "calls.txt"))


@pytest.fixture
def setup(tmp_path):
    def make(decl=DECL, work="work", **pdf_kw):
        pdf = build_pdf(tmp_path / "src.pdf", **pdf_kw)
        jobs = Jobs(tmp_path / work)
        job, _ = jobs.create(pdf, decl)
        cfg = Config(models_dir=tmp_path / "models", cache_dir=tmp_path / "cache", work_dir=tmp_path / work,
                     delivery_dir=tmp_path / "delivery")
        return jobs, job, cfg
    return make


def out(job, name):
    p = job.dir / "label" / name
    return read_jsonl(p) if name.endswith(".jsonl") else read_json(p)


def gaps_of(job, code=None):
    return [g for g in out(job, "gaps.jsonl") if code is None or g["code"] == code]


# ---------- the happy path ----------

def test_label_moves_job_to_planned_with_a_draft_workflow(tmp_path, setup):
    jobs, job, cfg = setup()
    seen = []
    stats = label.run(jobs, job.id, cfg, engine=fake_engine(tmp_path), progress=lambda *a: seen.append(a))
    j = jobs.get(job.id)
    assert j.status == "planned" and j.running_op is None and j.last_error is None
    wf = jobs.workflow(job.id)
    assert wf["version"] == 1 and workflow.parse(wf["text"]) == workflow.load_template()
    regions = out(job, "regions.jsonl")
    assert [r["region_id"] for r in regions] == [f"{DOC}:p0001:r001", f"{DOC}:p0001:r002", f"{DOC}:p0001:r003",
                                                  f"{DOC}:p0002:r001", f"{DOC}:p0002:r002"]
    assert [r["reading_order"] for r in regions] == list(range(5))
    assert [r["type"] for r in regions] == ["heading", "text", "table", "heading", "text"]
    assert {r["label_source"] for r in regions} == {"model"} and regions[2]["score"] == 0.95
    assert regions[0]["font"] == {"name": "Helvetica-Bold", "size": 12.0, "bold": True}
    assert regions[0]["bbox"] == [72, 68, 300, 84] and regions[0]["layer"] == "body"
    assert regions[2]["docling_label"] == "table" and regions[2]["classes"] is None
    assert set(regions[0]) == {"region_id", "page_no", "bbox", "type", "subtype", "score", "label_source", "layer",
                               "reading_order", "font", "docling_label", "classes"}
    assert [p["page_no"] for p in out(job, "pages.jsonl")] == [1, 2]
    assert out(job, "pages.jsonl")[0]["kind"] == "native" and "excluded" not in out(job, "pages.jsonl")[0]
    assert out(job, "layout.jsonl")[0]["words"][0][4] == "Section"
    assert (job.dir / "label" / "docling.json").is_file()
    run = out(job, "run.json")
    assert run["engine"]["status"] == "success" and run["engine"]["dropped_table_cells"] == 1
    assert run["engines"]["params"]["label"]["stage"] == "label" and run["declaration_filled"] == []
    assert stats == out(job, "stats.json") and stats["regions"] == 5
    assert gaps_of(job) == []
    assert seen and seen[-1][0] == "label" and seen[-1][1] == "done"
    assert [e["step"] for e in jobs.events(job.id)][-1] == "label"


def test_label_refuses_a_job_that_is_not_uploaded(tmp_path, setup):
    jobs, job, cfg = setup()
    label.run(jobs, job.id, cfg, engine=fake_engine(tmp_path))
    with pytest.raises(WrongState):
        label.run(jobs, job.id, cfg, engine=fake_engine(tmp_path))


def test_same_source_gives_identical_label_files(tmp_path, setup):
    jobs_a, a, cfg_a = setup(work="wa")
    jobs_b, b, cfg_b = setup(work="wb")
    label.run(jobs_a, a.id, cfg_a, engine=fake_engine(tmp_path))
    label.run(jobs_b, b.id, cfg_b, engine=fake_engine(tmp_path))
    for f in ("pages.jsonl", "layout.jsonl", "regions.jsonl", "gaps.jsonl", "docling.json"):
        assert (a.dir / "label" / f).read_bytes() == (b.dir / "label" / f).read_bytes(), f
    assert jobs_a.workflow(a.id)["text"] == jobs_b.workflow(b.id)["text"]


# ---------- pictures ----------

def test_classifier_maps_pictures_and_flags_picture_tables(tmp_path, setup):
    jobs, job, cfg = setup()
    pics = [(2, (100, 300, 250, 400), [["bar_chart", 0.97], ["line_chart", 0.02]]),
            (2, (300, 300, 450, 400), [["logo", 0.9]]),
            (2, (100, 450, 250, 550), [["table", 0.9], ["bar_chart", 0.05]]),
            (2, (300, 450, 450, 550), [["photograph", 0.4], ["full_page_image", 0.3]])]
    label.run(jobs, job.id, cfg, engine=fake_engine(tmp_path, pictures=pics))
    regions = [r for r in out(job, "regions.jsonl") if r["docling_label"] == "picture"]
    assert [(r["type"], r["subtype"]) for r in regions] == [("chart", "bar"), ("logo", None), ("picture", None),
                                                            ("picture", None)]
    assert regions[0]["classes"][0] == ["bar_chart", 0.97] and regions[0]["score"] == 0.8
    sus = gaps_of(job, "table_suspected")
    assert len(sus) == 1 and sus[0]["source"] == "classifier" and sus[0]["region_id"] == regions[2]["region_id"]
    low = gaps_of(job, "low_confidence")
    assert [(g["region_id"], g["source"]) for g in low] == [(regions[3]["region_id"], "classifier")]
    for g in gaps_of(job):
        assert contract.schema_errors("gap", g, "gap") == []


def test_layout_score_below_the_threshold_is_low_confidence(tmp_path, setup):
    jobs, job, cfg = setup()
    doc, clusters = document()
    clusters["2"] = [[*c[:5], 0.3] if c[4] == "text" else c for c in clusters["2"]]
    label.run(jobs, job.id, cfg, engine=engine_of(tmp_path, doc, clusters))
    low = gaps_of(job, "low_confidence")
    assert [(g["region_id"], g["source"], g["page_no"]) for g in low] == [(f"{DOC}:p0002:r002", "layout", 2)]


def test_the_low_confidence_threshold_comes_from_the_config(tmp_path, setup):
    jobs, job, cfg = setup()
    cfg = Config(**{**cfg.__dict__, "low_confidence": 0.2})
    doc, clusters = document()
    clusters["2"] = [[*c[:5], 0.3] if c[4] == "text" else c for c in clusters["2"]]
    label.run(jobs, job.id, cfg, engine=engine_of(tmp_path, doc, clusters))
    assert gaps_of(job, "low_confidence") == []


def test_picture_with_native_text_becomes_diagram_only_when_classified_picture(tmp_path, setup):
    def text(p):
        p.insert_textbox(pymupdf.Rect(80, 310, 500, 400), "Revenue grew strongly in every market we serve",
                         fontsize=10)
        p.insert_textbox(pymupdf.Rect(80, 460, 500, 550), "Loans and deposits rose across the whole group",
                         fontsize=10)
        p.insert_text((80, 640), "Q4", fontsize=10)
    jobs, job, cfg = setup(extra={2: text})
    pics = [(2, (72, 300, 520, 420), [["photograph", 0.9]]),      # 46 chars on 9% of the page -> diagram
            (2, (72, 450, 520, 570), [["bar_chart", 0.9]]),       # same amount of text, but a chart
            (2, (72, 600, 520, 720), [["photograph", 0.9]])]      # 2 chars -> stays a picture
    label.run(jobs, job.id, cfg, engine=fake_engine(tmp_path, pictures=pics))
    regions = [r for r in out(job, "regions.jsonl") if r["docling_label"] == "picture"]
    assert [(r["type"], r["subtype"]) for r in regions] == [("diagram", None), ("chart", "bar"), ("picture", None)]
    assert all(r["label_source"] == "model" for r in regions)


# ---------- find_tables and native text ----------

def test_find_tables_hit_outside_table_regions_is_table_suspected(tmp_path, setup):
    jobs, job, cfg = setup()
    doc, clusters = document(table_page=0)               # Docling saw no table on page 1 ...
    add_item(doc, clusters, "text", 1, (60, 190, 420, 310))   # ... only a text block around it (p106)
    label.run(jobs, job.id, cfg, engine=engine_of(tmp_path, doc, clusters))
    [g] = gaps_of(job, "table_suspected")
    assert g["source"] == "find_tables" and g["page_no"] == 1 and g["bbox"] == [72.0, 200.0, 400.0, 300.0]
    assert g["region_id"] == f"{DOC}:p0001:r003"
    assert contract.schema_errors("gap", g, "gap") == []
    assert out(job, "stats.json")["table_suspected"] == 1


def test_no_table_suspected_when_a_table_region_matches(tmp_path, setup):
    jobs, job, cfg = setup()
    label.run(jobs, job.id, cfg, engine=fake_engine(tmp_path))
    assert gaps_of(job, "table_suspected") == []


def test_uncovered_native_text_becomes_native_text_region(tmp_path, setup):
    jobs, job, cfg = setup(extra={1: lambda p: p.insert_text((72, 700), "A note no region covers", fontsize=8)})
    label.run(jobs, job.id, cfg, engine=fake_engine(tmp_path))
    regions = out(job, "regions.jsonl")
    native = [r for r in regions if r["label_source"] == "native"]
    assert len(native) == 1
    n = native[0]
    assert n["type"] == "text" and n["score"] is None and n["page_no"] == 1 and n["docling_label"] is None
    assert n["region_id"] == f"{DOC}:p0001:r004" and n["reading_order"] == 3 and n["layer"] == "body"
    assert 690 < n["bbox"][1] < n["bbox"][3] < 705 and n["font"]["size"] == 8.0
    assert [r["reading_order"] for r in regions] == list(range(len(regions)))


def test_native_regions_sit_between_body_and_furniture_of_their_page(tmp_path, setup):
    jobs, job, cfg = setup(extra={1: lambda p: (p.insert_text((72, 700), "Loose note", fontsize=8),
                                                p.insert_text((72, 820), "Demo Bank page footer", fontsize=7))})
    doc, clusters = document()
    add_item(doc, clusters, "page_footer", 1, (60, 810, 300, 825), layer="furniture")
    label.run(jobs, job.id, cfg, engine=engine_of(tmp_path, doc, clusters))
    p1 = [r for r in out(job, "regions.jsonl") if r["page_no"] == 1]
    assert [(r["type"], r["label_source"], r["layer"]) for r in p1] == [
        ("heading", "model", "body"), ("text", "model", "body"), ("table", "model", "body"),
        ("text", "native", "body"), ("page_footer", "model", "furniture")]
    assert p1[-1]["region_id"] == f"{DOC}:p0001:r005"


# ---------- degradation ----------

def test_docling_timeout_degrades(tmp_path, setup):
    jobs, job, cfg = setup()
    cfg = Config(**{**cfg.__dict__, "docling": DoclingParams(wall_timeout=3)})
    label.run(jobs, job.id, cfg, engine=fake_engine(tmp_path, "hang"))
    [g] = gaps_of(job, "extractor_degraded")
    assert g["page_no"] is None and "timeout" in g["detail"]
    assert jobs.get(job.id).status == "planned"


def test_an_unexpected_error_leaves_the_job_uploaded_with_the_error(tmp_path, setup, monkeypatch):
    jobs, job, cfg = setup()

    def boom(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(label, "write_jsonl", boom)
    with pytest.raises(RuntimeError):
        label.run(jobs, job.id, cfg, engine=fake_engine(tmp_path))
    j = jobs.get(job.id)
    assert j.status == "uploaded" and j.running_op is None and "disk full" in j.last_error
    assert jobs.events(job.id)[-1]["status"] == "failed"
    monkeypatch.undo()
    label.run(jobs, job.id, cfg, engine=fake_engine(tmp_path))          # the retry
    assert jobs.get(job.id).status == "planned"


def test_docling_failure_degrades_to_native_regions(tmp_path, setup):
    jobs, job, cfg = setup()
    label.run(jobs, job.id, cfg, engine=fake_engine(tmp_path, "raise"))
    assert jobs.get(job.id).status == "planned"
    [g] = gaps_of(job, "extractor_degraded")
    assert g["page_no"] is None and g["extractor"] == "docling" and "error" in g["detail"]
    assert contract.schema_errors("gap", g, "gap") == []
    regions = out(job, "regions.jsonl")
    assert {r["label_source"] for r in regions} == {"native"}
    tables = [r for r in regions if r["type"] == "table"]
    assert len(tables) == 1 and tables[0]["bbox"] == [72.0, 200.0, 400.0, 300.0] and tables[0]["page_no"] == 1
    texts = [r for r in regions if r["type"] == "text"]
    assert {r["page_no"] for r in texts} == {1, 2}
    # the table's words belong to the table region, not to a text region
    assert not any(r["bbox"][1] >= 200 and r["bbox"][3] <= 300 for r in texts)
    assert [r["reading_order"] for r in regions] == list(range(len(regions)))
    assert out(job, "run.json")["engine"]["status"] == "error"
    assert not (job.dir / "label" / "docling.json").exists()


def test_failed_pages_degrade_alone(tmp_path, setup):
    jobs, job, cfg = setup(table_page=2)
    label.run(jobs, job.id, cfg, engine=fake_engine(tmp_path, "failed_page", table_page=2))
    [g] = gaps_of(job, "extractor_degraded")
    assert g["page_no"] is None and g["pages"] == [2] and g["extractor"] == "docling"
    regions = out(job, "regions.jsonl")
    assert {r["label_source"] for r in regions if r["page_no"] == 1} == {"model"}
    p2 = [r for r in regions if r["page_no"] == 2]
    assert {r["label_source"] for r in p2} == {"native"} and "table" in {r["type"] for r in p2}
    assert jobs.get(job.id).status == "planned"


def test_disabled_layout_degrades_without_running_an_engine(tmp_path, setup):
    jobs, job, cfg = setup()
    cfg = Config(**{**cfg.__dict__, "layout_enabled": False})
    label.run(jobs, job.id, cfg, engine=fake_engine(tmp_path))
    assert not (tmp_path / "calls.txt").exists()
    assert gaps_of(job, "extractor_degraded")[0]["page_no"] is None
    assert jobs.get(job.id).status == "planned"


# ---------- stats, pages and metadata ----------

def test_stats_match_regions(tmp_path, setup):
    jobs, job, cfg = setup(extra={1: lambda p: p.insert_text((72, 700), "Loose note", fontsize=8)})
    pics = [(2, (300, 450, 450, 550), [["photograph", 0.4]]), (2, (100, 450, 250, 550), [["table", 0.9]])]
    stats = label.run(jobs, job.id, cfg, engine=fake_engine(tmp_path, pictures=pics))
    regions, gaps = out(job, "regions.jsonl"), out(job, "gaps.jsonl")
    assert stats["pages"] == 2 and stats["pages_by_kind"]["native"] == 2
    assert stats["regions"] == len(regions)
    assert stats["regions_by_type"] == dict(sorted(Counter(r["type"] for r in regions).items()))
    assert stats["low_confidence"] == sum(g["code"] == "low_confidence" for g in gaps) == 1
    assert stats["table_suspected"] == sum(g["code"] == "table_suspected" for g in gaps) == 1
    assert stats["estimated_extract_seconds"] == round(2 * 1.1) and stats["label_seconds"] >= 0
    assert any("suspected" in n for n in stats["workflow_notes"])


def test_estimate_is_zero_when_the_extract_stage_is_cached(tmp_path, setup):
    jobs, job, cfg = setup()
    hit = runner.run(fake_engine(tmp_path, stage="extract"), job.source, job.sha256, cfg.cache_dir, 60)
    assert hit.cache_written
    stats = label.run(jobs, job.id, cfg, engine=fake_engine(tmp_path))
    assert stats["estimated_extract_seconds"] == 0


def test_scanned_pages_get_no_text_layer_gap_and_a_workflow_note(tmp_path, setup):
    jobs, job, cfg = setup(scanned=(2,))
    stats = label.run(jobs, job.id, cfg, engine=fake_engine(tmp_path, skip_pages=(2,)))
    [g] = gaps_of(job, "no_text_layer")
    assert g["page_no"] == 2 and "region_id" not in g
    assert stats["pages_by_kind"]["scanned"] == 1
    assert any("OCR is not implemented" in n for n in stats["workflow_notes"])
    assert out(job, "pages.jsonl")[1]["kind"] == "scanned"
    assert jobs.get(job.id).status == "planned"


def test_declared_language_that_differs_is_a_gap(tmp_path, setup):
    jobs, job, cfg = setup(decl={**DECL, "lang": "zh"}, pages=3)
    label.run(jobs, job.id, cfg, engine=fake_engine(tmp_path, pages=3))
    [g] = gaps_of(job, "lang_mismatch")
    assert g["page_no"] is None and "zh" in g["detail"] and "en" in g["detail"]


def test_an_omitted_language_is_filled_from_detection(tmp_path, setup):
    decl = {k: v for k, v in DECL.items() if k != "lang"}
    jobs, job, cfg = setup(decl=decl, pages=3)
    label.run(jobs, job.id, cfg, engine=fake_engine(tmp_path, pages=3))
    assert jobs.get(job.id).declaration["lang"] == "en"
    assert out(job, "run.json")["declaration_filled"] == ["lang"]
    assert gaps_of(job, "lang_mismatch") == []


# ---------- findtables ----------

def test_find_tables_shapes(tmp_path):
    doc = pymupdf.open(build_pdf(tmp_path / "t.pdf", pages=1))
    page = doc[0]
    [t] = findtables.tables_on(page)
    assert t["bbox"] == [72.0, 200.0, 400.0, 300.0] and (t["n_rows"], t["n_cols"]) == (2, 2)
    assert t["rows"][0][0] == [72.0, 200.0, 236.0, 250.0] and len(t["rows"]) == 2
    assert findtables.table_in(page, [60, 190, 420, 310]) == t
    assert findtables.table_in(page, [60, 400, 420, 500]) is None


# ---------- review fixes ----------

def three_line_page():
    doc = pymupdf.open()
    p = doc.new_page(width=W, height=H)
    p.insert_textbox(pymupdf.Rect(72, 100, 500, 160), "Line one of the paragraph here\nLine two that Docling covers\n"
                     "Line three of the paragraph", fontsize=10)
    return doc, p


def assert_no_native_region_holds_a_covered_word(regions, words):
    others = [r["bbox"] for r in regions if r["label_source"] != "native"]
    for r in regions:
        if r["label_source"] == "native":
            inside = textlayer.words_in(words, r["bbox"])
            assert not [w[4] for w in inside if any(textlayer.centre_in(w, b, label.COVER_PAD) for b in others)]


def test_a_native_region_never_holds_words_of_a_model_region_in_the_same_block():
    doc, p = three_line_page()
    words = textlayer.words(p)
    assert len({w[5] for w in words}) == 1                                    # one text-layer block
    y0, y1 = sorted({(w[1], w[3]) for w in words})[1]                         # its middle line
    item = {"page_no": 1, "bbox": [70, y0 - 0.5, 510, y1 + 0.5], "label": "text", "content_layer": "body",
            "score": 0.9, "classes": None}
    regions, _ = label._docling_page(p, [item], words, textlayer.spans(p), 0.5)
    native = [r for r in regions if r["label_source"] == "native"]
    assert len(native) == 2                                                   # line one and line three
    assert_no_native_region_holds_a_covered_word(regions, words)
    covered = {id(w) for w in textlayer.words_in(words, item["bbox"], label.COVER_PAD)}
    assert sum(len(textlayer.words_in(words, r["bbox"])) for r in native) == len(words) - len(covered)


def test_a_fallback_text_region_never_holds_words_of_a_table_in_the_same_block():
    doc, p = three_line_page()
    words = textlayer.words(p)
    y0, y1 = sorted({(w[1], w[3]) for w in words})[1]
    found = [{"bbox": [70, y0 - 0.5, 510, y1 + 0.5]}]
    regions = label._fallback_page(1, found, words, textlayer.spans(p))
    assert [r["type"] for r in regions] == ["text", "table", "text"]
    assert_no_native_region_holds_a_covered_word(regions, words)


def test_a_picture_classified_table_without_a_score_does_not_crash(tmp_path, setup):
    jobs, job, cfg = setup()
    pics = [(2, (100, 450, 250, 550), [["table", None]])]
    label.run(jobs, job.id, cfg, engine=fake_engine(tmp_path, pictures=pics))
    [g] = [g for g in gaps_of(job, "table_suspected") if g.get("source") == "classifier"]
    assert "n/a" in g["detail"]


def test_a_retry_still_records_that_the_language_was_filled(tmp_path, setup, monkeypatch):
    decl = {k: v for k, v in DECL.items() if k != "lang"}
    jobs, job, cfg = setup(decl=decl, pages=3)

    def boom(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(label, "write_jsonl", boom)
    with pytest.raises(RuntimeError):
        label.run(jobs, job.id, cfg, engine=fake_engine(tmp_path, pages=3))
    monkeypatch.undo()
    label.run(jobs, job.id, cfg, engine=fake_engine(tmp_path, pages=3))
    assert jobs.get(job.id).declaration["lang"] == "en"
    assert out(job, "run.json")["declaration_filled"] == ["lang"]
