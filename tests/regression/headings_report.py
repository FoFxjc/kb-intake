"""Heading tree quality of one package, optionally against another package of the same file (e.g. a font profile
against automatic inference). Reports, per level: candidates (furniture excluded), furniture, the numbered sequence
(gaps and repeats in "N." numbering, subnotes whose parent note is missing), and the differences between the two.

usage: python tests/regression/headings_report.py <package> [<other package of the same file>]
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path


def load(pkg: Path) -> tuple[list[dict], dict]:
    rows = [json.loads(line) for line in (pkg / "headings.jsonl").read_text(encoding="utf-8").splitlines()]
    m = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
    params = next(s for s in m["steps"] if s["step"] == "headings")["params"]
    return rows, params


def quality(rows: list[dict]) -> dict:
    real = [r for r in rows if not r["furniture"]]
    by_level = Counter(r["level"] for r in real)
    notes = [int(r["numbering"].rstrip(".")) for r in real if r["numbering"] and re.fullmatch(r"\d+\.", r["numbering"])]
    subs = [r["numbering"] for r in real if r["numbering"] and re.fullmatch(r"\d+\.\d+(\.\d+)*", r["numbering"])]
    seq = {}
    if notes:
        span = range(min(notes), max(notes) + 1)
        seq = {"first": min(notes), "last": max(notes), "missing": [n for n in span if n not in notes],
               "repeated": sorted(n for n, k in Counter(notes).items() if k > 1),
               "out_of_order": sum(1 for a, b in zip(notes, notes[1:]) if b < a)}
    orphans = sorted({s for s in subs if int(s.split(".")[0]) not in set(notes)})
    return {"by_level": dict(sorted(by_level.items())), "furniture": sum(r["furniture"] for r in rows),
            "notes": seq, "subnotes": len(subs), "subnotes_without_parent_note": orphans[:20],
            "with_docling": sum("docling" in r["sources"] for r in real)}


def key(r: dict) -> tuple:
    return (r["page_no"], r["text"])


def main() -> int:
    a = Path(sys.argv[1])
    rows_a, params_a = load(a)
    out = {"package": a.name, "profile": params_a.get("profile"), "source": params_a.get("source"),
           "quality": quality(rows_a)}
    if len(sys.argv) > 2:
        b = Path(sys.argv[2])
        rows_b, params_b = load(b)
        out["other"] = {"package": str(b), "profile": params_b.get("profile"), "source": params_b.get("source"),
                        "quality": quality(rows_b)}
        la = {key(r): r for r in rows_a if not r["furniture"]}
        lb = {key(r): r for r in rows_b if not r["furniture"]}
        diff = {}
        for level in sorted({r["level"] for r in rows_a + rows_b}):
            sa = {k for k, r in la.items() if r["level"] == level}
            sb = {k for k, r in lb.items() if r["level"] == level}
            diff[level] = {"both": len(sa & sb), "only_first": len(sa - sb), "only_other": len(sb - sa),
                           "examples_only_first": sorted(sa - sb)[:8], "examples_only_other": sorted(sb - sa)[:8]}
        out["by_level_diff"] = diff
        out["level_changed"] = sorted((k, la[k]["level"], lb[k]["level"]) for k in la.keys() & lb.keys()
                                      if la[k]["level"] != lb[k]["level"])[:20]
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
