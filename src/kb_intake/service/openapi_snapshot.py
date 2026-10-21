"""The frozen OpenAPI document of the HTTP API (intake-ui-llm-design-v1 §3.5).

    uv run python -m kb_intake.service.openapi_snapshot           # exit 1 and print a diff when the API drifted
    uv run python -m kb_intake.service.openapi_snapshot --write   # update contract/api/openapi-v1.json

The document leaves out `info.version` (the package version changes with every release; the API does not).
"""

import difflib
import json
import sys
import tempfile
from pathlib import Path

from fastapi import FastAPI

SNAPSHOT = Path(__file__).resolve().parents[3] / "contract" / "api" / "openapi-v1.json"


def document(app: FastAPI) -> dict:
    doc = json.loads(json.dumps(app.openapi()))
    doc.get("info", {}).pop("version", None)
    return doc


def dump(doc: dict) -> str:
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def check(doc: dict) -> bool:
    """True when `doc` equals the snapshot; otherwise print a unified diff and return False."""
    want = SNAPSHOT.read_text(encoding="utf-8") if SNAPSHOT.is_file() else ""
    got = dump(doc)
    if got == want:
        return True
    sys.stdout.writelines(difflib.unified_diff(want.splitlines(True), got.splitlines(True),
                                               str(SNAPSHOT), "generated"))
    return False


def _app() -> FastAPI:
    from kb_intake.config import Config
    from kb_intake.service.app import create_app
    t = Path(tempfile.mkdtemp(prefix="kb-intake-openapi-"))
    return create_app(Config(models_dir=t / "models", cache_dir=t / "cache", work_dir=t / "work",
                             delivery_dir=t / "delivery", scan_roots=()))


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    doc = document(_app())
    if "--write" in argv:
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(dump(doc), encoding="utf-8")
        print(f"wrote {SNAPSHOT}")
        return 0
    return 0 if check(doc) else 1


if __name__ == "__main__":
    raise SystemExit(main())
