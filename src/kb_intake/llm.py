"""Optional LLM help (intake-ui-llm-design-v1 §4): a document summary, descriptions of charts and pictures, and an
experimental chart-to-table, through an OpenAI-compatible endpoint (MiniMax by default).

- Off unless the configuration enables it and the key is in its environment variable (config.public_llm_info), and
  the use is switched on: the configuration's `llm.features`, then for describe / chart_table the job's workflow
  `llm` block. Nothing leaves the machine otherwise.
- Every call goes through the cache (`cache_dir/llm`, kb_intake.llm_cache) keyed by provider, model, prompt
  version and the sha256 of the input; a re-run with the same input makes no call. The model gives different
  answers to the same request (MiniMax ignores temperature and has no seed), so the cache is what makes a package
  reproducible. Failed calls are not cached.
- An output is an llm.jsonl row (contract/v2/llm.schema.json): low weight, no time. A failure is an `llm_failed`
  gap; the step goes on.
- The key is read when a call is made and never written anywhere.
"""

import base64
import hashlib
import json
import os
import time
from pathlib import Path

import pymupdf

from kb_intake import config, workflow
from kb_intake.package import read_jsonl

PROMPTS = {
    "summary": ("summary-1", (
        "You are given the title information and the start of each page of one document, extracted from its PDF "
        "text layer. In 2 to 4 plain sentences, describe what the document is and how it is organised: its main "
        "parts with their page ranges, and where the tables and financial statements are. Describe only; do not "
        "evaluate, interpret figures or add facts that are not in the text. Answer with the sentences only.")),
    "describe": ("describe-1", (
        "The image is one region cut from a page of a PDF document; the text found inside the region by the PDF "
        "text layer follows the image. Describe what the region shows, in at most 120 words: its kind (for example "
        "bar chart, line chart, pie chart, diagram, photograph), its title if any, the axes and their units, the "
        "series or parts, and the main content that is visible. Do not interpret causes or add facts that are not "
        "visible. Answer with the description only.")),
    "chart_table": ("chart-table-1", (
        "The image is a chart cut from a page of a PDF document. Read the data the chart shows and answer with one "
        'JSON object and nothing else, with exactly these keys: "title" (string or null), "columns" (list of '
        'strings: the category column first, then one per series), "rows" (list of rows, each a list with one value '
        'per column; numbers as numbers, unknown values as null), "units" (string or null), "notes" (string or '
        "null: how certain the reading is, and anything estimated). Use the labels as written in the chart.")),
}
MAX_TOKENS = {"summary": 1024, "describe": 1024, "chart_table": 4096}
SUMMARY_PAGE_CHARS = 300
SUMMARY_MAX_CHARS = 24_000
CROP_DPI = 144
CROP_MAX_SIDE = 2000
PARALLEL = 4


class TableError(ValueError):
    pass


# ---------- inputs ----------

def crop_png(page: pymupdf.Page, bbox) -> bytes:
    """The region rendered at CROP_DPI, scaled down so its long side is at most CROP_MAX_SIDE pixels."""
    rect = pymupdf.Rect(bbox) & page.rect
    if rect.is_empty or rect.width < 1 or rect.height < 1:
        rect = pymupdf.Rect(rect.x0, rect.y0, rect.x0 + max(rect.width, 1), rect.y0 + max(rect.height, 1))
    zoom = CROP_DPI / 72
    zoom = min(zoom, CROP_MAX_SIDE / max(rect.width, rect.height))
    return page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=rect, alpha=False).tobytes("png")


def summary_input(job_dir: Path, decl: dict) -> str:
    """Declared title information, then `p{n}: ` + the first SUMMARY_PAGE_CHARS characters of each page's text
    layer, cut at SUMMARY_MAX_CHARS."""
    head = [f"{k}: {decl[k]}" for k in ("doc_id", "issuer_id", "doc_type", "period_end", "lang") if decl.get(k)]
    lines = ["\n".join(head), ""]
    for rec in read_jsonl(job_dir / "label" / "layout.jsonl"):
        words = " ".join(w[4] for w in rec["words"])
        if words:
            lines.append(f"p{rec['page_no']}: {words[:SUMMARY_PAGE_CHARS]}")
    return "\n".join(lines)[:SUMMARY_MAX_CHARS]


def input_sha256(kind: str, text: str, png: bytes | None) -> str:
    image = hashlib.sha256(png).hexdigest() if png is not None else None
    canon = json.dumps({"kind": kind, "text": text, "image_sha256": image}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def parse_table(text: str) -> dict:
    """The chart table of an answer: the first JSON object in it (Markdown fences allowed), checked for the keys
    and value types of contract/v2/llm.schema.json."""
    start = text.find("{")
    if start < 0:
        raise TableError("no JSON object in the answer")
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError as e:
        raise TableError(f"the answer is not JSON ({e.msg})") from None
    keys = ("title", "columns", "rows", "units", "notes")
    if not isinstance(obj, dict) or set(obj) != set(keys):
        raise TableError(f"the table must have exactly the keys {list(keys)}")
    cols, rows = obj["columns"], obj["rows"]
    if not isinstance(cols, list) or not all(isinstance(c, str) for c in cols):
        raise TableError("columns must be a list of strings")
    cell = (str, int, float, type(None))
    if not isinstance(rows, list) or not all(isinstance(r, list) and all(
            isinstance(v, cell) and not isinstance(v, bool) for v in r) for r in rows):
        raise TableError("rows must be lists of strings, numbers or nulls")
    for k in ("title", "units", "notes"):
        if not isinstance(obj[k], (str, type(None))):
            raise TableError(f"{k} must be a string or null")
    return {k: obj[k] for k in keys}


# ---------- the runner ----------

def _data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


class Runner:
    """The LLM uses of one job. `chat` and `cache` default to kb_intake.llm_client.chat and a Cache under
    cfg.cache_dir/llm (tests pass their own); `env` defaults to os.environ."""

    def __init__(self, cfg: config.Config, chat=None, cache=None, env=None):
        from kb_intake import llm_cache, llm_client
        self.cfg, self.llm = cfg, cfg.llm
        self.env = os.environ if env is None else env
        self.info = config.public_llm_info(cfg, self.env)
        self.chat = chat or llm_client.chat
        self.errors = (llm_client.LlmError,)
        self.cache = cache or llm_cache.Cache(cfg.cache_dir / "llm")
        self.cache_key = llm_cache.cache_key
        self.stats = {"calls": 0, "cached": 0, "failed": 0, "seconds": 0.0, "tokens": 0}

    def enabled(self, feature: str, wf: dict | None = None) -> bool:
        if not self.info["enabled"] or not self.llm.features.get(feature):
            return False
        return feature == "summary" or wf is None or workflow.llm_switch(wf, feature, True)

    def _row(self, kind: str, region_id, sha: str, output: dict) -> dict:
        return {"kind": kind, "region_id": region_id, "provider": self.llm.provider, "model": self.llm.model,
                "prompt_version": PROMPTS[kind][0], "input_sha256": sha, "output": output, "weight": "low"}

    @staticmethod
    def _gap(kind: str, page_no, region_id, reason: str) -> dict:
        return {"code": "llm_failed", "page_no": page_no, "region_id": region_id, "kind": kind, "reason": reason,
                "detail": f"{kind} failed: {reason}"}

    def _answer(self, kind: str, text: str, png: bytes | None) -> tuple[str, str]:
        """(input_sha256, the model's answer text), from the cache or one call. Raises the client's LlmError."""
        sha = input_sha256(kind, text, png)
        key = self.cache_key(self.llm.provider, self.llm.model, PROMPTS[kind][0], sha)
        hit = self.cache.get(key)
        if hit is not None:
            self.stats["cached"] += 1
            return sha, hit["text"]
        content = []
        if png is not None:
            content.append({"type": "image_url", "image_url": {"url": _data_url(png)}})
        content.append({"type": "text", "text": PROMPTS[kind][1] + ("\n\n" + text if text else "")})
        t0 = time.monotonic()
        try:
            done = self.chat(self.llm.base_url, self.llm.model, self.env.get(self.llm.api_key_env, ""), content,
                             max_tokens=MAX_TOKENS[kind])
        finally:
            self.stats["calls"] += 1
            self.stats["seconds"] = round(self.stats["seconds"] + time.monotonic() - t0, 3)
        self.stats["tokens"] += int((done.usage or {}).get("total_tokens") or 0)
        self.cache.put(key, {"text": done.text, "usage": done.usage, "model": done.model})
        return sha, done.text

    def _one(self, kind: str, page_no, region_id, text: str, png: bytes | None) -> dict:
        """An llm.jsonl row, or an llm_failed gap."""
        try:
            sha, answer = self._answer(kind, text, png)
            if not answer.strip():
                raise TableError("empty")
            output = {"table": parse_table(answer)} if kind == "chart_table" else {"text": answer.strip()}
            return self._row(kind, region_id, sha, output)
        except self.errors as e:
            reason = getattr(e, "code", "error")
        except TableError as e:
            reason = "empty" if str(e) == "empty" else "invalid_table"
        self.stats["failed"] += 1
        return self._gap(kind, page_no, region_id, reason)

    def summary(self, job_dir: Path, decl: dict) -> dict:
        return self._one("summary", None, None, summary_input(job_dir, decl), None)

    def describe_inputs(self, region: dict, page: pymupdf.Page, wf: dict | None) -> list[tuple]:
        """The calls a `describe` region needs: (kind, page_no, region_id, text, png). Rendering is done here, in
        the caller's thread (PyMuPDF is not thread-safe); the calls can then run in parallel."""
        png = crop_png(page, region["bbox"])
        text = "Text inside the region (from the PDF text layer):\n" + (region.get("text") or "(none)")
        out = [("describe", region["page_no"], region["region_id"], text, png)]
        if region["type"] == "chart" and self.enabled("chart_table", wf):
            out.append(("chart_table", region["page_no"], region["region_id"], "", png))
        return out

    def run_many(self, calls: list[tuple]) -> list[dict]:
        """Rows and gaps of the calls, in the order given."""
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
            return list(pool.map(lambda c: self._one(*c), calls))


def sort_rows(rows: list[dict], order: dict[str, int]) -> list[dict]:
    """llm.jsonl order: the summary first, then by the regions' reading order, describe before chart_table."""
    kinds = {"summary": 0, "describe": 1, "chart_table": 2}
    return sorted(rows, key=lambda r: (r["region_id"] is not None, order.get(r["region_id"], -1), kinds[r["kind"]]))

