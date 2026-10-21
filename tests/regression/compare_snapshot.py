"""Compare a contract v2 package with the Docling reference snapshot (intake-design-v2 §7, regression).

The snapshot is the docling-eval run on OCBC AR 2025 en: 24 tables whose every number was checked against the text
layer (tables/*.csv and tables_extra/*.csv, file names t<index>_p<page>.csv). For each snapshot table the package
must have a table on that page (tables.jsonl `page_no`) whose body numbers (cells that are not column headers; cell
text is read from the text layer) are the same multiset as the snapshot body. Also checked:
- p106 "NPAs by Period Overdue": a `table_suspected` gap on page 106 from find_tables, and a table on page 106 with
  extract_method "find_tables" whose region was reclassified in review (label_source "user");
- p216 -> p217 liquidity gap table: v2 never merges tables; the first table on page 217 must carry a
  `continued_hint` pointing at a table on page 216.

usage: python tests/regression/compare_snapshot.py --package <pkg dir> --snapshot ~/docling-eval
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


def header_rows(t: dict) -> int:
    """Rows above the body: down to the last row holding a header cell. A table with no header cell (find_tables
    knows no headers) takes its first row as the header, as the snapshot CSVs do."""
    return max((c["r"] + c["rs"] for c in t["cells"] if c["is_header"]), default=1)


def read(pkg: Path, name: str) -> list[dict]:
    return [json.loads(line) for line in (pkg / name).open(encoding="utf-8") if line.strip()]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--package", required=True)
    ap.add_argument("--snapshot", required=True)
    a = ap.parse_args()
    pkg = Path(a.package)
    tables, gaps, regions = read(pkg, "tables.jsonl"), read(pkg, "gaps.jsonl"), read(pkg, "regions.jsonl")
    by_region = {r["region_id"]: r for r in regions}
    results, ok = [], True
    for s in snapshot_tables(Path(a.snapshot).expanduser()):
        cands = [t for t in tables if t["page_no"] == s["page"]]
        best = None
        for t in cands:
            hdr = header_rows(t)            # rows above the body count as header, flagged or not
            body = numbers(c["text"] for c in t["cells"] if not c["is_header"] and c["r"] >= hdr)
            score = sum((body & s["body"]).values())
            if best is None or score > best[0]:
                best = (score, t, body)
        if best is None:
            results.append({"snapshot": s["name"], "ok": False, "reason": "no table on the page"})
            ok = False
            continue
        _, t, body = best
        missing, extra = s["body"] - body, body - s["body"]
        same = not missing and not extra
        ok &= same
        results.append({"snapshot": s["name"], "table_id": t["table_id"], "extract_method": t["extract_method"],
                        "numbers": sum(s["body"].values()), "ok": same,
                        "missing": sorted(missing.elements())[:10], "extra": sorted(extra.elements())[:10]})
    p106_gap = any(g["code"] == "table_suspected" and g.get("page_no") == 106 and g.get("source") == "find_tables"
                   for g in gaps)
    p106_table = [t["table_id"] for t in tables if t["page_no"] == 106 and t["extract_method"] == "find_tables"
                  and by_region.get(t["region_id"], {}).get("label_source") == "user"]
    p106_ok = p106_gap and bool(p106_table)
    first_217 = next((t for t in tables if t["page_no"] == 217), None)
    p216_ids = {t["table_id"] for t in tables if t["page_no"] == 216}
    continued = first_217 is not None and first_217.get("continued_hint") in p216_ids
    ok &= p106_ok and continued
    print(json.dumps({"ok": ok, "tables": results, "p106_table_suspected_find_tables": p106_ok,
                      "p106_tables": p106_table,
                      "p217_continues_p216": first_217["continued_hint"] if continued else None},
                     ensure_ascii=False, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
