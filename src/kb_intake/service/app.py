"""The HTTP API of intake-design-v2 §6 and the static web UI (same process, no build step).

    POST /v1/jobs                       multipart `file` + `declaration` (YAML text), or JSON {"path", "declaration"}
                                        for a file under scan_roots -> 202, starts label
    GET  /v1/jobs                       jobs, newest first
    GET  /v1/jobs/{id}                  the job, its label stats, extract run summary and recent events
    GET  /v1/jobs/{id}/events?after=n   progress events after seq n
    GET  /v1/jobs/{id}/gaps             label, extract and review gaps of the work directory (each with `stage`)
    GET  /v1/jobs/{id}/workflow         the latest (or ?version=) workflow: YAML text and parsed
    PUT  /v1/jobs/{id}/workflow         YAML text -> a new version (422 when invalid)
    POST /v1/jobs/{id}/approve          {"by", "version"?} -> 202, starts extract
    GET  /v1/jobs/{id}/pages/{n}        page review data (kb_intake.review.page_view)
    GET  /v1/jobs/{id}/pages/{n}/image  the page rendered from the job's source.pdf (?dpi=, PNG)
    POST /v1/jobs/{id}/review           {"by", "ops"} (400 when an op is refused; nothing is applied then)
    POST /v1/jobs/{id}/confirm          {"by"} -> 202, starts delivery
    POST /v1/jobs/{id}/retry            re-run the step that failed in the job's current state
    GET  /v1/packages                   the delivery folder, with a contract check of each package
    GET  /v1/engines                    Docling version, models and readiness of both stages
    GET  /v1/directories?path=          list a directory inside scan_roots (403 outside or without roots)
    GET  /v1/health                     alive, versions, the step that runs now

Steps run in one background thread (one step at a time across all jobs; 409 otherwise). A job busy with a step,
here or in a CLI process sharing the work directory, or in the wrong state for a request, is 409 too. The step
modules write the progress events; the server writes none. `jobs.recover()` runs when the app is created.

Directories on this machine are only reachable inside `scan_roots` (empty by default: uploads only). Paths are
resolved (symlinks, ..) before the check.
"""

import importlib
import shutil
import uuid
from dataclasses import asdict
from pathlib import Path

import pymupdf
import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import UploadFile

from kb_intake import CONTRACT_VERSION, __version__, config, contract, declaration, detect, vocab
from kb_intake import workflow as workflow_mod
from kb_intake.jobs import Busy, JobError, Jobs, WrongState
from kb_intake.package import read_json, read_jsonl, sha_file
from kb_intake.service.runner_thread import StepRunner

STATIC = Path(__file__).with_name("static")
STAGES = ("label", "extract")
GAP_FILES = (("label", "label/gaps.jsonl"), ("extract", "extract/gaps.jsonl"), ("review", "review/gaps.jsonl"))
# the step that a job resting in this state runs again on /retry
RETRY = {"uploaded": "label", "approved": "extract", "confirmed": "deliver"}


def _step(name: str):
    """A step module, looked up when used (so review/deliver load lazily and tests can replace them)."""
    return importlib.import_module(f"kb_intake.{name}")


class _NotStarted(Exception):
    """prepare() found nothing to run (an upload of a source that already has a job past `uploaded`)."""


def create_app(cfg: config.Config | None = None, engines: dict | None = None) -> FastAPI:
    """The app on the configuration's work and delivery directories. `engines` ({"label": ..., "extract": ...})
    replaces the Docling engines (tests)."""
    cfg = cfg or config.load()
    engines = engines or {}
    jobs = Jobs(cfg.work_dir)
    jobs.recover()
    cfg.delivery_dir.mkdir(parents=True, exist_ok=True)
    runner = StepRunner()
    app = FastAPI(title="kb-intake", version=__version__)
    app.state.jobs, app.state.runner, app.state.cfg = jobs, runner, cfg

    @app.exception_handler(JobError)
    async def job_error(request: Request, e: JobError):
        status = 409 if isinstance(e, (Busy, WrongState)) else 422
        return JSONResponse({"detail": str(e), "error": type(e).__name__}, status_code=status)

    # ---------- helpers ----------

    def get_job(job_id: str):
        try:
            return jobs.get(job_id)
        except KeyError:
            raise HTTPException(404, f"no job {job_id}") from None

    async def body_of(request: Request) -> dict:
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(422, "the body must be JSON") from None
        if not isinstance(body, dict):
            raise HTTPException(422, "the body must be a JSON object")
        return body

    def need_name(body: dict, what: str) -> str:
        by = body.get("by")
        if not isinstance(by, str) or not by.strip():
            raise HTTPException(422, f"{what} needs a name (by)")
        return by.strip()

    def idle(job, *states: str) -> None:
        if job.running_op:
            raise Busy(f"job {job.id} is running {job.running_op}")
        if states and job.status not in states:
            raise WrongState(f"job {job.id} is {job.status}; this needs {' or '.join(states)}")

    def allowed(path: str) -> Path:
        """The resolved path if it lies inside a scan root; 403 otherwise (also when no root is configured)."""
        if not cfg.scan_roots:
            raise HTTPException(403, "no scan_roots configured: directories on this machine are not available, "
                                     "upload files instead")
        p = Path(path).expanduser()
        if not p.is_absolute():
            raise HTTPException(422, "give an absolute path")
        if not config.inside(p, cfg.scan_roots):
            raise HTTPException(403, f"{path} is not inside the scan_roots")
        return p.resolve()

    def need_page(job, n: int) -> None:
        with pymupdf.open(job.source) as pdf:
            count = pdf.page_count
        if not 1 <= n <= count:
            raise HTTPException(404, f"job {job.id} has no page {n}")

    def run_label(job_id: str):
        return lambda: _step("label").run(jobs, job_id, cfg, engine=engines.get("label"))

    def run_extract(job_id: str):
        return lambda: _step("extract").run(jobs, job_id, cfg, engine=engines.get("extract"))

    def run_deliver(job_id: str):
        return lambda: _step("deliver").deliver(jobs, job_id, cfg)

    # ---------- jobs ----------

    def need_pdf(src: Path) -> None:
        with src.open("rb") as f:
            if detect.sniff(f.read(8)) != "pdf":
                raise HTTPException(422, f"{src.name} is not a PDF")

    def declared(text, src: Path) -> dict:
        """The validated declaration of `src` (plain or directory form; doc_id defaults to the file name's slug)."""
        if not isinstance(text, str) or not text.strip():
            raise HTTPException(422, "a declaration (YAML text) is required")
        try:
            raw = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise HTTPException(422, f"declaration is not YAML: {e}") from None
        try:
            return declaration.validate(declaration.for_file(raw, src, src.parent, sha_file(src)))
        except declaration.DeclarationError as e:
            raise HTTPException(422, {"param": e.param, "message": str(e), "allowed": e.allowed}) from None

    @app.post("/v1/jobs", status_code=202)
    async def create_job(request: Request):
        tmp = None
        try:
            if request.headers.get("content-type", "").startswith("application/json"):
                body = await body_of(request)
                src = allowed(str(body.get("path") or ""))
                if not src.is_file():
                    raise HTTPException(422, f"{body.get('path')} is not a file")
                text = body.get("declaration")
            else:
                form = await request.form()
                up = form.get("file")
                if not isinstance(up, UploadFile):
                    raise HTTPException(422, "upload a file (multipart field `file`), or post {\"path\": ...} as JSON")
                tmp = cfg.work_dir / "uploads" / uuid.uuid4().hex
                tmp.mkdir(parents=True)
                src = tmp / (Path(up.filename or "").name or "upload.pdf")
                with src.open("wb") as out:
                    shutil.copyfileobj(up.file, out)
                text = form.get("declaration")
            need_pdf(src)
            decl = declared(text, src)
            result = {}

            def prepare():
                job, created = jobs.create(src, decl)
                result.update(job_id=job.id, created=created, status=job.status)
                if job.status != "uploaded" or job.running_op:
                    raise _NotStarted
                return job.id, run_label(job.id)
            try:
                runner.start("label", prepare)
                started = True
            except _NotStarted:
                started = False
            return {**result, "started": started}
        finally:
            if tmp is not None:
                shutil.rmtree(tmp, ignore_errors=True)

    def summary(j) -> dict:
        return {"job_id": j.id, "status": j.status, "running_op": j.running_op, "doc_id": j.doc_id,
                "source_name": j.source_name, "created_at": j.created_at, "updated_at": j.updated_at,
                "last_error": j.last_error, "package": j.package}

    @app.get("/v1/jobs")
    def list_jobs():
        return {"jobs": [summary(j) for j in jobs.recent()]}

    @app.get("/v1/jobs/{job_id}")
    def job_detail(job_id: str):
        job = get_job(job_id)
        stats = job.dir / "label" / "stats.json"
        run = job.dir / "extract" / "run.json"
        return {"job": job.to_dict(), "stats": read_json(stats) if stats.is_file() else None,
                "extract": read_json(run) if run.is_file() else None, "events": jobs.events(job_id)[-50:],
                "busy": (runner.busy() or {}).get("job_id") == job_id}

    @app.get("/v1/jobs/{job_id}/events")
    def job_events(job_id: str, after: int = 0):
        get_job(job_id)
        return {"events": jobs.events(job_id, after)}

    @app.get("/v1/jobs/{job_id}/gaps")
    def job_gaps(job_id: str):
        job = get_job(job_id)
        out = []
        for stage, rel in GAP_FILES:
            p = job.dir / rel
            if p.is_file():
                out += [{**g, "stage": stage} for g in read_jsonl(p)]
        return {"gaps": out}

    # ---------- workflow and approval ----------

    @app.get("/v1/jobs/{job_id}/workflow")
    def get_workflow(job_id: str, version: int | None = None):
        job = get_job(job_id)
        wf = jobs.workflow(job_id, version)
        if wf is None:
            raise HTTPException(404, f"job {job_id} has no workflow {version or ''}".strip())
        try:
            parsed, error = workflow_mod.parse(wf["text"]), None
        except workflow_mod.WorkflowError as e:
            parsed, error = None, str(e)
        stats = job.dir / "label" / "stats.json"
        notes = (read_json(stats).get("workflow_notes") if stats.is_file() else None) or []
        return {**wf, "workflow": parsed, "error": error, "approved_version": job.workflow_version,
                "types": list(vocab.REGION_TYPES), "handlers": list(vocab.HANDLERS), "notes": notes}

    @app.put("/v1/jobs/{job_id}/workflow")
    async def put_workflow(job_id: str, request: Request):
        get_job(job_id)
        text = (await request.body()).decode("utf-8", errors="replace")
        try:
            wf = workflow_mod.parse(text)
        except workflow_mod.WorkflowError as e:
            raise HTTPException(422, str(e)) from None
        return {"version": jobs.save_workflow(job_id, workflow_mod.dump(wf))}

    @app.post("/v1/jobs/{job_id}/approve", status_code=202)
    async def approve(job_id: str, request: Request):
        get_job(job_id)
        body = await body_of(request)
        by = need_name(body, "approval")
        version = body.get("version")

        def prepare():
            jobs.approve(job_id, by, version)
            return job_id, run_extract(job_id)
        runner.start("extract", prepare)
        return {"job_id": job_id, "started": "extract"}

    # ---------- review ----------

    @app.get("/v1/jobs/{job_id}/pages/{n}")
    def page(job_id: str, n: int):
        job = get_job(job_id)
        need_page(job, n)
        try:
            return _step("review").page_view(jobs, job_id, n)
        except FileNotFoundError:
            raise HTTPException(409, f"job {job_id} is not labelled yet") from None

    @app.get("/v1/jobs/{job_id}/pages/{n}/image")
    def page_image(job_id: str, n: int, dpi: int = 110):
        job = get_job(job_id)
        need_page(job, n)
        dpi = max(24, min(300, dpi))
        out = job.dir / "images" / f"p{n:04d}-{dpi}.png"
        if not out.is_file():
            out.parent.mkdir(parents=True, exist_ok=True)
            with pymupdf.open(job.source) as pdf:
                data = pdf[n - 1].get_pixmap(dpi=dpi).tobytes("png")
            tmp = out.with_name(f".{out.name}.{uuid.uuid4().hex}")
            tmp.write_bytes(data)
            tmp.replace(out)
        return FileResponse(out, media_type="image/png")

    @app.post("/v1/jobs/{job_id}/review")
    async def review_ops(job_id: str, request: Request):
        get_job(job_id)
        body = await body_of(request)
        by = need_name(body, "a review")
        ops = body.get("ops")
        if not isinstance(ops, list) or not all(isinstance(o, dict) for o in ops):
            raise HTTPException(422, "ops must be a list of objects")
        review = _step("review")
        try:
            applied = review.apply(jobs, job_id, ops, by, cfg)
        except review.ReviewError as e:
            raise HTTPException(400, str(e)) from None
        return {"applied": applied, "status": jobs.get(job_id).status}

    @app.post("/v1/jobs/{job_id}/confirm", status_code=202)
    async def confirm(job_id: str, request: Request):
        get_job(job_id)
        by = need_name(await body_of(request), "confirmation")

        def prepare():
            job = jobs.get(job_id)
            idle(job, "in_review", "confirmed")
            if job.status == "in_review":
                jobs.confirm(job_id, by)            # recorded now: a failed delivery is retried, not re-confirmed
            return job_id, lambda: _step("deliver").confirm(jobs, job_id, by, cfg)
        runner.start("deliver", prepare)
        return {"job_id": job_id, "started": "deliver"}

    @app.post("/v1/jobs/{job_id}/retry", status_code=202)
    def retry(job_id: str):
        get_job(job_id)
        works = {"label": run_label, "extract": run_extract, "deliver": run_deliver}
        chosen = {}

        def prepare():
            job = jobs.get(job_id)
            idle(job)
            step = RETRY.get(job.status)
            if step is None:
                raise WrongState(f"job {job_id} is {job.status}: there is no step to retry")
            chosen["step"] = step
            return job_id, works[step](job_id)
        runner.start("retry", prepare)
        return {"job_id": job_id, "started": chosen["step"]}

    # ---------- packages, engines, directories, health ----------

    @app.get("/v1/packages")
    def packages():
        by_package = {j.package: j.id for j in jobs.recent(10000) if j.package}
        out = []
        for p in sorted(cfg.delivery_dir.iterdir()) if cfg.delivery_dir.is_dir() else []:
            if p.name.startswith(".") or not p.is_dir():
                continue
            out.append({"package": p.name, "job_id": by_package.get(p.name), "check": contract.check(p)})
        return {"delivery_dir": str(cfg.delivery_dir), "packages": out}

    @app.get("/v1/engines")
    def engines_info():
        from kb_intake.engines import DoclingEngine
        infos = {s: (engines.get(s) or DoclingEngine(cfg.models_dir, cfg.docling, stage=s)).info() for s in STAGES}
        return {"ready": all(i.ready for i in infos.values()), "stages": {s: asdict(i) for s, i in infos.items()},
                "models_dir": str(cfg.models_dir), "pymupdf": pymupdf.VersionBind,
                "layout_enabled": cfg.layout_enabled}

    @app.get("/v1/directories")
    def directories(path: str | None = None):
        roots = cfg.scan_roots
        if path is None:
            if not roots:
                raise HTTPException(403, "no scan_roots configured")
            return {"roots": [str(r) for r in roots]}
        p = allowed(path)
        if not p.is_dir():
            raise HTTPException(422, f"{path} is not a directory")
        entries = []
        for e in sorted(p.iterdir()):
            if e.name.startswith(".") or not config.inside(e, roots):
                continue
            if e.is_dir():
                entries.append({"name": e.name, "path": str(e.resolve()), "kind": "directory"})
            elif e.is_file():
                with e.open("rb") as f:
                    if detect.sniff(f.read(8)):
                        entries.append({"name": e.name, "path": str(e.resolve()), "kind": "pdf"})
        return {"path": str(p), "roots": [str(r) for r in roots], "entries": entries}

    @app.get("/v1/health")
    def health():
        return {"ok": True, "version": __version__, "contract_version": CONTRACT_VERSION, "busy": runner.busy(),
                "last": runner.last}

    @app.get("/")
    def root():
        return RedirectResponse("/ui/")

    app.mount("/ui", StaticFiles(directory=STATIC, html=True), name="ui")
    return app
