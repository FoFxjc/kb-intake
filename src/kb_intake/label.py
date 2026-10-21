"""The label stage (intake-design-v2 §2 steps 2-4): detection and page kinds, the text layer, Docling layout + picture
classifier (no table structure, no OCR) -> the region list, gaps, stats and a suggested workflow.

    uploaded -> labeled -> planned        (under jobs.running(job_id, "label"))

Regions (label/regions.jsonl, one row per region, rows in reading order):
- Docling items in its reading order (body, then furniture), stably sorted by page. type = vocab.docling_type;
  a picture takes the type of its classifier's top class (vocab.classified_type, §5.11). A picture whose top class
  is `table` stays a picture with a `table_suspected` gap (source classifier): most likely a table as an image.
- A region that is still a plain picture (classifier top class other than `table`, or no classification) with
  enough native text on it is a `diagram` (the step-d rule: >= 6 characters and >= 100 x the region's share of
  the page).
- Words of the text layer that no region covers become `text` regions, one per run of uncovered words of a
  text-layer block (split where a covered word or a joined box would take words of another region), with
  label_source `native` and score null; on a page they follow the page's body regions and precede its furniture.
- Pages Docling did not process (failure, timeout, failed page, layout disabled) get only native regions: the
  find_tables tables as `table` regions, then the native text blocks outside them, top to bottom.
- `region_id` = {doc_id}:p{page:04d}:r{n:03d}, numbered in reading order per page; `reading_order` is global.
- `score` is the layout cluster's confidence (null for native regions or when no cluster matched); the classifier's
  ranking is kept in the work-only `classes`.
- layer `furniture` when Docling put the item in the furniture layer or its type is page_header / page_footer.

Gaps (label/gaps.jsonl): low_confidence (layout score or classifier top score below cfg.low_confidence; for a
picture classified `table` the classifier side gives table_suspected instead, never low_confidence), table_suspected (find_tables found a table that no table region
matches with IoU >= 0.5; region_id = the non-table region overlapping it most), no_text_layer (one per page that
is scanned, or has no words and is not blank), lang_mismatch, extractor_degraded (Docling: one gap for the
document, with `pages` when only some pages failed; find_tables: one per page).

Every text comes from the text layer; Docling's text is never read.
"""

import dataclasses
import shutil
import time
from collections import Counter
from importlib.metadata import version as pkg_version

import pymupdf

from kb_intake import detect, findtables, runner, textlayer, vocab, workflow
from kb_intake.config import Config
from kb_intake.engines.docling import DoclingEngine, label_items
from kb_intake.geometry import area, inter, iou
from kb_intake.jobs import Jobs
from kb_intake.package import write_json, write_jsonl

NATIVE_TEXT_MIN = 6          # same thresholds as pages.py and step-d postprocess: a picture with native text on it
NATIVE_TEXT_DENSITY = 100    # is a diagram
SECONDS_PER_PAGE = 1.1       # whole pipeline on OCBC AR 2025 en: about 300 s for 276 pages on CPU
COVER_PAD = 1.0              # a word is covered by a region when its centre lies in the region grown by this


def _gap_key(g: dict) -> tuple:
    return (g["page_no"] or 0, g["code"], g.get("region_id") or "", g.get("table_id") or "", g["detail"])


def sort_gaps(gaps: list[dict]) -> list[dict]:
    return sorted(gaps, key=_gap_key)


def gap(code: str, page_no: int | None, detail: str, **extra) -> dict:
    """A gaps.jsonl row; optional fields are left out when None (the schema does not allow null for them)."""
    if code not in vocab.GAP_CODES:
        raise ValueError(f"unknown gap code {code!r}")
    return {"code": code, "page_no": page_no, **{k: v for k, v in extra.items() if v is not None}, "detail": detail}


def _fmt(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.3f}"


# ---------- regions ----------

def _model_region(item: dict, page: pymupdf.Page, words: list, page_spans: list, thr: float) -> tuple[dict, list]:
    """(region without id/order, gaps keyed later by the region) of one Docling item."""
    rtype, subtype = vocab.docling_type(item["label"]), None
    classes, gaps = item["classes"], []
    box = item["bbox"]
    score = item["score"]
    if score is not None and score < thr:
        gaps.append(("low_confidence", f"layout score {_fmt(score)} < {thr}", {"source": "layout"}))
    if rtype == "picture":
        top = classes[0] if classes else None
        top_class = top[0] if top else None
        if top_class == vocab.CLASSIFIER_TABLE:
            gaps.append(("table_suspected", f"picture classifier: table ({_fmt(top[1])}); probably a table as an "
                         "image, which needs OCR", {"source": "classifier"}))
        else:
            rtype, subtype = vocab.classified_type(top_class)
            if top is not None and top[1] is not None and top[1] < thr:
                gaps.append(("low_confidence", f"picture classifier top class {top_class} {_fmt(top[1])} < {thr}",
                             {"source": "classifier"}))
            if rtype == "picture":
                chars = sum(len(w[4]) for w in textlayer.words_in(words, box))
                share = area(box) / max(1e-9, page.rect.width * page.rect.height)
                if chars >= max(NATIVE_TEXT_MIN, NATIVE_TEXT_DENSITY * share):
                    rtype = "diagram"
    layer = "furniture" if item["content_layer"] == "furniture" or rtype in ("page_header", "page_footer") \
        else "body"
    region = {"page_no": item["page_no"], "bbox": box, "type": rtype, "subtype": subtype, "score": score,
              "label_source": "model", "layer": layer, "font": textlayer.font_of(page_spans, box),
              "docling_label": item["label"], "classes": classes}
    return region, gaps


def _native_region(page_no: int, rtype: str, box: list, page_spans: list) -> dict:
    return {"page_no": page_no, "bbox": box, "type": rtype, "subtype": None, "score": None, "label_source": "native",
            "layer": "body", "font": textlayer.font_of(page_spans, box), "docling_label": None, "classes": None}


def _union(ws: list) -> list[float]:
    return [min(w[0] for w in ws), min(w[1] for w in ws), max(w[2] for w in ws), max(w[3] for w in ws)]


def _uncovered_blocks(words: list, boxes: list) -> list[list[float]]:
    """Boxes of the runs of words no box covers, in text-layer order: a run is uncovered words of one text-layer
    block that follow each other in that order. Lines of a run are joined only while the joined box holds no covered
    word, so a native region never takes words of a region that covers part of its block."""
    covered = [any(textlayer.centre_in(w, b, COVER_PAD) for b in boxes) for w in words]
    hidden = [w for w, c in zip(words, covered) if c]
    segments: list[list] = []        # uncovered words of one line, broken by covered words
    prev = None
    for w, c in zip(words, covered):
        if c:
            prev = None
            continue
        if prev is not None and (prev[5], prev[6]) == (w[5], w[6]):
            segments[-1].append(w)
        else:
            segments.append([w])
        prev = w
    runs: list[list] = []
    for seg in segments:
        if runs and runs[-1][-1][5] == seg[0][5]:
            box = _union(runs[-1] + seg)
            if not any(textlayer.centre_in(h, box) for h in hidden):
                runs[-1] += seg
                continue
        runs.append(seg)
    return [_union(ws) for ws in runs]


# ---------- the stage ----------

def run(jobs: Jobs, job_id: str, cfg: Config, engine=None, progress=None) -> dict:
    """Label the job's source and move it to `planned` with a suggested workflow; return the stats."""
    def emit(step: str, status: str, info: dict | None = None):
        jobs.event(job_id, step, status, info)
        if progress:
            progress(step, status, info or {})

    with jobs.running(job_id, "label") as job:
        try:
            return _run(jobs, job, cfg, engine, emit)
        except Exception as e:
            emit("label", "failed", {"error": f"{type(e).__name__}: {e}"[:300]})
            raise


def _run(jobs: Jobs, job, cfg: Config, engine, emit) -> dict:
    job_id = job.id
    t0 = time.monotonic()
    out = job.dir / "label"
    if out.exists():
        shutil.rmtree(out)            # a retry starts from nothing
    out.mkdir(parents=True)
    emit("detect", "started")
    decl = dict(job.declaration)
    declared_lang = decl.get("lang") or None
    detection, page_rows = detect.detect_pdf(str(job.source), declared_lang)
    filled = []
    if declared_lang is None and detection["detected_lang"]:
        decl["lang"] = detection["detected_lang"]     # stored only once the stage has succeeded, so a retry
        filled.append("lang")                         # after a failure fills (and records) it again
    emit("detect", "done", {"pages": detection["page_count"], "page_kinds": detection.get("page_kinds")})

    engine = engine or DoclingEngine(cfg.models_dir, cfg.docling, stage="label")
    info = engine.info()
    emit("docling", "started")
    if cfg.layout_enabled:
        outcome = runner.run(engine, job.source, job.sha256, cfg.cache_dir, cfg.docling.wall_timeout, info=info)
    else:
        outcome = runner.Outcome("disabled", None, None, False, None, errors=["layout disabled"])
    items, engine_stats = [], {"dropped_table_cells": 0, "unscored": 0}
    if outcome.data is not None:
        try:
            li = label_items(outcome.data, outcome.clusters)
            items = li["items"]
            engine_stats = {"dropped_table_cells": li["dropped_table_cells"], "unscored": li["unscored"]}
        except Exception as e:  # noqa: BLE001 - an unreadable result degrades like a failed conversion
            outcome = dataclasses.replace(outcome, status="error",
                                          errors=[*outcome.errors, f"{type(e).__name__}: {e}"[:300]])
            items = []
    if not items and outcome.status not in ("success", "partial_success"):
        degraded = None               # the whole document
    else:
        degraded = sorted(set(outcome.failed_pages))
    emit("docling", "done", {"status": outcome.status, "cached": outcome.cached, "seconds": outcome.seconds})
    if outcome.data is not None:
        (out / "docling.json").write_bytes(outcome.data)

    regions, gaps, pages, layouts = _label_pages(job, cfg, items, degraded, outcome, page_rows, detection)
    if detection.get("lang_matches") is False:
        gaps.append(gap("lang_mismatch", None, f"declared lang {declared_lang}, detected "
                                               f"{detection['detected_lang']}"))
    gaps = sort_gaps(gaps)
    write_jsonl(out / "pages.jsonl", pages)
    write_jsonl(out / "layout.jsonl", layouts)
    write_jsonl(out / "regions.jsonl", regions)
    write_jsonl(out / "gaps.jsonl", gaps)
    emit("regions", "done", {"regions": len(regions), "gaps": len(gaps)})

    codes = Counter(g["code"] for g in gaps)
    kinds = detection.get("page_kinds") or dict(sorted(Counter(r["kind"] for r in page_rows).items()))
    seconds = round(time.monotonic() - t0, 1)
    stats = {"pages": detection["page_count"], "pages_by_kind": kinds, "regions": len(regions),
             "regions_by_type": dict(sorted(Counter(r["type"] for r in regions).items())),
             "low_confidence": codes["low_confidence"], "table_suspected": codes["table_suspected"],
             "estimated_extract_seconds": _estimate(engine, job, cfg, detection["page_count"]),
             "label_seconds": seconds}
    wf, notes = workflow.suggest(stats)
    stats["workflow_notes"] = notes
    write_json(out / "stats.json", stats)
    write_json(out / "run.json", {
        "seconds": seconds,
        "engine": {"status": outcome.status, "seconds": outcome.seconds, "cached": outcome.cached,
                   "cache_key": outcome.cache_key, "failed_pages": list(outcome.failed_pages),
                   "errors": list(outcome.errors), **engine_stats},
        "engines": {"docling": info.version, "models": list(info.models), "params": {"label": info.params},
                    "pymupdf": pkg_version("pymupdf"), "device": cfg.docling.device},
        "detection": detection, "declaration_filled": filled})

    jobs.finish_label(job_id, workflow.dump(wf), decl if filled else None)
    emit("label", "done", {"regions": len(regions), "gaps": len(gaps), "seconds": seconds})
    return stats


def _estimate(engine, job, cfg: Config, page_count: int) -> int:
    """Seconds the extract stage will take: 0 when its Docling output is already cached."""
    try:
        if runner.cached(dataclasses.replace(engine, stage="extract").info(), job.sha256, cfg.cache_dir):
            return 0
    except Exception:  # noqa: BLE001 - an engine without stages, unreadable cache: just estimate
        pass
    return round(page_count * SECONDS_PER_PAGE)


def _label_pages(job, cfg: Config, items: list[dict], degraded: list[int] | None, outcome, page_rows: list[dict],
                 detection: dict) -> tuple[list, list, list, list]:
    """(regions, gaps, pages rows, layout rows). `degraded` is None when Docling gave nothing usable at all, else
    the pages it failed."""
    thr = cfg.low_confidence
    labels = detection.get("page_labels") or []
    kinds = {r["page_no"]: r["kind"] for r in page_rows}
    gaps: list[dict] = []
    if degraded is None:
        detail = "; ".join(outcome.errors)[:300] or "no output"
        gaps.append(gap("extractor_degraded", None, f"docling {outcome.status}: {detail}; regions come from the text "
                        "layer and find_tables", extractor="docling"))
    elif degraded:
        gaps.append(gap("extractor_degraded", None, f"docling did not complete {len(degraded)} page(s); their regions "
                        "come from the text layer and find_tables", extractor="docling", pages=list(degraded)))
    by_page: dict[int, list] = {}
    for it in items:
        by_page.setdefault(it["page_no"], []).append(it)     # stable: Docling's reading order within the page
    regions: list[dict] = []
    pages, layouts = [], []
    with pymupdf.open(job.source) as pdf:
        for i, page in enumerate(pdf):
            page_no = i + 1
            words, page_spans = textlayer.words(page), textlayer.spans(page)
            kind = kinds.get(page_no)
            pages.append(textlayer.page_record(page, page_no, labels[i] if i < len(labels) else None, kind))
            layouts.append(textlayer.layout_record(page_no, words))
            if kind == "scanned" or (not words and kind != "blank"):     # a blank page lacks nothing
                gaps.append(gap("no_text_layer", page_no, f"page has no text layer (kind {kind})"))
            try:
                found = findtables.tables_on(page)
            except Exception as e:  # noqa: BLE001 - find_tables failing on a page only loses its hints
                found = []
                gaps.append(gap("extractor_degraded", page_no, f"find_tables: {type(e).__name__}: {e}"[:300],
                                extractor=findtables.METHOD))
            if degraded is None or page_no in degraded:
                page_regions = _fallback_page(page_no, found, words, page_spans)
                page_gaps: list[tuple[int, tuple]] = []
            else:
                page_regions, page_gaps = _docling_page(page, by_page.get(page_no, []), words, page_spans, thr)
            for n, r in enumerate(page_regions, 1):
                r["region_id"] = f"{job.doc_id}:p{page_no:04d}:r{n:03d}"
            for idx, (code, detail, extra) in page_gaps:
                gaps.append(gap(code, page_no, detail, region_id=page_regions[idx]["region_id"], **extra))
            if degraded is None or page_no not in degraded:
                gaps += _suspected(page_no, found, page_regions)
            regions += page_regions
    rows = []
    for order, r in enumerate(regions):
        rows.append({"region_id": r["region_id"], "page_no": r["page_no"], "bbox": r["bbox"], "type": r["type"],
                     "subtype": r["subtype"], "score": r["score"], "label_source": r["label_source"],
                     "layer": r["layer"], "reading_order": order, "font": r["font"],
                     "docling_label": r["docling_label"], "classes": r["classes"]})
    return rows, gaps, pages, layouts


def _docling_page(page, items: list[dict], words: list, page_spans: list, thr: float):
    """Regions of a page Docling processed: its items (body first, then furniture) with the native text blocks
    no item covers between the two. Gaps are (index of the region, (code, detail, extra))."""
    body, furniture, gaps_b, gaps_f = [], [], [], []
    for it in items:
        region, gs = _model_region(it, page, words, page_spans, thr)
        target, tgaps = (furniture, gaps_f) if region["layer"] == "furniture" else (body, gaps_b)
        tgaps += [(len(target), g) for g in gs]
        target.append(region)
    native = [_native_region(page.number + 1, "text", b, page_spans)
              for b in _uncovered_blocks(words, [r["bbox"] for r in body + furniture])]
    offset = len(body) + len(native)
    regions = body + native + furniture
    return regions, [*gaps_b, *[(offset + i, g) for i, g in gaps_f]]


def _fallback_page(page_no: int, found: list[dict], words: list, page_spans: list) -> list[dict]:
    tables = [_native_region(page_no, "table", t["bbox"], page_spans) for t in found]
    texts = [_native_region(page_no, "text", b, page_spans)
             for b in _uncovered_blocks(words, [t["bbox"] for t in tables])]
    return sorted(tables + texts, key=lambda r: (r["bbox"][1], r["bbox"][0]))


def _suspected(page_no: int, found: list[dict], regions: list[dict]) -> list[dict]:
    """table_suspected gaps for find_tables tables that no table region matches (IoU >= 0.5)."""
    out = []
    tables = [r["bbox"] for r in regions if r["type"] == "table"]
    for t in found:
        if any(iou(t["bbox"], b) >= 0.5 for b in tables):
            continue
        overlap = [(inter(t["bbox"], r["bbox"]), -i) for i, r in enumerate(regions) if r["type"] != "table"]
        best = max(overlap, default=(0.0, 0))
        rid = regions[-best[1]]["region_id"] if best[0] > 0 else None
        out.append(gap("table_suspected", page_no, f"find_tables found a {t['n_rows']} x {t['n_cols']} table that "
                       "is not a table region", region_id=rid, source="find_tables", bbox=t["bbox"]))
    return out
