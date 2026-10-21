"""intake.yaml (intake-design-v2 §1, §6). Every key is optional.

Precedence: command-line flag > INTAKE_* environment variable > intake.yaml > default. The CLI (and so `serve`)
first reads the INTAKE_* variables of a `.env` in the working directory that the environment does not set
(load_dotenv); that is where the LLM key may live (`.env.example`; `.env` is never committed). Unknown keys are refused, so
a setting that is not implemented can never look as if it took effect.

    models_dir:   ~/.kb-intake/models     # INTAKE_MODELS_DIR; pinned weights (kb-intake models download)
    cache_dir:    ~/.kb-intake/cache      # INTAKE_CACHE_DIR; model outputs keyed by source + versions + params
    work_dir:     ~/.kb-intake/work       # INTAKE_WORK_DIR; jobs before confirmation, and state.db (SQLite)
    delivery_dir: ~/.kb-intake/delivery   # INTAKE_DELIVERY_DIR; confirmed packages only
    low_confidence: 0.5                   # layout score / classifier top score below this -> low_confidence gap
    docling:
      table_mode: accurate              # or fast (extraction stage)
      device: cpu                       # formal packages use cpu; mps only for debugging (recorded in the manifest)
      num_threads: 4
      document_timeout: 1800            # Docling's own budget, seconds
      wall_timeout: 2100                # hard limit: the conversion subprocess is killed after this many seconds
    scan_roots: []                      # serve: directories the web UI / API may list; empty = uploads only
    llm:                                # optional LLM help (intake-ui-llm-design-v1 §4); off unless enabled here
      enabled: false                    #   and the key is in the environment variable named by api_key_env
      provider: openai_compatible
      base_url: https://api.minimax.io/v1
      model: MiniMax-M3
      api_key_env: INTAKE_LLM_API_KEY   # the key itself is never read from a file
      remote: true                      # data leaves this machine (UI and manifest say so); false needs a localhost base_url
      features: {summary: true, describe: true, chart_table: false}
"""

import json
import os
import sys
import time
import urllib.parse
from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

from kb_intake.vocab import LOW_CONFIDENCE

HOME = Path("~/.kb-intake")
STAGES = ("label", "extract")
KEYS = {"models_dir": None, "cache_dir": None, "work_dir": None, "delivery_dir": None, "scan_roots": None,
        "low_confidence": None,
        "docling": {"table_mode", "device", "num_threads", "document_timeout", "wall_timeout"},
        "llm": {"enabled", "provider", "base_url", "model", "api_key_env", "remote", "allow_http", "features"}}
LLM_FEATURES = ("summary", "describe", "chart_table")
LLM_PROVIDERS = ("openai_compatible",)


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class DoclingParams:
    table_mode: str = "accurate"
    device: str = "cpu"
    num_threads: int = 4
    document_timeout: float = 1800.0
    wall_timeout: float = 2100.0

    def engine_params(self, stage: str) -> dict:
        """The parameters that shape the model output of one stage (they go into the cache key); wall_timeout does
        not. label: layout + picture classifier, no table structure. extract: layout + TableFormer, no classifier."""
        if stage not in STAGES:
            raise ValueError(f"unknown stage {stage!r}")
        common = {"stage": stage, "do_ocr": False, "device": self.device, "num_threads": self.num_threads,
                  "document_timeout": self.document_timeout}
        if stage == "label":
            return {**common, "do_table_structure": False, "do_picture_classification": True}
        return {**common, "do_table_structure": True, "table_mode": self.table_mode, "do_cell_matching": True,
                "do_picture_classification": False}


@dataclass(frozen=True)
class LlmConfig:
    enabled: bool = False
    provider: str = "openai_compatible"
    base_url: str = "https://api.minimax.io/v1"
    model: str = "MiniMax-M3"
    api_key_env: str = "INTAKE_LLM_API_KEY"
    remote: bool = True                 # derived from base_url when not configured: not localhost = remote
    allow_http: bool = False
    features: dict = field(default_factory=lambda: {"summary": True, "describe": True, "chart_table": False})


@dataclass(frozen=True)
class Config:
    models_dir: Path = field(default_factory=lambda: (HOME / "models").expanduser())
    cache_dir: Path = field(default_factory=lambda: (HOME / "cache").expanduser())
    work_dir: Path = field(default_factory=lambda: (HOME / "work").expanduser())
    delivery_dir: Path = field(default_factory=lambda: (HOME / "delivery").expanduser())
    low_confidence: float = LOW_CONFIDENCE
    docling: DoclingParams = field(default_factory=DoclingParams)
    scan_roots: tuple[Path, ...] = ()   # resolved; only `serve` uses them
    layout_enabled: bool = True         # False only for the degradation tests (no Docling at all)
    llm: LlmConfig = field(default_factory=LlmConfig)

    @property
    def state_db(self) -> Path:
        return self.work_dir / "state.db"


def _path(v) -> Path:
    return Path(str(v)).expanduser()


def inside(path: Path, roots) -> bool:
    """Whether the real path (symlinks and .. resolved) lies in one of the (resolved) roots."""
    try:
        real = path.resolve(strict=True)
    except OSError:
        return False
    return any(real == r or real.is_relative_to(r) for r in roots)


LOOPBACK = ("localhost", "127.0.0.1", "::1")
# INTAKE_LLM_* variables (.env, setup.sh) over the llm block of intake.yaml
LLM_ENV = {"INTAKE_LLM_ENABLED": "enabled", "INTAKE_LLM_BASE_URL": "base_url", "INTAKE_LLM_MODEL": "model",
           "INTAKE_LLM_ALLOW_HTTP": "allow_http"}
TRUE, FALSE = ("true", "1", "yes", "on"), ("false", "0", "no", "off", "")


def _bool_env(env: dict, var: str) -> bool:
    v = env[var].strip().lower()
    if v not in TRUE + FALSE:
        raise ConfigError(f"{var} must be true or false, not {env[var]!r}")
    return v in TRUE


def _llm(raw: dict, env: dict | None = None) -> LlmConfig:
    d = LlmConfig()
    for key in ("enabled", "remote", "allow_http"):
        if key in raw and not isinstance(raw[key], bool):
            raise ConfigError(f"llm.{key} must be true or false")
    for key in ("provider", "base_url", "model", "api_key_env"):
        if key in raw and (not isinstance(raw[key], str) or not raw[key].strip()):
            raise ConfigError(f"llm.{key} must be a non-empty string")
    if raw.get("provider", d.provider) not in LLM_PROVIDERS:
        raise ConfigError(f"llm.provider must be one of {list(LLM_PROVIDERS)}")
    features = raw.get("features") or {}
    if not isinstance(features, dict):
        raise ConfigError("llm.features must be a mapping")
    bad = sorted(set(features) - set(LLM_FEATURES))
    if bad:
        raise ConfigError(f"unknown llm feature(s) {bad} (known: {list(LLM_FEATURES)})")
    if not all(isinstance(v, bool) for v in features.values()):
        raise ConfigError("llm.features values must be true or false")
    raw = dict(raw)
    for var, key in LLM_ENV.items():
        if (env or {}).get(var, "").strip():
            raw[key] = _bool_env(env, var) if key in ("enabled", "allow_http") else env[var].strip()
    features = dict(features)
    if (env or {}).get("INTAKE_LLM_DESCRIBE", "").strip():    # the user's own switch
        features["describe"] = _bool_env(env, "INTAKE_LLM_DESCRIBE")
    url = urllib.parse.urlsplit(raw.get("base_url", d.base_url))
    loopback = url.hostname in LOOPBACK
    allow_http = raw.get("allow_http", d.allow_http)
    if url.scheme not in ("https", "http") or not url.hostname:
        raise ConfigError(f"llm.base_url must be an http(s) URL, not {raw.get('base_url')!r}")
    if url.scheme == "http" and not loopback and not allow_http:
        raise ConfigError(f"llm.base_url is plain http on another host ({url.hostname}): the key would cross the "
                          "network unencrypted. Use https, or set INTAKE_LLM_ALLOW_HTTP=true (llm.allow_http) "
                          "to accept that")
    if raw.get("remote") is False and not loopback:
        raise ConfigError(f"llm.remote is false, but llm.base_url is on another host ({url.hostname}); "
                          "only a localhost endpoint is local")
    return LlmConfig(enabled=raw.get("enabled", d.enabled), provider=raw.get("provider", d.provider),
                     base_url=raw.get("base_url", d.base_url), model=raw.get("model", d.model),
                     api_key_env=raw.get("api_key_env", d.api_key_env), remote=raw.get("remote", not loopback),
                     allow_http=allow_http, features={**d.features, **features})


CAPABILITIES = "llm-capabilities.json"


def capabilities_path(cfg: "Config") -> Path:
    """Where kb-intake doctor records what the configured model accepts (in the data directory, never in .env)."""
    return cfg.work_dir / CAPABILITIES


def record_capabilities(cfg: "Config", images: bool, reason: str | None) -> Path:
    """Record the latest image check of cfg's endpoint and model (kb-intake doctor runs it every time)."""
    p = capabilities_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tmp")
    tmp.write_text(json.dumps({"base_url": cfg.llm.base_url, "model": cfg.llm.model, "images": images,
                               "reason": reason, "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}, indent=1)
                   + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return p


def images_accepted(cfg: "Config") -> bool | None:
    """The latest image check for this endpoint and model: True, False, or None when there is none."""
    try:
        rec = json.loads(capabilities_path(cfg).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict) or (rec.get("base_url"), rec.get("model")) != (cfg.llm.base_url, cfg.llm.model):
        return None
    return rec.get("images") if isinstance(rec.get("images"), bool) else None


def llm_host(llm: LlmConfig) -> str:
    """Where the LLM's data goes: the host (and port) of base_url."""
    return urllib.parse.urlsplit(llm.base_url).netloc


def public_llm_info(cfg: Config, env: dict | None = None) -> dict:
    """What the API and UI may show about the LLM settings; never the key. `enabled` is true only when the
    configuration enables it and the key's environment variable is set."""
    env = os.environ if env is None else env
    llm = cfg.llm
    key_present = bool(env.get(llm.api_key_env))
    return {"enabled": llm.enabled and key_present, "configured": llm.enabled, "key_present": key_present,
            "remote": llm.remote, "provider": llm.provider, "model": llm.model, "base_url": llm.base_url,
            "images": images_accepted(cfg),
            "features": dict(llm.features)}


# besides INTAKE_*: what the LLM client needs to reach a company endpoint (proxy, CA). PIP_* are setup.sh's.
DOTENV_NETWORK = ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy", "SSL_CERT_FILE",
                  "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE")
# path values: a relative one is relative to the .env file, not to wherever kb-intake was started
DOTENV_PATHS = ("INTAKE_MODELS_DIR", "INTAKE_DATA_DIR", "INTAKE_CACHE_DIR", "INTAKE_WORK_DIR", "INTAKE_DELIVERY_DIR",
                "INTAKE_CONFIG", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE")


def load_dotenv(path: Path, env: dict | None = None) -> list[str]:
    """Set the INTAKE_* (and DOTENV_NETWORK) variables of a `.env` file (KEY=value lines; `#` comments, an `export `
    prefix and quotes around the value allowed) that `env` (default os.environ) does not set yet; a variable already
    set wins. Relative paths (DOTENV_PATHS) are made absolute against the file's directory. Other names are ignored,
    values are never printed. Returns the names it set; a missing file sets nothing."""
    env = os.environ if env is None else env
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()     # -sig: a BOM (Windows editors) is dropped
    except OSError:
        return []
    except UnicodeDecodeError:
        print(f"warning: {path} is not UTF-8; it was not read", file=sys.stderr)
        return []
    added = []
    for line in lines:
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        name, eq, value = line.partition("=")
        name, value = name.strip(), value.strip()
        if not eq or not (name.startswith("INTAKE_") or name in DOTENV_NETWORK) \
                or not name.replace("_", "").isalnum() or name in env:
            continue
        if value[:1] in ("'", '"') and value[0] in value[1:]:          # quoted: up to the closing quote
            value = value[1:value.index(value[0], 1)]
        else:                                                           # unquoted: ' #' starts a comment
            value = value.split(" #", 1)[0].rstrip()
        if name in DOTENV_PATHS and value:
            p = Path(value).expanduser()
            value = str(p if p.is_absolute() else (path.parent / p).resolve())
        env[name] = value
        added.append(name)
    return added


ENV_PATHS = {"INTAKE_MODELS_DIR": "models_dir", "INTAKE_CACHE_DIR": "cache_dir", "INTAKE_WORK_DIR": "work_dir",
             "INTAKE_DELIVERY_DIR": "delivery_dir"}


def load(path: str | os.PathLike | None = None, env: dict | None = None) -> Config:
    """The configuration from `path` (or $INTAKE_CONFIG), with the INTAKE_*_DIR variables on top."""
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
    base = _path(path).parent if path else Path.cwd()
    cfg = Config()
    for key in ("models_dir", "cache_dir", "work_dir", "delivery_dir"):
        if key in raw:
            p = _path(raw[key])
            cfg = replace(cfg, **{key: p if p.is_absolute() else base / p})
    if "low_confidence" in raw:
        lc = raw["low_confidence"]
        if not isinstance(lc, (int, float)) or not 0 <= lc <= 1:
            raise ConfigError("low_confidence must be a number between 0 and 1")
        cfg = replace(cfg, low_confidence=float(lc))
    d = raw.get("docling") or {}
    params = DoclingParams(table_mode=d.get("table_mode", "accurate"), device=d.get("device", "cpu"),
                           num_threads=int(d.get("num_threads", 4)),
                           document_timeout=float(d.get("document_timeout", 1800)),
                           wall_timeout=float(d.get("wall_timeout", 2100)))
    if params.table_mode not in ("accurate", "fast"):
        raise ConfigError("docling.table_mode must be accurate or fast")
    if params.device not in ("cpu", "mps", "cuda", "auto"):
        raise ConfigError("docling.device must be cpu, mps, cuda or auto")
    roots = raw.get("scan_roots") or []
    if isinstance(roots, str):
        roots = [roots]
    if not isinstance(roots, list):
        raise ConfigError("scan_roots must be a list of directories")
    scan_roots = tuple((p if p.is_absolute() else base / p).resolve() for p in map(_path, roots))
    cfg = replace(cfg, docling=params, scan_roots=scan_roots, llm=_llm(raw.get("llm") or {}, env))
    if env.get("INTAKE_DATA_DIR"):                     # one data directory: cache, work and delivery below it
        data = _path(env["INTAKE_DATA_DIR"])
        cfg = replace(cfg, cache_dir=data / "cache", work_dir=data / "work", delivery_dir=data / "delivery")
    for var, key in ENV_PATHS.items():
        if env.get(var):
            cfg = replace(cfg, **{key: _path(env[var])})
    if images_accepted(cfg) is False:                 # the latest check: this model refuses images
        cfg = replace(cfg, llm=replace(cfg.llm, features={**cfg.llm.features, "describe": False}))
    return cfg
