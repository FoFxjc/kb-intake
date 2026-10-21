"""Content-addressed cache of LLM answers (intake-ui-llm-design-v1 §4).

One JSON file per key at `root/key[:2]/key.json`. The key hashes everything that determines the answer (provider,
model, prompt version, input hash), so a hit can be reused without a call and a changed prompt or input is a miss.
Writes go to a temporary file that is renamed into place, so concurrent writers of one key leave one complete file;
a file that cannot be read as a JSON object counts as a miss.
"""

import hashlib
import json
import re
from pathlib import Path

from kb_intake.package import write_json

_KEY = re.compile(r"[0-9a-f]{2,128}")


def cache_key(provider: str, model: str, prompt_version: str, input_sha256: str) -> str:
    """sha256 hex over a canonical JSON list of the parts (a list, so parts cannot shift across fields)."""
    canon = json.dumps([provider, model, prompt_version, input_sha256], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


class Cache:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        if not _KEY.fullmatch(key):  # keys are lowercase hex; anything else could escape root
            raise ValueError(f"bad cache key: {key!r}")
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> dict | None:
        path = self._path(key)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):  # missing, unreadable, not UTF-8 or not JSON
            return None
        return value if isinstance(value, dict) else None

    def put(self, key: str, value: dict) -> None:
        write_json(self._path(key), value)
