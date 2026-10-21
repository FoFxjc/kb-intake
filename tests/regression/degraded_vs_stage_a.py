"""Full degradation (intake-design §9.4): packages built with the layout engine off (`scan --no-layout`) must equal
the stage-A packages (find_tables only, before the engine existed), with the field mapping decided for step C:

- tables.jsonl: the stage-A cell `bbox` (find_tables grid box) is now `grid_bbox`; `bbox` is the text box and `bbox_source`
  its origin (checked separately: inside the grid box with a source, or both null for an empty cell). New fields `role` and `context_above` are dropped
  before comparing (context_above is checked to be present). Everything else byte for byte, in order.
- pages.jsonl, layout.jsonl, regions.jsonl: byte for byte.
- headings.jsonl: not compared (stage A had none; heading candidates do not depend on the engine).
- gaps.jsonl: the stage-A gaps plus exactly one document-level `extractor_degraded` gap, nothing per table.
- manifest.json: declaration, source, detection and flow equal (steps and versions differ by design).

usage: python tests/regression/degraded_vs_stage_a.py <degraded packages dir> <stage-A packages dir>
"""

import json
import sys
from pathlib import Path


def rows(p: Path) -> list[dict]:
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()]


def dump(r) -> str:
    return json.dumps(r, ensure_ascii=False)


def compare(new: Path, old: Path) -> dict:
    out = {}
    for f in ("pages.jsonl", "layout.jsonl", "regions.jsonl"):
        out[f] = (new / f).read_bytes() == (old / f).read_bytes()
    nt, ot = rows(new / "tables.jsonl"), rows(old / "tables.jsonl")
    text_boxes_ok, context_ok, mapped = True, True, []
    for t in nt:
        context_ok &= isinstance(t.get("context_above"), list)
        t = {k: v for k, v in t.items() if k not in ("role", "context_above")}
        cells = []
        for c in t["cells"]:
            g, b = c["grid_bbox"], c["bbox"]
            if b is None:
                text_boxes_ok &= not (c["text"] or "").strip()
            else:
                text_boxes_ok &= g[0] - 1e-6 <= b[0] <= b[2] <= g[2] + 1e-6 and g[1] - 1e-6 <= b[1] <= b[3] <= g[3] + 1e-6
            text_boxes_ok &= c.get("bbox_source") in (("text", "words", "cell") if b is not None else (None,))
            c = {k: v for k, v in c.items() if k not in ("grid_bbox", "bbox_source")}
            c["bbox"] = g
            cells.append(c)
        mapped.append({**t, "cells": cells})
    out["tables.jsonl"] = [dump(r) for r in mapped] == [dump(r) for r in ot]
    out["tables_count"] = [len(nt), len(ot)]
    out["cell_text_boxes_inside_grid"] = text_boxes_ok
    out["context_above_present"] = context_ok
    ng, og = rows(new / "gaps.jsonl"), rows(old / "gaps.jsonl")
    doc_level = [g for g in ng if g["code"] == "extractor_degraded" and "page_no" not in g]
    rest = [g for g in ng if g not in doc_level]
    out["gaps.jsonl"] = len(doc_level) == 1 and doc_level[0].get("pages") == "all" and [dump(g) for g in rest] == [dump(g) for g in og]
    mn, mo = (json.loads((d / "manifest.json").read_text(encoding="utf-8")) for d in (new, old))
    out["manifest"] = all(mn.get(k) == mo.get(k) for k in ("declaration", "source", "detection", "flow"))
    out["ok"] = all(v for k, v in out.items() if k != "tables_count")
    return out


def main() -> int:
    new_dir, old_dir = Path(sys.argv[1]), Path(sys.argv[2])
    report = {}
    for old in sorted(p for p in old_dir.iterdir() if p.is_dir() and "@" in p.name and not p.name.startswith(".")):
        new = new_dir / old.name
        report[old.name] = compare(new, old) if new.is_dir() else {"ok": False, "missing": True}
    ok = all(r["ok"] for r in report.values())
    print(json.dumps({"ok": ok, "packages": report}, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
