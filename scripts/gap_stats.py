"""Gap, table and cell-box statistics over a directory of packages (for the reports).

usage: python scripts/gap_stats.py <packages dir>
"""

import json
import sys
from collections import Counter
from pathlib import Path


def _cell_heavy(tables: list[dict], share: float = 0.10, minimum: int = 3) -> list[dict]:
    """Primary tables where bbox_source "cell" (no text found for the cell) is at least `share` of the non-empty
    cells and at least `minimum` cells: the cell box stands in for the text box there."""
    out = []
    for t in tables:
        if t.get("role", "primary") != "primary":
            continue
        filled = [c for c in t["cells"] if c.get("bbox") is not None]
        n = sum(c.get("bbox_source") == "cell" for c in filled)
        if filled and n >= minimum and n / len(filled) >= share:
            out.append({"table_id": t["table_id"], "extract_method": t["extract_method"], "cell": n,
                        "filled": len(filled), "share": round(n / len(filled), 3)})
    return sorted(out, key=lambda r: -r["share"])


def main() -> None:
    root = Path(sys.argv[1])
    total, out = Counter(), {}
    for pkg in sorted(p for p in root.iterdir() if p.is_dir() and "@" in p.name):
        rows = [json.loads(line) for line in (pkg / "gaps.jsonl").open(encoding="utf-8")]
        gaps = Counter(g["code"] for g in rows)
        detail = Counter(f"{g['code']}:{g.get('resolution') or g.get('source') or '-'}" for g in rows)
        tables = [json.loads(line) for line in (pkg / "tables.jsonl").open(encoding="utf-8")]
        heads = [json.loads(line) for line in (pkg / "headings.jsonl").open(encoding="utf-8")]
        regions = Counter(json.loads(line)["type"] for line in (pkg / "regions.jsonl").open(encoding="utf-8"))
        m = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
        dl = next((s for s in m["steps"] if s["step"] == "layout"), {})
        hs = next((s for s in m["steps"] if s["step"] == "headings"), {}).get("params", {})
        out[pkg.name.split("@")[0]] = {
            "gaps": dict(sorted(gaps.items())),
            "gaps_detail": dict(sorted(detail.items())),
            "tables_primary": dict(Counter(t["extract_method"] for t in tables if t.get("role", "primary") == "primary")),
            "tables_secondary": sum(t.get("role") == "secondary" for t in tables),
            "bbox_source": dict(sorted(Counter(c.get("bbox_source") or "empty" for t in tables
                                               if t.get("role", "primary") == "primary" for c in t["cells"]).items())),
            "tables_with_many_cell_boxes": _cell_heavy(tables),
            "headings": {"profile": hs.get("profile"), "source": hs.get("source"), "by_level": hs.get("by_level"),
                         "furniture": sum(h["furniture"] for h in heads)},
            "regions": dict(sorted(regions.items())),
            "layout": {"status": dl.get("status"), "failed_pages": dl.get("failed_pages"), **(m["run"].get("layout") or {})},
            "degraded": m["degraded"],
        }
        total += gaps
    print(json.dumps({"total_gaps": dict(sorted(total.items())), "packages": out}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
