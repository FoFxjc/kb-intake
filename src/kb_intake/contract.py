"""Contract v2 check of a delivered package (`kb-intake check`, intake-design-v2 §5; llm.jsonl, intake-ui-llm-design-v1
§4.4).

The JSON Schemas live in `contract/v2/` of this repository (shipped inside the wheel as kb_intake/contract).
Besides the schemas, the check verifies what a schema cannot: the package name, the files and their hashes against
the manifest, page numbering, the links between regions and tables, and that manifest.stats agrees with the rows.
"""

import json
import re
import zipfile
from collections import Counter
from functools import cache
from pathlib import Path

from jsonschema import Draft202012Validator

from kb_intake import CONTRACT_VERSION
from kb_intake.package import sha_file

ROWS = {"pages.jsonl": "page", "layout.jsonl": "layout", "regions.jsonl": "region", "tables.jsonl": "table",
        "gaps.jsonl": "gap", "review.jsonl": "review"}
REQUIRED_FILES = ("source.pdf", *ROWS, "workflow.json")
OPTIONAL_FILES = ("raw/docling.json", "raw/docling.extract.json", "llm.jsonl")
OPTIONAL_ROWS = {"llm.jsonl": "llm"}
NAME = re.compile(r"(?P<doc_id>[a-z0-9][a-z0-9._-]*)@(?P<sha>[0-9a-f]{12})")
MAX_ERRORS = 50


def schema_dir() -> Path:
    here = Path(__file__).resolve().parent
    for d in (here / "contract" / "v2", here.parents[1] / "contract" / "v2"):   # wheel, then source checkout
        if d.is_dir():
            return d
    raise FileNotFoundError("contract/v2 schemas not found")


@cache
def validator(kind: str) -> Draft202012Validator:
    schema = json.loads((schema_dir() / f"{kind}.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def schema_errors(kind: str, obj, where: str) -> list[str]:
    return [f"{where}: {'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message[:200]}"
            for e in validator(kind).iter_errors(obj)]


def check(pkg: Path) -> dict:
    """Check one package directory: {"contract_version", "ok", "errors", "counts"}."""
    pkg = Path(pkg)
    errors: list[str] = []
    counts: dict[str, int] = {}

    def add(errs):
        errors.extend(errs[: max(0, MAX_ERRORS - len(errors))])

    def result() -> dict:
        return {"contract_version": CONTRACT_VERSION, "ok": not errors, "errors": errors, "counts": counts}

    m = NAME.fullmatch(pkg.name)
    if not m:
        add([f"package name {pkg.name!r} is not <doc_id>@<sha256[:12]>"])
    try:
        manifest = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
    except OSError:
        add(["manifest.json missing"])
        return result()
    except ValueError as e:
        add([f"manifest.json is not JSON ({e})"])
        return result()
    if not isinstance(manifest, dict):
        add(["manifest.json is not an object"])
        return result()
    if manifest.get("contract_version") != CONTRACT_VERSION:
        add([f"contract_version {manifest.get('contract_version')!r} is not {CONTRACT_VERSION!r}"])
        return result()
    add(schema_errors("manifest", manifest, "manifest.json"))
    source = manifest.get("source") or {}
    if m and (manifest.get("doc_id") != m["doc_id"] or not str(source.get("sha256", "")).startswith(m["sha"])):
        add(["package name does not match manifest doc_id / source sha256"])
    if manifest.get("package") != pkg.name:
        add([f"manifest package {manifest.get('package')!r} != directory name {pkg.name!r}"])

    files = manifest.get("files") or {}
    for f in REQUIRED_FILES:
        if f not in files:
            add([f"manifest.files lacks {f}"])
    for f in files:
        if f not in REQUIRED_FILES and f not in OPTIONAL_FILES:
            add([f"manifest.files lists {f}, which is not a contract v2 file"])
    on_disk = sorted(p.relative_to(pkg).as_posix() for p in pkg.rglob("*") if p.is_file())
    for f in on_disk:
        if f != "manifest.json" and f not in files:
            add([f"{f}: in the package but not listed in manifest.files"])
    root = pkg.resolve()
    for f, meta in files.items():
        p = pkg / f
        if not p.resolve().is_relative_to(root):
            add([f"{f}: listed file is outside the package"])
            continue
        if not isinstance(meta, dict):
            continue                    # already reported by the schema
        if not p.is_file():
            add([f"{f}: listed in manifest but missing"])
            continue
        if sha_file(p) != meta.get("sha256"):
            add([f"{f}: sha256 does not match manifest"])
        if p.stat().st_size != meta.get("bytes"):
            add([f"{f}: size does not match manifest"])
    if (pkg / "source.pdf").is_file() and sha_file(pkg / "source.pdf") != source.get("sha256"):
        add(["source.pdf sha256 does not match manifest.source"])

    rows: dict[str, list] = {}
    for f, kind in {**ROWS, **OPTIONAL_ROWS}.items():
        p = pkg / f
        if not p.is_file():
            continue
        rows[f] = []
        with p.open(encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as e:
                    add([f"{f}:{i}: not JSON ({e.msg})"])
                    continue
                if len(errors) < MAX_ERRORS:
                    add(schema_errors(kind, row, f"{f}:{i}"))
                rows[f].append(row)
        counts[f] = len(rows[f])
        if (files.get(f) or {}).get("rows") not in (None, len(rows[f])):
            add([f"{f}: {len(rows[f])} rows but manifest says {files[f]['rows']}"])
    try:
        add(schema_errors("workflow", json.loads((pkg / "workflow.json").read_text(encoding="utf-8")),
                          "workflow.json"))
    except OSError:
        pass                            # reported as missing above
    except ValueError as e:
        add([f"workflow.json is not JSON ({e})"])

    add(_consistency(manifest, rows))
    return result()


def _consistency(manifest: dict, rows: dict[str, list]) -> list[str]:
    errs = []
    page_count = (manifest.get("metadata") or {}).get("page_count") or 0
    for f in ("pages.jsonl", "layout.jsonl"):
        if f in rows and [r.get("page_no") for r in rows[f]] != list(range(1, page_count + 1)):
            errs.append(f"{f}: page_no must run 1..page_count without gaps")
    regions, tables, gaps = rows.get("regions.jsonl", []), rows.get("tables.jsonl", []), rows.get("gaps.jsonl", [])
    ids = [r.get("region_id") for r in regions]
    if len(set(ids)) != len(ids):
        errs.append("regions.jsonl: region_id is not unique")
    if [r.get("reading_order") for r in regions] != list(range(len(regions))):
        errs.append("regions.jsonl: rows must be in reading_order 0..n-1")
    by_table = {t.get("table_id"): t for t in tables}
    if len(by_table) != len(tables):
        errs.append("tables.jsonl: table_id is not unique")
    by_region = {r.get("region_id"): r for r in regions}
    for r in regions:
        tid = r.get("table_id")
        if tid is not None and (tid not in by_table or by_table[tid].get("region_id") != r.get("region_id")):
            errs.append(f"regions.jsonl: {r.get('region_id')} points at table {tid}, which does not point back")
    for t in tables:
        if (by_region.get(t.get("region_id")) or {}).get("table_id") != t.get("table_id"):
            errs.append(f"tables.jsonl: {t.get('table_id')} belongs to a region that does not point back")
    for row in rows.get("llm.jsonl", []):
        if row.get("region_id") is not None and row.get("region_id") not in by_region:
            errs.append(f"llm.jsonl: {row.get('kind')} names region {row.get('region_id')}, which is not in "
                        "regions.jsonl")
    stats = manifest.get("stats") or {}
    if stats.get("regions_by_type") != dict(sorted(Counter(r.get("type") for r in regions).items())):
        errs.append("manifest.stats.regions_by_type does not match regions.jsonl")
    if stats.get("regions_by_status") != dict(sorted(Counter(r.get("status") for r in regions).items())):
        errs.append("manifest.stats.regions_by_status does not match regions.jsonl")
    if stats.get("tables") != len(tables):
        errs.append("manifest.stats.tables does not match tables.jsonl")
    if stats.get("gaps_by_code") != dict(sorted(Counter(g.get("code") for g in gaps).items())):
        errs.append("manifest.stats.gaps_by_code does not match gaps.jsonl")
    return errs


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
            return check(dirs[0])
    return check(path)
