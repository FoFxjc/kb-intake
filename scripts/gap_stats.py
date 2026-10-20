"""Gap and table statistics over a directory of packages (for the 3b report).

usage: python scripts/gap_stats.py <packages dir>
"""

import json
import sys
from collections import Counter
from pathlib import Path


def main() -> None:
    root = Path(sys.argv[1])
    total, out = Counter(), {}
    for pkg in sorted(p for p in root.iterdir() if p.is_dir() and "@" in p.name):
        gaps = Counter(json.loads(line)["code"] for line in (pkg / "gaps.jsonl").open(encoding="utf-8"))
        tables = [json.loads(line) for line in (pkg / "tables.jsonl").open(encoding="utf-8")]
        heads = [json.loads(line) for line in (pkg / "headings.jsonl").open(encoding="utf-8")]
        regions = Counter(json.loads(line)["type"] for line in (pkg / "regions.jsonl").open(encoding="utf-8"))
        m = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
        dl = next((s for s in m["steps"] if s["step"] == "docling"), {})
        hs = next((s for s in m["steps"] if s["step"] == "headings"), {}).get("params", {})
        out[pkg.name.split("@")[0]] = {
            "gaps": dict(sorted(gaps.items())),
            "tables_primary": dict(Counter(t["extract_method"] for t in tables if t.get("role", "primary") == "primary")),
            "tables_secondary": sum(t.get("role") == "secondary" for t in tables),
            "headings": {"profile": hs.get("profile"), "source": hs.get("source"), "by_level": hs.get("by_level"),
                         "furniture": sum(h["furniture"] for h in heads)},
            "regions": dict(sorted(regions.items())),
            "docling": {"status": dl.get("status"), "cached": dl.get("cached"), "failed_pages": dl.get("failed_pages")},
            "degraded": m["degraded"],
        }
        total += gaps
    print(json.dumps({"total_gaps": dict(sorted(total.items())), "packages": out}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
