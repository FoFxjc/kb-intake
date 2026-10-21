"""Shared helpers for the CLI and service tests: a synthetic PDF shaped like tests/fakes.document, a declaration,
and stand-ins for kb_intake.review / kb_intake.deliver (Task C) that move the job through kb_intake.jobs the way
the real modules do. label.run and extract.run are the real ones, run on tests/fakes.fake_engine.
"""

import sys
import types

import pymupdf
import yaml
from fakes import H, W

import kb_intake
from kb_intake import jobs as jobs_mod
from kb_intake import package
from kb_intake.package import read_jsonl

DOC = "demo-ar-2025-en"
DECL = {"doc_id": DOC, "issuer_id": "demo", "doc_type": "annual_report", "period_end": "2025-12-31",
        "source_url": "https://example.com/demo.pdf", "completeness": {"full": True}, "lang": "en"}
BODY = ("The Group reported a net profit for the year and the board is pleased with the results of this year. "
        "It is the view of the board that the bank is in a strong position for the next year.")
TABLE_BOX = (72, 200, 400, 300)


def build_pdf(path, pages: int = 2):
    """Each page: a heading at y 67-84 and body text in 100-140; page 1 has a ruled 2 x 2 table in TABLE_BOX (the
    boxes of fakes.document)."""
    doc = pymupdf.open()
    for n in range(1, pages + 1):
        p = doc.new_page(width=W, height=H)
        p.insert_text((72, 80), f"Section {n}", fontsize=12, fontname="hebo")
        p.insert_textbox(pymupdf.Rect(72, 100, 500, 140), BODY, fontsize=9)
        if n == 1:
            x0, y0, x1, y1 = TABLE_BOX
            xs, ys = [x0, (x0 + x1) / 2, x1], [y0, (y0 + y1) / 2, y1]
            for r in range(2):
                for c in range(2):
                    p.draw_rect(pymupdf.Rect(xs[c], ys[r], xs[c + 1], ys[r + 1]), color=(0, 0, 0), width=0.8)
                    p.insert_text((xs[c] + 5, ys[r] + 30), f"cell {r}{c}", fontsize=10)
    doc.save(path)
    doc.close()
    return path


def decl_yaml(**over) -> str:
    return yaml.safe_dump({**DECL, **over}, sort_keys=False)


class ReviewError(ValueError):
    pass


def _regions(job) -> list[dict]:
    d = job.dir / "extract" / "regions.jsonl"
    return read_jsonl(d if d.is_file() else job.dir / "label" / "regions.jsonl")


def _apply(jobs, job_id, ops, by, cfg):
    job = jobs.get(job_id)
    if job.status != "in_review":
        raise jobs_mod.WrongState(f"job {job_id} is {job.status}; this needs in_review")
    regions = {r["region_id"]: r for r in _regions(job)}
    for op in ops:
        if op.get("action") not in ("reclassify", "exclude") or op.get("region_id") not in regions:
            raise ReviewError(f"bad op {op}")
    rows = [{"action": op["action"], "region_id": op["region_id"], "from": regions[op["region_id"]]["type"],
             "to": op.get("to"), "by": by, "at": jobs_mod.now(), "note": op.get("note")} for op in ops]
    jobs.add_ops(job_id, rows)
    return rows


def _page_view(jobs, job_id, page_no):
    job = jobs.get(job_id)
    regions = [r for r in _regions(job) if r["page_no"] == page_no]
    tables_file = job.dir / "extract" / "tables.jsonl"
    tables = [t for t in read_jsonl(tables_file) if t["page_no"] == page_no] if tables_file.is_file() else []
    return {"page_no": page_no, "width": W, "height": H, "page_label": None, "kind": "native", "excluded": False,
            "regions": regions, "tables": tables, "gaps": []}


def _deliver(jobs, job_id, cfg):
    with jobs.running(job_id, "deliver") as job:
        name = package.name(job.doc_id, job.sha256)
        d = cfg.delivery_dir / name
        if d.exists():
            raise FileExistsError(f"{d} exists")
        d.mkdir(parents=True)
        (d / "manifest.json").write_text("{}\n", encoding="utf-8")
        jobs.event(job_id, "deliver", "done", {"package": name})
        jobs.advance(job_id, ("delivered",), op="deliver", package=name)
    return d


def _confirm(jobs, job_id, by, cfg):
    if jobs.get(job_id).status == "in_review":
        jobs.confirm(job_id, by)
    return _deliver(jobs, job_id, cfg)


def install(monkeypatch) -> tuple[types.ModuleType, types.ModuleType]:
    """Put stand-in kb_intake.review and kb_intake.deliver modules in place for one test."""
    review = types.ModuleType("kb_intake.review")
    review.ReviewError, review.apply, review.page_view = ReviewError, _apply, _page_view
    deliver = types.ModuleType("kb_intake.deliver")
    deliver.confirm, deliver.deliver = _confirm, _deliver
    for name, mod in (("review", review), ("deliver", deliver)):
        monkeypatch.setitem(sys.modules, f"kb_intake.{name}", mod)
        monkeypatch.setattr(kb_intake, name, mod, raising=False)
    return review, deliver
