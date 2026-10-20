"""Contract v1 check of an intake package (`kb-intake check`).

The JSON Schemas live in `contract/v1/` of this repository (shipped inside the wheel as kb_intake/contract);
kb keeps a copy and compares it with a script. Besides the schemas, the check verifies what a schema cannot:
the package name, file hashes against the manifest, and page numbering.
"""

import hashlib
import json
import re
import zipfile
from functools import cache
from pathlib import Path

from jsonschema import Draft202012Validator

from kb_intake import CONTRACT_VERSION

ROWS = {"pages.jsonl": "page", "layout.jsonl": "layout", "regions.jsonl": "region", "tables.jsonl": "table",
        "headings.jsonl": "heading", "gaps.jsonl": "gap"}
NAME = re.compile(r"(?P<doc_id>[a-z0-9-]+_[a-z][a-z0-9_]*_\d{4}_(?:en|zh))@(?P<sha>[0-9a-f]{12})")
MAX_ERRORS = 50


def schema_dir() -> Path:
    here = Path(__file__).resolve().parent
    for d in (here / "contract" / "v1", here.parents[1] / "contract" / "v1"):   # wheel, then source checkout
        if d.is_dir():
            return d
    raise FileNotFoundError("contract/v1 schemas not found")


@cache
def validator(kind: str) -> Draft202012Validator:
    schema = json.loads((schema_dir() / f"{kind}.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _schema_errors(kind: str, obj, where: str) -> list[str]:
    return [f"{where}: {'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message[:200]}"
            for e in validator(kind).iter_errors(obj)]


def check(pkg: Path, expected_name: str | None = None, require_checks: bool = False) -> dict:
    """Check one package directory. `require_checks`: also validate checks.json (a finished package has one;
    the builder calls this before writing it)."""
    pkg = Path(pkg)
    errors: list[str] = []
    counts: dict[str, int] = {}

    def add(errs):
        errors.extend(errs[: max(0, MAX_ERRORS - len(errors))])

    m = NAME.fullmatch(pkg.name)
    if not m:
        add([f"package name {pkg.name!r} is not <doc_id>@<sha256[:12]>"])
    if expected_name and pkg.name != expected_name:
        add([f"package name {pkg.name!r} != expected {expected_name!r}"])
    mpath = pkg / "manifest.json"
    if not mpath.is_file():
        return {"contract_version": CONTRACT_VERSION, "ok": False, "errors": errors + ["manifest.json missing"], "counts": {}}
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    add(_schema_errors("manifest", manifest, "manifest.json"))
    if manifest.get("contract_version") != CONTRACT_VERSION:
        return {"contract_version": CONTRACT_VERSION, "ok": False,
                "errors": errors + [f"contract_version {manifest.get('contract_version')!r} is not {CONTRACT_VERSION!r}"],
                "counts": {}}
    if m and (manifest.get("doc_id") != m["doc_id"] or not str(manifest.get("source", {}).get("sha256", "")).startswith(m["sha"])):
        add(["package name does not match manifest doc_id / source sha256"])
    if manifest.get("package") != pkg.name:
        add([f"manifest package {manifest.get('package')!r} != directory name {pkg.name!r}"])

    files = manifest.get("files") or {}
    for f in ("working.pdf", f"source.{manifest.get('source', {}).get('ext')}", *ROWS):
        if f not in files:
            add([f"manifest.files lacks {f}"])
    for f, meta in files.items():
        p = pkg / f
        if not p.is_file():
            add([f"{f}: listed in manifest but missing"])
            continue
        if _sha(p) != meta.get("sha256"):
            add([f"{f}: sha256 does not match manifest"])
    src = pkg / f"source.{manifest.get('source', {}).get('ext')}"
    if src.is_file() and _sha(src) != manifest.get("source", {}).get("sha256"):
        add(["source file sha256 does not match manifest.source"])

    page_nos: dict[str, list[int]] = {}
    for f, kind in ROWS.items():
        p = pkg / f
        if not p.is_file():
            continue
        n = 0
        with p.open(encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                n += 1
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as e:
                    add([f"{f}:{i}: not JSON ({e.msg})"])
                    continue
                if len(errors) < MAX_ERRORS:
                    add(_schema_errors(kind, row, f"{f}:{i}"))
                if kind in ("page", "layout") and isinstance(row, dict):
                    page_nos.setdefault(f, []).append(row.get("page_no"))
        counts[f] = n
        if (files.get(f) or {}).get("rows") not in (None, n):
            add([f"{f}: {n} rows but manifest says {files[f]['rows']}"])
    expected_pages = list(range(1, (manifest.get("detection") or {}).get("page_count", 0) + 1))
    for f in ("pages.jsonl", "layout.jsonl"):
        if page_nos.get(f, []) != expected_pages:
            add([f"{f}: page_no must run 1..page_count without gaps"])

    if require_checks:
        cpath = pkg / "checks.json"
        if not cpath.is_file():
            add(["checks.json missing"])
        else:
            add(_schema_errors("checks", json.loads(cpath.read_text(encoding="utf-8")), "checks.json"))
    return {"contract_version": CONTRACT_VERSION, "ok": not errors, "errors": errors, "counts": counts}


def check_path(path: Path) -> dict:
    """Check a package directory or a zip that holds one package directory."""
    path = Path(path)
    if path.is_file() and zipfile.is_zipfile(path):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(path) as z:
            z.extractall(tmp)
            dirs = [p for p in Path(tmp).iterdir() if p.is_dir()]
            if len(dirs) != 1:
                return {"contract_version": CONTRACT_VERSION, "ok": False,
                        "errors": ["zip must contain exactly one package directory"], "counts": {}}
            return check(dirs[0], require_checks=True)
    return check(path, require_checks=True)
