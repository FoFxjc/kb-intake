"""Running a layout engine with cache and fault isolation (intake-design §9.3).

- Cache key: source sha256 + engine name and version + the installed versions of the dependency closure
  (kb_intake.deps) + model revisions + parameters. A hit returns the stored output byte for byte, which is what makes
  repeated runs give identical packages.
- Only complete successes are cached (status success, no failed page). A timeout caused by a busy machine must not be
  replayed forever.
- A cache entry that cannot be read, does not parse, or whose output does not match the recorded sha256 is a miss.
- The conversion runs in a child process (spawned, not forked) with a hard wall-clock limit; on timeout the child is
  killed. The child exists only to isolate faults: one per file, no resident worker, no queue.
"""

import hashlib
import json
import multiprocessing
import os
import shutil
import tempfile
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path

from kb_intake import deps
from kb_intake.engines.base import EngineInfo, LayoutEngine, RawResult


@dataclass
class Outcome:
    status: str                  # success | partial_success | failure | timeout | crashed | error | not_ready
    data: bytes | None
    cache_key: str
    cached: bool
    seconds: float | None
    failed_pages: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    page_confidence: dict = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return self.status == "success" and not self.failed_pages and self.data is not None


def cache_key(source_sha256: str, info: EngineInfo) -> str:
    material = {"source": source_sha256, "engine": info.name, "version": info.version, "deps": deps.closure(),
                "models": [(m["repo_id"], m["revision"]) for m in info.models], "params": info.params}
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


def _entry(cache_dir: Path, engine: str, key: str) -> Path:
    return cache_dir / engine / key


def read_cache(entry: Path) -> tuple[bytes, dict] | None:
    try:
        meta = json.loads((entry / "meta.json").read_text(encoding="utf-8"))
        data = (entry / "output.bin").read_bytes()
        if hashlib.sha256(data).hexdigest() != meta["sha256"]:
            return None
        RawResult(**meta["raw"])                       # the shape still fits
        return data, meta
    except Exception:  # noqa: BLE001 - anything wrong with an entry makes it a miss
        return None


def write_cache(entry: Path, data: bytes, raw: RawResult, seconds: float) -> None:
    entry.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{entry.name}.", dir=entry.parent))
    meta = {"sha256": hashlib.sha256(data).hexdigest(), "seconds": seconds,
            "raw": {**asdict(raw), "data": None}}
    (tmp / "output.bin").write_bytes(data)
    (tmp / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    if entry.exists():
        shutil.rmtree(entry)
    os.replace(tmp, entry)


def _child(engine: LayoutEngine, pdf: str, out: str) -> None:
    """Child process: run the conversion and leave output.bin + raw.json in `out` (or error.txt)."""
    try:
        raw = engine.convert(Path(pdf))
        if raw.data is not None:
            Path(out, "output.bin").write_bytes(raw.data)
        Path(out, "raw.json").write_text(json.dumps({**asdict(raw), "data": None}), encoding="utf-8")
    except BaseException:  # noqa: BLE001 - reported to the parent as a failed conversion
        Path(out, "error.txt").write_text(traceback.format_exc()[-2000:], encoding="utf-8")
        raise


def convert_isolated(engine: LayoutEngine, pdf: Path, wall_timeout: float) -> tuple[str, RawResult | None, str]:
    """(status, raw result or None, detail). status is "done" when the child returned a result."""
    ctx = multiprocessing.get_context("spawn")
    with tempfile.TemporaryDirectory(prefix="kb-intake-convert-") as out:
        proc = ctx.Process(target=_child, args=(engine, str(pdf), out), daemon=True)
        proc.start()
        proc.join(wall_timeout)
        if proc.is_alive():
            proc.kill()
            proc.join(10)
            return "timeout", None, f"conversion killed after {wall_timeout:.0f} s (hard wall-clock limit)"
        o = Path(out)
        if not (o / "raw.json").is_file():
            err = (o / "error.txt").read_text(encoding="utf-8") if (o / "error.txt").is_file() else ""
            status = "error" if err else "crashed"
            return status, None, (err.strip().splitlines() or [f"child exited with code {proc.exitcode}"])[-1][:500]
        meta = json.loads((o / "raw.json").read_text(encoding="utf-8"))
        data = (o / "output.bin").read_bytes() if (o / "output.bin").is_file() else None
        return "done", RawResult(**{**meta, "data": data}), ""


def run(engine: LayoutEngine, pdf: Path, source_sha256: str, cache_dir: Path, wall_timeout: float,
        info: EngineInfo | None = None, force: bool = False) -> Outcome:
    """The engine's output for one PDF: from the cache when possible, else converted in a child process."""
    info = info or engine.info()
    key = cache_key(source_sha256, info)
    if not info.ready and not force:
        return Outcome("not_ready", None, key, False, None, errors=list(info.problems))
    entry = _entry(cache_dir, info.name, key)
    hit = read_cache(entry)
    if hit:
        data, meta = hit
        raw = RawResult(**{**meta["raw"], "data": data})
        return Outcome(raw.status, data, key, True, meta["seconds"], raw.failed_pages, raw.errors,
                       raw.page_confidence)
    t0 = time.monotonic()
    status, raw, detail = convert_isolated(engine, pdf, wall_timeout)
    seconds = round(time.monotonic() - t0, 1)
    if raw is None:
        return Outcome(status, None, key, False, seconds, errors=[detail])
    out = Outcome(raw.status, raw.data, key, False, seconds, raw.failed_pages, raw.errors, raw.page_confidence)
    if out.complete:
        write_cache(entry, raw.data, raw, seconds)
    return out
