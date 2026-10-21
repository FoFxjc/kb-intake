"""`kb-intake scan <file|dir>` (intake-design §1, §3.2): one package per supported file.

- A directory is read at its top level (recursively with `recursive`); files are recognised by their magic bytes,
  not their extension; hidden files are ignored.
- A file whose sha256 already has a package in the output directory is skipped (whatever doc_id it was packaged
  under); `replace` rebuilds it.
- The declaration of each file is the directory declaration's defaults + the file's override (kb_intake.declaration).
  A file whose declaration is incomplete is skipped and listed; the other files go on.
- Two files of one scan may not share a doc_id (e.g. "A b.pdf" and "a-b.pdf" slug alike): the second fails.
- One result per file: ok | skipped_same_sha | skipped_no_declaration | failed. A failure never stops the scan.
  Overrides in the declaration that match no scanned file are reported at the end (status "unused_overrides").
"""

import hashlib
from pathlib import Path

from kb_intake import declaration, detect, package, pipeline
from kb_intake.config import Config


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def files_of(target: Path, recursive: bool = False) -> list[Path]:
    if target.is_file():
        return [target]
    found = sorted(target.rglob("*") if recursive else target.iterdir())
    out = []
    for p in found:
        if not p.is_file() or any(part.startswith(".") for part in p.relative_to(target).parts):
            continue
        with p.open("rb") as f:
            if detect.sniff(f.read(8)):
                out.append(p)
    return out


def packaged(out: Path) -> dict[str, str]:
    """sha256 of the source -> package name, for the packages already in `out`."""
    index = {}
    if out.is_dir():
        for p in sorted(out.iterdir()):
            if p.is_dir() and "@" in p.name and not p.name.startswith("."):
                sha = package.source_sha(p)
                if sha:
                    index[sha] = p.name
    return index


def scan(target: Path, decl_raw: dict | None, out: Path, config: Config | None = None, replace: bool = False,
         recursive: bool = False, force: bool = False):
    """Yield one result dict per file."""
    root = target if target.is_dir() else None
    index = packaged(out)
    used_ids: dict[str, str] = {}
    seen: set[str] = set()
    for path in files_of(target, recursive):
        rel = path.relative_to(root).as_posix() if root else path.name
        result = {"file": rel}
        seen.add(rel)
        try:
            sha = _sha(path)
            if sha in index and not replace:
                yield {**result, "status": "skipped_same_sha", "package": index[sha]}
                continue
            decl = declaration.for_file(decl_raw, path, root, sha)
            missing = [k for k in declaration.REQUIRED if decl.get(k) in (None, "")]
            if missing:
                yield {**result, "status": "skipped_no_declaration", "missing": missing}
                continue
            if decl["doc_id"] in used_ids:
                yield {**result, "status": "failed", "error": "declaration", "param": "doc_id",
                       "message": f"doc_id {decl['doc_id']!r} is already used by {used_ids[decl['doc_id']]} in this scan"}
                continue
            used_ids[decl["doc_id"]] = rel
            pkg = pipeline.process(path, decl, out, config, replace=replace, force=force)
            index[sha] = pkg.name
            yield {**result, "status": "ok", "package": pkg.name}
        except declaration.DeclarationError as e:
            yield {**result, "status": "failed", "error": "declaration", "param": e.param, "message": str(e)}
        except Exception as e:  # noqa: BLE001 - one file never stops the scan
            yield {**result, "status": "failed", "error": type(e).__name__, "message": str(e)[:500]}
    if declaration.is_directory_form(decl_raw):
        unused = sorted(set(decl_raw.get("files") or {}) - seen)
        if unused:
            yield {"status": "unused_overrides", "files": unused}
