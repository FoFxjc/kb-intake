"""Command line (intake-design-v2 §6):

    kb-intake models download [--to DIR]       pinned weights into $INTAKE_MODELS_DIR (default ~/.kb-intake/models)
    kb-intake models verify [--dir DIR]        check a models directory against the shipped lock
    kb-intake engines                          engine versions, models, dependency check (ready or not)
    kb-intake check <package> ...              contract v2 check of packages (directories or zips)
    kb-intake serve [--data DIR] [--port 8700] web UI + HTTP API; jobs run one at a time in this process

stdout carries JSON only (one object per line); errors are JSON on stderr. Hugging Face is put offline for every
command except `models download`, so nothing but that command can reach the network.
"""

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path



def _err(**kw) -> None:
    print(json.dumps({"ok": False, **kw}, ensure_ascii=False), file=sys.stderr)


def _config(args):
    from kb_intake import config
    cfg = config.load(args.config)
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
    import pymupdf

    from kb_intake.engines import DoclingEngine
    cfg = _config(args)
    infos = {stage: DoclingEngine(models_dir=cfg.models_dir, params=cfg.docling, stage=stage).info()
             for stage in ("label", "extract")}
    ready = all(i.ready for i in infos.values())
    print(json.dumps({"ok": ready, "docling": {k: asdict(v) for k, v in infos.items()},
                      "models_dir": str(cfg.models_dir), "pymupdf": pymupdf.VersionBind}, ensure_ascii=False))
    return 0 if ready else 1


def _serve(args) -> int:
    import uvicorn

    from kb_intake.service.app import create_app
    data = Path(args.data or os.environ.get("INTAKE_DATA_DIR") or "intake-data").expanduser()
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(json.dumps({"warning": f"listening on {args.host}: the UI and API have no authentication"}),
              file=sys.stderr, flush=True)
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

    v = sub.add_parser("serve", help="web UI and HTTP API")
    v.add_argument("--data", help="data directory: packages, uploads, intake.db, intake.yaml, cache "
                                  "(default: $INTAKE_DATA_DIR or ./intake-data)")
    v.add_argument("--host", default="127.0.0.1",
                   help="address to listen on (default 127.0.0.1; anything else must be given explicitly)")
    v.add_argument("--port", type=int, default=8700)
    v.set_defaults(fn=_serve)

    c = sub.add_parser("check", help="check packages (directory or zip) against contract v2")
    c.add_argument("package", nargs="+")
    c.set_defaults(fn=_check)

    args = ap.parse_args(argv)
    if args.cmd != "models":
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
