"""Compare a contract v2 package with the Docling reference snapshot, cell by cell (intake-design-v2 §7, regression).

The snapshot is the docling-eval run on OCBC AR 2025 en: 24 tables (tables/*.csv, tables_extra/*.csv, file names
t<index>_p<page>.csv) whose every number was checked against the text layer. Each CSV is Docling's DataFrame export:
the rows above the body are collapsed into the column names, the body rows follow; tables_check.json gives each
table's shape (body rows x columns).

For each snapshot table, the package table on the same page with the same number of columns and at least as many
rows (the fewest extra rows first, then reading order) is compared position by position: the snapshot's body rows
are the last rows of the package grid, the rows above them are its header (a spanning cell's text fills every
position it covers, as in the DataFrame):
- numbers: the numeric tokens of every body cell must be exactly the same, in order. Any difference fails.
- text: every other difference is listed, not failed. v2 reads cell text from the text layer, where the snapshot has
  Docling's text; the expected differences are the en dash "\\u2013" that Docling rewrote as "-", and header or label
  text that Docling's cell matching cut or merged. Column names (the header rows joined per column) are listed the
  same way.
Also checked (and failing when absent):
- p106 "NPAs by Period Overdue": a `table_suspected` gap on page 106 from find_tables, and a table on page 106 with
  extract_method "find_tables" whose region was reclassified in review (label_source "user");
- p216 -> p217 liquidity gap table: the first table on page 217 carries a `continued_hint` ({table_id, reason})
  pointing at a table on page 216.

usage: python tests/regression/compare_snapshot.py --package <pkg dir> --snapshot ~/docling-eval
Prints one JSON object; exit 0 when every number matches and both checks hold.
"""

import argparse
import csv
import json
import re
import sys
from pathlib import Path

NUM_IN = re.compile(r"(?<![\w.])\(?[-+\u2212\u2013]?\d[\d,]*(?:\.\d+)?\)?%?(?![\w])")
DASHES = {"-", "–", "—"}


def tokens(text: str | None) -> list[str]:
    """The numbers of a cell, in order (thousands separators and parentheses kept as written); a number is found
    wherever it is not glued to a letter, so a note marker written "FVOCI,(1)" in the text layer and "FVOCI, (1)" by
    Docling counts the same."""
    return [m.replace("\u2212", "-") for m in NUM_IN.findall((text or "").replace("\x07", ""))]


def norm(text: str | None) -> str:
    return " ".join((text or "").replace("\x07", "").split())


def snapshot_tables(root: Path) -> list[dict]:
    out = []
    for sub in ("tables", "tables_extra"):
        checks = {c["idx"]: c for c in json.loads((root / sub / "tables_check.json").read_text(encoding="utf-8"))}
        for f in sorted((root / sub).glob("t*_p*.csv")):
            m = re.match(r"t(\d+)_p(\d+)\.csv", f.name)
            idx = int(m[1])
            rows = list(csv.reader(f.open(encoding="utf-8")))
            c = checks[idx]
            out.append({"name": f.stem, "idx": idx, "page": int(m[2]), "columns": rows[0], "body": rows[1:],
                        "shape": c["shape"]})
    return sorted(out, key=lambda s: (s["page"], s["idx"]))


def grid(t: dict) -> list[list[str | None]]:
    g: list[list[str | None]] = [[None] * t["n_cols"] for _ in range(t["n_rows"])]
    for c in t["cells"]:
        for r in range(c["r"], min(t["n_rows"], c["r"] + c["rs"])):
            for k in range(c["c"], min(t["n_cols"], c["c"] + c["cs"])):
                g[r][k] = c["text"]
    return g


def column_names(g: list[list], header_rows: int) -> list[str]:
    """The header rows joined per column with "." (Docling's DataFrame naming), repeated parts dropped."""
    names = []
    for k in range(len(g[0]) if g else 0):
        parts = []
        for r in range(header_rows):
            t = norm(g[r][k])
            if t and (not parts or parts[-1] != t):
                parts.append(t)
        names.append(".".join(parts))
    return names


def read(pkg: Path, name: str) -> list[dict]:
    return [json.loads(line) for line in (pkg / name).open(encoding="utf-8") if line.strip()]


def compare(s: dict, t: dict) -> dict:
    g = grid(t)
    hr = t["n_rows"] - len(s["body"])          # the package rows above the snapshot's body
    numbers_ok, number_diffs, text_diffs, dash_cells, cells = True, [], [], 0, 0
    for i, srow in enumerate(s["body"]):
        for k, scell in enumerate(srow):
            ours = g[hr + i][k]
            cells += 1
            a, b = tokens(scell), tokens(ours)
            if a != b:
                numbers_ok = False
                number_diffs.append({"row": i, "col": k, "snapshot": scell, "package": ours})
            elif norm(scell) != norm(ours):
                if norm(scell) in DASHES and norm(ours) in DASHES:
                    dash_cells += 1
                else:
                    text_diffs.append({"row": i, "col": k, "snapshot": norm(scell), "package": norm(ours)})
    names = column_names(g, hr)
    header_diffs = [{"col": k, "snapshot": norm(a), "package": b}
                    for k, (a, b) in enumerate(zip(s["columns"], names)) if norm(a) != b]
    return {"snapshot": s["name"], "table_id": t["table_id"], "extract_method": t["extract_method"],
            "header_rows": hr, "cells": cells, "numeric_cells": sum(1 for r in s["body"] for x in r if tokens(x)),
            "numbers_ok": numbers_ok, "number_diffs": number_diffs[:20], "dash_cells": dash_cells,
            "text_diffs": text_diffs, "header_diffs": header_diffs}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--package", required=True)
    ap.add_argument("--snapshot", required=True)
    a = ap.parse_args()
    pkg = Path(a.package)
    tables, gaps, regions = read(pkg, "tables.jsonl"), read(pkg, "gaps.jsonl"), read(pkg, "regions.jsonl")
    by_region = {r["region_id"]: r for r in regions}
    results, ok, used = [], True, set()
    for s in snapshot_tables(Path(a.snapshot).expanduser()):
        rows, cols = len(s["body"]), s["shape"][1]
        cands = [t for t in tables if t["page_no"] == s["page"] and t["table_id"] not in used
                 and t["n_cols"] == cols and t["n_rows"] >= rows]
        t = min(cands, key=lambda t: t["n_rows"] - rows, default=None)     # min keeps reading order on ties
        if t is None:
            ok = False
            results.append({"snapshot": s["name"], "numbers_ok": False,
                            "reason": f"no unused table with {cols} columns and >= {rows} rows on page {s['page']}",
                            "tables_on_page": [(x["table_id"], x["n_rows"], x["n_cols"]) for x in tables
                                               if x["page_no"] == s["page"]]})
            continue
        used.add(t["table_id"])
        r = compare(s, t)
        ok &= r["numbers_ok"]
        results.append(r)
    p106_gap = any(g["code"] == "table_suspected" and g.get("page_no") == 106 and g.get("source") == "find_tables"
                   for g in gaps)
    p106_table = [t["table_id"] for t in tables if t["page_no"] == 106 and t["extract_method"] == "find_tables"
                  and by_region.get(t["region_id"], {}).get("label_source") == "user"]
    p106_ok = p106_gap and bool(p106_table)
    first_217 = next((t for t in tables if t["page_no"] == 217), None)
    p216_ids = {t["table_id"] for t in tables if t["page_no"] == 216}
    hint = (first_217 or {}).get("continued_hint") or {}
    continued = hint.get("table_id") in p216_ids
    ok &= p106_ok and continued
    summary = {"tables": len(results), "numbers_ok": sum(1 for r in results if r["numbers_ok"]),
               "numeric_cells": sum(r.get("numeric_cells", 0) for r in results),
               "dash_cells": sum(r.get("dash_cells", 0) for r in results),
               "text_diffs": sum(len(r.get("text_diffs", [])) for r in results),
               "header_diffs": sum(len(r.get("header_diffs", [])) for r in results)}
    print(json.dumps({"ok": ok, "summary": summary, "p106_table_suspected_find_tables": p106_ok,
                      "p106_tables": p106_table, "p217_continues_p216": hint if continued else None,
                      "tables": results}, ensure_ascii=False, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
