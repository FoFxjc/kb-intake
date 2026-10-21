"""Step 8 of the pipeline (intake-design §3.1, §4): writing an intake package.

Layout: `{doc_id}@{sha256[:12]}/` with manifest.json, source.<ext>, working.pdf, pages.jsonl, layout.jsonl,
regions.jsonl, tables.jsonl, headings.jsonl, gaps.jsonl, docling.json (when the layout engine produced it),
checks.json.

Determinism: the same file with the same configuration gives byte-identical files; the only exception is the `run`
field of manifest.json (timestamps, host, durations, cache hits). JSON is written with a fixed key order.
A package is built in a hidden work directory next to its final place and moved there in one rename.
"""

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from kb_intake import contract

DATA_FILES = ("pages.jsonl", "layout.jsonl", "regions.jsonl", "tables.jsonl", "headings.jsonl", "gaps.jsonl")


class IntakeError(ValueError):
    pass


def name(doc_id: str, sha256: str) -> str:
    return f"{doc_id}@{sha256[:12]}"


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def write_jsonl(path: Path, rows) -> int:
    n = 0
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(dumps(r) + "\n")
            n += 1
    return n


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def existing(out: Path, doc_id: str) -> list[Path]:
    return sorted(p for p in out.glob(f"{doc_id}@*") if p.is_dir())


def source_sha(pkg: Path) -> str | None:
    """sha256 of the source a package was built from (None when its manifest cannot be read)."""
    try:
        return json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))["source"]["sha256"]
    except Exception:  # noqa: BLE001
        return None


class Builder:
    """A package under construction: `dir` is the package directory inside a work directory."""

    def __init__(self, out: Path, pkg_name: str):
        out.mkdir(parents=True, exist_ok=True)
        self.out, self.name = out, pkg_name
        self.work = Path(tempfile.mkdtemp(prefix=f".{pkg_name}.", dir=out))
        self.dir = self.work / pkg_name          # the final name, so the self-check sees the real package name
        self.dir.mkdir()

    def files(self, names) -> dict:
        return {f: {"sha256": sha_file(self.dir / f), "bytes": (self.dir / f).stat().st_size, "rows": n}
                for f, n in names.items()}

    def finish(self, manifest: dict, replace: list[Path]) -> Path:
        (self.dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n",
                                                encoding="utf-8")
        result = contract.check(self.dir, expected_name=self.name)
        (self.dir / "checks.json").write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        if not result["ok"]:
            raise IntakeError(f"package fails its own contract check: {result['errors'][:3]}")
        final = self.out / self.name
        # Move old packages aside inside the work directory first, so that a crash never leaves a half-deleted
        # package under the final name; the work directory is removed by close().
        for k, old in enumerate(p for p in [final, *replace] if p.exists()):
            os.replace(old, self.work / f"old-{k}")
        os.replace(self.dir, final)
        return final

    def close(self) -> None:
        shutil.rmtree(self.work, ignore_errors=True)
