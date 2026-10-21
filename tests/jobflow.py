"""Jobs driven through the real steps for the review and delivery tests: a synthetic PDF, label.run and extract.run
with the fake Docling engine (tests/fakes.py), so the work files are the ones the steps really write.

The source has two pages, each with a heading (y 68-84) and body text (y 100-140):
- page 1: a ruled 2 x 2 table in TABLE_BOX that the fake Docling labels as a table (and gives a grid for);
- page 2: a ruled 2 x 2 table in P106_TABLE that the fake Docling only sees as part of a text item covering
  P106_BOX (OCBC p106: a table Docling missed), so label raises a find_tables `table_suspected` gap.
"""

import dataclasses
import json
from types import SimpleNamespace

import pymupdf
from fakes import H, W, FakeEngine, bl, document

from kb_intake import extract, label
from kb_intake.config import Config, DoclingParams
from kb_intake.jobs import Jobs

DOC = "demo-ar-2025-en"
DECL = {"doc_id": DOC, "issuer_id": "demo", "doc_type": "annual_report", "period_end": "2025-12-31",
        "source_url": "https://example.com/demo.pdf", "completeness": {"full": True}, "lang": "en"}
BODY = ("The Group reported a net profit for the year and the board is pleased with the results of this year. "
        "It is the view of the board that the bank is in a strong position for the next year.")
TABLE_BOX = (72, 200, 400, 300)
TABLE_WORDS = (("Item", 80, 230), ("2025", 244, 230), ("Loans", 80, 280), ("1,234", 244, 280))
P106_BOX = (60, 390, 420, 510)
P106_TABLE = (72, 400, 400, 500)
P106_WORDS = (("Overdue", 80, 430), ("2025", 244, 430), ("90 days", 80, 480), ("5,678", 244, 480))


def ruled(page, box, words):
    x0, y0, x1, y1 = box
    xm, ym = (x0 + x1) / 2, (y0 + y1) / 2
    for x in (x0, xm, x1):
        page.draw_line((x, y0), (x, y1))
    for y in (y0, ym, y1):
        page.draw_line((x0, y), (x1, y))
    for text, x, y in words:
        page.insert_text((x, y), text, fontsize=10)


def build_pdf(path):
    doc = pymupdf.open()
    for n in (1, 2):
        p = doc.new_page(width=W, height=H)
        p.insert_text((72, 80), f"Section {n}", fontsize=12, fontname="hebo")
        p.insert_textbox(pymupdf.Rect(72, 100, 500, 140), BODY, fontsize=9)
        if n == 1:
            ruled(p, TABLE_BOX, TABLE_WORDS)
        else:
            ruled(p, P106_TABLE, P106_WORDS)
    doc.save(path)
    return path


P106_SPLIT = ((60, 390, 420, 445), (60, 455, 420, 510))     # OCBC p106 as Docling saw it: one text item per row


CHART_BOX = (80, 600, 400, 760)     # with `chart`: a picture the classifier calls a bar chart, on page 1


def engine(tmp_path, stage: str, behaviour: str = "ok", p106_split: bool = False, chart: bool = False) -> FakeEngine:
    """The fake Docling of this source. Page 2's table is one text item over P106_BOX, or with `p106_split` one
    text item per table row (P106_SPLIT), the first of which gets the table_suspected gap."""
    pictures = [(1, CHART_BOX, [["bar_chart", 0.93], ["line_chart", 0.04]])] if chart else []
    doc, clusters = document(pages=2, stage=stage, table_page=1, table_box=TABLE_BOX, pictures=pictures)
    for box in (P106_SPLIT if p106_split else (P106_BOX,)):     # the text item(s) that swallow page 2's table
        ref = f"#/texts/{len(doc['texts'])}"
        doc["texts"].append({"self_ref": ref, "label": "text", "content_layer": "body", "text": "docling (ignored)",
                             "prov": [{"page_no": 2, "bbox": bl(*box)}], "children": [],
                             "parent": {"$ref": "#/body"}})
        doc["body"]["children"].append({"$ref": ref})
        clusters.setdefault("2", []).append([*box, "text", 0.9])
    return FakeEngine(models_dir=tmp_path / "models", params=DoclingParams(), stage=stage, behaviour=behaviour,
                      doc=json.dumps([doc, clusters]), calls=str(tmp_path / "calls.txt"))


def config(tmp_path, work="work", delivery="delivery", llm_url: str | None = None) -> Config:
    """With `llm_url`, the LLM is enabled against that endpoint (the key must be in INTAKE_LLM_API_KEY)."""
    cfg = Config(models_dir=tmp_path / "models", cache_dir=tmp_path / "cache", work_dir=tmp_path / work,
                 delivery_dir=tmp_path / delivery)
    if llm_url:
        cfg = dataclasses.replace(cfg, llm=dataclasses.replace(cfg.llm, enabled=True, base_url=llm_url))
    return cfg


def in_review(tmp_path, work="work", delivery="delivery", workflow_text=None, source=None, p106_split=False,
              chart=False, llm_url=None):
    """A job labelled, approved (optionally with `workflow_text`) and extracted: state `in_review`."""
    cfg = config(tmp_path, work, delivery, llm_url)
    jobs = Jobs(cfg.work_dir)
    src = source or tmp_path / "demo.pdf"
    if not src.exists():
        build_pdf(src)
    job, _ = jobs.create(src, dict(DECL))
    label.run(jobs, job.id, cfg, engine=engine(tmp_path, "label", p106_split=p106_split, chart=chart))
    if workflow_text is not None:
        jobs.save_workflow(job.id, workflow_text)
    jobs.approve(job.id, "peter")
    extract.run(jobs, job.id, cfg, engine=engine(tmp_path, "extract", p106_split=p106_split, chart=chart))
    return SimpleNamespace(jobs=jobs, job=jobs.get(job.id), cfg=cfg, src=src)


def rid(page: int, n: int) -> str:
    return f"{DOC}:p{page:04d}:r{n:03d}"
