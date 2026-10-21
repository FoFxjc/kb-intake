"""Optional LLM help (intake-ui-llm-design-v1 §4): a document summary, descriptions of charts and pictures, and an
experimental chart-to-table, through an OpenAI-compatible endpoint (MiniMax by default).

- Off unless the configuration enables it and the key is in its environment variable (config.public_llm_info), and
  the use is switched on: the configuration's `llm.features`, then for describe / chart_table the job's workflow
  `llm` block. Nothing leaves the machine otherwise.
- Every call goes through the cache (`cache_dir/llm`, kb_intake.llm_cache) keyed by provider, model, prompt
  version and the sha256 of the input; a re-run with the same input makes no call. The model gives different
  answers to the same request (MiniMax ignores temperature and has no seed), so the cache is what makes a package
  reproducible. A call that failed (no answer, an empty or cut answer) is not cached; an answer that came back
  but cannot be used (a chart table that is not valid) is, so a re-run reproduces the same package.
- An output is an llm.jsonl row (contract/v2/llm.schema.json): low weight, no time. A failure is an `llm_failed`
  gap; the step goes on.
- The key is read when a call is made and never written anywhere.
"""

import base64
import fcntl
import hashlib
import json
import os
import time
from contextlib import contextmanager
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
# completion budgets include the model's reasoning (MiniMax-M3 reasons before it answers); only use is billed
MAX_TOKENS = {"summary": 4096, "describe": 4096, "chart_table": 8192}
SUMMARY_PAGE_CHARS = 300                     # at most, per page; less on long documents so every page fits
SUMMARY_MIN_PAGE_CHARS = 60
SUMMARY_MAX_CHARS = 24_000
# charts read into a table (Peter, 2026-10-21): flow diagrams and maps gave text tables or invented values
TABLE_CHARTS = ("bar", "line", "pie")
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
    """Declared title information, then `p{n}: ` + the start of each page's text layer: an equal share of
    SUMMARY_MAX_CHARS per page with text, between SUMMARY_MIN_PAGE_CHARS and SUMMARY_PAGE_CHARS characters, so the
    whole document is seen (OCBC, 276 pages: 86 characters each). The result is cut at SUMMARY_MAX_CHARS."""
    head = [f"{k}: {decl[k]}" for k in ("doc_id", "issuer_id", "doc_type", "period_end", "lang") if decl.get(k)]
    pages = [(rec["page_no"], " ".join(w[4] for w in rec["words"]))
             for rec in read_jsonl(job_dir / "label" / "layout.jsonl")]
    pages = [(n, words) for n, words in pages if words]
    budget = SUMMARY_MAX_CHARS - len("\n".join(head)) - 2
    share = (budget // len(pages)) - len("pNNN: \n") if pages else 0
    share = max(SUMMARY_MIN_PAGE_CHARS, min(SUMMARY_PAGE_CHARS, share))
    lines = ["\n".join(head), ""] + [f"p{n}: {words[:share]}" for n, words in pages]
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
    def no_constant(name):                     # NaN / Infinity are not JSON: strict consumers could not read them
        raise TableError(f"the table holds {name}, which is not a JSON number")
    try:
        obj, _ = json.JSONDecoder(parse_constant=no_constant).raw_decode(text[start:])
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
        self.by_kind = {k: {"calls": 0, "cached": 0, "failed": 0, "seconds": 0.0, "tokens": 0} for k in PROMPTS}

    @property
    def stats(self) -> dict:
        """Totals over the uses: calls, cached, failed, seconds, tokens."""
        return {k: round(sum(s[k] for s in self.by_kind.values()), 3) for k in ("calls", "cached", "failed",
                                                                                "seconds", "tokens")}

    def enabled(self, feature: str, wf: dict | None = None) -> bool:
        if not self.info["enabled"] or not self.llm.features.get(feature):
            return False
        return feature == "summary" or wf is None or workflow.llm_switch(wf, feature, True)

    def _row(self, kind: str, region_id, sha: str, output: dict) -> dict:
        return {"kind": kind, "region_id": region_id, "provider": self.llm.provider, "model": self.llm.model,
                "prompt_version": PROMPTS[kind][0], "input_sha256": sha, "output": output, "weight": "low"}

    @staticmethod
    def _gap(kind: str, page_no, region_id, reason: str) -> dict:
        """An llm_failed gap; a document-level one (the summary) has no region_id key, as other gaps."""
        where = {"region_id": region_id} if region_id is not None else {}
        return {"code": "llm_failed", "page_no": page_no, **where, "kind": kind, "reason": reason,
                "detail": f"{kind} failed: {reason}"}

    def _answer(self, kind: str, text: str, png: bytes | None) -> tuple[str, str]:
        """(input_sha256, the model's answer text), from the cache or one call. Raises the client's LlmError."""
        sha = input_sha256(kind, text, png)
        key = self.cache_key(self.llm.provider, self.llm.model, PROMPTS[kind][0], sha)
        st = self.by_kind[kind]
        hit = self.cache.get(key)
        if hit is not None:
            st["cached"] += 1
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
            st["calls"] += 1
            st["seconds"] = round(st["seconds"] + time.monotonic() - t0, 3)
        st["tokens"] += int((done.usage or {}).get("total_tokens") or 0)
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
        except Exception as e:  # noqa: BLE001 - any failure of an LLM use is a gap, never a failed step (§4.4)
            reason = f"error_{type(e).__name__}"
        self.by_kind[kind]["failed"] += 1
        return self._gap(kind, page_no, region_id, reason)

    def summary(self, job_dir: Path, decl: dict) -> dict:
        return self._one("summary", None, None, summary_input(job_dir, decl), None)

    def describe_inputs(self, region: dict, page: pymupdf.Page, wf: dict | None) -> list[tuple]:
        """The calls a `describe` region needs: (kind, page_no, region_id, text, png). Rendering is done here, in
        the caller's thread (PyMuPDF is not thread-safe); the calls can then run in parallel."""
        png = crop_png(page, region["bbox"])
        text = "Text inside the region (from the PDF text layer):\n" + (region.get("text") or "(none)")
        out = [("describe", region["page_no"], region["region_id"], text, png)]
        if region["type"] == "chart" and region.get("subtype") in TABLE_CHARTS and self.enabled("chart_table", wf):
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



# ---------- a job's LLM work files: llm/llm.jsonl, llm/gaps.jsonl, llm/summary.json, llm/run.json ----------

def rows_of(job_dir: Path) -> list[dict]:
    p = job_dir / "llm" / "llm.jsonl"
    return read_jsonl(p) if p.is_file() else []


def gaps_of(job_dir: Path) -> list[dict]:
    p = job_dir / "llm" / "gaps.jsonl"
    return read_jsonl(p) if p.is_file() else []


def _order(job_dir: Path) -> dict[str, int]:
    p = job_dir / "extract" / "regions.jsonl"
    p = p if p.is_file() else job_dir / "label" / "regions.jsonl"
    return {r["region_id"]: r["reading_order"] for r in read_jsonl(p)} if p.is_file() else {}


@contextmanager
def _locked(job_dir: Path):
    """Hold the job's LLM files: the background summary, the extract / review describe pass and a CLI process may
    write them at the same time; each re-reads them under this lock before it saves (flock: across processes)."""
    (job_dir / "llm").mkdir(exist_ok=True)
    with open(job_dir / "llm" / ".lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _save(job_dir: Path, rows: list[dict], gaps: list[dict], runner: "Runner | None" = None) -> None:
    from kb_intake.package import read_json, write_json, write_jsonl
    d = job_dir / "llm"
    d.mkdir(exist_ok=True)
    order = _order(job_dir)
    write_jsonl(d / "llm.jsonl", sort_rows(rows, order))
    write_jsonl(d / "gaps.jsonl", sorted(gaps, key=lambda g: (g.get("region_id") is not None,
                                                               order.get(g.get("region_id"), -1), g["kind"])))
    if runner is not None:                     # durations, tokens and cache hits: manifest.run.llm, never llm.jsonl
        run = read_json(d / "run.json") if (d / "run.json").is_file() else {}
        for kind, st in runner.by_kind.items():
            old = run.get(kind, {})
            run[kind] = {k: round(old.get(k, 0) + v, 3) for k, v in st.items()}
        if runner.stats["calls"]:                # whether data left the machine: the endpoint used, not today's config
            run["remote"] = bool(run.get("remote")) or runner.llm.remote
        write_json(d / "run.json", run)


def expect_summary(jobs, job_id: str, cfg: config.Config, env=None) -> bool:
    """Mark the summary pending now, when it is enabled (the label step calls this before the summary starts on
    its own thread, so /summary never answers "off" in between); True when a summary will follow."""
    from kb_intake.package import write_json
    if not Runner(cfg, env=env).enabled("summary"):
        return False
    d = jobs.get(job_id).dir
    (d / "llm").mkdir(exist_ok=True)
    write_json(d / "llm" / "summary.json", {"status": "pending"})
    return True


def summarize(jobs, job_id: str, cfg: config.Config, chat=None, env=None) -> dict | None:
    """The document summary of a labelled job (llm.jsonl row or llm_failed gap); None when the summary is off.
    llm/summary.json says pending, done or failed while and after it runs."""
    from kb_intake.package import write_json
    runner = Runner(cfg, chat=chat, env=env)
    if not runner.enabled("summary"):
        return None
    job = jobs.get(job_id)
    d = job.dir
    (d / "llm").mkdir(exist_ok=True)
    write_json(d / "llm" / "summary.json", {"status": "pending"})
    try:
        out = runner.summary(d, job.declaration)
    except Exception as e:                     # not a call failure (those are gaps): the input could not be built
        write_json(d / "llm" / "summary.json", {"status": "failed", "reason": f"error_{type(e).__name__}"})
        raise
    with _locked(d):
        rows = [r for r in rows_of(d) if r["kind"] != "summary"]
        gaps = [g for g in gaps_of(d) if g["kind"] != "summary"]
        if out.get("code") == "llm_failed":
            gaps.append(out)
            status = {"status": "failed", "reason": out["reason"]}
        else:
            rows.append(out)
            status = {"status": "done"}
        _save(d, rows, gaps, runner)
        write_json(d / "llm" / "summary.json", status)
    return out


def describe_regions(job_dir: Path, cfg: config.Config, wf: dict, regions: list[dict], pdf: pymupdf.Document,
                     only: set[str] | None = None, chat=None, env=None, progress=None) -> dict:
    """Describe (and, for charts, read into a table) the regions whose handler is `describe` and status
    extracted, replacing the describe / chart_table rows and gaps of `only` (all regions when None). Returns
    {"rows", "failed", "regions"}; nothing is called or written when the LLM or the use is off (the old rows of
    those regions are still dropped)."""
    runner = Runner(cfg, chat=chat, env=env)
    on = runner.enabled("describe", wf)
    todo = [r for r in regions if r.get("handler") == "describe" and r.get("status") == "extracted"
            and (only is None or r["region_id"] in only)] if on else []
    keep = (lambda x: x["kind"] == "summary") if only is None else \
        (lambda x: x["kind"] == "summary" or x.get("region_id") not in only)
    if not todo and not (job_dir / "llm").is_dir():
        return {"rows": 0, "failed": 0, "regions": 0}
    if progress and todo:
        progress("llm", "started", {"regions": len(todo)})
    calls = [c for r in todo for c in runner.describe_inputs(r, pdf[r["page_no"] - 1], wf)]
    outs = runner.run_many(calls)
    with _locked(job_dir):                     # read now, not before the calls: a summary may have been saved since
        rows, gaps = [r for r in rows_of(job_dir) if keep(r)], [g for g in gaps_of(job_dir) if keep(g)]
        for out in outs:
            (gaps if out.get("code") == "llm_failed" else rows).append(out)
        _save(job_dir, rows, gaps, runner if todo else None)
    done = {"rows": sum(1 for r in rows if r["kind"] != "summary"), "failed": runner.stats["failed"],
            "regions": len(todo)}
    if progress and todo:
        progress("llm", "done", done)
    return done


def manifest_entry(job_dir: Path, cfg: config.Config) -> tuple[dict, dict | None]:
    """(manifest.llm, manifest.run.llm or None) of a job about to be delivered."""
    from kb_intake.package import read_json
    rows, gaps = rows_of(job_dir), gaps_of(job_dir)
    run = job_dir / "llm" / "run.json"
    if not rows and not gaps:
        return {"used": False}, None
    counts = {k: sum(r["kind"] == k for r in rows) for k in PROMPTS}
    by_kind = read_json(run) if run.is_file() else {}
    first = rows[0] if rows else {}
    # the model that answered (from the rows) and whether data was sent out (from the runs), not the configuration
    # of the day of delivery; unknown counts as remote
    entry = {"used": True, "provider": first.get("provider", cfg.llm.provider), "model": first.get("model", cfg.llm.model),
             "remote": by_kind.get("remote", True), "counts": {**counts, "failed": len(gaps)}}
    totals = {k: round(sum(v.get(k, 0) for v in by_kind.values() if isinstance(v, dict)), 3)
              for k in ("calls", "cached", "failed", "seconds", "tokens")}
    return entry, totals


def recover(work_dir: Path) -> list[str]:
    """Mark summaries left `pending` by a stopped process as failed (reason interrupted); the job ids. Call at
    server start, when no summary of this server can be running."""
    from kb_intake.package import read_json, write_json
    out = []
    for state in sorted((work_dir / "jobs").glob("*/llm/summary.json")):
        try:
            pending = read_json(state).get("status") == "pending"
        except (OSError, ValueError):
            continue
        if pending:
            write_json(state, {"status": "failed", "reason": "interrupted"})
            out.append(state.parent.parent.name)
    return out
