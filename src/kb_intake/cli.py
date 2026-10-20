"""Command line: `kb-intake run` and `kb-intake check` (intake-design §3)."""

import argparse
import json
import os
import sys
from pathlib import Path

import yaml

from kb_intake import contract, declaration, package


def _run(args) -> int:
    out = args.out or os.environ.get("KB_PACKAGES_DIR")
    if not out:
        print("error: give --out or set KB_PACKAGES_DIR", file=sys.stderr)
        return 2
    try:
        decl = yaml.safe_load(Path(args.decl).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        print(json.dumps({"ok": False, "error": "declaration", "param": "decl", "message": f"{type(e).__name__}: {e}"}),
              file=sys.stderr)
        return 1
    if not Path(args.file).is_file():
        print(json.dumps({"ok": False, "error": "file", "message": f"no such file: {args.file}"}), file=sys.stderr)
        return 1
    try:
        pkg = package.run(Path(args.file), decl, Path(out), replace=args.replace)
    except declaration.DeclarationError as e:
        print(json.dumps({"ok": False, "error": "declaration", "param": e.param, "message": str(e)}), file=sys.stderr)
        return 1
    except package.IntakeError as e:
        print(json.dumps({"ok": False, "error": "intake", "message": str(e)}), file=sys.stderr)
        return 1
    manifest = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
    print(json.dumps({"ok": True, "package": str(pkg), "pages": manifest["detection"]["page_count"],
                      "rows": {f: m["rows"] for f, m in manifest["files"].items() if m["rows"] is not None},
                      "duration_ms": manifest["run"]["duration_ms"]}, ensure_ascii=False))
    return 0


def _check(args) -> int:
    worst = 0
    for p in args.package:
        result = contract.check_path(Path(p))
        print(json.dumps({"package": p, **result}, ensure_ascii=False))
        worst = max(worst, 0 if result["ok"] else 1)
    return worst


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kb-intake", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="build the intake package of one file")
    r.add_argument("file")
    r.add_argument("--decl", required=True, help="declaration YAML (doc_id, issuer_id, doc_type, period_end, lang, "
                                                 "source_url, completeness)")
    r.add_argument("--out", help="directory that receives packages (default: $KB_PACKAGES_DIR)")
    r.add_argument("--replace", action="store_true", help="replace a package of the same doc_id built from another file")
    r.set_defaults(fn=_run)
    c = sub.add_parser("check", help="check packages (directory or zip) against contract v1")
    c.add_argument("package", nargs="+")
    c.set_defaults(fn=_check)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
