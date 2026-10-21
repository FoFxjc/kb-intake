"""Pinned model weights (intake-design §7, §9.3.2): `kb-intake models download` and `kb-intake models verify`.

`models.lock.json` ships inside the package: for each model the Hugging Face repo, the revision (a commit sha) and
the sha256 of every file. `download` fetches exactly that revision and refuses files that do not match; `verify`
checks a directory, e.g. one copied from a machine with network access. At run time only the local directory is read;
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


def verify(models_dir: Path) -> list[dict]:
    """Check every pinned file under models_dir; raise ModelsError listing what is missing or different."""
    problems = []
    for spec in lock():
        problems += _check_dir(models_dir / folder(spec["repo_id"]), spec)
    if problems:
        raise ModelsError(f"models in {models_dir} are not the pinned weights: " + "; ".join(problems[:5])
                          + (" (run `kb-intake models download`)" if any("missing" in p for p in problems) else ""))
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
