"""intake.yaml (intake-design §3.2, §7). Every key is optional.

Precedence: command-line flag > INTAKE_* environment variable > intake.yaml > default. Unknown keys are refused, so
a setting that is not implemented can never look as if it took effect.

    models_dir: ~/.kb-intake/models     # INTAKE_MODELS_DIR; pinned weights (kb-intake models download)
    cache_dir:  ~/.kb-intake/cache      # INTAKE_CACHE_DIR; model outputs keyed by source + versions + params
    tables:
      primary: docling                  # the layout engine (only docling)
      secondary: find_tables            # cross-check extractor; "none" turns it off
    docling:
      table_mode: accurate              # or fast
      device: cpu                       # formal packages use cpu; mps only for debugging (recorded in the manifest)
      num_threads: 4
      document_timeout: 1800            # Docling's own budget, seconds
      wall_timeout: 2100                # hard limit: the conversion subprocess is killed after this many seconds
    headings:
      font_profiles: []                 # directories of font profiles; none -> levels inferred from the document
    scan_roots: []                      # serve: directories the web UI / API may list and scan; empty = uploads only
"""

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

HOME = Path("~/.kb-intake")
ENGINES = ("docling",)
SECONDARY = ("find_tables", "none")
KEYS = {"models_dir": None, "cache_dir": None, "scan_roots": None, "tables": {"primary", "secondary"},
        "docling": {"table_mode", "device", "num_threads", "document_timeout", "wall_timeout"},
        "headings": {"font_profiles"}}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class DoclingParams:
    table_mode: str = "accurate"
    device: str = "cpu"
    num_threads: int = 4
    document_timeout: float = 1800.0
    wall_timeout: float = 2100.0

    def engine_params(self) -> dict:
        """The parameters that shape the model output (they go into the cache key); wall_timeout does not."""
        return {"do_ocr": False, "do_table_structure": True, "table_mode": self.table_mode, "do_cell_matching": True,
                "device": self.device, "num_threads": self.num_threads, "document_timeout": self.document_timeout}


@dataclass(frozen=True)
class Config:
    models_dir: Path = field(default_factory=lambda: (HOME / "models").expanduser())
    cache_dir: Path = field(default_factory=lambda: (HOME / "cache").expanduser())
    primary: str = "docling"
    secondary: str = "find_tables"
    docling: DoclingParams = field(default_factory=DoclingParams)
    font_profiles: tuple[Path, ...] = ()
    scan_roots: tuple[Path, ...] = ()   # resolved; only `serve` uses them (the CLI scans what it is given)
    layout_enabled: bool = True         # False only for the full-degradation check (`scan --no-layout`)


def _path(v) -> Path:
    return Path(str(v)).expanduser()


def load(path: str | os.PathLike | None = None, env: dict | None = None) -> Config:
    """The configuration from `path` (or $INTAKE_CONFIG), with INTAKE_MODELS_DIR / INTAKE_CACHE_DIR on top."""
    env = os.environ if env is None else env
    path = path or env.get("INTAKE_CONFIG")
    raw = {}
    if path:
        try:
            raw = yaml.safe_load(_path(path).read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as e:
            raise ConfigError(f"cannot read {path}: {type(e).__name__}: {e}") from e
        if not isinstance(raw, dict):
            raise ConfigError(f"{path}: the configuration must be a mapping")
    for k, v in raw.items():
        if k not in KEYS:
            raise ConfigError(f"unknown configuration key {k!r} (known: {sorted(KEYS)})")
        if KEYS[k] is not None:
            if not isinstance(v, dict):
                raise ConfigError(f"{k} must be a mapping")
            bad = sorted(set(v) - KEYS[k])
            if bad:
                raise ConfigError(f"unknown key(s) under {k}: {bad} (known: {sorted(KEYS[k])})")
    cfg = Config()
    if "models_dir" in raw:
        cfg = replace(cfg, models_dir=_path(raw["models_dir"]))
    if "cache_dir" in raw:
        cfg = replace(cfg, cache_dir=_path(raw["cache_dir"]))
    t = raw.get("tables") or {}
    primary, secondary = t.get("primary", "docling"), t.get("secondary", "find_tables")
    if primary not in ENGINES:
        raise ConfigError(f"tables.primary must be one of {list(ENGINES)}")
    if secondary not in SECONDARY:
        raise ConfigError(f"tables.secondary must be one of {list(SECONDARY)}")
    d = raw.get("docling") or {}
    params = DoclingParams(table_mode=d.get("table_mode", "accurate"), device=d.get("device", "cpu"),
                           num_threads=int(d.get("num_threads", 4)),
                           document_timeout=float(d.get("document_timeout", 1800)),
                           wall_timeout=float(d.get("wall_timeout", 2100)))
    if params.table_mode not in ("accurate", "fast"):
        raise ConfigError("docling.table_mode must be accurate or fast")
    if params.device not in ("cpu", "mps", "cuda", "auto"):
        raise ConfigError("docling.device must be cpu, mps, cuda or auto")
    profiles = (raw.get("headings") or {}).get("font_profiles") or []
    if isinstance(profiles, str):
        profiles = [profiles]
    base = _path(path).parent if path else Path.cwd()
    dirs = tuple(p if p.is_absolute() else base / p for p in map(_path, profiles))   # relative to intake.yaml
    roots = raw.get("scan_roots") or []
    if isinstance(roots, str):
        roots = [roots]
    if not isinstance(roots, list):
        raise ConfigError("scan_roots must be a list of directories")
    scan_roots = tuple((p if p.is_absolute() else base / p).resolve() for p in map(_path, roots))
    cfg = replace(cfg, primary=primary, secondary=secondary, docling=params, font_profiles=dirs, scan_roots=scan_roots)
    if env.get("INTAKE_MODELS_DIR"):
        cfg = replace(cfg, models_dir=_path(env["INTAKE_MODELS_DIR"]))
    if env.get("INTAKE_CACHE_DIR"):
        cfg = replace(cfg, cache_dir=_path(env["INTAKE_CACHE_DIR"]))
    return cfg
