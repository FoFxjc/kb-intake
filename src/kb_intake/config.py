"""intake.yaml (intake-design §3). Only the parts used by the CLI flow are read here; everything has a default.

    tables:
      primary: docling          # or find_tables (step 2 behaviour, no Docling)
    docling:
      table_mode: accurate
      device: cpu               # formal packages use cpu; mps only for local debugging (recorded in the manifest)
      num_threads: 4
      artifacts_path: ~/intake-data/models   # pinned weights + models.lock.json (scripts/fetch_models.py)
      cache_dir: ~/intake-data/cache
      document_timeout: 1800
"""

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from kb_intake.docling_run import DoclingConfig


@dataclass
class Config:
    docling: DoclingConfig | None = None      # None: find_tables only (contract-compatible step 2 output)


def load(path: str | None) -> Config:
    path = path or os.environ.get("KB_INTAKE_CONFIG")
    if not path:
        return Config()
    raw = yaml.safe_load(Path(path).expanduser().read_text(encoding="utf-8")) or {}
    if (raw.get("tables") or {}).get("primary", "find_tables") != "docling":
        return Config()
    d = raw.get("docling") or {}
    return Config(docling=DoclingConfig(
        artifacts_path=Path(d.get("artifacts_path", "intake-data/models")).expanduser(),
        cache_dir=Path(d.get("cache_dir", "intake-data/cache")).expanduser(),
        device=d.get("device", "cpu"), num_threads=int(d.get("num_threads", 4)),
        table_mode=d.get("table_mode", "accurate"), document_timeout=float(d.get("document_timeout", 1800))))
