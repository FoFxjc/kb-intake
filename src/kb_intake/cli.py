"""Command line (intake-design §3.2):

    kb-intake models download [--to DIR]       pinned weights into $INTAKE_MODELS_DIR (default ~/.kb-intake/models)
    kb-intake models verify [--dir DIR]        check a models directory against the shipped lock
    kb-intake engines                          engine versions, models, dependency check (ready or not)
    kb-intake scan <file|dir> --decl d.yaml    one package per file into --out (default $INTAKE_PACKAGES_DIR or
                                               ./packages); already packaged files are skipped
    kb-intake check <package> ...              contract v1 check of packages (directories or zips)
    kb-intake serve [--data DIR] [--port 8700] web UI + HTTP API; jobs run one at a time in this process

stdout carries JSON only (one object per line); errors are JSON on stderr. Hugging Face is put offline for every
command except `models download`, so nothing but that command can reach the network.
"""

import argparse
import json
import os
import sys
from dataclasses import asdict, replace
from pathlib import Path

import yaml


def _err(**kw) -> None:
    print(json.dumps({"ok": False, **kw}, ensure_ascii=False), file=sys.stderr)


def _config(args):
    from kb_intake import config
    cfg = config.load(args.config)
    if getattr(args, "no_layout", False):
        cfg = replace(cfg, layout_enabled=False)
    return cfg


def _models(args) -> int:
    from kb_intake import config, models
    cfg = config.load(args.config)
    target = Path(args.to or args.dir).expanduser() if (args.to or args.dir) else cfg.models_dir
    try:
        if args.action == "download":
            pins = models.download(target, log=lambda m: print(m, file=sys.stderr))
        else:
            pins = models.verify(target)
    except Exception as e:  # noqa: BLE001
        _err(error="models", message=f"{type(e).__name__}: {e}")
        return 1
    print(json.dumps({"ok": True, "models_dir": str(target), "models": pins}))
    return 0


def _engines(args) -> int:
    from kb_intake.engines import LAYOUT
    cfg = _config(args)
    info = LAYOUT[cfg.primary](models_dir=cfg.models_dir, params=cfg.docling).info()
    import pymupdf
    print(json.dumps({"ok": info.ready, "layout": asdict(info), "models_dir": str(cfg.models_dir),
                      "pymupdf": pymupdf.VersionBind, "secondary": cfg.secondary}, ensure_ascii=False))
    return 0 if info.ready else 1


def _scan(args) -> int:
    from kb_intake import scan
    target = Path(args.target)
    if not target.exists():
        _err(error="file", message=f"no such file or directory: {target}")
        return 2
    try:
        decl = yaml.safe_load(Path(args.decl).read_text(encoding="utf-8")) if args.decl else None
        cfg = _config(args)
    except Exception as e:  # noqa: BLE001
        _err(error="config" if not args.decl else "declaration", message=f"{type(e).__name__}: {e}")
        return 2
    out = Path(args.out or os.environ.get("INTAKE_PACKAGES_DIR") or "packages").expanduser()
    worst = 0
    for result in scan.scan(target, decl, out, cfg, replace=args.replace, recursive=args.recursive, force=args.force):
        print(json.dumps(result, ensure_ascii=False), flush=True)
        worst = max(worst, 1 if result["status"] == "failed" else 0)
    return worst


def _serve(args) -> int:
    import uvicorn

    from kb_intake.service.app import create_app
    data = Path(args.data or os.environ.get("INTAKE_DATA_DIR") or "intake-data").expanduser()
    print(json.dumps({"ok": True, "url": f"http://{args.host}:{args.port}/", "data": str(data.resolve())}),
          flush=True)
    uvicorn.run(create_app(data), host=args.host, port=args.port, log_level="warning")
    return 0


def _check(args) -> int:
    from kb_intake import contract
    worst = 0
    for p in args.package:
        result = contract.check_path(Path(p))
        print(json.dumps({"package": p, **result}, ensure_ascii=False))
        worst = max(worst, 0 if result["ok"] else 1)
    return worst


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kb-intake", description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("models", help="download or verify the pinned model weights")
    m.add_argument("action", choices=["download", "verify"])
    m.add_argument("--to", help="target directory for download (default: models_dir)")
    m.add_argument("--dir", help="directory to verify (default: models_dir)")
    m.add_argument("--config", help="intake.yaml (default: $INTAKE_CONFIG)")
    m.set_defaults(fn=_models)

    e = sub.add_parser("engines", help="engine versions, models and dependency check")
    e.add_argument("--config", help="intake.yaml (default: $INTAKE_CONFIG)")
    e.set_defaults(fn=_engines)

    s = sub.add_parser("scan", help="build packages for a file or every supported file of a directory")
    s.add_argument("target")
    s.add_argument("--decl", help="declaration YAML (plain, or defaults + files for a directory)")
    s.add_argument("--out", help="directory that receives packages (default: $INTAKE_PACKAGES_DIR or ./packages)")
    s.add_argument("--config", help="intake.yaml (default: $INTAKE_CONFIG)")
    s.add_argument("--replace", action="store_true", help="rebuild packages that already exist")
    s.add_argument("--recursive", action="store_true", help="also scan subdirectories")
    s.add_argument("--force", action="store_true",
                   help="debugging: run the layout engine although it is not ready (recorded in the manifest)")
    s.add_argument("--no-layout", action="store_true",
                   help="debugging: skip the layout engine; the package is the fully degraded one")
    s.set_defaults(fn=_scan)

    v = sub.add_parser("serve", help="web UI and HTTP API")
    v.add_argument("--data", help="data directory: packages, uploads, intake.db, intake.yaml, cache "
                                  "(default: $INTAKE_DATA_DIR or ./intake-data)")
    v.add_argument("--host", default="127.0.0.1")
    v.add_argument("--port", type=int, default=8700)
    v.set_defaults(fn=_serve)

    c = sub.add_parser("check", help="check packages (directory or zip) against contract v1")
    c.add_argument("package", nargs="+")
    c.set_defaults(fn=_check)

    args = ap.parse_args(argv)
    if args.cmd != "models":
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
