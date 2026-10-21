"""Pinned model weights (intake-design §7, §9.3.2): `kb-intake models download` and `kb-intake models verify`.

`models.lock.json` ships inside the package: for each model the Hugging Face repo, the revision (a commit sha) and
the sha256 of every file. `download` fetches exactly that revision and refuses files that do not match; `verify`
checks a directory, e.g. one copied from a machine with network access; `import` finds the pinned files under any
directory (a Hugging Face cache, files downloaded one by one) by name and sha256 and copies them into this layout. At run time only the local directory is read;
the pipeline refuses weights that do not match the lock, and never downloads.

Layout of the directory: one folder per repo, named `<org>--<name>` (what Docling expects under artifacts_path).
"""

import hashlib
import json
import os
import shutil
import tempfile
from functools import cache
from pathlib import Path

LOCK_FILE = Path(__file__).with_name("models.lock.json")


class ModelsError(RuntimeError):
    pass


@cache
def lock() -> tuple[dict, ...]:
    return tuple(json.loads(LOCK_FILE.read_text(encoding="utf-8"))["models"])


def pins() -> list[dict]:
    """repo_id, revision and role of each model (what the manifest and the cache key record)."""
    return [{k: m[k] for k in ("repo_id", "revision", "role")} for m in lock()]


def folder(repo_id: str) -> str:
    return repo_id.replace("/", "--")


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _check_dir(d: Path, spec: dict) -> list[str]:
    problems = []
    for rel, sha in spec["files"].items():
        p = d / rel
        if not p.is_file():
            problems.append(f"{spec['repo_id']}: {rel} missing")
        elif sha_file(p) != sha:
            problems.append(f"{spec['repo_id']}: {rel} does not match the lock")
    return problems


def _signature(models_dir: Path) -> tuple:
    out = []
    for spec in lock():
        for rel in spec["files"]:
            p = models_dir / folder(spec["repo_id"]) / rel
            st = p.stat() if p.is_file() else None
            out.append((str(p), st.st_size if st else None, st.st_mtime_ns if st else None))
    return tuple(out)


_passed: set[tuple] = set()          # signatures (path, size, mtime of every pinned file) that verified


def verify(models_dir: Path) -> list[dict]:
    """Check every pinned file under models_dir; raise ModelsError listing what is missing or different. A
    directory that passed is not hashed again in the same process while no file changed size or mtime."""
    sig = _signature(models_dir)
    if sig in _passed:
        return pins()
    problems = []
    for spec in lock():
        problems += _check_dir(models_dir / folder(spec["repo_id"]), spec)
    if problems:
        raise ModelsError(f"models in {models_dir} are not the pinned weights: " + "; ".join(problems[:5])
                          + (" (run `kb-intake models download`)" if any("missing" in p for p in problems) else ""))
    _passed.add(sig)
    return pins()


def download(models_dir: Path, log=print) -> list[dict]:
    """Fetch the pinned revisions into models_dir (atomically per model); already complete models are kept."""
    os.environ.pop("HF_HUB_OFFLINE", None)
    from huggingface_hub import snapshot_download

    models_dir.mkdir(parents=True, exist_ok=True)
    for spec in lock():
        dest = models_dir / folder(spec["repo_id"])
        if dest.is_dir() and not _check_dir(dest, spec):
            log(f"{spec['repo_id']}@{spec['revision'][:12]}: already present")
            continue
        with tempfile.TemporaryDirectory(prefix=".download-", dir=models_dir) as tmp:
            snap = Path(snapshot_download(repo_id=spec["repo_id"], revision=spec["revision"],
                                          cache_dir=Path(tmp) / "hf", allow_patterns=list(spec["files"])))
            staged = Path(tmp) / "model"
            staged.mkdir()
            for rel in spec["files"]:
                (staged / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(snap / rel, staged / rel)          # resolves the hub cache's symlinks
            problems = _check_dir(staged, spec)
            if problems:
                raise ModelsError("downloaded files do not match the lock: " + "; ".join(problems[:5]))
            if dest.exists():
                shutil.rmtree(dest)
            os.replace(staged, dest)
        log(f"{spec['repo_id']}@{spec['revision'][:12]}: {len(spec['files'])} files")
    return verify(models_dir)


def tree_url(spec: dict) -> str:
    """The pinned commit's file list on Hugging Face."""
    return f"https://huggingface.co/{spec['repo_id']}/tree/{spec['revision']}"


def import_from(src: Path, models_dir: Path, log=print) -> dict:
    """Find every pinned file under `src` (recursively; a Hugging Face cache's snapshot links are followed) by its
    file name and sha256, copy it to models_dir/<org>--<name>/<path>, then verify. Files already in place are kept.
    A file with the right name but other content is not the pinned one. Raises ModelsError naming each file not
    found, with the page of its pinned commit; what was found is copied anyway."""
    src, models_dir = Path(src).expanduser(), Path(models_dir).expanduser()
    if not src.is_dir():
        raise ModelsError(f"{src} is not a directory")
    todo, already = [], 0
    for spec in lock():
        for rel, h in spec["files"].items():
            dest = models_dir / folder(spec["repo_id"]) / rel
            if dest.is_file() and sha_file(dest) == h:
                already += 1
            else:
                todo.append((spec, rel, h))
    names = {Path(rel).name for _, rel, _ in todo} | {h for _, _, h in todo}   # a cache blob is named by its sha256
    want, found, hashed = {h for _, _, h in todo}, {}, {}
    for dirpath, _dirs, files in os.walk(src):
        for name in files:
            if name not in names:
                continue
            try:
                real = (Path(dirpath) / name).resolve(strict=True)
            except OSError:
                continue
            if not real.is_file():
                continue
            if real not in hashed:
                hashed[real] = sha_file(real)
            if hashed[real] in want:
                found.setdefault(hashed[real], real)
    copied = 0
    for spec, rel, h in todo:
        if h in found:
            dest = models_dir / folder(spec["repo_id"]) / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(f".{dest.name}.import")
            shutil.copyfile(found[h], tmp)
            os.replace(tmp, dest)
            copied += 1
    log(f"{copied} file(s) copied, {already} already in place")
    missing = [(spec, rel) for spec, rel, h in todo if h not in found]
    if missing:
        raise ModelsError(f"{len(missing)} pinned file(s) not found under {src}: "
                          + "; ".join(f"{spec['repo_id']}: {rel} (from {tree_url(spec)})" for spec, rel in missing))
    return {"copied": copied, "already": already, "missing": [], "models": verify(models_dir)}
