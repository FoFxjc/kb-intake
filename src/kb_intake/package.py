"""JSON and JSONL as packages and the work directory write them: UTF-8, one fixed serialisation, LF line ends.
The same rows always give the same bytes (intake-design-v2 §5 determinism)."""

import hashlib
import json
import os
import tempfile
from pathlib import Path


def name(doc_id: str, sha256: str) -> str:
    """The package directory name: {doc_id}@{first 12 hex digits of the source sha256}."""
    return f"{doc_id}@{sha256[:12]}"


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def write_jsonl(path: Path, rows) -> int:
    """Write rows atomically (temporary file + rename); return the number of rows."""
    n = 0
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            for r in rows:
                f.write(dumps(r) + "\n")
                n += 1
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return n


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_json(path: Path, obj) -> None:
    """Indented JSON, written atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(obj, ensure_ascii=False, indent=1) + "\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
