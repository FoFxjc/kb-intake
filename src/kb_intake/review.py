"""Step 7, review (intake-design-v2 §2, §4): the user reclassifies or excludes regions of an extracted job; a changed
region is extracted again with the workflow handler of its new type. Content is never edited: it comes from the
source.

    apply(jobs, job_id, [{"action": "reclassify", "region_id", "to", "note"?},
                         {"action": "exclude", "region_id", "note"?}], by, cfg)

- All or nothing per call: every op is validated against the current regions before any is applied; one bad op
  (unknown action, key, region or type; the region's current type; a region already excluded, by review or by the
  workflow's exclude_pages; the same region twice in one call) raises ReviewError and nothing changes.
- Reclassify: type = to, subtype null, label_source "user", score null, then extract.extract_region with the
  workflow's handler for the new type. To `table`: the extract-stage Docling grid matched to the region is reused
  when there is one (a label-stage table reclassified back); otherwise find_tables runs in the region's box
  (extract_method find_tables); nothing found -> status unhandled + `unhandled` gap.
- Exclude: status excluded, text null, handler null (as for a page the workflow excludes), its table removed.
- Gaps: the label stage's gaps are kept whatever review does. A changed region's extract-stage gaps (those with its
  region_id) are removed from extract/gaps.jsonl, and those of a re-extraction written to review/gaps.jsonl.
- Each op is recorded with jobs.add_ops as a review.jsonl row {"action", "region_id", "from", "to", "by", "at",
  "note"}: reclassify from / to are types; exclude goes from the region's status to "excluded".

The current state lives in the work files extract/regions.jsonl, extract/tables.jsonl, extract/gaps.jsonl and
review/gaps.jsonl. A per-job file lock (`review_lock`, also taken by delivery while it reads them) serialises the CLI
and the server; ops are stored in the job store before the files are replaced, so a concurrent confirmation either
refuses the ops or waits for the files.
"""

import fcntl
from contextlib import contextmanager
from pathlib import Path

from kb_intake import extract, workflow
from kb_intake.config import Config
from kb_intake.jobs import Jobs, WrongState, now
from kb_intake.package import read_jsonl, write_jsonl
from kb_intake.vocab import REGION_TYPES

ACTIONS = {"reclassify": {"action", "region_id", "to", "note"}, "exclude": {"action", "region_id", "note"}}
POST_EXTRACT = ("extracted", "in_review", "confirmed", "delivered")


class ReviewError(ValueError):
    pass


@contextmanager
def review_lock(job_dir: Path):
    """An exclusive lock on the job's review files (across processes)."""
    d = job_dir / "review"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / ".lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def current(job_dir: Path) -> dict:
    """The current extract-stage state of a job: regions, tables, extract gaps, review gaps."""
    rg = job_dir / "review" / "gaps.jsonl"
    return {"regions": read_jsonl(job_dir / "extract" / "regions.jsonl"),
            "tables": read_jsonl(job_dir / "extract" / "tables.jsonl"),
            "extract_gaps": read_jsonl(job_dir / "extract" / "gaps.jsonl"),
            "review_gaps": read_jsonl(rg) if rg.is_file() else []}


# ---------- validation ----------

def _validate(ops, by, regions: dict[str, dict]) -> None:
    if not isinstance(by, str) or not by.strip():
        raise ReviewError("review needs a name (by)")
    if not isinstance(ops, list) or not ops:
        raise ReviewError("review needs a non-empty list of ops")
    seen = set()
    for i, op in enumerate(ops, 1):
        where = f"op {i}"
        if not isinstance(op, dict):
            raise ReviewError(f"{where}: an op must be a mapping")
        action = op.get("action")
        if action not in ACTIONS:
            raise ReviewError(f"{where}: unknown action {action!r} (known: {sorted(ACTIONS)}); content cannot be "
                              "edited in review")
        extra = sorted(map(str, set(op) - ACTIONS[action]))
        if extra:
            raise ReviewError(f"{where}: unknown key(s) {extra} for {action}")
        note = op.get("note")
        if note is not None and not isinstance(note, str):
            raise ReviewError(f"{where}: note must be a string")
        rid = op.get("region_id")
        if not isinstance(rid, str) or rid not in regions:
            raise ReviewError(f"{where}: unknown region {rid!r}")
        if rid in seen:
            raise ReviewError(f"{where}: region {rid} appears more than once in this call")
        seen.add(rid)
        region = regions[rid]
        if region["status"] == "excluded":
            raise ReviewError(f"{where}: region {rid} is excluded")
        if action == "reclassify":
            to = op.get("to")
            if to not in REGION_TYPES:
                raise ReviewError(f"{where}: unknown region type {to!r} (known: {list(REGION_TYPES)})")
            if to == region["type"]:
                raise ReviewError(f"{where}: region {rid} already has type {to}")


# ---------- apply ----------

def apply(jobs: Jobs, job_id: str, ops: list[dict], by: str, cfg: Config) -> list[dict]:
    """Validate then apply the ops on a job in review; return the recorded op rows. Raises ReviewError (nothing
    applied), jobs.WrongState / jobs.Busy, KeyError for an unknown job."""
    job = jobs.get(job_id)
    with review_lock(job.dir):
        job = jobs.get(job_id)
        if job.status != "in_review":
            raise WrongState(f"job {job_id} is {job.status}; review needs in_review")
        state = current(job.dir)
        regions = {r["region_id"]: r for r in state["regions"]}
        _validate(ops, by, regions)
        label = {r["region_id"]: r for r in read_jsonl(job.dir / "label" / "regions.jsonl")}

        new_regions, tables = dict(regions), {t["region_id"]: t for t in state["tables"]}
        changed, review_gaps, records = set(), [], []
        with extract.Context.load(jobs, job_id) as ctx:
            wf = extract.load_workflow(jobs, job)
            for op in ops:
                rid = op["region_id"]
                old = regions[rid]
                changed.add(rid)
                tables.pop(rid, None)
                if op["action"] == "reclassify":
                    if label[rid]["type"] != "table":       # reuse the Docling grid only for a label-stage table
                        ctx.matched.pop(rid, None)
                    src = {**label[rid], "type": op["to"], "subtype": None, "label_source": "user", "score": None}
                    row, table, gaps = extract.extract_region(src, workflow.handler_for(wf, op["to"]), ctx)
                    new_regions[rid] = row
                    if table is not None:
                        tables[rid] = table
                    review_gaps += gaps
                    frm, to = old["type"], op["to"]
                else:
                    new_regions[rid] = {**old, "status": "excluded", "text": None, "text_source": None,
                                        "handler": None, "table_id": None}
                    frm, to = old["status"], "excluded"
                records.append({"action": op["action"], "region_id": rid, "from": frm, "to": to, "by": by,
                                "at": now(), "note": op.get("note")})

        rows, out_tables = extract.finish_tables([new_regions[r["region_id"]] for r in state["regions"]],
                                                 list(tables.values()))
        extract_gaps = [g for g in state["extract_gaps"] if g.get("region_id") not in changed]
        review_gaps = [g for g in state["review_gaps"] if g.get("region_id") not in changed] + review_gaps
        extract_gaps = sorted(extract.tag_table_gaps(extract_gaps, out_tables), key=extract.gap_key)
        review_gaps = sorted(extract.tag_table_gaps(review_gaps, out_tables), key=extract.gap_key)

        # The point of no return: refuses if the job left in_review. Known window: the four writes below are each
        # atomic but not together, so a crash among them leaves ops recorded whose effects are not (all) in the
        # work files; nothing replays them.
        jobs.add_ops(job_id, records)
        write_jsonl(job.dir / "extract" / "regions.jsonl", rows)
        write_jsonl(job.dir / "extract" / "tables.jsonl", out_tables)
        write_jsonl(job.dir / "extract" / "gaps.jsonl", extract_gaps)
        write_jsonl(job.dir / "review" / "gaps.jsonl", review_gaps)
    jobs.event(job_id, "review", "applied", {"ops": len(records), "by": by})
    return records


# ---------- the page view (UI) ----------

def _excluded_pages(jobs: Jobs, job) -> list[int]:
    wf = jobs.workflow(job.id, job.workflow_version)       # the approved one, else the latest draft
    if wf is None:
        return []
    try:
        return workflow.parse(wf["text"])["exclude_pages"]
    except workflow.WorkflowError:
        return []


def page_view(jobs: Jobs, job_id: str, page_no: int) -> dict:
    """One page as the UI shows it: {"page_no", "width", "height", "page_label", "kind", "excluded", "regions",
    "tables", "gaps"}; label-stage regions before extraction, the current regions after. KeyError for an unknown
    job, a job not labelled yet, or a page out of range."""
    job = jobs.get(job_id)
    d = job.dir
    pages_file = d / "label" / "pages.jsonl"
    if not pages_file.is_file():
        raise KeyError(f"job {job_id} has no labelled pages")
    page = next((p for p in read_jsonl(pages_file) if p["page_no"] == page_no), None)
    if page is None:
        raise KeyError(f"job {job_id} has no page {page_no}")
    gaps = read_jsonl(d / "label" / "gaps.jsonl")
    if job.status in POST_EXTRACT:
        with review_lock(d):
            state = current(d)
        regions, tables = state["regions"], state["tables"]
        gaps += state["extract_gaps"] + state["review_gaps"]
    else:
        regions, tables = read_jsonl(d / "label" / "regions.jsonl"), []
    return {"page_no": page_no, "width": page["width"], "height": page["height"], "page_label": page["page_label"],
            "kind": page["kind"], "excluded": page_no in _excluded_pages(jobs, job),
            "regions": [r for r in regions if r["page_no"] == page_no],
            "tables": [t for t in tables if t["page_no"] == page_no],
            "gaps": sorted((g for g in gaps if g["page_no"] == page_no), key=extract.gap_key)}
