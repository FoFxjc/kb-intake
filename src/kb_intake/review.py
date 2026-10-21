"""Step 7, review (intake-design-v2 §2, §4): the user reclassifies or excludes regions of an extracted job; a changed
region is extracted again with the workflow handler of its new type. Content is never edited: it comes from the
source.

    apply(jobs, job_id, [{"action": "reclassify", "region_id", "to", "note"?},
                         {"action": "exclude", "region_id", "note"?},
                         {"action": "ack", "item_id", "verdict"?: "ok" | "wrong", "note"?}], by, cfg)

- Ack (intake-ui-llm-design-v1 §3.4): the reviewer looked at a queue item (kb_intake.queue) and changes nothing.
  The row records the item as `target` and the `verdict`; a gap item gets `resolved: {action: ack}`. Refused for an
  unknown or already resolved item, twice in a call, or with a reclassify / exclude of the item's region in the same
  call.

- All or nothing per call: every op is validated against the current regions before any is applied; one bad op
  (unknown action, key, region or type; the region's current type; a region already excluded, by review or by the
  workflow's exclude_pages; the same region twice in one call) raises ReviewError and nothing changes.
- Reclassify: type = to, subtype null, label_source "user", score null, then extract.extract_region with the
  workflow's handler for the new type. To `table`: the extract-stage Docling grid matched to the region is reused
  when there is one (a label-stage table reclassified back); otherwise find_tables runs in the region's box
  (extract_method find_tables); nothing found -> status unhandled + `unhandled` gap.
- A suspected table (intake-design-v2 b543c5a): reclassifying to `table` a region that has a find_tables
  `table_suspected` gap takes the union of the region's box and the find_tables box as the table's box (OCBC p106:
  Docling split the table into many text regions and the gap hangs on one of them). Other non-table regions whose
  centre lies in that box get status `absorbed` and `absorbed_into` = the new table's id (review/absorbed.json keeps
  who absorbed whom, so ids follow renumbering); reviewing the table region again restores them. An absorbed region
  cannot be reviewed itself.
- Exclude: status excluded, text null, handler null (as for a page the workflow excludes), its table removed.
- Resolved gaps: a label gap that review dealt with stays, with `resolved: {action, review_seq}` (review/
  resolved.jsonl; all_gaps applies it): reclassify resolves the region's low_confidence (and table_suspected when
  the new type is table, also on the regions it absorbed); exclude resolves all of the region's label gaps.
- Gaps: the label stage's gaps are kept whatever review does. A changed region's extract-stage gaps (those with its
  region_id) are removed from extract/gaps.jsonl, and those of a re-extraction written to review/gaps.jsonl.
- Each op is recorded with jobs.add_ops as a review.jsonl row {"action", "region_id", "from", "to", "by", "at",
  "note"}: reclassify from / to are types; exclude goes from the region's status to "excluded".

The current state lives in the work files extract/regions.jsonl, extract/tables.jsonl, extract/gaps.jsonl and
review/gaps.jsonl. A review call is a step of the job store (`jobs.running(job, "review")`): one review, confirmation
or delivery of a job at a time, across the CLI and the server (confirmation is Busy while a review runs). Ops are
stored in the job store just before the files are replaced; a crash between the two is not replayed.
"""

from contextlib import nullcontext
from pathlib import Path

from kb_intake import extract, textlayer, workflow
from kb_intake.config import Config
from kb_intake.jobs import Jobs, WrongState, now
from kb_intake.package import read_json, read_jsonl, write_json, write_jsonl
from kb_intake.vocab import REGION_TYPES

ACTIONS = {"reclassify": {"action", "region_id", "to", "note"}, "exclude": {"action", "region_id", "note"},
           "ack": {"action", "item_id", "verdict", "note"}}
REGION_ACTIONS = ("reclassify", "exclude")
VERDICTS = ("ok", "wrong")
POST_EXTRACT = ("extracted", "in_review", "confirmed", "delivered")


class ReviewError(ValueError):
    pass


def current(job_dir: Path) -> dict:
    """The current extract-stage state of a job: regions, tables, extract gaps, review gaps, the absorbed map
    {absorbed region_id: region_id of the table region that absorbed it}, and the gap resolutions."""
    r = job_dir / "review"
    return {"regions": read_jsonl(job_dir / "extract" / "regions.jsonl"),
            "tables": read_jsonl(job_dir / "extract" / "tables.jsonl"),
            "extract_gaps": read_jsonl(job_dir / "extract" / "gaps.jsonl"),
            "review_gaps": read_jsonl(r / "gaps.jsonl") if (r / "gaps.jsonl").is_file() else [],
            "absorbed": read_json(r / "absorbed.json") if (r / "absorbed.json").is_file() else {},
            "resolved": read_jsonl(r / "resolved.jsonl") if (r / "resolved.jsonl").is_file() else []}


def _gap_id(g: dict) -> tuple:
    return (g["code"], g["page_no"], g.get("region_id"))


def all_gaps(job_dir: Path, state: dict | None = None) -> list[dict]:
    """Label + extract + review gaps, with `resolved` set on those review dealt with, in delivery order."""
    state = state or current(job_dir)
    resolved = {(x["code"], x["page_no"], x["region_id"]): x["resolved"] for x in state["resolved"]}
    gaps = read_jsonl(job_dir / "label" / "gaps.jsonl") + state["extract_gaps"] + state["review_gaps"]
    out = [{**g, "resolved": resolved[_gap_id(g)]} if _gap_id(g) in resolved else g for g in gaps]
    return sorted(out, key=extract.gap_key)


# ---------- validation ----------

def _validate(ops, by, regions: dict[str, dict], items: dict[str, dict]) -> None:
    if not isinstance(by, str) or not by.strip():
        raise ReviewError("review needs a name (by)")
    if not isinstance(ops, list) or not ops:
        raise ReviewError("review needs a non-empty list of ops")
    seen, acked = set(), {}
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
        if action == "ack":
            iid = op.get("item_id")
            if not isinstance(iid, str) or iid not in items:
                raise ReviewError(f"{where}: unknown queue item {iid!r}")
            if iid in acked:
                raise ReviewError(f"{where}: item {iid} appears more than once in this call")
            if items[iid]["status"] == "resolved":
                raise ReviewError(f"{where}: item {iid} is already resolved")
            if op.get("verdict", "ok") not in VERDICTS:
                raise ReviewError(f"{where}: verdict must be one of {list(VERDICTS)}")
            acked[iid] = where
            continue
        rid = op.get("region_id")
        if not isinstance(rid, str) or rid not in regions:
            raise ReviewError(f"{where}: unknown region {rid!r}")
        if rid in seen:
            raise ReviewError(f"{where}: region {rid} appears more than once in this call")
        seen.add(rid)
        region = regions[rid]
        if region["status"] == "excluded":
            raise ReviewError(f"{where}: region {rid} is excluded")
        if region["status"] == "absorbed":
            raise ReviewError(f"{where}: region {rid} is part of table {region['absorbed_into']}; review that table's "
                              "region instead")
        if action == "reclassify":
            to = op.get("to")
            if to not in REGION_TYPES:
                raise ReviewError(f"{where}: unknown region type {to!r} (known: {list(REGION_TYPES)})")
            if to == region["type"]:
                raise ReviewError(f"{where}: region {rid} already has type {to}")
    for iid, where in acked.items():         # an ack answers the item as it is; a change in the same call moves it
        if items[iid]["region_id"] in seen:
            raise ReviewError(f"{where}: region {items[iid]['region_id']} of item {iid} is also changed in this call; "
                              "ack it in a call of its own")


# ---------- apply ----------

def apply(jobs: Jobs, job_id: str, ops: list[dict], by: str, cfg: Config) -> list[dict]:
    """Validate then apply the ops on a job in review; return the recorded op rows. Raises ReviewError (nothing
    applied), jobs.WrongState / jobs.Busy, KeyError for an unknown job."""
    job = jobs.get(job_id)
    if job.status != "in_review":
        raise WrongState(f"job {job_id} is {job.status}; review needs in_review")
    from kb_intake import queue                   # queue reads review's state
    _validate(ops, by, {r["region_id"]: r for r in current(job.dir)["regions"]},     # bad input is not a failed step
              {i["item_id"]: i for i in queue.items(jobs, job_id)})
    # The job store's step lock: one review, extract or delivery of a job at a time, across processes.
    with jobs.running(job_id, "review") as job:
        state = current(job.dir)
        regions = {r["region_id"]: r for r in state["regions"]}
        items = {i["item_id"]: i for i in queue.items(jobs, job_id)}
        _validate(ops, by, regions, items)
        touches_regions = any(op["action"] in REGION_ACTIONS for op in ops)
        (job.dir / "review").mkdir(exist_ok=True)
        label = {r["region_id"]: r for r in read_jsonl(job.dir / "label" / "regions.jsonl")}
        label_gaps = read_jsonl(job.dir / "label" / "gaps.jsonl")
        absorbed: dict[str, str] = dict(state["absorbed"])
        resolved = list(state["resolved"])
        seq0 = len(jobs.ops(job_id))

        new_regions, tables = dict(regions), {t["region_id"]: t for t in state["tables"]}
        changed, review_gaps, records = set(), [], []
        with extract.Context.load(jobs, job_id) if touches_regions else nullcontext() as ctx:
            wf = extract.load_workflow(jobs, job) if touches_regions else None

            def redo(src: dict) -> None:
                rid = src["region_id"]
                row, table, gaps = extract.extract_region(src, workflow.handler_for(wf, src["type"]), ctx)
                new_regions[rid] = {**row, "absorbed_into": None}
                tables.pop(rid, None)
                if table is not None:
                    tables[rid] = table
                review_gaps.extend(gaps)
                changed.add(rid)

            for i, op in enumerate(ops):
                seq = seq0 + i + 1
                if op["action"] == "ack":
                    item = items[op["item_id"]]
                    if item["kind"] == "gap":
                        resolved.append({"code": item["code"], "page_no": item["page_no"],
                                         "region_id": item["region_id"], "resolved": {"action": "ack", "review_seq": seq}})
                    records.append({"action": "ack", "region_id": item["region_id"], "from": None, "to": None, "by": by,
                                    "at": now(), "note": op.get("note"), "target": item["target"],
                                    "verdict": op.get("verdict", "ok")})
                    continue
                rid = op["region_id"]
                old = regions[rid]
                changed.add(rid)
                tables.pop(rid, None)
                # undo what this region's table absorbed before: those regions are extracted again as they were
                for a in sorted(x for x, owner in absorbed.items() if owner == rid):
                    del absorbed[a]
                    resolved = [x for x in resolved if not (x["region_id"] == a and x.get("absorbed"))]
                    prev = new_regions[a]
                    redo({**label[a], "type": prev["type"], "subtype": prev["subtype"],
                          "label_source": prev["label_source"], "score": prev["score"]})
                mine = [g for g in label_gaps if g.get("region_id") == rid]
                if op["action"] == "reclassify":
                    if label[rid]["type"] != "table":       # reuse the Docling grid only for a label-stage table
                        ctx.matched.pop(rid, None)
                    box = label[rid]["bbox"]
                    if op["to"] == "table":                 # a suspected table: take the find_tables box in too
                        for g in mine:
                            if g["code"] == "table_suspected" and "bbox" in g:
                                box = union(box, g["bbox"])
                    src = {**label[rid], "bbox": box, "type": op["to"], "subtype": None, "label_source": "user",
                           "score": None, "layer": _layer(label[rid], op["to"])}
                    redo(src)
                    if op["to"] == "table" and rid in tables:
                        for other in _covered(new_regions.values(), rid, box):
                            absorbed[other["region_id"]] = rid
                            changed.add(other["region_id"])
                            new_regions[other["region_id"]] = {**other, "status": "absorbed", "text": None,
                                                               "text_source": None, "handler": None, "table_id": None}
                            tables.pop(other["region_id"], None)
                            resolved += [_resolution(g, "reclassify", seq, absorbed=True) for g in label_gaps
                                         if g.get("region_id") == other["region_id"]]
                    codes = ("low_confidence", "table_suspected") if op["to"] == "table" else ("low_confidence",)
                    resolved += [_resolution(g, "reclassify", seq) for g in mine if g["code"] in codes]
                    frm, to = old["type"], op["to"]
                else:
                    new_regions[rid] = {**old, "status": "excluded", "text": None, "text_source": None,
                                        "handler": None, "table_id": None, "absorbed_into": None}
                    resolved += [_resolution(g, "exclude", seq) for g in mine]
                    frm, to = old["status"], "excluded"
                records.append({"action": op["action"], "region_id": rid, "from": frm, "to": to, "by": by,
                                "at": now(), "note": op.get("note")})

        rows, out_tables = extract.finish_tables([new_regions[r["region_id"]] for r in state["regions"]],
                                                 list(tables.values()))
        rows = with_absorbed_into(rows, absorbed)
        extract_gaps = [g for g in state["extract_gaps"] if g.get("region_id") not in changed]
        review_gaps = [g for g in state["review_gaps"] if g.get("region_id") not in changed] + review_gaps
        extract_gaps = sorted(extract.tag_table_gaps(extract_gaps, out_tables), key=extract.gap_key)
        review_gaps = sorted(extract.tag_table_gaps(review_gaps, out_tables), key=extract.gap_key)
        resolved = _dedup(resolved)

        # The point of no return: refuses if the job left in_review. Known window: the writes below are each
        # atomic but not together; a crash among them leaves ops recorded whose effects are not (all) in the work
        # files. Nothing replays them, but delivery refuses a package whose regions do not match the review record
        # (deliver.check_review).
        jobs.add_ops(job_id, records, op="review")
        if touches_regions:                       # ack ops change no region, table or gap file
            write_jsonl(job.dir / "extract" / "regions.jsonl", rows)
            write_jsonl(job.dir / "extract" / "tables.jsonl", out_tables)
            write_jsonl(job.dir / "extract" / "gaps.jsonl", extract_gaps)
            write_jsonl(job.dir / "review" / "gaps.jsonl", review_gaps)
            write_json(job.dir / "review" / "absorbed.json", dict(sorted(absorbed.items())))
        write_jsonl(job.dir / "review" / "resolved.jsonl", resolved)
    jobs.event(job_id, "review", "applied", {"ops": len(records), "by": by})
    return records


FURNITURE_TYPES = ("page_header", "page_footer")


def _layer(label_region: dict, new_type: str) -> str:
    """Page headers and footers are furniture, as in the label stage; a region taken out of them is body; any other
    region keeps its label-stage layer."""
    if new_type in FURNITURE_TYPES:
        return "furniture"
    return "body" if label_region["type"] in FURNITURE_TYPES else label_region["layer"]


def union(a, b) -> list[float]:
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]


def _covered(regions, rid: str, box) -> list[dict]:
    """The other regions of the table's page that a table in `box` takes in: not tables, not already excluded or
    absorbed, with their centre in the box."""
    page = next(r["page_no"] for r in regions if r["region_id"] == rid)
    return [r for r in regions if r["page_no"] == page and r["region_id"] != rid and r["type"] != "table"
            and r["status"] not in ("excluded", "absorbed") and textlayer.centre_in(r["bbox"], box)]


def with_absorbed_into(rows: list[dict], absorbed: dict[str, str]) -> list[dict]:
    """Region rows with `absorbed_into` = the current table_id of the region that absorbed them (table ids are
    renumbered by finish_tables after every change)."""
    tid = {r["region_id"]: r["table_id"] for r in rows}
    return [{**r, "absorbed_into": tid.get(absorbed[r["region_id"]]) if r["region_id"] in absorbed else None}
            for r in rows]


def _resolution(g: dict, action: str, seq: int, absorbed: bool = False) -> dict:
    out = {"code": g["code"], "page_no": g["page_no"], "region_id": g.get("region_id"),
           "resolved": {"action": action, "review_seq": seq}}
    return {**out, "absorbed": True} if absorbed else out


def _dedup(resolved: list[dict]) -> list[dict]:
    """One resolution per gap, the latest op winning, in a stable order."""
    last = {}
    for x in resolved:
        last[(x["code"], x["page_no"], x["region_id"])] = x
    return sorted(last.values(), key=lambda x: (x["page_no"] or 0, x["code"], x["region_id"] or ""))


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
    if job.status in POST_EXTRACT:
        state = current(d)       # each file is replaced atomically; a view taken mid-review may mix old and new
        regions, tables, gaps = state["regions"], state["tables"], all_gaps(d, state)
    else:
        regions, tables, gaps = read_jsonl(d / "label" / "regions.jsonl"), [], read_jsonl(d / "label" / "gaps.jsonl")
    return {"page_no": page_no, "width": page["width"], "height": page["height"], "page_label": page["page_label"],
            "kind": page["kind"], "excluded": page_no in _excluded_pages(jobs, job),
            "regions": [r for r in regions if r["page_no"] == page_no],
            "tables": [t for t in tables if t["page_no"] == page_no],
            "gaps": sorted((g for g in gaps if g["page_no"] == page_no), key=extract.gap_key)}
