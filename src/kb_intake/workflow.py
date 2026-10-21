"""Workflows (intake-design-v2 §3): which handler processes each region type, and which pages are excluded.

A workflow is YAML text (what the user edits and approves) and, parsed, the dict

    {"name": str, "version": int >= 1, "handlers": {region type: handler, every type in vocab order},
     "exclude_pages": [page_no, ...] sorted, "llm"?: {"describe"?: bool, "chart_table"?: bool}}

`describe` (text by box plus an LLM description) applies to chart, diagram and picture regions only. The optional
`llm` block switches the LLM uses of this job (intake-ui-llm-design-v1 §4); absent, the configuration decides.

`parse` refuses anything else: an unknown or missing region type, an unknown handler, `table_structure` on a type
other than `table`, or exclude_pages that are not distinct positive integers. `dump` writes one fixed layout, so
dump(parse(dump(wf))) == dump(wf).

Templates ship with the package under kb_intake/workflows/<name>.yaml (only `default` so far). `suggest` starts
from the default and adds notes for the user from the label-stage stats; it does not guess page numbers.
"""

import json
import re
from pathlib import Path

import yaml

from kb_intake.vocab import DESCRIBE_TYPES, HANDLERS, REGION_TYPES

TEMPLATES = Path(__file__).with_name("workflows")
KEYS = ("name", "version", "handlers", "exclude_pages")
OPTIONAL_KEYS = ("llm",)
LLM_SWITCHES = ("describe", "chart_table")
TEMPLATE_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*")


class WorkflowError(ValueError):
    pass


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def validate(raw) -> dict:
    """The normalised workflow of an already loaded mapping (raises WorkflowError)."""
    if not isinstance(raw, dict):
        raise WorkflowError("a workflow must be a mapping with name, version, handlers and exclude_pages")
    unknown = sorted(map(str, set(raw) - set(KEYS) - set(OPTIONAL_KEYS)))
    if unknown:
        raise WorkflowError(f"unknown workflow key(s) {unknown} (known: {list(KEYS + OPTIONAL_KEYS)})")
    missing = [k for k in KEYS if k not in raw]
    if missing:
        raise WorkflowError(f"workflow lacks {missing}")
    name = raw["name"]
    if not isinstance(name, str) or not name.strip():
        raise WorkflowError("name must be a non-empty string")
    if not _int(raw["version"]) or raw["version"] < 1:
        raise WorkflowError("version must be an integer >= 1")
    handlers = raw["handlers"]
    if not isinstance(handlers, dict):
        raise WorkflowError("handlers must be a mapping of region type -> handler")
    bad_types = sorted(map(str, set(handlers) - set(REGION_TYPES)))
    if bad_types:
        raise WorkflowError(f"unknown region type(s) {bad_types} (known: {list(REGION_TYPES)})")
    missing_types = [t for t in REGION_TYPES if t not in handlers]
    if missing_types:
        raise WorkflowError(f"handlers lack region type(s) {missing_types}; every type needs a handler")
    for t in REGION_TYPES:
        h = handlers[t]
        if h not in HANDLERS:
            raise WorkflowError(f"handler {h!r} for {t} is unknown (known: {list(HANDLERS)})")
        if h == "table_structure" and t != "table":
            raise WorkflowError(f"table_structure only applies to table regions, not to {t}")
        if h == "describe" and t not in DESCRIBE_TYPES:
            raise WorkflowError(f"describe only applies to {', '.join(DESCRIBE_TYPES)} regions, not to {t}")
    pages = raw["exclude_pages"]
    if not isinstance(pages, list) or not all(_int(p) and p >= 1 for p in pages):
        raise WorkflowError("exclude_pages must be a list of page numbers (integers >= 1)")
    if len(set(pages)) != len(pages):
        raise WorkflowError("exclude_pages lists a page more than once")
    out = {"name": name, "version": raw["version"], "handlers": {t: handlers[t] for t in REGION_TYPES},
           "exclude_pages": sorted(pages)}
    if "llm" in raw:
        llm = raw["llm"]
        if not isinstance(llm, dict) or set(llm) - set(LLM_SWITCHES) or \
                not all(isinstance(v, bool) for v in llm.values()):
            raise WorkflowError(f"llm must map some of {list(LLM_SWITCHES)} to true or false")
        out["llm"] = {k: llm[k] for k in LLM_SWITCHES if k in llm}
    return out


def parse(text: str) -> dict:
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise WorkflowError(f"workflow is not valid YAML: {e}") from e
    return validate(raw)


def dump(wf: dict) -> str:
    """The YAML text of a workflow dict, in one fixed layout: handlers in vocab order (any other keys after them,
    sorted). It only serialises; `parse` is what validates, so an edited dict can be dumped and then refused."""
    head = yaml.safe_dump({"name": wf["name"], "version": wf["version"]}, sort_keys=False, allow_unicode=True)
    handlers = wf["handlers"]
    order = [t for t in REGION_TYPES if t in handlers] + sorted(set(handlers) - set(REGION_TYPES), key=str)
    lines = [head.rstrip("\n"), "handlers:"]
    lines += [f"  {t}: {yaml.safe_dump(handlers[t], default_flow_style=True).strip().removesuffix('...').strip()}"
              for t in order]
    lines.append(f"exclude_pages: {json.dumps(wf['exclude_pages'])}")
    if "llm" in wf:
        lines.append("llm: {" + ", ".join(f"{k}: {json.dumps(v)}" for k, v in wf["llm"].items()) + "}")
    return "\n".join(lines) + "\n"


def load_template(name: str = "default") -> dict:
    if not TEMPLATE_NAME.fullmatch(name or ""):
        raise WorkflowError(f"invalid template name {name!r}")
    path = TEMPLATES / f"{name}.yaml"
    if not path.is_file():
        known = sorted(p.stem for p in TEMPLATES.glob("*.yaml"))
        raise WorkflowError(f"no workflow template {name!r} (known: {known})")
    return parse(path.read_text(encoding="utf-8"))


def handler_for(wf: dict, region_type: str) -> str:
    try:
        return wf["handlers"][region_type]
    except KeyError:
        raise WorkflowError(f"the workflow has no handler for region type {region_type!r}") from None


def llm_switch(wf: dict, feature: str, default: bool) -> bool:
    """The job's switch for an LLM feature (describe, chart_table): the workflow's `llm` block, else `default`
    (the configuration's feature setting)."""
    return wf.get("llm", {}).get(feature, default)


def suggest(stats: dict) -> tuple[dict, list[str]]:
    """(the suggested workflow, notes for the user) from the label-stage stats. The suggestion is the default
    template; the notes point at what the user should look at before approving."""
    wf = load_template()
    notes: list[str] = []
    kinds = stats.get("pages_by_kind") or {}
    scanned, mixed = kinds.get("scanned", 0), kinds.get("mixed", 0)
    if scanned:
        notes.append(f"{scanned} scanned page(s): OCR is not implemented, so their regions get no text and the "
                     "pages carry no_text_layer gaps; exclude them if nothing on them is needed")
    if mixed:
        notes.append(f"{mixed} mixed page(s): images without a text layer are not read (OCR is not implemented)")
    if stats.get("table_suspected"):
        notes.append(f"{stats['table_suspected']} suspected table(s) are not table regions; check them in review "
                     "and reclassify to table where needed")
    if stats.get("low_confidence"):
        notes.append(f"{stats['low_confidence']} low-confidence region(s); check their types in review")
    unknown = (stats.get("regions_by_type") or {}).get("unknown", 0)
    if unknown:
        notes.append(f"{unknown} region(s) of type unknown are handled as {handler_for(wf, 'unknown')}")
    return wf, notes
