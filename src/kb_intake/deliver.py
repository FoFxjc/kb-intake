"""Steps 8-9, confirm and deliver (intake-design-v2 §2, §5): the confirmed job's package is written to the delivery
folder, atomically.

    confirm(jobs, job_id, by, cfg)   in_review -> confirmed, then deliver; on a confirmed job only delivers (retry)
    deliver(jobs, job_id, cfg)       confirmed -> delivered    (under jobs.running(job_id, "deliver"))

Delivery builds `delivery_dir/.tmp-<name>-<uuid>/<name>/`, checks it against contract v2 (contract.check must be ok),
renames it to `delivery_dir/<name>` and removes the tmp directory, so a package under its final name is always
complete. A package already under that name is refused, unless it is this job's own package renamed into place by
a delivery that stopped before recording it (manifest.run.job_id, and the package checks ok): then the retry only
records it. Stale `.tmp-<name>-*` directories (a crash) are removed first.

Package files (§5): source.pdf, manifest.json, pages.jsonl (label pages + `excluded` from the workflow),
regions.jsonl and tables.jsonl (the current extract-stage files, after review), layout.jsonl, gaps.jsonl (label +
extract + review gaps, sorted by extract.gap_key), workflow.json, review.jsonl (the review ops + a final confirm
row), raw/docling.json and raw/docling.extract.json when the work directory has them.

Determinism (§5): the same source, workflow and review ops give byte-identical packages except manifest.run, the
`at` of review.jsonl, the approval time (workflow.json, manifest.workflow), the confirmation time
(manifest.status) and the manifest.files entries of the two files holding those times. Whether Docling's output
came from the cache therefore goes into manifest.run, not manifest.engines.
"""

import platform
import shutil
import time
import uuid
from collections import Counter
from pathlib import Path

from kb_intake import __version__, contract, extract, llm, review, workflow
from kb_intake.config import Config
from kb_intake.jobs import Job, Jobs, WrongState, now
from kb_intake.package import name as package_name
from kb_intake.package import read_json, read_jsonl, sha_file, write_json, write_jsonl



class DeliveryError(RuntimeError):
    pass


def confirm(jobs: Jobs, job_id: str, by: str, cfg: Config) -> Path:
    """Confirm a job in review and deliver it; a job already confirmed (a failed delivery) is only delivered."""
    job = jobs.get(job_id)
    if job.status == "in_review":
        jobs.confirm(job_id, by)                 # Busy while a review step of the job is running
    elif job.status != "confirmed":
        raise WrongState(f"job {job_id} is {job.status}; confirm needs in_review (or confirmed, to retry delivery)")
    return deliver(jobs, job_id, cfg)


def deliver(jobs: Jobs, job_id: str, cfg: Config) -> Path:
    """Write the package of a confirmed job to the delivery folder and mark the job delivered; return its path."""
    t0 = time.monotonic()
    with jobs.running(job_id, "deliver") as job:
        jobs.event(job_id, "deliver", "started")
        name = package_name(job.doc_id, job.sha256)
        try:
            final = _deliver(jobs, job, cfg, name, t0)
            jobs.advance(job_id, "delivered", op="deliver", package=name)
        except Exception as e:
            jobs.event(job_id, "deliver", "failed", {"error": f"{type(e).__name__}: {e}"[:300]})
            raise
    jobs.event(job_id, "deliver", "done", {"package": name, "seconds": round(time.monotonic() - t0, 3)})
    return final


def _deliver(jobs: Jobs, job: Job, cfg: Config, name: str, t0: float) -> Path:
    root = cfg.delivery_dir
    root.mkdir(parents=True, exist_ok=True)
    final = root / name
    for stale in root.glob(f".tmp-{name}-*"):         # left by a delivery killed before its cleanup
        shutil.rmtree(stale, ignore_errors=True)
    if final.exists() or final.is_symlink():
        if _own_package(final, job):
            return final
        raise DeliveryError(f"{final} already exists; a delivered package is never replaced")
    tmp = root / f".tmp-{name}-{uuid.uuid4().hex}"
    try:
        pkg = tmp / name
        pkg.mkdir(parents=True)
        _build(jobs, job, pkg, t0, cfg)          # confirmed: no review can change the files any more
        result = contract.check(pkg)
        if not result["ok"]:
            raise DeliveryError("the package fails the contract v2 check: " + "; ".join(result["errors"][:5]))
        pkg.rename(final)                         # fails if something appeared under the name meanwhile
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return final


def _own_package(final: Path, job: Job) -> bool:
    """Whether `final` is this job's package, renamed into place by a delivery that stopped before recording it."""
    try:
        run = read_json(final / "manifest.json")["run"]
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return isinstance(run, dict) and run.get("job_id") == job.id and contract.check(final)["ok"]


# ---------- the package ----------

def check_review(ops: list[dict], regions: list[dict], gaps: list[dict] | None = None) -> list[str]:
    """Where the package does not show what the review record says. The last reclassify or exclude on each region
    wins: a reclassified region must have the new type and label_source "user", an excluded one status "excluded".
    An ack of a gap needs that gap resolved, if the gap still exists (when `gaps` is given). Guards the known crash window of review.apply
    (ops recorded, work files not all replaced)."""
    by_id = {r["region_id"]: r for r in regions}
    last = {op["region_id"]: op for op in ops if op.get("region_id") and op["action"] in ("reclassify", "exclude")}
    errors = []
    for rid, op in sorted(last.items()):
        r = by_id.get(rid)
        if r is None:
            errors.append(f"review op on {rid}, which is not a region")
        elif op["action"] == "reclassify" and (r["type"] != op["to"] or r["label_source"] != "user"):
            errors.append(f"{rid} was reclassified to {op['to']}, but is {r['type']} ({r['label_source']})")
        elif op["action"] == "exclude" and r["status"] != "excluded":
            errors.append(f"{rid} was excluded, but its status is {r['status']}")
    for seq, op in enumerate(ops, 1):
        t = op.get("target") or {}
        if gaps is None or op["action"] != "ack" or t.get("kind") != "gap":
            continue
        same = [g for g in gaps if (g["code"], g["page_no"], g.get("region_id"), g.get("table_id")) ==
                (t["code"], t["page_no"], t["region_id"], t["table_id"])]
        if same and not any(g.get("resolved") for g in same):     # gone: a later reclassify removed it
            where = t["region_id"] or t["table_id"] or f"page {t['page_no']}"
            errors.append(f"ack {seq} of gap {t['code']} on {where}: the gap is not resolved")
    return errors


def _counts(rows, key: str) -> dict:
    return dict(sorted(Counter(r[key] for r in rows).items()))


def _versions(label_run: dict, extract_run: dict) -> dict:
    """The package versions the stages recorded when they ran (deps.stage_versions). The two stages normally ran
    with the same installation; if not (an upgrade between them), the extract stage's versions are reported
    separately."""
    lv, xv = label_run.get("packages") or {}, extract_run.get("packages") or {}
    return lv if lv == xv or not xv else {**lv, "extract_stage": xv}


def _build(jobs: Jobs, job: Job, pkg: Path, t0: float, cfg: Config) -> None:
    d = job.dir
    wf_row = jobs.workflow(job.id, job.workflow_version)
    wf = workflow.parse(wf_row["text"])
    excluded = set(wf["exclude_pages"])
    label_run, label_stats = read_json(d / "label" / "run.json"), read_json(d / "label" / "stats.json")
    extract_run = read_json(d / "extract" / "run.json")
    detection = label_run["detection"]

    shutil.copyfile(job.source, pkg / "source.pdf")
    pages = [{**p, "excluded": p["page_no"] in excluded} for p in read_jsonl(d / "label" / "pages.jsonl")]
    state = review.current(d)
    regions, tables, gaps = state["regions"], state["tables"], review.all_gaps(d, state)
    mismatch = check_review(jobs.ops(job.id), regions, gaps)
    if mismatch:
        raise DeliveryError("the work files do not match the review record (a review was interrupted?): "
                            + "; ".join(mismatch[:5]))
    ops = jobs.ops(job.id) + [{"action": "confirm", "region_id": None, "from": None, "to": None,
                               "by": job.confirmed_by, "at": job.confirmed_at, "note": None}]
    write_jsonl(pkg / "pages.jsonl", pages)
    write_jsonl(pkg / "regions.jsonl", regions)
    write_jsonl(pkg / "tables.jsonl", tables)
    shutil.copyfile(d / "label" / "layout.jsonl", pkg / "layout.jsonl")
    write_jsonl(pkg / "gaps.jsonl", gaps)
    write_jsonl(pkg / "review.jsonl", ops)
    write_json(pkg / "workflow.json", {"workflow": wf, "approved_by": job.approved_by,
                                       "approved_at": job.approved_at})
    llm_rows = llm.rows_of(d)
    if llm_rows:                                   # LLM outputs (low weight): only when there are any
        write_jsonl(pkg / "llm.jsonl", llm_rows)
    for src, dst in ((d / "label" / "docling.json", "raw/docling.json"),
                     (d / "extract" / "docling.extract.json", "raw/docling.extract.json")):
        if src.is_file():
            (pkg / "raw").mkdir(exist_ok=True)
            shutil.copyfile(src, pkg / dst)

    files = {}
    for p in sorted(pkg.rglob("*")):
        if p.is_file():
            rel = p.relative_to(pkg).as_posix()
            rows = sum(1 for line in p.open(encoding="utf-8") if line.strip()) if rel.endswith(".jsonl") else None
            files[rel] = {"sha256": sha_file(p), "bytes": p.stat().st_size, "rows": rows}

    llm_entry, llm_run = llm.manifest_entry(d, cfg)
    engines = label_run["engines"]
    le, ee = label_run["engine"], extract_run["engine"]
    kinds = detection.get("page_kinds") or _counts([p for p in pages if p["kind"] is not None], "kind")
    manifest = {
        "contract_version": "2",
        "package": pkg.name,
        "doc_id": job.doc_id,
        "source": {"filename": job.source_name, "sha256": job.sha256, "bytes": job.source.stat().st_size,
                   "media_type": "application/pdf"},
        "metadata": {"declaration": job.declaration, "declaration_filled": label_run.get("declaration_filled", []),
                     "page_count": detection["page_count"], "lang": job.declaration.get("lang"),
                     "detected_lang": detection.get("detected_lang"), "page_kinds": kinds,
                     "has_text_layer": detection["has_text_layer"],
                     "page_label_method": detection.get("page_label_method") or {}},
        "stats": {"labeled_by_type": label_stats["regions_by_type"], "regions_by_type": _counts(regions, "type"),
                  "regions_by_status": _counts(regions, "status"), "tables": len(tables),
                  "gaps_by_code": _counts(gaps, "code")},
        "engines": {"docling": {"version": engines["docling"], "packages": _versions(label_run, extract_run)},
                    "models": engines["models"], "pymupdf": engines["pymupdf"], "device": engines["device"],
                    "params": {"label": engines["params"]["label"], "extract": extract_run["params"]["extract"]},
                    "stages": {"label": {"status": le["status"], "cache_key": le["cache_key"],
                                         "failed_pages": le["failed_pages"]},
                               "extract": {"status": ee["status"], "cache_key": ee["cache_key"],
                                           "failed_pages": ee["failed_pages"]}}},
        "workflow": {"name": wf["name"], "version": wf["version"],
                     "approved_by": job.approved_by, "approved_at": job.approved_at},
        "status": {"state": "confirmed", "confirmed_by": job.confirmed_by, "confirmed_at": job.confirmed_at},
        "files": files,
        "llm": llm_entry,
        "kb_intake_version": __version__,
        "run": {"job_id": job.id, "label_seconds": label_run.get("seconds"),
                "extract_seconds": extract_run.get("seconds"), "deliver_seconds": round(time.monotonic() - t0, 3),
                "cache_hits": {"label": le["cached"], "extract": ee["cached"]},
                "engine_seconds": {"label": le["seconds"], "extract": ee["seconds"]},
                "python": platform.python_version(), "platform": platform.platform(), "delivered_at": now(),
                **({"llm": llm_run} if llm_run is not None else {})},
    }
    write_json(pkg / "manifest.json", manifest)
