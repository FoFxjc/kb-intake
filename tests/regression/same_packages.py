"""Determinism (intake-design-v2 §5, §7): packages of the same source, workflow and review ops from different runs are
byte-identical, except these fields, which are exempt:

- manifest.json: `run`, `workflow.approved_at`, `status.confirmed_at`, and the `files` entries (sha256, bytes) of
  review.jsonl and workflow.json, which hold the times below;
- review.jsonl: `at` of every row;
- workflow.json: `approved_at`.

Every other file must be byte-identical. Compares every package of the first directory with the package of the
same name in the others.

usage: python tests/regression/same_packages.py <packages dir A> <packages dir B> [<packages dir C> ...]
"""

import json
import sys
from pathlib import Path

TIMED_FILES = ("review.jsonl", "workflow.json")


def _files(pkg: Path) -> list[str]:
    return sorted(p.relative_to(pkg).as_posix() for p in pkg.rglob("*") if p.is_file())


def _manifest(p: Path) -> dict:
    m = json.loads(p.read_text(encoding="utf-8"))
    m.pop("run", None)
    (m.get("workflow") or {}).pop("approved_at", None)
    (m.get("status") or {}).pop("confirmed_at", None)
    for f in TIMED_FILES:
        entry = (m.get("files") or {}).get(f)
        if isinstance(entry, dict):
            entry.pop("sha256", None)
            entry.pop("bytes", None)
    return m


def _review(p: Path) -> list[dict]:
    rows = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [{k: v for k, v in r.items() if k != "at"} for r in rows]


def _workflow(p: Path) -> dict:
    w = json.loads(p.read_text(encoding="utf-8"))
    w.pop("approved_at", None)
    return w


EXEMPT = {"manifest.json": _manifest, "review.jsonl": _review, "workflow.json": _workflow}


def differences(a: Path, b: Path) -> list[str]:
    """What differs between package directories a and b outside the exempt fields; empty when they are the same."""
    a, b = Path(a), Path(b)
    out = []
    names_a, names_b = _files(a), _files(b)
    if names_a != names_b:
        out.append(f"files differ: {sorted(set(names_a) ^ set(names_b))}")
    for f in sorted(set(names_a) & set(names_b)):
        if f in EXEMPT:
            va, vb = EXEMPT[f](a / f), EXEMPT[f](b / f)
            if va != vb:
                if isinstance(va, dict) and isinstance(vb, dict):
                    keys = sorted(k for k in va.keys() | vb.keys() if va.get(k) != vb.get(k))
                    out.append(f"{f} (outside the exempt fields): {keys}")
                else:
                    out.append(f"{f} (outside the exempt fields)")
        elif (a / f).read_bytes() != (b / f).read_bytes():
            out.append(f)
    return out


def main() -> int:
    dirs = [Path(d) for d in sys.argv[1:]]
    if len(dirs) < 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    first = dirs[0]
    report, ok = {}, True
    for pkg in sorted(p for p in first.iterdir() if p.is_dir() and "@" in p.name and not p.name.startswith(".")):
        row = {}
        for other in dirs[1:]:
            twin = other / pkg.name
            diff = differences(pkg, twin) if twin.is_dir() else ["missing"]
            row[str(other)] = diff or "identical"
            ok &= not diff
        row["cache_hits"] = [json.loads((d / pkg.name / "manifest.json").read_text())["run"].get("cache_hits")
                             for d in dirs if (d / pkg.name).is_dir()]
        report[pkg.name] = row
    print(json.dumps({"ok": ok, "packages": report}, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
