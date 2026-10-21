"""Determinism (intake-design §9.4): packages of the same files from different runs are byte-identical, except
manifest.json -> run. Compares every package of the first directory with the package of the same name in the others.

usage: python tests/regression/same_packages.py <packages dir A> <packages dir B> [<packages dir C> ...]
"""

import json
import sys
from pathlib import Path


def differences(a: Path, b: Path) -> list[str]:
    out = []
    names_a, names_b = sorted(p.name for p in a.iterdir()), sorted(p.name for p in b.iterdir())
    if names_a != names_b:
        out.append(f"files differ: {sorted(set(names_a) ^ set(names_b))}")
    for f in sorted(set(names_a) & set(names_b)):
        if f == "manifest.json":
            ma, mb = (json.loads((x / f).read_text(encoding="utf-8")) for x in (a, b))
            ma.pop("run", None), mb.pop("run", None)
            if ma != mb:
                out.append(f"manifest.json (outside run): {sorted(k for k in ma.keys() | mb.keys() if ma.get(k) != mb.get(k))}")
        elif (a / f).read_bytes() != (b / f).read_bytes():
            out.append(f)
    return out


def main() -> int:
    dirs = [Path(d) for d in sys.argv[1:]]
    first = dirs[0]
    report, ok = {}, True
    for pkg in sorted(p for p in first.iterdir() if p.is_dir() and "@" in p.name and not p.name.startswith(".")):
        row = {}
        for other in dirs[1:]:
            twin = other / pkg.name
            diff = differences(pkg, twin) if twin.is_dir() else ["missing"]
            row[str(other)] = diff or "identical"
            ok &= not diff
        layout = [json.loads((d / pkg.name / "manifest.json").read_text())["run"].get("layout") for d in dirs
                  if (d / pkg.name).is_dir()]
        row["layout_runs"] = layout
        report[pkg.name] = row
    print(json.dumps({"ok": ok, "packages": report}, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
