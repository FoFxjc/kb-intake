"""The HTTP API of intake-design §3.2 and the static web UI (served from the same process, no build step).

    GET  /v1/health                         alive, versions
    GET  /v1/engines                        layout engine, versions, model revisions, weights in place, dependency check
    GET  /v1/config  PUT /v1/config         the configuration (intake.yaml in the data directory)
    POST /v1/jobs                           upload files + declaration, or {"directory": ...} on this machine
    GET  /v1/jobs  GET /v1/jobs/{id}        jobs; one job: status, step events per file, results
    GET  /v1/jobs/{id}/pages/{n}            page review data of a package of the job (?package=, default the first)
    GET  /v1/packages                       package list
    GET  /v1/packages/{name}                download as zip
    GET  /v1/packages/{name}/manifest       manifest.json and checks.json
    GET  /v1/packages/{name}/pages/{n}      page review data; .../image renders the page
    POST /v1/packages/check                 upload a package zip, check it against contract v1
"""

import json
import os
import shutil
import tempfile
import zipfile
from dataclasses import asdict, replace
from pathlib import Path

import pymupdf
import yaml
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from kb_intake import CONTRACT_VERSION, __version__, config, deps
from kb_intake.engines import LAYOUT
from kb_intake.service import review
from kb_intake.service.store import Store
from kb_intake.service.worker import Busy, Worker

STATIC = Path(__file__).with_name("static")


class Service:
    def __init__(self, data: Path):
        self.data = data.expanduser().resolve()
        self.packages = self.data / "packages"
        self.uploads = self.data / "uploads"
        self.config_path = self.data / "intake.yaml"
        for d in (self.packages, self.uploads):
            d.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.data / "intake.db")
        self.worker = Worker(self.store, self.packages, self.config)

    def raw_config(self) -> dict:
        if not self.config_path.is_file():
            return {}
        return yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}

    def config(self) -> config.Config:
        """intake.yaml of the data directory; the cache defaults to <data>/cache."""
        cfg = config.load(self.config_path if self.config_path.is_file() else None)
        if "cache_dir" not in self.raw_config() and not os.environ.get("INTAKE_CACHE_DIR"):
            cfg = replace(cfg, cache_dir=self.data / "cache")
        return cfg

    @property
    def cache(self) -> Path:
        return self.config().cache_dir


def _jsonable(cfg: config.Config) -> dict:
    d = asdict(cfg)
    return json.loads(json.dumps(d, default=str))


def create_app(data: Path) -> FastAPI:
    svc = Service(data)
    app = FastAPI(title="kb-intake", version=__version__)
    app.state.service = svc

    def pkg(name: str) -> Path:
        try:
            return review.package_dir(svc.packages, name)
        except review.NotFound:
            raise HTTPException(404, f"no package {name}")

    @app.get("/v1/health")
    def health():
        return {"ok": True, "version": __version__, "contract_version": CONTRACT_VERSION,
                "busy": svc.worker.current}

    @app.get("/v1/engines")
    def engines():
        cfg = svc.config()
        info = LAYOUT[cfg.primary](models_dir=cfg.models_dir, params=cfg.docling).info()
        return {"ready": info.ready, "layout": asdict(info), "models_dir": str(cfg.models_dir),
                "pymupdf": pymupdf.VersionBind, "secondary": cfg.secondary,
                "dependencies": {"checked": len(deps.closure()), "problems": deps.problems()}}

    @app.get("/v1/config")
    def get_config():
        return {"path": str(svc.config_path), "yaml": svc.config_path.read_text(encoding="utf-8")
                if svc.config_path.is_file() else "", "effective": _jsonable(svc.config()),
                "environment": {k: os.environ.get(k) for k in ("INTAKE_CONFIG", "INTAKE_MODELS_DIR",
                                                                "INTAKE_CACHE_DIR")}}

    @app.put("/v1/config")
    async def put_config(request: Request):
        body = await request.json()
        text = body.get("yaml", "")
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", dir=svc.data, delete=False, encoding="utf-8") as f:
            f.write(text)
        try:
            config.load(f.name)                         # refuses unknown keys and bad values
        except config.ConfigError as e:
            os.unlink(f.name)
            raise HTTPException(422, str(e))
        os.replace(f.name, svc.config_path)
        return get_config()

    @app.post("/v1/jobs", status_code=202)
    async def create_job(request: Request, files: list[UploadFile] | None = File(None),
                         declaration: str | None = Form(None), replace_: bool = Form(False, alias="replace"),
                         recursive: bool = Form(False)):
        if request.headers.get("content-type", "").startswith("application/json"):
            body = await request.json()
            directory = Path(str(body.get("directory", ""))).expanduser()
            if not directory.exists():
                raise HTTPException(422, f"no such file or directory on this machine: {directory}")
            declaration, options = body.get("declaration"), {"replace": bool(body.get("replace")),
                                                            "recursive": bool(body.get("recursive"))}
            kind, target = "directory", directory
        else:
            if not files:
                raise HTTPException(422, "upload at least one file, or post {\"directory\": ...} as JSON")
            options, kind = {"replace": replace_, "recursive": False}, "upload"
            target = None
        if declaration:
            try:
                yaml.safe_load(declaration)
            except yaml.YAMLError as e:
                raise HTTPException(422, f"declaration is not YAML: {e}")
        if svc.worker.current is not None:
            raise HTTPException(409, f"job {svc.worker.current} is running; one job at a time")
        job_id = svc.store.create_job(kind, str(target) if target else ", ".join(f.filename for f in files),
                                      declaration, options)
        if kind == "upload":
            target = svc.uploads / job_id
            target.mkdir(parents=True)
            for f in files:
                name = Path(f.filename or "upload.pdf").name
                with (target / name).open("wb") as out:
                    shutil.copyfileobj(f.file, out)
        try:
            svc.worker.submit(job_id, target, declaration, options)
        except Busy as e:
            svc.store.set_status(job_id, "refused", error=str(e))
            raise HTTPException(409, str(e))
        return {"job_id": job_id}

    @app.get("/v1/jobs")
    def list_jobs():
        return {"jobs": svc.store.jobs()}

    @app.get("/v1/jobs/{job_id}")
    def get_job(job_id: str):
        job = svc.store.job(job_id)
        if job is None:
            raise HTTPException(404, f"no job {job_id}")
        job["packages"] = []
        for r in job["results"]:
            if r.get("package"):
                try:
                    job["packages"].append(review.summary(pkg(r["package"])))
                except HTTPException:
                    pass
        return job

    @app.get("/v1/jobs/{job_id}/pages/{n}")
    def job_page(job_id: str, n: int, package: str | None = None):
        job = get_job(job_id)
        names = [p["package"] for p in job["packages"]]
        if not names:
            raise HTTPException(404, "the job has no package yet")
        name = package or names[0]
        if name not in names:
            raise HTTPException(404, f"{name} is not a package of job {job_id}")
        return package_page(name, n)

    @app.get("/v1/packages")
    def list_packages():
        return {"packages": review.packages(svc.packages)}

    @app.get("/v1/packages/{name}")
    def download(name: str):
        p = pkg(name)
        out = svc.cache / "zips" / f"{name}.zip"
        if not out.is_file() or out.stat().st_mtime < (p / "manifest.json").stat().st_mtime:
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_suffix(".tmp")
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
                for f in sorted(p.iterdir()):
                    z.write(f, f"{name}/{f.name}")
            tmp.replace(out)
        return FileResponse(out, media_type="application/zip", filename=f"{name}.zip")

    @app.get("/v1/packages/{name}/manifest")
    def manifest(name: str):
        p = pkg(name)
        return {"manifest": json.loads((p / "manifest.json").read_text(encoding="utf-8")),
                "checks": json.loads((p / "checks.json").read_text(encoding="utf-8"))}

    @app.get("/v1/packages/{name}/pages/{n}")
    def package_page(name: str, n: int):
        try:
            return review.page(pkg(name), n)
        except review.NotFound as e:
            raise HTTPException(404, str(e))

    @app.get("/v1/packages/{name}/pages/{n}/image")
    def page_image(name: str, n: int, dpi: int = 110):
        try:
            return FileResponse(review.page_image(pkg(name), n, svc.cache, dpi), media_type="image/png")
        except review.NotFound as e:
            raise HTTPException(404, str(e))

    @app.post("/v1/packages/check")
    async def check_package(file: UploadFile = File(...)):
        with tempfile.TemporaryDirectory(dir=svc.data) as tmp:
            z = Path(tmp) / "package.zip"
            with z.open("wb") as out:
                shutil.copyfileobj(file.file, out)
            if not zipfile.is_zipfile(z):
                return JSONResponse({"ok": False, "errors": ["not a zip file"]}, status_code=422)
            return review.check(z)

    @app.get("/")
    def root():
        return RedirectResponse("/ui/")

    app.mount("/ui", StaticFiles(directory=STATIC, html=True), name="ui")
    return app
