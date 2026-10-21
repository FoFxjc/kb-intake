"""Step 6, extract (intake-design-v2 §2, §3, §5.4, §5.5, §5.7): each label-stage region is processed by the handler
the approved workflow gives its type.

- Docling runs again with table structure (TableFormer) and only gives structure. Its tables are matched back to the
  label-stage table regions by box (IoU >= 0.5, one to one); the label stage's region_ids are final.
  - A label table region with no extract-stage match gets status `unhandled` and a `table_unmatched` gap
    (`side: label`). An extract-stage table that matches no label region gets a `table_unmatched` gap
    (`side: extract`) and is not delivered.
  - Where Docling has no answer for a region, `find_tables` is run in the region's box (`extract_method:
    find_tables`): when Docling failed or timed out (the whole document or that page; `extractor_degraded`), and
    for regions the model did not label (`label_source` user after a review reclassify, or native when the label
    stage itself degraded to find_tables).
- Every text, region and cell alike, is read from the PDF text layer by box (kb_intake.textlayer).
- Words centred in a table's box but in none of its cells are reported (`table_words_unassigned`).
- `finish_tables` gives table ids in reading order and the continued-table hints; review calls it again after every
  change.

Work files: extract/regions.jsonl, tables.jsonl, gaps.jsonl, docling.extract.json, run.json (see the plan's "Work
directory" section).
"""

import dataclasses
import resource
import time
from pathlib import Path

import pymupdf

from kb_intake import geometry, runner, textlayer
from kb_intake.config import Config
from kb_intake.engines.docling import DoclingEngine, extract_tables
from kb_intake.jobs import Job, Jobs
from kb_intake.package import read_json, read_jsonl, write_json, write_jsonl

MATCH_IOU = 0.5
TABLE_LABELS = ("table", "document_index")      # Docling labels whose items carry a table grid
BODY_TYPES = ("text", "list", "table")          # content that breaks a table continuing on the next page
SAMPLE_WORDS = 10
REGION_KEYS = ("region_id", "page_no", "bbox", "type", "subtype", "score", "label_source", "layer", "reading_order")
NO_FIND_TABLES = "find_tables found no table in the region"


# ---------- Task A modules, imported when used ----------

def parse_workflow(text: str) -> dict:
    from kb_intake import workflow
    return workflow.parse(text)


def handler_for(wf: dict, region_type: str) -> str:
    from kb_intake import workflow
    return workflow.handler_for(wf, region_type)


def load_workflow(jobs: Jobs, job: Job) -> dict:
    """The approved workflow of a job, parsed."""
    wf = jobs.workflow(job.id, job.workflow_version)
    if wf is None:
        raise ValueError(f"job {job.id} has no approved workflow")
    return parse_workflow(wf["text"])


# ---------- context ----------

class Context:
    """What extracting one region needs: the document, its text layer per page, the extract-stage Docling tables
    matched to label regions, the pages Docling did not complete, and the pages the workflow excludes.

    `docling` is the list of extract-stage tables (engines.docling.extract_tables), or None when Docling gave no
    output at all. Tables on `failed_pages` are ignored."""

    def __init__(self, doc_id: str, pdf: pymupdf.Document, label_regions: list[dict], docling: list[dict] | None,
                 failed_pages=(), excluded_pages=()):
        self.doc_id = doc_id
        self.pdf = pdf
        self.failed_pages = frozenset(failed_pages)
        self.excluded_pages = frozenset(excluded_pages)
        self.docling_ok = docling is not None
        self._words: dict[int, list] = {}
        self._spans: dict[int, list] = {}
        self.matched: dict[str, dict] = {}
        self.unmatched: list[dict] = []
        if docling is not None:
            self._match(label_regions, [t for t in docling if t["page_no"] not in self.failed_pages])

    def _match(self, label_regions: list[dict], tables: list[dict]) -> None:
        """One-to-one IoU matching per page between label regions that are (or were labelled as) tables and the
        extract-stage tables, in page order."""
        cands = [r for r in label_regions if r["type"] == "table" or r.get("docling_label") in TABLE_LABELS]
        for page_no in sorted({r["page_no"] for r in cands} | {t["page_no"] for t in tables}):
            left = [r for r in cands if r["page_no"] == page_no]
            right = [t for t in tables if t["page_no"] == page_no]
            pairs = geometry.match_one_to_one([r["bbox"] for r in left], [t["bbox"] for t in right], MATCH_IOU)
            for i, j, _ in pairs:
                self.matched[left[i]["region_id"]] = right[j]
            used = {j for _, j, _ in pairs}
            self.unmatched += [t for j, t in enumerate(right) if j not in used]

    @classmethod
    def load(cls, jobs: Jobs, job_id: str) -> "Context":
        """The context of an extracted job, rebuilt from its work files (review re-extraction)."""
        job = jobs.get(job_id)
        wf = load_workflow(jobs, job)
        label_regions = read_jsonl(job.dir / "label" / "regions.jsonl")
        d = job.dir / "extract"
        raw = d / "docling.extract.json"
        docling = extract_tables(raw.read_bytes()) if raw.is_file() else None
        failed = read_json(d / "run.json")["engine"]["failed_pages"] if (d / "run.json").is_file() else []
        return cls(job.doc_id, pymupdf.open(job.source), label_regions, docling, failed, wf.get("exclude_pages") or [])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self) -> None:
        self.pdf.close()

    def page(self, page_no: int) -> pymupdf.Page:
        return self.pdf[page_no - 1]

    def words(self, page_no: int) -> list[list]:
        if page_no not in self._words:
            self._words[page_no] = textlayer.words(self.page(page_no))
        return self._words[page_no]

    def spans(self, page_no: int) -> list[list]:
        if page_no not in self._spans:
            self._spans[page_no] = textlayer.spans(self.page(page_no))
        return self._spans[page_no]

    def docling_tables(self, page_no: int) -> dict[str, dict]:
        """region_id -> matched extract-stage table, for the regions of one page."""
        return {rid: t for rid, t in self.matched.items() if t["page_no"] == page_no}

    def docling_done(self, page_no: int) -> bool:
        """Whether Docling's extract stage has an answer for this page."""
        return self.docling_ok and page_no not in self.failed_pages

    def table_in(self, page_no: int, bbox) -> dict | None:
        from kb_intake import findtables
        return findtables.table_in(self.page(page_no), bbox)


# ---------- one region ----------

def _gap(code: str, page_no: int | None, detail: str, **extra) -> dict:
    return {"code": code, "page_no": page_no, **{k: v for k, v in extra.items() if v is not None}, "detail": detail}


def extract_region(region: dict, handler: str, ctx: Context) -> tuple[dict, dict | None, list[dict]]:
    """(contract region row, table row or None, gaps) of one label-stage region under `handler`. The table row has
    no `table_id` / `continued_hint` yet (finish_tables)."""
    page_no, rid = region["page_no"], region["region_id"]
    font = region["font"] if "font" in region else textlayer.font_of(ctx.spans(page_no), region["bbox"])
    row = {k: region[k] for k in REGION_KEYS}
    row.update(text=None, text_source=None, font=font, status=None, handler=handler, table_id=None)
    if page_no in ctx.excluded_pages:
        row.update(status="excluded", handler=None)
        return row, None, []
    if handler in ("record", "discard"):
        row["status"] = "recorded" if handler == "record" else "discarded"
        return row, None, []
    if handler in ("unhandled", "ocr"):
        row["status"] = "unhandled"
        why = "OCR is not implemented" if handler == "ocr" else "the workflow does not handle this type"
        return row, None, [_gap("unhandled", page_no, f"{region['type']}: {why}", region_id=rid)]

    words = ctx.words(page_no)
    text = textlayer.text_of(textlayer.words_in(words, region["bbox"]))
    gaps: list[dict] = []
    if text is None and words:          # a page with no words at all already has a page-level gap from label
        gaps.append(_gap("no_text_layer", page_no, "no text-layer words in the region", region_id=rid))
    if handler == "text":
        row.update(status="extracted", text=text, text_source="native" if text is not None else None)
        return row, None, gaps
    if handler != "table_structure":
        raise ValueError(f"unknown handler {handler!r}")

    table, more = None, []
    if rid in ctx.matched:
        table, more = table_from_cells(region, ctx.matched[rid], words, "docling")
    elif ctx.docling_done(page_no) and region["label_source"] == "model":
        row["status"] = "unhandled"
        return row, None, [_gap("table_unmatched", page_no, "labelled as a table, but the extract stage found no "
                                f"table matching its box (IoU >= {MATCH_IOU})", region_id=rid, side="label",
                                bbox=list(region["bbox"]))]
    else:
        try:
            found = ctx.table_in(page_no, region["bbox"])
        except Exception as e:  # noqa: BLE001 - a find_tables failure degrades this region, not the job
            row["status"] = "unhandled"
            return row, None, [_gap("extractor_degraded", page_no, f"find_tables: {type(e).__name__}: {e}"[:300],
                                    region_id=rid, extractor="find_tables")]
        if found is None:
            row["status"] = "unhandled"
            return row, None, [_gap("unhandled", page_no, NO_FIND_TABLES, region_id=rid)]
        table, more = table_from_cells(region, found, words, "find_tables")
    row.update(status="extracted", text=text, text_source="native" if text is not None else None)
    return row, table, gaps + more


def table_from_cells(region: dict, grid: dict, words: list[list], method: str) -> tuple[dict, list[dict]]:
    """A table row (no table_id / continued_hint) and its gaps. `grid` is an extract-stage Docling table
    ({"bbox", "n_rows", "n_cols", "cells": [{"r", "c", "rs", "cs", "bbox", "is_header"}]}) or a find_tables table
    ({"bbox", "n_rows", "n_cols", "rows": [[cell box or None, ...], ...]}; spans are not known: rs = cs = 1)."""
    if "cells" in grid:
        raw = [(c["r"], c["c"], c["rs"], c["cs"], c["bbox"], c["is_header"]) for c in grid["cells"]]
    else:
        raw = [(r, c, 1, 1, box, False) for r, row in enumerate(grid["rows"]) for c, box in enumerate(row)
               if box is not None]
    cells, assigned = [], set()
    for r, c, rs, cs, box, is_header in sorted(raw, key=lambda x: (x[0], x[1])):
        ws = textlayer.words_in(words, box) if box is not None else []
        assigned.update(id(w) for w in ws)
        text = textlayer.text_of(ws)
        bbox, source = textlayer.text_box(words, box) if text is not None else (None, None)
        cells.append({"r": r, "c": c, "rs": rs, "cs": cs, "text": text, "bbox": bbox, "bbox_source": source,
                      "is_header": bool(is_header)})
    table = {"region_id": region["region_id"], "page_no": region["page_no"], "bbox": list(grid["bbox"]),
             "n_rows": grid["n_rows"], "n_cols": grid["n_cols"], "cells": cells,
             "markdown": markdown(grid["n_rows"], grid["n_cols"], cells), "extract_method": method}
    lost = [w for w in textlayer.words_in(words, grid["bbox"]) if id(w) not in assigned]
    gaps = []
    if lost:
        sample = " ".join(w[4] for w in lost[:SAMPLE_WORDS]) + (" ..." if len(lost) > SAMPLE_WORDS else "")
        gaps.append(_gap("table_words_unassigned", region["page_no"],
                         f"{len(lost)} word(s) in the table box but in no cell: {sample}",
                         region_id=region["region_id"], count=len(lost)))
    return table, gaps


def markdown(n_rows: int, n_cols: int, cells: list[dict]) -> str:
    """A pipe table for reading; a spanning cell's text fills every position it covers."""
    if n_rows <= 0 or n_cols <= 0:
        return ""
    grid = [[""] * n_cols for _ in range(n_rows)]
    for c in cells:
        text = (c["text"] or "").replace("\n", " ").replace("|", "\\|")
        for r in range(c["r"], min(n_rows, c["r"] + c["rs"])):
            for k in range(c["c"], min(n_cols, c["c"] + c["cs"])):
                grid[r][k] = text
    lines = ["|" + "|".join(grid[0]) + "|", "|" + "|".join(["---"] * n_cols) + "|"]
    return "\n".join(lines + ["|" + "|".join(row) + "|" for row in grid[1:]])


# ---------- tables of the document ----------

def _centre_y(b) -> float:
    return (b[1] + b[3]) / 2


def finish_tables(regions: list[dict], tables: list[dict]) -> tuple[list[dict], list[dict]]:
    """(regions, tables) with table ids `{doc_id}:t{page:04d}_{n:02d}` (n: the page's tables in reading order, from
    01), each region's `table_id`, and `continued_hint`, tables in reading order. The inputs are not changed.

    Table T, the first table on page p+1, may continue L, the last table on page p, when they have the same number
    of columns, no body text / list / table region stands above T on its page, and none stands below L on its page.
    Only a hint: tables are never merged."""
    order = {r["region_id"]: r["reading_order"] for r in regions}
    ordered = sorted(tables, key=lambda t: order[t["region_id"]])
    ids, counts = {}, {}
    for t in ordered:
        counts[t["page_no"]] = counts.get(t["page_no"], 0) + 1
        doc_id = t["region_id"].split(":p")[0]
        ids[t["region_id"]] = f"{doc_id}:t{t['page_no']:04d}_{counts[t['page_no']]:02d}"

    def blocking(page_no: int, exclude: str):
        return [r for r in regions if r["page_no"] == page_no and r["region_id"] != exclude
                and r["layer"] == "body" and r["type"] in BODY_TYPES and r["status"] != "excluded"]

    first, last = {}, {}
    for t in ordered:
        first.setdefault(t["page_no"], t)
        last[t["page_no"]] = t
    hints = {}
    for page_no, t in first.items():
        prev = last.get(page_no - 1)
        if prev is None or prev["n_cols"] != t["n_cols"]:
            continue
        if any(_centre_y(r["bbox"]) < t["bbox"][1] for r in blocking(page_no, t["region_id"])):
            continue
        if any(_centre_y(r["bbox"]) > prev["bbox"][3] for r in blocking(page_no - 1, prev["region_id"])):
            continue
        hints[t["region_id"]] = ids[prev["region_id"]]

    out_tables = [{"table_id": ids[t["region_id"]], **{k: v for k, v in t.items()
                                                        if k not in ("table_id", "continued_hint")},
                   "continued_hint": hints.get(t["region_id"])} for t in ordered]
    out_regions = [{**r, "table_id": ids.get(r["region_id"])} for r in regions]
    return out_regions, out_tables


def tag_table_gaps(gaps: list[dict], tables: list[dict]) -> list[dict]:
    """The gaps, with `table_id` set on table_words_unassigned gaps from their region's table (finish_tables gives
    the ids). Call it after every finish_tables. The inputs are not changed."""
    ids = {t["region_id"]: t["table_id"] for t in tables}
    return [{**g, "table_id": ids[g["region_id"]]} if g["code"] == "table_words_unassigned"
            and g.get("region_id") in ids else g for g in gaps]


def gap_key(g: dict) -> tuple:
    return (g["page_no"] or 0, g["code"], g.get("region_id") or "", g.get("table_id") or "", g["detail"])


# ---------- the step ----------

def _cpu() -> tuple[float, float]:
    self_, children = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
    return self_.ru_utime + self_.ru_stime, children.ru_utime + children.ru_stime


def run(jobs: Jobs, job_id: str, cfg: Config, engine=None, progress=None) -> dict:
    """Extract an approved job: write extract/*, move it to `extracted` then `in_review`. Returns a summary.
    Progress events are written to the job store; `progress` only mirrors them for live display."""
    def emit(step: str, status: str, info: dict | None = None):
        jobs.event(job_id, step, status, info)
        if progress:
            progress(step, status, info or {})

    try:
        return _run(jobs, job_id, cfg, engine, emit)
    except Exception as e:
        jobs.event(job_id, "extract", "failed", {"error": f"{type(e).__name__}: {e}"[:300]})
        raise


def _run(jobs: Jobs, job_id: str, cfg: Config, engine, progress) -> dict:
    t0, cpu0 = time.monotonic(), _cpu()
    with jobs.running(job_id, "extract") as job:
        wf = load_workflow(jobs, job)
        out = job.dir / "extract"
        out.mkdir(parents=True, exist_ok=True)
        label_regions = read_jsonl(job.dir / "label" / "regions.jsonl")

        progress("docling", "started", {"stage": "extract"})
        outcome, params, docling, gaps = _docling(job, cfg, engine, out)
        progress("docling", "done", {"status": outcome.status, "cached": outcome.cached,
                                     "seconds": outcome.seconds})

        progress("regions", "started", {"regions": len(label_regions)})
        rows, tables = [], []
        ctx = Context(job.doc_id, pymupdf.open(job.source), label_regions, docling, outcome.failed_pages,
                      wf.get("exclude_pages") or [])
        with ctx:
            for r in label_regions:
                row, table, g = extract_region(r, handler_for(wf, r["type"]), ctx)
                rows.append(row)
                gaps += g
                if table is not None:
                    tables.append(table)
            extra = [t for t in ctx.unmatched if t["page_no"] not in ctx.excluded_pages]
        for t in extra:
            gaps.append(_gap("table_unmatched", t["page_no"], "the extract stage found a table that matches no "
                             f"label table region (IoU >= {MATCH_IOU}); not delivered", side="extract",
                             bbox=list(t["bbox"])))
        rows, tables = finish_tables(rows, tables)
        gaps = sorted(tag_table_gaps(gaps, tables), key=gap_key)
        write_jsonl(out / "regions.jsonl", rows)
        write_jsonl(out / "tables.jsonl", tables)
        write_jsonl(out / "gaps.jsonl", gaps)
        progress("regions", "done", {"regions": len(rows), "tables": len(tables), "gaps": len(gaps)})

        cpu1 = _cpu()
        seconds = round(time.monotonic() - t0, 3)
        write_json(out / "run.json", {
            "seconds": seconds,
            "cpu_seconds": {"self": round(cpu1[0] - cpu0[0], 3), "children": round(cpu1[1] - cpu0[1], 3)},
            "engine": {"status": outcome.status, "seconds": outcome.seconds, "cached": outcome.cached,
                       "cache_key": outcome.cache_key, "failed_pages": list(outcome.failed_pages),
                       "errors": list(outcome.errors), "dropped_table_cells": None, "unscored": None},
            "params": {"extract": params}, "unmatched_extract_tables": len(extra)})
        jobs.advance(job_id, ("extracted", "in_review"), op="extract")
    progress("extract", "done", {"seconds": seconds})
    by_status: dict[str, int] = {}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    return {"regions": len(rows), "tables": len(tables), "gaps": len(gaps), "regions_by_status": dict(
        sorted(by_status.items())), "unmatched_extract_tables": len(extra), "engine_status": outcome.status,
        "seconds": seconds}


def _docling(job: Job, cfg: Config, engine, out: Path):
    """(outcome, params, extract-stage tables or None, gaps). Any failure degrades: tables come from find_tables."""
    params = cfg.docling.engine_params("extract")
    raw = out / "docling.extract.json"
    raw.unlink(missing_ok=True)                 # a retry must not keep the output of an earlier run
    if not cfg.layout_enabled:
        outcome = runner.Outcome("disabled", None, None, False, None, errors=["layout engine disabled"])
    else:
        engine = engine or DoclingEngine(cfg.models_dir, cfg.docling, stage="extract")
        info = engine.info()
        params = info.params
        outcome = runner.run(engine, job.source, job.sha256, cfg.cache_dir, cfg.docling.wall_timeout, info=info)
    docling = None
    if outcome.data is not None:
        try:
            docling = extract_tables(outcome.data)
        except Exception as e:  # noqa: BLE001 - output that cannot be read degrades like a failed conversion
            # Not kept: Context.load (review re-extraction) and delivery read docling.extract.json, and run.json
            # must not report a success that the extractor_degraded gap contradicts.
            outcome = dataclasses.replace(outcome, status="error", errors=[
                *outcome.errors, f"unreadable Docling output: {type(e).__name__}: {e}"[:300]])
        else:
            raw.write_bytes(outcome.data)
    gaps = []
    if docling is None:
        why = "; ".join(outcome.errors)[:300] or "no output"
        gaps.append(_gap("extractor_degraded", None, f"extract: Docling {outcome.status} ({why}); table regions "
                         "fall back to find_tables", extractor="docling"))
    elif outcome.failed_pages:
        gaps.append(_gap("extractor_degraded", None, f"extract: Docling did not complete {len(outcome.failed_pages)}"
                         " page(s); their table regions fall back to find_tables", extractor="docling",
                         pages=sorted(outcome.failed_pages)))
    return outcome, params, docling, gaps
