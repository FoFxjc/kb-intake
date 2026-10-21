"""The HTTP API of intake-design-v2 §6 and intake-ui-llm-design-v1 §3, and the web UI (same process).
The API is frozen in contract/api/openapi-v1.json (kb_intake.service.openapi_snapshot).

    POST /v1/jobs                       multipart `file` + `declaration` (YAML text), or JSON {"path", "declaration"}
                                        for a file under scan_roots -> 202, starts label
    GET  /v1/jobs                       jobs, newest first
    GET  /v1/jobs/{id}                  the job, its label stats, extract run summary and recent events
    GET  /v1/jobs/{id}/events?after=n   progress events after seq n
    GET  /v1/jobs/{id}/gaps             label, extract and review gaps of the work directory (each with `stage`, and
                                        `resolved` when review dealt with it)
    GET  /v1/jobs/{id}/source.pdf       the source file, with HTTP Range
    GET  /v1/jobs/{id}/regions          regions in reading order: search (q), filters, cursor paging
    GET  /v1/jobs/{id}/facets           counts per type, status and gap code for the same filters
    GET  /v1/jobs/{id}/clusters         groups of similar regions (kb_intake.clusters)
    GET  /v1/jobs/{id}/queue            the review inbox (kb_intake.queue)
    POST /v1/jobs/{id}/queue/seen       {"item_ids"}: items shown to the reviewer (work state only)
    GET  /v1/jobs/{id}/summary          the document summary (LLM; status off when not enabled)
    GET  /v1/jobs/{id}/llm              LLM outputs and progress
    GET  /v1/jobs/{id}/workflow         the latest (or ?version=) workflow: YAML text and parsed
    PUT  /v1/jobs/{id}/workflow         YAML text -> a new version (422 when invalid)
    POST /v1/jobs/{id}/approve          {"by", "version"?} -> 202, starts extract
    GET  /v1/jobs/{id}/pages/{n}        page review data (kb_intake.review.page_view)
    GET  /v1/jobs/{id}/pages/{n}/image  the page rendered from the job's source.pdf (?dpi=, PNG)
    GET  /v1/jobs/{id}/review           the review operations so far
    POST /v1/jobs/{id}/review           {"by", "ops"} (400 when an op is refused; nothing is applied then); ops are
                                        reclassify, exclude and ack (a queue item looked at, nothing changed)
    POST /v1/jobs/{id}/confirm          {"by"} -> 202, starts delivery
    POST /v1/jobs/{id}/retry            re-run the step that failed in the job's current state
    GET  /v1/packages                   the delivery folder, with a contract check of each package
    GET  /v1/engines                    Docling version, models and readiness of both stages
    GET  /v1/directories?path=          list a directory inside scan_roots (403 outside or without roots)
    GET  /v1/health                     alive, versions, the step that runs now

Steps run in one background thread (one step at a time across all jobs; 409 otherwise). A job busy with a step,
here or in a CLI process sharing the work directory, or in the wrong state for a request, is 409 too. The step
modules write the progress events; the server writes none. `jobs.recover()` runs when the app is created.
`recover()` clears running_op on every job without checking that its owner is alive, so do not start `serve`
while a CLI step runs on the same work_dir: the server could run that step a second time.

Directories on this machine are only reachable inside `scan_roots` (empty by default: uploads only). Paths are
resolved (symlinks, ..) before the check.
"""

import base64
import binascii
import importlib
import shutil
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Literal

import pymupdf
import yaml
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from kb_intake import (CONTRACT_VERSION, __version__, clusters, config, contract, declaration, detect, queue,
                       search, vocab)
from kb_intake import workflow as workflow_mod
from kb_intake.jobs import AlreadyDelivered, Busy, JobError, Jobs, WrongState
from kb_intake.package import read_json, read_jsonl, sha_file
from kb_intake.service import api_models as m
from kb_intake.service.runner_thread import Background, StepRunner

STATIC = Path(__file__).with_name("static")
STAGES = ("label", "extract")
GAP_FILES = (("label", "label/gaps.jsonl"), ("extract", "extract/gaps.jsonl"), ("review", "review/gaps.jsonl"))
# the step that a job resting in this state runs again on /retry
RETRY = {"uploaded": "label", "approved": "extract", "confirmed": "deliver"}
# request bodies are parsed by the routes; these models only document them (added to the OpenAPI components)
BODY_MODELS = (m.CreateJobJson, m.CreateJobUpload, m.ApproveBody, m.ReviewBody, m.ConfirmBody, m.SeenBody)


def errors(*codes: int) -> dict:
    """OpenAPI `responses` for the error codes a route can answer with."""
    return {c: {"model": m.ErrorBody} for c in codes}


def json_body(model, required: bool = True) -> dict:
    return {"requestBody": {"required": required, "content": {
        "application/json": {"schema": {"$ref": f"#/components/schemas/{model.__name__}"}}}}}


def route(model=None, *codes: int, body: dict | None = None, **kw) -> dict:
    """Keyword arguments of a route: its response model (unset fields left out), error codes and documented body."""
    out = {"responses": errors(*codes), "response_model_exclude_unset": True, **kw}
    if model is not None:
        out["response_model"] = model
    if body is not None:
        out["openapi_extra"] = body
    return out


def _step(name: str):
    """A step module, looked up when used (so review/deliver load lazily and tests can replace them)."""
    return importlib.import_module(f"kb_intake.{name}")


class _NotStarted(Exception):
    """prepare() found nothing to run (an upload of a source that already has a job past `uploaded`)."""


LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def _hostname(netloc: str) -> str:
    """The host of "host", "host:port", "[v6]" or "[v6]:port", lower case."""
    netloc = netloc.strip().lower()
    if netloc.startswith("["):
        return netloc[1:netloc.find("]")] if "]" in netloc else netloc
    return netloc.rsplit(":", 1)[0] if netloc.count(":") == 1 else netloc


def create_app(cfg: config.Config | None = None, engines: dict | None = None, hosts: tuple = ()) -> FastAPI:
    """The app on the configuration's work and delivery directories. `engines` ({"label": ..., "extract": ...})
    replaces the Docling engines (tests). Requests are only served for a Host of this machine (127.0.0.1,
    localhost, ::1) or one of `hosts` (the address serve listens on), against DNS rebinding; a write whose Origin
    names another host is refused, and JSON bodies must say application/json, against cross-site requests (a
    browser sends a cross-site text/plain or multipart POST without asking first)."""
    cfg = cfg or config.load()
    engines = engines or {}
    if not search.fts5_available():
        raise RuntimeError("this Python's SQLite has no FTS5, which search needs; use a Python built with it "
                           "(the uv-managed CPython has it)")
    jobs = Jobs(cfg.work_dir)
    jobs.recover()
    _step("llm").recover(cfg.work_dir)          # a summary a stopped server left pending is failed (interrupted)
    cfg.delivery_dir.mkdir(parents=True, exist_ok=True)
    runner = StepRunner()
    summaries = Background()
    app = FastAPI(title="kb-intake", version=__version__)
    app.state.jobs, app.state.runner, app.state.cfg, app.state.summaries = jobs, runner, cfg, summaries
    allowed_hosts = {*LOCAL_HOSTS, *(_hostname(h) for h in hosts if h not in ("0.0.0.0", "::", ""))}

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        if _hostname(request.headers.get("host", "")) not in allowed_hosts:
            return JSONResponse({"detail": "unknown Host"}, status_code=403)
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD", "OPTIONS") and origin is not None \
                and _hostname(origin.split("://", 1)[-1]) not in allowed_hosts:
            return JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
        return await call_next(request)

    @app.exception_handler(JobError)
    async def job_error(request: Request, e: JobError):
        status = 409 if isinstance(e, (Busy, WrongState, AlreadyDelivered)) else 422
        return JSONResponse({"detail": str(e), "error": type(e).__name__}, status_code=status)

    # ---------- helpers ----------

    def get_job(job_id: str):
        try:
            return jobs.get(job_id)
        except KeyError:
            raise HTTPException(404, f"no job {job_id}") from None

    async def body_of(request: Request) -> dict:
        if not request.headers.get("content-type", "").startswith("application/json"):
            raise HTTPException(415, "the body must be JSON (Content-Type: application/json)")
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
        def work():
            _step("label").run(jobs, job_id, cfg, engine=engines.get("label"))
            # the summary (LLM, when enabled) must not keep the job from its workflow: own thread, no step slot
            summaries.start(f"summary-{job_id}", lambda: _step("llm").summarize(jobs, job_id, cfg))
        return work

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

    def start_label(src: Path, text, upload) -> dict:
        """Copy an upload, check the file and the declaration, create the job and start label (blocking work: run
        in the thread pool)."""
        if upload is not None:
            with src.open("wb") as out:
                shutil.copyfileobj(upload, out)
        need_pdf(src)
        decl = declared(text, src)
        result = {}

        def prepare():
            job, created = jobs.create(src, decl, cfg.delivery_dir)
            result.update(job_id=job.id, created=created, existing_job=not created, status=job.status)
            if not created:
                result["existing_note"] = ("this file already has a job that is not delivered; the declaration sent "
                                           "now was not used")
            if job.status != "uploaded" or job.running_op:
                raise _NotStarted
            return job.id, run_label(job.id)
        try:
            runner.start("label", prepare)
            started = True
        except _NotStarted:
            started = False
        return {**result, "started": started}

    @app.post("/v1/jobs", status_code=202, **route(m.CreateJobResult, 403, 409, 415, 422, body={"requestBody": {
        "required": True, "content": {
            "multipart/form-data": {"schema": {"$ref": "#/components/schemas/CreateJobUpload"}},
            "application/json": {"schema": {"$ref": "#/components/schemas/CreateJobJson"}}}}}))
    async def create_job(request: Request):
        tmp = None
        try:
            if request.headers.get("content-type", "").startswith("application/json"):
                body = await body_of(request)
                src = allowed(str(body.get("path") or ""))
                if not src.is_file():
                    raise HTTPException(422, f"{body.get('path')} is not a file")
                text, upload = body.get("declaration"), None
            else:
                form = await request.form()
                up = form.get("file")
                if not isinstance(up, UploadFile):
                    raise HTTPException(422, "upload a file (multipart field `file`), or post {\"path\": ...} as JSON")
                tmp = cfg.work_dir / "uploads" / uuid.uuid4().hex
                tmp.mkdir(parents=True)
                fname = Path(up.filename or "").name
                src = tmp / (fname if fname not in ("", ".", "..") else "upload.pdf")
                text, upload = form.get("declaration"), up.file
            return await run_in_threadpool(start_label, src, text, upload)
        finally:
            if tmp is not None:
                shutil.rmtree(tmp, ignore_errors=True)

    def summary(j) -> dict:
        return {"job_id": j.id, "status": j.status, "running_op": j.running_op, "doc_id": j.doc_id,
                "source_name": j.source_name, "created_at": j.created_at, "updated_at": j.updated_at,
                "last_error": j.last_error, "package": j.package}

    @app.get("/v1/jobs", **route(m.JobList))
    def list_jobs():
        return {"jobs": [summary(j) for j in jobs.recent()]}

    @app.get("/v1/jobs/{job_id}", **route(m.JobDetail, 404))
    def job_detail(job_id: str):
        job = get_job(job_id)
        stats = job.dir / "label" / "stats.json"
        run = job.dir / "extract" / "run.json"
        return {"job": job.to_dict(), "stats": read_json(stats) if stats.is_file() else None,
                "extract": read_json(run) if run.is_file() else None, "events": jobs.events(job_id)[-50:],
                "busy": (runner.busy() or {}).get("job_id") == job_id}

    @app.get("/v1/jobs/{job_id}/events", **route(m.EventList, 404))
    def job_events(job_id: str, after: int = 0):
        get_job(job_id)
        return {"events": jobs.events(job_id, after)}

    @app.get("/v1/jobs/{job_id}/gaps", **route(m.GapList, 404))
    def job_gaps(job_id: str):
        job = get_job(job_id)
        res = job.dir / "review" / "resolved.jsonl"
        lookup = _step("review").resolution_of(read_jsonl(res) if res.is_file() else [])
        out = []
        for stage, rel in GAP_FILES:
            p = job.dir / rel
            if p.is_file():
                for g in read_jsonl(p):
                    r = lookup(g)
                    out.append({**g, "stage": stage, **({"resolved": r} if r else {})})
        return {"gaps": out}

    @app.get("/v1/jobs/{job_id}/review", **route(m.ReviewOps, 404))
    def review_list(job_id: str):
        get_job(job_id)
        return {"ops": jobs.ops(job_id)}

    @app.get("/v1/jobs/{job_id}/source.pdf", response_class=FileResponse, responses={
        200: {"content": {"application/pdf": {}}, "description": "the source file"},
        206: {"content": {"application/pdf": {}}, "description": "the requested byte range (HTTP Range)"},
        416: {"description": "range not satisfiable"}, **errors(404)})
    def source_pdf(job_id: str):
        """The job's source file, with HTTP Range (PDF.js loads pages on demand)."""
        job = get_job(job_id)
        return FileResponse(job.source, media_type="application/pdf",
                            headers={"ETag": f'"{job.sha256}"', "Cache-Control": "private, max-age=0, must-revalidate"})

    # ---------- search (intake-ui-llm-design-v1 §3.2) ----------

    def decode_cursor(cursor: str | None) -> int | None:
        if cursor is None:
            return None
        try:
            value = int(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode("ascii"))
        except (ValueError, binascii.Error, UnicodeDecodeError):
            raise HTTPException(422, "cursor is not one this API gave out") from None
        if value < 0:
            raise HTTPException(422, "cursor is not one this API gave out")
        return value

    def encode_cursor(value: int | None) -> str | None:
        return None if value is None else base64.urlsafe_b64encode(str(value).encode()).decode().rstrip("=")

    def filters(q, type_, status, page, gap, min_score, max_score) -> search.Filters:
        f = search.Filters(q=q, types=type_, statuses=status, page=page, gaps=gap, min_score=min_score,
                           max_score=max_score)
        try:
            f.check()
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        return f

    def searched(job_id: str, fn):
        get_job(job_id)
        try:
            return fn()
        except KeyError:
            raise HTTPException(409, f"job {job_id} is not labelled yet") from None

    @app.get("/v1/jobs/{job_id}/regions", **route(m.RegionPage, 404, 409, 422))
    def regions(job_id: str, q: str = "", type: list[str] = Query([]), status: list[str] = Query([]),
                page: int | None = None, gap: list[str] = Query([]), min_score: float | None = None,
                max_score: float | None = None, cursor: str | None = None,
                limit: int = Query(100, ge=1, le=500)):
        """Regions in reading order: label-stage before extraction, current after. `q` searches region text, table
        cells and LLM descriptions (every word as a prefix); repeat `type`, `status`, `gap` for several values."""
        f = filters(q, type, status, page, gap, min_score, max_score)
        after = decode_cursor(cursor)
        out = searched(job_id, lambda: search.find(jobs, job_id, f, cursor=after, limit=limit))
        return {**out, "next_cursor": encode_cursor(out["next_cursor"])}

    @app.get("/v1/jobs/{job_id}/facets", **route(m.Facets, 404, 409, 422))
    def facets(job_id: str, q: str = "", type: list[str] = Query([]), status: list[str] = Query([]),
               page: int | None = None, gap: list[str] = Query([]), min_score: float | None = None,
               max_score: float | None = None):
        """Counts per type, status and open gap code for the same filters as /regions; each dimension is counted
        without its own filter."""
        f = filters(q, type, status, page, gap, min_score, max_score)
        return searched(job_id, lambda: search.facets(jobs, job_id, f))

    @app.get("/v1/jobs/{job_id}/clusters", **route(m.ClusterList, 404, 409, 422))
    def job_clusters(job_id: str, kind: Literal["repeated_text", "same_shape", "same_table_header",
                                                "same_gap"] | None = None):
        """Groups of similar regions (two or more members each); a group changes no data."""
        return {"clusters": searched(job_id, lambda: clusters.for_job(jobs, job_id, kind))}

    @app.get("/v1/jobs/{job_id}/queue", **route(m.QueueView, 404, 409))
    def job_queue(job_id: str):
        """The review inbox: gaps and weak continuation hints grouped by problem, each open, seen or resolved (empty
        before extraction)."""
        return queue.view(searched(job_id, lambda: queue.items(jobs, job_id)))

    @app.post("/v1/jobs/{job_id}/queue/seen", **route(m.SeenResult, 404, 409, 415, 422, body=json_body(m.SeenBody)))
    async def queue_seen(job_id: str, request: Request):
        """Mark queue items as shown to the reviewer (work state only; never delivered)."""
        job = get_job(job_id)
        body = await body_of(request)
        ids = body.get("item_ids")
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids) or len(ids) > 1000:
            raise HTTPException(422, "item_ids must be a list of at most 1000 strings")
        if job.status not in queue.POST_EXTRACT:
            raise WrongState(f"job {job_id} is {job.status}; the queue exists from in_review on")
        known = {i["item_id"] for i in await run_in_threadpool(queue.items, jobs, job_id)}
        unknown = sorted(set(ids) - known)
        if unknown:
            raise HTTPException(422, f"unknown queue items {unknown[:5]}")
        return {"updated": jobs.mark_seen(job_id, ids)}

    # ---------- LLM (intake-ui-llm-design-v1 §4) ----------

    @app.get("/v1/jobs/{job_id}/summary", **route(m.Summary, 404))
    def job_summary(job_id: str):
        """The document summary (LLM, low weight); `off` when the LLM is not enabled."""
        job = get_job(job_id)
        info = config.public_llm_info(cfg)
        llm = _step("llm")
        state = job.dir / "llm" / "summary.json"
        status = read_json(state)["status"] if state.is_file() else "off"
        row = next((r for r in llm.rows_of(job.dir) if r["kind"] == "summary"), None)
        return {"status": status, "provider": row["provider"] if row else info["provider"],
                "model": row["model"] if row else info["model"], "remote": info["remote"],
                "text": row["output"]["text"] if row else None}

    @app.get("/v1/jobs/{job_id}/llm", **route(m.LlmView, 404))
    def job_llm(job_id: str, kind: Literal["summary", "describe", "chart_table"] | None = None,
                region_id: str | None = None):
        """LLM outputs of the job (llm.jsonl rows) and progress per use."""
        job = get_job(job_id)
        info = config.public_llm_info(cfg)
        llm = _step("llm")
        rows, gaps = llm.rows_of(job.dir), llm.gaps_of(job.dir)
        run = job.dir / "llm" / "run.json"
        cached = read_json(run) if run.is_file() else {}
        progress = {}
        for k in config.LLM_FEATURES:
            done, failed = sum(r["kind"] == k for r in rows), sum(g["kind"] == k for g in gaps)
            progress[k] = {"total": done + failed, "done": done, "cached": int(cached.get(k, {}).get("cached", 0)),
                           "failed": failed}
        items = [r for r in rows if (kind is None or r["kind"] == kind)
                 and (region_id is None or r["region_id"] == region_id)]
        return {"enabled": info["enabled"], "remote": info["remote"], "progress": progress, "items": items}

    # ---------- workflow and approval ----------

    @app.get("/v1/jobs/{job_id}/workflow", **route(m.WorkflowView, 404))
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
                "types": list(vocab.REGION_TYPES), "handlers": list(vocab.HANDLERS), "notes": notes,
                "llm": config.public_llm_info(cfg)}

    @app.put("/v1/jobs/{job_id}/workflow", **route(m.WorkflowSaved, 404, 409, 422, body={"requestBody": {
        "required": True, "content": {"application/yaml": {"schema": {"type": "string"}}}}}))
    async def put_workflow(job_id: str, request: Request):
        get_job(job_id)
        text = (await request.body()).decode("utf-8", errors="replace")
        try:
            wf = workflow_mod.parse(text)
        except workflow_mod.WorkflowError as e:
            raise HTTPException(422, str(e)) from None
        return {"version": jobs.save_workflow(job_id, workflow_mod.dump(wf))}

    @app.post("/v1/jobs/{job_id}/approve", status_code=202, **route(m.Started, 404, 409, 415, 422, body=json_body(m.ApproveBody)))
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

    @app.get("/v1/jobs/{job_id}/pages/{n}", **route(m.PageView, 404, 409))
    def page(job_id: str, n: int):
        job = get_job(job_id)
        need_page(job, n)
        try:
            return _step("review").page_view(jobs, job_id, n)
        except (FileNotFoundError, KeyError):
            raise HTTPException(409, f"job {job_id} is not labelled yet") from None

    @app.get("/v1/jobs/{job_id}/pages/{n}/image", response_class=FileResponse, responses={
        200: {"content": {"image/png": {}}, "description": "the page as PNG"}, **errors(404)})
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

    @app.post("/v1/jobs/{job_id}/review", **route(m.ReviewResult, 400, 404, 409, 415, 422, body=json_body(m.ReviewBody)))
    async def review_ops(job_id: str, request: Request):
        get_job(job_id)
        body = await body_of(request)
        by = need_name(body, "a review")
        ops = body.get("ops")
        if not isinstance(ops, list) or not all(isinstance(o, dict) for o in ops):
            raise HTTPException(422, "ops must be a list of objects")
        idle(get_job(job_id), "in_review")
        review = _step("review")
        try:                                    # apply re-extracts: keep it off the event loop
            applied = await run_in_threadpool(review.apply, jobs, job_id, ops, by, cfg)
        except review.ReviewError as e:
            raise HTTPException(400, str(e)) from None
        return {"applied": applied, "status": jobs.get(job_id).status}

    @app.post("/v1/jobs/{job_id}/confirm", status_code=202, **route(m.Started, 404, 409, 415, 422, body=json_body(m.ConfirmBody)))
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

    @app.post("/v1/jobs/{job_id}/retry", status_code=202, **route(m.Started, 404, 409))
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

    @app.get("/v1/packages", **route(m.PackagesView))
    def packages():
        by_package = {j.package: j.id for j in jobs.recent(10000) if j.package}
        out = []
        for p in sorted(cfg.delivery_dir.iterdir()) if cfg.delivery_dir.is_dir() else []:
            if p.name.startswith(".") or not p.is_dir():
                continue
            out.append({"package": p.name, "job_id": by_package.get(p.name), "check": contract.check(p)})
        return {"delivery_dir": str(cfg.delivery_dir), "packages": out}

    @app.get("/v1/engines", **route(m.EnginesView))
    def engines_info():
        from kb_intake.engines import DoclingEngine
        infos = {s: (engines.get(s) or DoclingEngine(cfg.models_dir, cfg.docling, stage=s)).info() for s in STAGES}
        return {"ready": all(i.ready for i in infos.values()), "stages": {s: asdict(i) for s, i in infos.items()},
                "models_dir": str(cfg.models_dir), "pymupdf": pymupdf.VersionBind,
                "layout_enabled": cfg.layout_enabled, "llm": config.public_llm_info(cfg)}

    @app.get("/v1/directories", **route(m.DirectoriesView, 403, 422))
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

    @app.get("/v1/health", **route(m.Health))
    def health():
        return {"ok": True, "version": __version__, "contract_version": CONTRACT_VERSION, "busy": runner.busy(),
                "last": runner.last, "llm": config.public_llm_info(cfg)}

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/ui/")

    app.mount("/ui", StaticFiles(directory=STATIC, html=True), name="ui")

    def openapi() -> dict:
        if app.openapi_schema is None:
            doc = get_openapi(title=app.title, version=app.version, routes=app.routes)
            schemas = doc.setdefault("components", {}).setdefault("schemas", {})
            for model in BODY_MODELS:
                schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
                for name, sub in schema.pop("$defs", {}).items():
                    schemas.setdefault(name, sub)
                schemas.setdefault(model.__name__, schema)
            app.openapi_schema = doc
        return app.openapi_schema
    app.openapi = openapi
    return app
