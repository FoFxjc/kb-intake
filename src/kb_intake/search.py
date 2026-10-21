"""Search, filters and facets over a job's regions (intake-ui-llm-design-v1 §3.2).

- Rows: the label-stage regions before extraction, the current ones after (statuses extracted ... absorbed). Each row
  carries `text`: the region's own text, or, where that is null (label regions; discarded, recorded, excluded or
  absorbed regions), the text by box from the text layer, the rule the `text` handler uses. That text is computed
  once per job into `jobs/<id>/search/label_text.jsonl`; delivered files never carry it. `gaps` lists the codes of
  the region's open (not resolved) gaps.
- Index: an FTS5 table in the job store's state.db, one row per region text, per non-empty table cell (a table
  region with a table is indexed by its cells, so a hit points at the cell) and per LLM text. It is rebuilt for a
  job when `signature(job.dir)` (sizes and mtimes of the work files it reads) changed, so a review applied from the
  CLI shows up on the next search.
- Query text: each whitespace-separated word is a quoted prefix term (`"word"*`), all of them required; FTS5 syntax
  in the input is matched as text. A word without letters or digits matches nothing.
- Results: in reading order, paged with a cursor (the reading_order of the last item). Facets count each dimension
  with every filter except its own.
"""

import hashlib
import json
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from kb_intake import textlayer
from kb_intake.jobs import Jobs
from kb_intake.package import read_jsonl, sha_file, write_jsonl
from kb_intake.vocab import GAP_CODES, REGION_TYPES, STATUSES

POST_EXTRACT = ("extracted", "in_review", "confirmed", "delivered")
SIGNED_FILES = ("label/regions.jsonl", "extract/regions.jsonl", "extract/tables.jsonl", "extract/gaps.jsonl",
                "review/gaps.jsonl", "review/resolved.jsonl", "llm/llm.jsonl")
SOURCES = ("region", "cell", "llm")
PREVIEW = 200
FTS = """create virtual table if not exists search_fts using fts5(
  job_id unindexed, region_id unindexed, source unindexed, ref unindexed, text,
  tokenize = "unicode61 remove_diacritics 2");
create table if not exists search_meta (job_id text primary key, signature text not null);"""


def fts5_available() -> bool:
    try:
        sqlite3.connect(":memory:").execute("create virtual table temp.t using fts5(x)")
        return True
    except sqlite3.OperationalError:
        return False


@dataclass
class Filters:
    q: str = ""
    types: list[str] = field(default_factory=list)
    statuses: list[str] = field(default_factory=list)
    page: int | None = None
    gaps: list[str] = field(default_factory=list)
    min_score: float | None = None
    max_score: float | None = None

    def check(self) -> None:
        for name, values, known in (("type", self.types, REGION_TYPES), ("status", self.statuses, STATUSES),
                                    ("gap", self.gaps, GAP_CODES)):
            bad = sorted(set(values) - set(known))
            if bad:
                raise ValueError(f"unknown {name} {bad} (known: {list(known)})")


@dataclass
class Rows:
    stage: str                       # "label" | "current"
    regions: list[dict]
    tables: list[dict]


# ---------- rows ----------

def _label_text(job_dir: Path, label_regions: list[dict]) -> dict[str, str | None]:
    """region_id -> text by box from the text layer, cached in search/label_text.jsonl (keyed by the label file)."""
    cache = job_dir / "search" / "label_text.jsonl"
    key = sha_file(job_dir / "label" / "regions.jsonl")
    if cache.is_file():
        rows = read_jsonl(cache)
        if rows and rows[0].get("label_regions_sha256") == key:
            return {r["region_id"]: r["text"] for r in rows[1:]}
    out: dict[str, str | None] = {}
    by_page: dict[int, list[dict]] = {}
    for r in label_regions:
        by_page.setdefault(r["page_no"], []).append(r)
    with pymupdf.open(job_dir / "source.pdf") as pdf:
        for page_no, regions in sorted(by_page.items()):
            words = textlayer.words(pdf[page_no - 1])
            for r in regions:
                out[r["region_id"]] = textlayer.text_of(textlayer.words_in(words, r["bbox"]))
    cache.parent.mkdir(exist_ok=True)
    write_jsonl(cache, [{"label_regions_sha256": key}] + [{"region_id": k, "text": v} for k, v in out.items()])
    return out


_ROWS: dict[tuple, Rows] = {}            # (job dir, status, signature) -> rows; the last few jobs only
_ROWS_KEPT = 4
_ROWS_LOCK = threading.Lock()


def load_rows(jobs: Jobs, job_id: str) -> Rows:
    """The searchable rows of a job, reused while its work files and state stay the same (callers must not change
    them). KeyError for an unknown job or one that is not labelled yet."""
    job = jobs.get(job_id)
    key = (str(job.dir), job.status, signature(job.dir))
    with _ROWS_LOCK:
        if key in _ROWS:
            return _ROWS[key]
    rows = _load_rows(job)
    with _ROWS_LOCK:
        for old in [k for k in _ROWS if k[0] == key[0]]:
            del _ROWS[old]
        _ROWS[key] = rows
        while len(_ROWS) > _ROWS_KEPT:
            del _ROWS[next(iter(_ROWS))]
    return rows


def _load_rows(job) -> Rows:
    from kb_intake import review           # review imports extract; keep this module light to import
    job_id = job.id
    d = job.dir
    if not (d / "label" / "regions.jsonl").is_file():
        raise KeyError(f"job {job_id} is not labelled yet")
    label_regions = read_jsonl(d / "label" / "regions.jsonl")
    by_box = _label_text(d, label_regions)
    if job.status in POST_EXTRACT and (d / "extract" / "regions.jsonl").is_file():
        state = review.current(d)
        stage, regions, tables, gaps = "current", state["regions"], state["tables"], review.all_gaps(d, state)
    else:
        stage, regions, tables = "label", label_regions, []
        gaps = read_jsonl(d / "label" / "gaps.jsonl") if (d / "label" / "gaps.jsonl").is_file() else []
    open_codes: dict[str, set[str]] = {}
    for g in gaps:
        if g.get("region_id") and not g.get("resolved"):
            open_codes.setdefault(g["region_id"], set()).add(g["code"])
    rows = []
    for r in regions:
        text = r.get("text") if stage == "current" else None
        rows.append({**r, "status": r.get("status"), "handler": r.get("handler"), "table_id": r.get("table_id"),
                     "text": text if text is not None else by_box.get(r["region_id"]),
                     "gaps": sorted(open_codes.get(r["region_id"], ())), "llm": []})
    return Rows(stage, rows, tables)


def signature(job_dir: Path) -> str:
    h = hashlib.sha256()
    for rel in SIGNED_FILES:
        p = job_dir / rel
        if p.is_file():
            st = p.stat()
            h.update(f"{rel}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    return h.hexdigest()


# ---------- index ----------

class Index:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        db.executescript(FTS)
        return db

    def ensure(self, job_id: str, rows: Rows, sig: str) -> None:
        db = self._db()
        try:
            row = db.execute("select signature from search_meta where job_id = ?", (job_id,)).fetchone()
            if row and row[0] == sig:
                return
            db.execute("begin immediate")
            row = db.execute("select signature from search_meta where job_id = ?", (job_id,)).fetchone()
            if row and row[0] == sig:          # another request rebuilt it meanwhile
                db.execute("rollback")
                return
            db.execute("delete from search_fts where job_id = ?", (job_id,))
            db.executemany("insert into search_fts (job_id, region_id, source, ref, text) values (?, ?, ?, ?, ?)",
                           [(job_id, *x) for x in _documents(rows)])
            db.execute("insert or replace into search_meta (job_id, signature) values (?, ?)", (job_id, sig))
            db.execute("commit")
        finally:
            db.close()

    def match(self, job_id: str, q: str) -> dict[str, dict]:
        """region_id -> the best match {source, snippet, bbox, table_id, r, c} (region before cell before llm)."""
        expr = fts_query(q)
        if expr is None:
            return {}
        db = self._db()
        try:
            hits = db.execute("select region_id, source, ref, snippet(search_fts, 4, '«', '»', '…', 12) "
                              "from search_fts where search_fts match ? and job_id = ? order by rowid",
                              (expr, job_id)).fetchall()
        finally:
            db.close()
        best: dict[str, dict] = {}
        for region_id, source, ref, snippet in hits:
            if region_id in best and SOURCES.index(best[region_id]["source"]) <= SOURCES.index(source):
                continue
            ref = json.loads(ref)
            best[region_id] = {"source": source, "snippet": snippet, "bbox": ref.get("bbox"),
                               "table_id": ref.get("table_id"), "r": ref.get("r"), "c": ref.get("c")}
        return best


def _documents(rows: Rows):
    tables = {t["region_id"]: t for t in rows.tables}
    for r in rows.regions:
        table = tables.get(r["region_id"])
        if table is not None:
            for c in table["cells"]:
                if c.get("text"):
                    yield (r["region_id"], "cell", json.dumps({"table_id": table["table_id"], "r": c["r"], "c": c["c"],
                                                              "bbox": c.get("bbox") or table["bbox"]}), c["text"])
        elif r.get("text"):
            yield (r["region_id"], "region", json.dumps({"bbox": r["bbox"]}), r["text"])
        for text in r.get("llm_texts", ()):
            yield (r["region_id"], "llm", json.dumps({"bbox": r["bbox"]}), text)


WORD = re.compile(r"\w", re.UNICODE)


def fts_query(q: str) -> str | None:
    """An FTS5 expression that matches every word of `q` as a prefix; None when no word has a letter or digit
    (nothing can match then)."""
    words = [w for w in q.split() if WORD.search(w)]
    if not words:
        return None
    return " ".join('"' + w.replace('"', '""') + '"*' for w in words)


# ---------- query ----------

def _passes(r: dict, f: Filters, matches: dict | None, skip: str = "") -> bool:
    if matches is not None and r["region_id"] not in matches:
        return False
    if skip != "type" and f.types and r["type"] not in f.types:
        return False
    if skip != "status" and f.statuses and r["status"] not in f.statuses:
        return False
    if skip != "gap" and f.gaps and not set(f.gaps) & set(r["gaps"]):
        return False
    if f.page is not None and r["page_no"] != f.page:
        return False
    if f.min_score is not None and (r["score"] is None or r["score"] < f.min_score):
        return False
    if f.max_score is not None and (r["score"] is None or r["score"] > f.max_score):
        return False
    return True


def _item(r: dict, matches: dict | None) -> dict:
    keys = ("region_id", "page_no", "bbox", "type", "subtype", "score", "label_source", "layer", "reading_order",
            "status", "handler", "table_id", "font")
    text = r["text"]
    return {**{k: r.get(k) for k in keys}, "text_preview": text[:PREVIEW] if text else None, "gaps": r["gaps"],
            "llm": r["llm"], "match": (matches or {}).get(r["region_id"])}


def _prepare(jobs: Jobs, job_id: str, f: Filters) -> tuple[Rows, dict | None]:
    f.check()
    rows = load_rows(jobs, job_id)
    if not f.q.strip():
        return rows, None
    index = Index(jobs.work_dir / "state.db")
    index.ensure(job_id, rows, signature(jobs.get(job_id).dir))
    return rows, index.match(job_id, f.q)


def find(jobs: Jobs, job_id: str, f: Filters, cursor: int | None = None, limit: int = 100) -> dict:
    """{"total", "items", "next_cursor"}: the regions that pass `f`, in reading order, after `cursor`."""
    rows, matches = _prepare(jobs, job_id, f)
    hits = sorted((r for r in rows.regions if _passes(r, f, matches)), key=lambda r: r["reading_order"])
    rest = [r for r in hits if cursor is None or r["reading_order"] > cursor]
    page = rest[:limit]
    more = len(rest) > limit
    return {"total": len(hits), "items": [_item(r, matches) for r in page],
            "next_cursor": page[-1]["reading_order"] if more else None}


def facets(jobs: Jobs, job_id: str, f: Filters) -> dict:
    """{"total", "type": {}, "status": {}, "gap": {}}; each dimension counted without its own filter."""
    rows, matches = _prepare(jobs, job_id, f)
    out: dict = {"total": sum(_passes(r, f, matches) for r in rows.regions), "type": {}, "status": {}, "gap": {}}
    for r in rows.regions:
        if _passes(r, f, matches, skip="type"):
            out["type"][r["type"]] = out["type"].get(r["type"], 0) + 1
        if r["status"] is not None and _passes(r, f, matches, skip="status"):
            out["status"][r["status"]] = out["status"].get(r["status"], 0) + 1
        if _passes(r, f, matches, skip="gap"):
            for code in r["gaps"]:
                out["gap"][code] = out["gap"].get(code, 0) + 1
    return {k: dict(sorted(v.items())) if isinstance(v, dict) else v for k, v in out.items()}
