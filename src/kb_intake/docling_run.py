"""Run Docling's standard PDF pipeline (OCR off, TableFormer ACCURATE) with pinned, local model weights, and cache
the lossless DoclingDocument (intake-design §9.2 step 2, §9.3).

Stability rules implemented here:
- Docling packages are pinned in uv.lock; their versions go into the cache key and the manifest.
- Model weights are read from `artifacts_path` only. `models.lock.json` in that directory records the Hugging Face
  revision and the sha256 of every file; weights that do not match the lock are refused. Hugging Face is put into
  offline mode before Docling is imported, so a run never downloads anything.
- Determinism comes from the cache, not from the engine: the key is source sha256 + tool versions + model revisions
  + parameters (device and threads included). A cache hit returns the stored docling.json byte for byte.
- A timeout or a non-SUCCESS status is reported, never hidden: failed pages are listed so the caller can fall back
  to find_tables for them; an exception becomes an error result, the text layer products are not affected.
"""

import hashlib
import importlib.metadata
import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

PACKAGES = ("docling", "docling-core", "docling-parse", "docling-ibm-models", "torch", "transformers")
# The two models the standard PDF pipeline needs with OCR off. Revisions are pinned here; scripts/fetch_models.py
# copies exactly these into artifacts_path and writes models.lock.json.
MODELS = (
    {"repo_id": "docling-project/docling-layout-heron", "revision": "8f39ad3c0b4c58e9c2d2c84a38465abf757272d8",
     "role": "layout"},
    {"repo_id": "docling-project/docling-models", "revision": "fc0f2d45e2218ea24bce5045f58a389aed16dc23",
     "tag": "v2.3.0", "role": "table_structure"},
)
LOCK = "models.lock.json"


@dataclass
class DoclingConfig:
    artifacts_path: Path
    cache_dir: Path
    device: str = "cpu"                 # formal packages use cpu; mps only for local debugging (recorded)
    num_threads: int = 4
    table_mode: str = "accurate"
    document_timeout: float = 1800.0

    def params(self) -> dict:
        return {"do_ocr": False, "do_table_structure": True, "table_mode": self.table_mode, "do_cell_matching": True,
                "device": self.device, "num_threads": self.num_threads, "document_timeout": self.document_timeout}


@dataclass
class DoclingResult:
    ok: bool
    status: str
    docling_json: bytes | None
    cache_key: str
    cached: bool
    seconds: float
    failed_pages: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    confidence_pages: dict = field(default_factory=dict)
    versions: dict = field(default_factory=dict)
    models: list[dict] = field(default_factory=list)


def versions() -> dict:
    return {p: importlib.metadata.version(p) for p in PACKAGES}


def _sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def folder(repo_id: str) -> str:
    return repo_id.replace("/", "--")         # the layout Docling expects under artifacts_path


def verify_models(artifacts: Path) -> list[dict]:
    """Check artifacts_path against models.lock.json and the pinned revisions; raise if anything differs."""
    lock_path = artifacts / LOCK
    if not lock_path.is_file():
        raise FileNotFoundError(f"{lock_path} missing: run scripts/fetch_models.py first")
    lock = {m["repo_id"]: m for m in json.loads(lock_path.read_text())["models"]}
    out = []
    for spec in MODELS:
        m = lock.get(spec["repo_id"])
        if m is None or m["revision"] != spec["revision"]:
            raise ValueError(f"{spec['repo_id']}: artifacts are not at the pinned revision {spec['revision']}")
        for rel, sha in m["files"].items():
            p = artifacts / folder(spec["repo_id"]) / rel
            if not p.is_file() or _sha_file(p) != sha:
                raise ValueError(f"{spec['repo_id']}: {rel} does not match models.lock.json")
        out.append({"repo_id": spec["repo_id"], "revision": spec["revision"], "role": spec["role"],
                    "files": len(m["files"])})
    return out


def cache_key(source_sha256: str, cfg: DoclingConfig, vers: dict) -> str:
    material = {"source": source_sha256, "versions": vers, "models": [(m["repo_id"], m["revision"]) for m in MODELS],
                "params": cfg.params()}
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


def _offline() -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"         # never download at run time (§9.3.2)
    os.environ["TRANSFORMERS_OFFLINE"] = "1"


def _convert(pdf: Path, cfg: DoclingConfig) -> tuple[bytes, dict]:
    _offline()
    from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode, TableStructureOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions(
        do_ocr=False,
        do_table_structure=True,
        table_structure_options=TableStructureOptions(
            mode=TableFormerMode.ACCURATE if cfg.table_mode == "accurate" else TableFormerMode.FAST,
            do_cell_matching=True),
        accelerator_options=AcceleratorOptions(device=AcceleratorDevice(cfg.device), num_threads=cfg.num_threads),
        artifacts_path=str(cfg.artifacts_path),
        document_timeout=cfg.document_timeout,
    )
    conv = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
    res = conv.convert(str(pdf), raises_on_error=False)
    doc = res.document
    data = json.dumps(doc.export_to_dict(), ensure_ascii=False).encode()
    n = res.input.page_count
    failed = sorted(set(range(1, n + 1)) - set(doc.pages))      # pages Docling could not convert (timeout, error)
    conf = {}
    for page_no, c in (res.confidence.pages or {}).items():
        d = c.model_dump()
        conf[str(page_no)] = {"grade": str(d.get("mean_grade")).split(".")[-1].lower(),
                              "layout_score": None if d.get("layout_score") != d.get("layout_score") else d.get("layout_score")}
    meta = {"status": str(res.status).split(".")[-1].lower(), "errors": [str(e)[:300] for e in res.errors],
            "failed_pages": failed, "confidence_pages": conf}
    return data, meta


def run(pdf: Path, source_sha256: str, cfg: DoclingConfig) -> DoclingResult:
    """Docling for one PDF, from the cache when possible."""
    vers = versions()
    key = cache_key(source_sha256, cfg, vers)
    entry = cfg.cache_dir / "docling" / key
    try:
        models = verify_models(cfg.artifacts_path)
    except Exception as e:  # noqa: BLE001 - no trusted weights: report, the caller degrades to find_tables
        return DoclingResult(False, "models_unverified", None, key, False, 0.0, errors=[f"{type(e).__name__}: {e}"],
                             versions=vers)
    if (entry / "docling.json").is_file() and (entry / "meta.json").is_file():
        meta = json.loads((entry / "meta.json").read_text())
        return DoclingResult(meta["status"] == "success", meta["status"], (entry / "docling.json").read_bytes(), key,
                             True, meta["seconds"], meta["failed_pages"], meta["errors"], meta["confidence_pages"],
                             vers, models)
    t0 = time.monotonic()
    try:
        data, meta = _convert(pdf, cfg)
    except Exception as e:  # noqa: BLE001 - a Docling error never takes the text layer down with it (§9.3.6)
        return DoclingResult(False, "error", None, key, False, time.monotonic() - t0,
                             errors=[f"{type(e).__name__}: {e}"[:500]], versions=vers, models=models)
    meta["seconds"] = round(time.monotonic() - t0, 1)
    if data:
        entry.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix=f".{key}.", dir=entry.parent))
        (tmp / "docling.json").write_bytes(data)
        (tmp / "meta.json").write_text(json.dumps(meta, indent=1))
        if entry.exists():
            shutil.rmtree(entry)
        os.replace(tmp, entry)
    return DoclingResult(meta["status"] == "success", meta["status"], data, key, False, meta["seconds"],
                         meta["failed_pages"], meta["errors"], meta["confidence_pages"], vers, models)
