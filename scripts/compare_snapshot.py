"""Compare a package with the Docling reference snapshot (intake-design §9.3.4 / §9.4).

The snapshot is the docling-eval run on OCBC AR 2025 en: 24 tables whose every number was checked against the text
layer (tables/*.csv and tables_extra/*.csv, file names t<index>_p<page>.csv). For each snapshot table the package
must have a primary table on that page whose body numbers (cells that are not column headers) are the same multiset
as the snapshot body. Also checked:
- p106 "NPAs by Period Overdue": a `table_missed` gap resolved with find_tables, and that table entered as primary;
- p216 -> p217 liquidity gap table: one primary table with extract_method "merged" covering both pages.

usage: python scripts/compare_snapshot.py --package <pkg dir> --snapshot ~/docling-eval
"""

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

NUM = re.compile(r"^\(?[-+−]?\d[\d,]*(?:\.\d+)?\)?%?$")


def numbers(texts) -> Counter:
    out = Counter()
    for t in texts:
        for tok in (t or "").replace("\x07", "").split():
            tok = tok.strip(".,;:")
            if NUM.match(tok):
                out[tok.strip("()%").replace("−", "-").lstrip("+")] += 1
    return out


def snapshot_tables(root: Path) -> list[dict]:
    out = []
    for f in sorted(list((root / "tables").glob("t*_p*.csv")) + list((root / "tables_extra").glob("t*_p*.csv"))):
        m = re.match(r"t(\d+)_p(\d+)\.csv", f.name)
        rows = list(csv.reader(f.open(encoding="utf-8")))
        out.append({"name": f.stem, "page": int(m[2]), "body": numbers(x for r in rows[1:] for x in r)})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--package", required=True)
    ap.add_argument("--snapshot", required=True)
    a = ap.parse_args()
    pkg = Path(a.package)
    tables = [json.loads(line) for line in (pkg / "tables.jsonl").open(encoding="utf-8")]
    gaps = [json.loads(line) for line in (pkg / "gaps.jsonl").open(encoding="utf-8")]
    primary = [t for t in tables if t.get("role", "primary") == "primary"]
    results, ok = [], True
    merged = [t for t in primary if t["extract_method"] == "merged" and t["pages"][:2] == [216, 217]]
    for s in snapshot_tables(Path(a.snapshot).expanduser()):
        cands = [t for t in primary if s["page"] in t["pages"]]
        best = None
        for t in cands:
            hdr = t.get("header_rows") or 0      # rows above the body count as header, flagged or not
            body = numbers(c["text"] for c in t["cells"] if not c["is_header"] and c["r"] >= hdr
                           and c.get("page_no", s["page"]) == s["page"])
            score = sum((body & s["body"]).values())
            if best is None or score > best[0]:
                best = (score, t, body)
        if best is None:
            results.append({"snapshot": s["name"], "ok": False, "reason": "no primary table on the page"})
            ok = False
            continue
        _, t, body = best
        missing, extra = s["body"] - body, body - s["body"]
        same = not missing and not extra
        if s["page"] in (216, 217):     # the merged table holds both blocks; compare the page's own cells
            same = same and bool(merged)
        ok &= same
        results.append({"snapshot": s["name"], "table_id": t["table_id"], "extract_method": t["extract_method"],
                        "numbers": sum(s["body"].values()), "ok": same,
                        "missing": sorted(missing.elements())[:10], "extra": sorted(extra.elements())[:10]})
    p106 = [g for g in gaps if g["code"] == "table_missed" and g.get("page_no") == 106]
    p106_ok = any(g.get("resolution") == "find_tables" for g in p106) and any(
        t["pages"] == [106] and t["extract_method"] == "find_tables" for t in primary)
    ok &= p106_ok and bool(merged)
    print(json.dumps({"ok": ok, "tables": results, "p106_table_missed_find_tables": p106_ok,
                      "p216_217_merged": [t["table_id"] for t in merged]}, ensure_ascii=False, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
