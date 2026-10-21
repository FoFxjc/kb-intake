"""Read side of the service: the package list and the data of one page for the page-by-page review
(intake-design §3.2 UI view 3): page image, regions with types, table cells (primary and secondary), words,
heading candidates and the page's gaps. Everything comes from the package files; nothing is recomputed."""

import json
import re
from functools import lru_cache
from pathlib import Path

import pymupdf

from kb_intake import contract

NAME = re.compile(r"[a-z0-9][a-z0-9._-]*@[0-9a-f]{12}")


class NotFound(LookupError):
    pass


def package_dir(packages: Path, name: str) -> Path:
    if not NAME.fullmatch(name):
        raise NotFound(name)
    p = packages / name
    if not (p / "manifest.json").is_file():
        raise NotFound(name)
    return p


def _rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


@lru_cache(maxsize=8)
def _load(pkg: str, mtime: float) -> dict:
    p = Path(pkg)
    return {"manifest": json.loads((p / "manifest.json").read_text(encoding="utf-8")),
            **{k: _rows(p / f"{k}.jsonl") for k in ("pages", "layout", "regions", "tables", "tables_secondary",
                                                     "headings", "gaps")}}


def load(pkg: Path) -> dict:
    return _load(str(pkg), (pkg / "manifest.json").stat().st_mtime)


def summary(pkg: Path) -> dict:
    m = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
    checks = json.loads((pkg / "checks.json").read_text(encoding="utf-8")) if (pkg / "checks.json").is_file() else {}
    gaps = _rows(pkg / "gaps.jsonl")
    layout = next((s for s in m.get("steps", []) if s["step"] == "layout"), {})
    counts: dict[str, int] = {}
    for g in gaps:
        counts[g["code"]] = counts.get(g["code"], 0) + 1
    return {"package": pkg.name, "doc_id": m["doc_id"], "source": m["source"]["filename"],
            "pages": m["detection"]["page_count"], "lang": m["declaration"].get("lang"),
            "degraded": m["degraded"], "layout_status": layout.get("status"),
            "tables": (m.get("cross_checks") or {}).get("tables_primary"), "gaps": dict(sorted(counts.items())),
            "check_ok": checks.get("ok"), "finished_at": m["run"].get("finished_at")}


def packages(root: Path) -> list[dict]:
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.iterdir()):
        if p.is_dir() and NAME.fullmatch(p.name) and (p / "manifest.json").is_file():
            try:
                out.append(summary(p))
            except Exception as e:  # noqa: BLE001 - a broken package is listed, not fatal
                out.append({"package": p.name, "error": f"{type(e).__name__}: {e}"})
    return sorted(out, key=lambda r: r.get("finished_at") or "", reverse=True)


def page(pkg: Path, page_no: int) -> dict:
    d = load(pkg)
    m = d["manifest"]
    n = m["detection"]["page_count"]
    if not 1 <= page_no <= n:
        raise NotFound(f"page {page_no}")
    prow = d["pages"][page_no - 1]
    table_ids = set()
    tables = []
    for t in [*d["tables"], *({**x, "role": "secondary"} for x in d["tables_secondary"])]:
        if page_no not in t["pages"]:
            continue
        table_ids.add(t["table_id"])
        box = next(b for p, b in zip(t["pages"], t["bboxes"]) if p == page_no)
        cells = [{k: c.get(k) for k in ("r", "c", "rs", "cs", "text", "bbox", "bbox_source", "grid_bbox", "is_header",
                                         "nil")}
                 for c in t["cells"] if c.get("page_no", t["pages"][0]) == page_no]
        tables.append({"table_id": t["table_id"], "role": t.get("role", "primary"),
                       "extract_method": t["extract_method"], "pages": t["pages"], "bbox": box,
                       "n_rows": t["n_rows"], "n_cols": t["n_cols"], "header_rows": t["header_rows"],
                       "checks": t["checks"], "context_above": t.get("context_above", []), "cells": cells})
    gaps = [g for g in d["gaps"] if g.get("page_no") == page_no or (g.get("table_id") in table_ids)]
    doc_gaps = [g for g in d["gaps"] if "page_no" not in g and "table_id" not in g]
    gap_pages = sorted({g["page_no"] for g in d["gaps"] if g.get("page_no")})
    return {"package": pkg.name, "page_no": page_no, "page_count": n, "page_label": prow.get("page_label"),
            "kind": prow.get("kind"), "width": prow["width"], "height": prow["height"],
            "docling_confidence": prow.get("docling_confidence"), "text": prow["text"],
            "regions": [r for r in d["regions"] if r["page_no"] == page_no],
            "tables": tables, "words": [w[:5] for w in d["layout"][page_no - 1]["words"]],
            "headings": [h for h in d["headings"] if h["page_no"] == page_no],
            "gaps": gaps, "document_gaps": doc_gaps, "pages_with_gaps": gap_pages,
            "image": f"/v1/packages/{pkg.name}/pages/{page_no}/image"}


def page_image(pkg: Path, page_no: int, cache: Path, dpi: int = 110) -> Path:
    """The page rendered from working.pdf (cached as PNG)."""
    dpi = max(50, min(200, dpi))
    out = cache / "pages" / pkg.name / f"{page_no:04d}-{dpi}.png"
    if not out.is_file():
        out.parent.mkdir(parents=True, exist_ok=True)
        with pymupdf.open(pkg / "working.pdf") as pdf:
            if not 1 <= page_no <= len(pdf):
                raise NotFound(f"page {page_no}")
            tmp = out.with_suffix(".tmp")
            pdf[page_no - 1].get_pixmap(dpi=dpi).save(tmp, output="png")
            tmp.replace(out)
    return out


def check(path: Path) -> dict:
    return contract.check_path(path)
