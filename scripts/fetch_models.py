"""Fetch the pinned Docling model weights into an artifacts directory and write models.lock.json.

The revisions come from kb_intake.docling_run.MODELS. Files are taken from the local Hugging Face cache when present
(set HF_HUB_OFFLINE=1 to forbid the network), otherwise downloaded once; at run time kb-intake only reads
artifacts_path and checks every file against the lock.

usage: python scripts/fetch_models.py <artifacts_path>
"""

import hashlib
import json
import shutil
import sys
from pathlib import Path

from huggingface_hub import snapshot_download

from kb_intake.docling_run import LOCK, MODELS, folder


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    out = Path(sys.argv[1]).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    lock = []
    for spec in MODELS:
        dest = out / folder(spec["repo_id"])
        # The snapshot of this exact revision (from the local cache, or downloaded once), copied as plain files
        snap = Path(snapshot_download(repo_id=spec["repo_id"], revision=spec["revision"]))
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(snap, dest, symlinks=False)
        files = {str(p.relative_to(dest)): sha(p) for p in sorted(dest.rglob("*")) if p.is_file()}
        lock.append({**spec, "files": files})
        print(spec["repo_id"], spec["revision"], len(files), "files")
    (out / LOCK).write_text(json.dumps({"models": lock}, indent=1) + "\n")


if __name__ == "__main__":
    main()
