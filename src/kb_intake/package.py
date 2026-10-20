"""Build an intake package (contract v1, intake-design §4) from one file and its declaration.

Layout of a package: `{doc_id}@{sha256[:12]}/` with manifest.json, source.<ext>, working.pdf, pages.jsonl,
layout.jsonl, regions.jsonl, tables.jsonl, headings.jsonl, gaps.jsonl, checks.json.

Determinism: the same file with the same configuration gives byte-identical files; the only exception is the
`run` field of manifest.json (timestamps, host). JSON is written with a fixed key order and no sorting surprises.
"""

import hashlib
import json
import os
import platform
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import pymupdf

from kb_intake import CONTRACT_VERSION, __version__, assemble, contract, declaration, detect, docling_run, headings, native
from kb_intake.config import Config

DATA_FILES = ("pages.jsonl", "layout.jsonl", "regions.jsonl", "tables.jsonl", "headings.jsonl", "gaps.jsonl")


class IntakeError(ValueError):
    pass


def package_name(doc_id: str, sha256: str) -> str:
    return f"{doc_id}@{sha256[:12]}"


def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _write_jsonl(path: Path, rows) -> int:
    n = 0
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(_dumps(r) + "\n")
            n += 1
    return n


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _existing(out: Path, doc_id: str) -> list[Path]:
    return sorted(p for p in out.glob(f"{doc_id}@*") if p.is_dir())


def run(file: Path, decl: dict, out: Path, replace: bool = False, config: Config | None = None) -> Path:
    """Run the intake for one file and write its package under `out`. Returns the package directory.

    Dedup (moved from kb registry.register): the same doc_id with the same file is idempotent and rewrites the
    package; the same doc_id with a different file is refused unless `replace` is given.
    With a Docling configuration the tables, regions and headings come from the 3b extraction layer (§9)."""
    config = config or Config()
    started, t0 = datetime.now(timezone.utc), time.monotonic()
    decl = declaration.validate(decl)
    data = file.read_bytes()
    kind = detect.sniff(data)
    if kind is None:
        raise declaration.DeclarationError("file", "unsupported file type (flow A needs a PDF)")
    sha = hashlib.sha256(data).hexdigest()
    name = package_name(decl["doc_id"], sha)
    out.mkdir(parents=True, exist_ok=True)
    others = [p for p in _existing(out, decl["doc_id"]) if p.name != name]
    if others and not replace:
        raise IntakeError(f"{decl['doc_id']} already has a package from a different file: {others[0].name}")

    work = Path(tempfile.mkdtemp(prefix=f".{name}.", dir=out))
    tmp = work / name           # build under the final name so the self-check sees the real package name
    tmp.mkdir()
    try:
        detection, kinds = detect.detect_pdf(str(file), decl["lang"])
        labels = detection.pop("page_labels")
        gaps: list[dict] = []
        if not detection["has_text_layer"]:
            gaps.append({"code": "no_text_layer", "detail": "the PDF has no text layer; flow B (OCR) is not implemented"})
        if not detection["lang_matches"]:
            gaps.append({"code": "lang_mismatch", "declared": decl["lang"], "detected": detection["detected_lang"]})
        if "page_kinds_error" in detection:
            gaps.append({"code": "extractor_degraded", "extractor": "page_kinds", "detail": detection["page_kinds_error"]})

        (tmp / f"source.{kind}").write_bytes(data)
        (tmp / "working.pdf").write_bytes(data)        # a native PDF is its own working PDF
        by_page = {k["page_no"]: k for k in kinds}
        pages_rows, layout_rows, tables = [], [], []
        with pymupdf.open(tmp / "working.pdf") as pdf:
            for i, page in enumerate(pdf):
                pages_rows.append(native.page_record(page, i + 1, labels[i], by_page.get(i + 1)))
            for i, page in enumerate(pdf):
                layout_rows.append(native.layout_record(page, i + 1))
                t, g = native.tables_on(page, decl["doc_id"], i + 1)
                tables += t
                gaps += g
        regions_rows, heading_rows, extra_steps, cross = [], [], [], {}
        files_extra = {}
        if config.docling is not None:
            regions_rows, heading_rows, tables, extra_steps, cross, g = _extraction_layer(
                tmp, decl["doc_id"], sha, config.docling, tables, layout_rows, pages_rows)
            gaps += g
            files_extra = {"docling.json": None} if (tmp / "docling.json").exists() else {}
        counts = {"pages.jsonl": _write_jsonl(tmp / "pages.jsonl", pages_rows),
                  "layout.jsonl": _write_jsonl(tmp / "layout.jsonl", layout_rows),
                  "regions.jsonl": _write_jsonl(tmp / "regions.jsonl", regions_rows),
                  "tables.jsonl": _write_jsonl(tmp / "tables.jsonl", tables),
                  "headings.jsonl": _write_jsonl(tmp / "headings.jsonl", heading_rows),
                  "gaps.jsonl": _write_jsonl(tmp / "gaps.jsonl", gaps)}

        files = {f"source.{kind}": None, "working.pdf": None, **files_extra, **{f: counts[f] for f in DATA_FILES}}
        manifest = {
            "contract_version": CONTRACT_VERSION,
            "package": name,
            "doc_id": decl["doc_id"],
            "declaration": decl,
            "source": {"filename": file.name, "ext": kind, "media_type": "application/pdf", "sha256": sha,
                       "bytes": len(data)},
            "detection": detection,
            "flow": detect.FLOWS[kind],
            "steps": [
                {"step": "detect", "tool": "pymupdf", "version": pymupdf.VersionBind, "params": {}},
                {"step": "page_labels", "tool": "kb_intake.labels", "version": __version__,
                 "params": {"method": detection["page_label_method"]["method"]}},
                {"step": "text_layer", "tool": "pymupdf", "version": pymupdf.VersionBind, "params": {}},
                {"step": "words", "tool": "pymupdf", "version": pymupdf.VersionBind, "params": {}},
                {"step": "tables", "tool": "pymupdf.find_tables", "version": pymupdf.VersionBind,
                 "params": {"primary": native.TABLE_METHOD, "secondary": None,
                            "pymupdf_layout": native.PYMUPDF_LAYOUT}},
                *(extra_steps or [
                    {"step": "regions", "tool": None, "version": None, "params": {}, "status": "not_run"},
                    {"step": "headings", "tool": None, "version": None, "params": {}, "status": "not_run"}]),
            ],
            "files": {f: {"sha256": _sha(tmp / f), "bytes": (tmp / f).stat().st_size, "rows": n}
                      for f, n in files.items()},
            "cross_checks": cross,
            "degraded": any(g["code"] == "extractor_degraded" for g in gaps),
            "llm": {"used": False},
            "kb_intake_version": __version__,
            "run": {},
        }
        finished = datetime.now(timezone.utc)
        manifest["run"] = {"started_at": started.isoformat(), "finished_at": finished.isoformat(),
                           "duration_ms": int((time.monotonic() - t0) * 1000),
                           "python": sys.version.split()[0], "platform": platform.platform()}
        (tmp / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        result = contract.check(tmp, expected_name=name)
        (tmp / "checks.json").write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        if not result["ok"]:
            raise IntakeError(f"package fails its own contract check: {result['errors'][:3]}")

        final = out / name
        # Move the old package(s) aside inside the work directory first, so that a crash never leaves a half-deleted
        # package under the final name; the work directory is removed below (review nit 1).
        for k, old in enumerate(p for p in [final, *(others if replace else [])] if p.exists()):
            os.replace(old, work / f"old-{k}")
        os.replace(tmp, final)
        return final
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _extraction_layer(tmp: Path, doc_id: str, sha: str, cfg, ft_tables: list[dict], layout_rows: list[dict],
                      pages_rows: list[dict]):
    """Docling + post-processing + checks + headings (§9.2). A Docling failure degrades to find_tables; the text
    layer products written before this step are never touched (§9.3.6)."""
    res = docling_run.run(tmp / "working.pdf", sha, cfg)
    gaps = []
    if res.docling_json is not None:
        (tmp / "docling.json").write_bytes(res.docling_json)
    if not res.ok:
        gaps.append({"code": "extractor_degraded", "extractor": "docling", "status": res.status,
                     "detail": "; ".join(res.errors)[:500] or "Docling did not succeed",
                     "failed_pages": res.failed_pages})
    layout_model = docling_run.MODELS[0]["repo_id"]
    layout_version = docling_run.MODELS[0]["revision"]
    with pymupdf.open(tmp / "working.pdf") as pdf:
        font_heads, _ = headings.headings(pdf, [])          # context for continued tables (no Docling needed)
        out = assemble.assemble(doc_id, res.docling_json, res.failed_pages if res.docling_json else [],
                                ft_tables, layout_rows, pdf, layout_model, layout_version, res.confidence_pages,
                                font_heads)
        heads = [r for r in out["regions"] if r["type"] in ("title", "heading")]
        heading_rows, heading_summary = headings.headings(pdf, heads)     # with Docling as corroboration
    for p in pages_rows:
        c = res.confidence_pages.get(str(p["page_no"]))
        p["docling_confidence"] = c["grade"] if c else None
    steps = [
        {"step": "docling", "tool": "docling", "version": res.versions.get("docling"), "params": cfg.params(),
         "versions": res.versions, "models": res.models, "cache_key": res.cache_key, "cached": res.cached,
         "status": res.status, "seconds": res.seconds if not res.cached else None,
         "failed_pages": res.failed_pages},
        {"step": "regions", "tool": "kb_intake.docling_post", "version": __version__,
         "params": {"layout_model": layout_model, "layout_version": layout_version},
         "counts": dict(sorted(__import__("collections").Counter(r["type"] for r in out["regions"]).items()))},
        {"step": "headings", "tool": "kb_intake.headings", "version": __version__, "params": heading_summary},
        {"step": "tables_3b", "tool": "kb_intake.assemble", "version": __version__,
         "params": {"primary": "docling", "secondary": native.TABLE_METHOD}, "summary": out["summary"]},
    ]
    return out["regions"], heading_rows, out["tables"], steps, out["summary"], gaps + out["gaps"]
