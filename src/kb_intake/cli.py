"""Command line (intake-design-v2 §6), equivalent to the web UI so the steps can be scripted:

    kb-intake label   <file> --decl decl.yaml          upload + label + stats; prints the job id and the suggested
                                                       workflow
    kb-intake approve <job> [--workflow wf.yaml] --by NAME
                                                       approve the latest workflow, or save wf.yaml as a new version
                                                       and approve that
    kb-intake extract <job>                            extract by the approved workflow; the job moves to in_review
    kb-intake review  <job> --apply ops.yaml --by NAME reclassify / exclude regions (a list of ops, or {ops: [...]})
    kb-intake confirm <job> --by NAME                  confirm and write the package to the delivery folder
    kb-intake jobs | status <job>                      the jobs of the work directory; one job with stats and events
    kb-intake models download [--to DIR]               pinned weights into $INTAKE_MODELS_DIR (default ~/.kb-intake/models)
    kb-intake models verify [--dir DIR]                check a models directory against the shipped lock
    kb-intake engines                                  engine versions, models, dependency check (ready or not)
    kb-intake check <package> ...                      contract v2 check of packages (directories or zips)
    kb-intake serve [--host 127.0.0.1] [--port 8700]   web UI + HTTP API on the same work and delivery directories

Every command takes --config intake.yaml (default $INTAKE_CONFIG). stdout carries JSON only (one object per line);
errors are JSON on stderr, and so are progress lines of a running step. Exit codes: 0 ok, 1 failed, 2 bad input
(unknown job, wrong state, invalid declaration / workflow / review ops). Hugging Face is put offline for every
command except `models download`, so nothing but that command can reach the network.
"""

import argparse
import importlib
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path


class BadInput(Exception):
    """Exit 2: the input was wrong (an error JSON with these fields goes to stderr)."""

    def __init__(self, error: str, message: str, **extra):
        super().__init__(message)
        self.fields = {"error": error, "message": message, **extra}


def _err(**kw) -> None:
    print(json.dumps({"ok": False, **kw}, ensure_ascii=False, default=str), file=sys.stderr)


def _out(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, default=str), flush=True)


def _progress(step: str, status: str, info: dict) -> None:
    """Live display of a step's progress on stderr (the step itself writes the events to the job store)."""
    print(json.dumps({"step": step, "status": status, "info": info}, ensure_ascii=False, default=str),
          file=sys.stderr, flush=True)


def _config(args):
    from kb_intake import config
    try:
        return config.load(args.config)
    except config.ConfigError as e:
        raise BadInput("config", str(e)) from None


def _step(name: str):
    """A step module, imported when it is used (review and deliver are only needed by their commands)."""
    return importlib.import_module(f"kb_intake.{name}")


def _jobs(args):
    from kb_intake.jobs import Jobs
    cfg = _config(args)
    jobs = Jobs(cfg.work_dir)
    jobs.recover()          # steps left by a process that died (only those: a live owner is left alone)
    return cfg, jobs


def _get(jobs, job_id: str):
    try:
        return jobs.get(job_id)
    except KeyError:
        raise BadInput("not_found", f"no job {job_id}") from None


def _read_yaml(path: str, what: str):
    """(text, parsed) of a YAML file."""
    import yaml
    try:
        text = Path(path).expanduser().read_text(encoding="utf-8")
        return text, yaml.safe_load(text)
    except OSError as e:
        raise BadInput(what, f"cannot read {path}: {e}") from None
    except yaml.YAMLError as e:
        raise BadInput(what, f"{path} is not YAML: {e}") from None


def _stats(job) -> dict | None:
    from kb_intake.package import read_json
    p = job.dir / "label" / "stats.json"
    return read_json(p) if p.is_file() else None


EXISTING_NOTE = ("this file already has a job that is not delivered; it is reported as it is, and the declaration "
                 "given now was not used")


def _label_result(jobs, job, existing: bool = False) -> dict:
    stats = _stats(job)
    wf = jobs.workflow(job.id)
    out = {"job_id": job.id, "status": job.status, "existing_job": existing, "stats": stats,
           "workflow": wf["text"] if wf else None, "notes": (stats or {}).get("workflow_notes", [])}
    return {**out, "existing_note": EXISTING_NOTE} if existing else out


def _label(args) -> int:
    from kb_intake import declaration, detect
    from kb_intake.package import sha_file
    cfg, jobs = _jobs(args)
    src = Path(args.file).expanduser()
    if not src.is_file():
        raise BadInput("file", f"{src} is not a file")
    with src.open("rb") as f:
        if detect.sniff(f.read(8)) != "pdf":
            raise BadInput("file", f"{src} is not a PDF")
    _, raw = _read_yaml(args.decl, "declaration")
    try:
        decl = declaration.validate(declaration.for_file(raw, src.resolve(), src.resolve().parent, sha_file(src)))
    except declaration.DeclarationError as e:
        raise BadInput("declaration", str(e), param=e.param) from None
    job, created = jobs.create(src, decl, cfg.delivery_dir)
    if job.status == "uploaded":                 # a new job, or the retry of a label step that failed
        _step("label").run(jobs, job.id, cfg, progress=_progress)
        _step("llm").summarize(jobs, job.id, cfg)     # when the LLM is enabled; a failure is an llm_failed gap
        job = jobs.get(job.id)
    _out(_label_result(jobs, job, existing=not created))
    return 0


def _approve(args) -> int:
    from kb_intake import workflow
    _, jobs = _jobs(args)
    _get(jobs, args.job)
    version = None
    if args.workflow:
        text, _ = _read_yaml(args.workflow, "workflow")
        try:
            wf = workflow.parse(text)
        except workflow.WorkflowError as e:
            raise BadInput("workflow", str(e)) from None
        version = jobs.save_workflow(args.job, workflow.dump(wf))
    job = jobs.approve(args.job, args.by, version)
    _out({"job_id": job.id, "status": job.status, "workflow_version": job.workflow_version,
          "approved_by": job.approved_by, "approved_at": job.approved_at})
    return 0


def _extract(args) -> int:
    cfg, jobs = _jobs(args)
    _get(jobs, args.job)
    summary = _step("extract").run(jobs, args.job, cfg, progress=_progress)
    _out({"job_id": args.job, "status": jobs.get(args.job).status, "summary": summary})
    return 0


def _review(args) -> int:
    from kb_intake.jobs import Busy, WrongState
    cfg, jobs = _jobs(args)
    job = _get(jobs, args.job)
    if job.running_op:
        raise Busy(f"job {job.id} is running {job.running_op}")
    if job.status != "in_review":
        raise WrongState(f"job {job.id} is {job.status}; this needs in_review")
    _, raw = _read_yaml(args.apply, "ops")
    ops = raw.get("ops") if isinstance(raw, dict) else raw
    if not isinstance(ops, list) or not all(isinstance(o, dict) for o in ops):
        raise BadInput("ops", "ops must be a list of mappings (or {ops: [...]})")
    review = _step("review")
    try:
        applied = review.apply(jobs, args.job, ops, args.by, cfg)
    except review.ReviewError as e:
        raise BadInput("ReviewError", str(e)) from None
    _out({"job_id": args.job, "status": jobs.get(args.job).status, "applied": applied})
    return 0


def _confirm(args) -> int:
    from kb_intake import contract
    cfg, jobs = _jobs(args)
    _get(jobs, args.job)
    path = Path(_step("deliver").confirm(jobs, args.job, args.by, cfg))
    check = contract.check_path(path)
    _out({"package": path.name, "path": str(path), "check": check})
    return 0 if check["ok"] else 1


def _list(args) -> int:
    _, jobs = _jobs(args)
    for j in jobs.recent(args.limit):
        _out({"job_id": j.id, "status": j.status, "running_op": j.running_op, "doc_id": j.doc_id,
              "source_name": j.source_name, "updated_at": j.updated_at, "package": j.package,
              "last_error": j.last_error})
    return 0


def _status(args) -> int:
    _, jobs = _jobs(args)
    job = _get(jobs, args.job)
    _out({"job": job.to_dict(), "stats": _stats(job), "events": jobs.events(job.id)[-args.events:]})
    return 0


def _models(args) -> int:
    from kb_intake import models
    cfg = _config(args)
    target = Path(args.to or args.dir).expanduser() if (args.to or args.dir) else cfg.models_dir
    try:
        if args.action == "download":
            pins = models.download(target, log=lambda m: print(m, file=sys.stderr))
        else:
            pins = models.verify(target)
    except Exception as e:  # noqa: BLE001
        _err(error="models", message=f"{type(e).__name__}: {e}")
        return 1
    _out({"ok": True, "models_dir": str(target), "models": pins})
    return 0


def _engines(args) -> int:
    import pymupdf

    from kb_intake.engines import DoclingEngine
    cfg = _config(args)
    infos = {stage: DoclingEngine(models_dir=cfg.models_dir, params=cfg.docling, stage=stage).info()
             for stage in ("label", "extract")}
    ready = all(i.ready for i in infos.values())
    _out({"ok": ready, "docling": {k: asdict(v) for k, v in infos.items()}, "models_dir": str(cfg.models_dir),
          "pymupdf": pymupdf.VersionBind})
    return 0 if ready else 1


def _serve(args) -> int:
    import uvicorn

    from kb_intake.service.app import create_app
    cfg = _config(args)
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(json.dumps({"warning": f"listening on {args.host}: the UI and API have no authentication"}),
              file=sys.stderr, flush=True)
    _out({"ok": True, "url": f"http://{args.host}:{args.port}/", "work_dir": str(cfg.work_dir),
          "delivery_dir": str(cfg.delivery_dir), "scan_roots": [str(r) for r in cfg.scan_roots]})
    uvicorn.run(create_app(cfg, hosts=(args.host,)), host=args.host, port=args.port, log_level="warning")
    return 0


def _check(args) -> int:
    from kb_intake import contract
    worst = 0
    for p in args.package:
        result = contract.check_path(Path(p))
        _out({"package": p, **result})
        worst = max(worst, 0 if result["ok"] else 1)
    return worst


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="kb-intake", description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def cmd(name: str, fn, help_: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_)
        p.add_argument("--config", help="intake.yaml (default: $INTAKE_CONFIG)")
        p.set_defaults(fn=fn)
        return p

    p = cmd("label", _label, "upload a file, label its regions and suggest a workflow")
    p.add_argument("file")
    p.add_argument("--decl", required=True, help="declaration YAML (doc_id defaults to the file name's slug)")
    p = cmd("approve", _approve, "approve the job's workflow (optionally a new one from --workflow)")
    p.add_argument("job")
    p.add_argument("--workflow", help="workflow YAML to save as a new version and approve")
    p.add_argument("--by", required=True, help="who approves")
    p = cmd("extract", _extract, "extract an approved job")
    p.add_argument("job")
    p = cmd("review", _review, "apply review ops (reclassify / exclude) from a YAML file")
    p.add_argument("job")
    p.add_argument("--apply", required=True, help="YAML: a list of ops, or {ops: [...]}")
    p.add_argument("--by", required=True, help="who reviews")
    p = cmd("confirm", _confirm, "confirm the review and write the package to the delivery folder")
    p.add_argument("job")
    p.add_argument("--by", required=True, help="who confirms")
    p = cmd("jobs", _list, "the jobs of the work directory, newest first")
    p.add_argument("--limit", type=int, default=100)
    p = cmd("status", _status, "one job: its state, label stats and recent events")
    p.add_argument("job")
    p.add_argument("--events", type=int, default=20, help="how many recent events (default 20)")

    p = cmd("models", _models, "download or verify the pinned model weights")
    p.add_argument("action", choices=["download", "verify"])
    p.add_argument("--to", help="target directory for download (default: models_dir)")
    p.add_argument("--dir", help="directory to verify (default: models_dir)")
    cmd("engines", _engines, "engine versions, models and dependency check")
    p = cmd("serve", _serve, "web UI and HTTP API (work_dir and delivery_dir from the configuration)")
    p.add_argument("--host", default="127.0.0.1",
                   help="address to listen on (default 127.0.0.1; anything else must be given explicitly)")
    p.add_argument("--port", type=int, default=8700)
    p = sub.add_parser("check", help="check packages (directory or zip) against contract v2")
    p.add_argument("package", nargs="+")
    p.set_defaults(fn=_check)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.cmd != "models":
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    from kb_intake.jobs import Busy, JobError
    try:
        return args.fn(args)
    except BadInput as e:
        _err(**e.fields)
        return 2
    except Busy as e:                       # another process runs a step of this job: try again later
        _err(error="Busy", message=str(e))
        return 1
    except JobError as e:                   # wrong state, a missing name: the request was wrong
        _err(error=type(e).__name__, message=str(e))
        return 2
    except Exception as e:  # noqa: BLE001 - a failed step: the job keeps its state and last_error
        _err(error="failed", message=f"{type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
