"""The processing flow of one file (intake-design §3.1):

    0 source -> 1 detection and page kinds -> 2 text layer (PyMuPDF) -> 3 layout engine (Docling: regions, tables)
    -> 4 secondary tables (find_tables) -> 5-7 post-processing and the three checks -> heading candidates -> 8 package

Failure isolation (§9.3.5, §9.3.6): the text layer is written whatever happens to the layout engine. The engine runs
in a child process with a hard timeout; when it is not ready, times out, crashes, or post-processing fails, the file
falls back to the find_tables tables (as without an engine) and the document gets one `extractor_degraded` gap.
Pages the engine could not convert fall back the same way, page by page.

`progress(step, status, info)` is called when a step starts ("running") and ends ("done"), for the run view of the
web UI; it never changes the package.
"""

import hashlib
import platform
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pymupdf

from kb_intake import CONTRACT_VERSION, __version__, checks, declaration, deps, detect, headings, models, runner, secondary
from kb_intake import postprocess as post
from kb_intake import textlayer
from kb_intake.config import Config
from kb_intake.engines import LAYOUT
from kb_intake.package import DATA_FILES, Builder, IntakeError, existing, name, write_jsonl


def _quiet(step: str, status: str, info: dict | None = None) -> None:
    pass


class _Steps:
    """Reports step boundaries with their durations to a progress callback."""

    def __init__(self, progress):
        self.progress, self.t = progress or _quiet, {}

    def start(self, step: str, **info) -> None:
        self.t[step] = time.monotonic()
        self.progress(step, "running", info)

    def done(self, step: str, **info) -> None:
        ms = int((time.monotonic() - self.t.pop(step, time.monotonic())) * 1000)
        self.progress(step, "done", {**info, "duration_ms": ms})


def process(file: Path, decl: dict, out: Path, config: Config | None = None, replace: bool = False,
            force: bool = False, progress=None) -> Path:
    """Build the package of one file under `out`; return its directory.

    The same doc_id with the same file rewrites the package; the same doc_id with a different file is refused unless
    `replace`. `force` runs the layout engine although it is not ready (debugging only; recorded in the manifest)."""
    cfg = config or Config()
    steps = _Steps(progress)
    started, t0 = datetime.now(timezone.utc), time.monotonic()
    decl = declaration.validate(decl)
    data = file.read_bytes()
    kind = detect.sniff(data)
    if kind is None:
        raise declaration.DeclarationError("file", "unsupported file type (only PDF is implemented)")
    sha = hashlib.sha256(data).hexdigest()
    pkg_name = name(decl["doc_id"], sha)
    others = [p for p in existing(out, decl["doc_id"]) if p.name != pkg_name]
    if others and not replace:
        raise IntakeError(f"{decl['doc_id']} already has a package from a different file: {others[0].name}")

    # 1 detection; an omitted lang is taken from the detection
    steps.start("detect")
    detection, kinds = detect.detect_pdf(str(file), decl.get("lang"))
    labels = detection.pop("page_labels")
    filled = []
    if decl.get("lang") is None:
        if detection["detected_lang"] is None:
            raise declaration.DeclarationError("lang", "lang is not declared and could not be detected; declare it")
        decl["lang"], filled = detection["detected_lang"], ["lang"]
    gaps: list[dict] = []
    if not detection["has_text_layer"]:
        gaps.append({"code": "no_text_layer", "detail": "the PDF has no text layer; flow B (OCR) is not implemented"})
    if detection["lang_matches"] is False:
        gaps.append({"code": "lang_mismatch", "declared": decl["lang"], "detected": detection["detected_lang"]})
    if "page_kinds_error" in detection:
        gaps.append({"code": "extractor_degraded", "extractor": "page_kinds", "detail": detection["page_kinds_error"]})
    steps.done("detect", pages=detection["page_count"], flow=detect.FLOWS[kind], lang=decl["lang"],
               page_kinds=detection.get("page_kinds"), has_text_layer=detection["has_text_layer"])

    b = Builder(out, pkg_name)
    try:
        (b.dir / f"source.{kind}").write_bytes(data)
        working = b.dir / "working.pdf"
        working.write_bytes(data)                      # a native PDF is its own working PDF
        with pymupdf.open(working) as pdf:
            # 2 text layer, 4 secondary tables
            steps.start("text_layer")
            by_page = {k["page_no"]: k for k in kinds}
            pages_rows = [textlayer.page_record(p, i + 1, labels[i], by_page.get(i + 1)) for i, p in enumerate(pdf)]
            layout_rows = [textlayer.layout_record(p, i + 1) for i, p in enumerate(pdf)]
            words = {r["page_no"]: [w[:5] for w in r["words"]] for r in layout_rows}
            steps.done("text_layer", words=sum(len(w) for w in words.values()))
            steps.start("tables_secondary")
            ft_tables = []
            if cfg.secondary == "find_tables":
                for i, page in enumerate(pdf):
                    t, g = secondary.tables_on(page, decl["doc_id"], i + 1, words[i + 1])
                    ft_tables += t
                    gaps += g
            steps.done("tables_secondary", tables=len(ft_tables))
            # 3 layout engine, 5-7 post-processing and checks, heading candidates
            layer = _extraction(pdf, working, b.dir, decl["doc_id"], sha, cfg, force, ft_tables, words, pages_rows,
                                steps)
        steps.start("package")
        gaps += layer["gaps"]
        counts = {"pages.jsonl": write_jsonl(b.dir / "pages.jsonl", pages_rows),
                  "layout.jsonl": write_jsonl(b.dir / "layout.jsonl", layout_rows),
                  "regions.jsonl": write_jsonl(b.dir / "regions.jsonl", layer["regions"]),
                  "tables.jsonl": write_jsonl(b.dir / "tables.jsonl", layer["tables"]),
                  "tables_secondary.jsonl": write_jsonl(b.dir / "tables_secondary.jsonl", layer["tables_secondary"]),
                  "headings.jsonl": write_jsonl(b.dir / "headings.jsonl", layer["headings"]),
                  "gaps.jsonl": write_jsonl(b.dir / "gaps.jsonl", gaps)}
        extra = {"docling.json": None} if (b.dir / "docling.json").exists() else {}
        manifest = {
            "contract_version": CONTRACT_VERSION,
            "package": pkg_name,
            "doc_id": decl["doc_id"],
            "declaration": decl,
            "declaration_filled": filled,
            "source": {"filename": file.name, "ext": kind, "media_type": "application/pdf", "sha256": sha,
                       "bytes": len(data)},
            "detection": detection,
            "flow": detect.FLOWS[kind],
            "steps": [
                {"step": "detect", "tool": "pymupdf", "version": pymupdf.VersionBind, "params": {}},
                {"step": "page_labels", "tool": "kb_intake.labels", "version": __version__,
                 "params": {"method": detection["page_label_method"]["method"]}},
                {"step": "text_layer", "tool": "pymupdf", "version": pymupdf.VersionBind, "params": {}},
                {"step": "words", "tool": "pymupdf", "version": pymupdf.VersionBind, "params": {}},
                {"step": "tables_secondary", "tool": "pymupdf.find_tables" if cfg.secondary == "find_tables" else None,
                 "version": pymupdf.VersionBind, "params": {"pymupdf_layout": secondary.PYMUPDF_LAYOUT},
                 "status": "ok" if cfg.secondary == "find_tables" else "not_run"},
                *layer["steps"],
            ],
            "files": b.files({f"source.{kind}": None, "working.pdf": None, **extra,
                              **{f: counts[f] for f in DATA_FILES}}),
            "cross_checks": layer["summary"],
            "degraded": any(g["code"] == "extractor_degraded" for g in gaps),
            "llm": {"used": False},
            "kb_intake_version": __version__,
            "run": {},
        }
        manifest["run"] = {"started_at": started.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
                           "duration_ms": int((time.monotonic() - t0) * 1000), "python": sys.version.split()[0],
                           "platform": platform.platform(), "layout": layer["run"]}
        final = b.finish(manifest, others if replace else [])
        steps.done("package", package=pkg_name, degraded=manifest["degraded"])
        return final
    finally:
        b.close()


def _extraction(pdf, working: Path, pkg_dir: Path, doc_id: str, sha: str, cfg: Config, force: bool,
                ft_tables: list[dict], words: dict, pages_rows: list[dict], steps: _Steps | None = None) -> dict:
    """Layout engine + post-processing + checks + headings. Never raises for an engine, runner, cache or
    post-processing failure: those degrade to the find_tables tables, as without an engine."""
    engine = LAYOUT[cfg.primary](models_dir=cfg.models_dir, params=cfg.docling)
    pins = models.pins()
    layout_model, layout_version = pins[0]["repo_id"], pins[0]["revision"]
    steps = steps or _Steps(None)
    info = None
    steps.start("layout", engine=cfg.primary)
    outcome = runner.Outcome("disabled", None, None, False, None, errors=["layout engine disabled (--no-layout)"])
    if cfg.layout_enabled:
        try:
            info = engine.info()
            outcome = runner.run(engine, working, sha, cfg.cache_dir, cfg.docling.wall_timeout, info, force)
        except Exception as e:  # noqa: BLE001 - the engine or its runner failed before producing output
            outcome = runner.Outcome("error", None, None, False, None, errors=[f"{type(e).__name__}: {e}"[:500]])
    steps.done("layout", status=outcome.status, cached=outcome.cached, seconds=outcome.seconds,
               failed_pages=outcome.failed_pages, errors=outcome.errors[:3])
    steps.start("postprocess")
    all_pages = set(range(1, len(pdf) + 1))
    lines = headings.lines(pdf)
    profiles = headings.load_profiles(cfg.font_profiles)
    full_words: dict[int, list] = {}

    def page_words(page_no):
        if page_no not in full_words:
            full_words[page_no] = pdf[page_no - 1].get_text("words")
        return full_words[page_no]

    layer, post_error, covered = None, None, set()
    if outcome.data is not None and outcome.status in ("success", "partial_success"):
        (pkg_dir / "docling.json").write_bytes(outcome.data)
        try:
            covered = all_pages - set(outcome.failed_pages)
            font_heads, _ = headings.headings(lines, [], profiles)   # what a table sits under (continued tables)
            primary, secondary_, regions, summary, gaps = _assemble(
                pdf, engine, outcome.data, doc_id, covered, ft_tables, words, page_words, font_heads,
                layout_model, layout_version)
            layer = _finish(primary, secondary_, regions, summary, gaps, lines, profiles, page_words)
        except Exception as e:  # noqa: BLE001 - post-processing must not fail the file (§9.3.5)
            post_error = f"post-processing failed: {type(e).__name__}: {e}"[:500]
            layer, covered = None, set()
    if layer is None:                                   # no usable engine output: as without an engine
        primary, secondary_, gaps, summary = checks.combine(doc_id, [], ft_tables, set(), [], {}, words)
        layer = _finish(primary, secondary_, [], summary, gaps, lines, profiles, page_words)
    else:
        for p in pages_rows:
            c = outcome.page_confidence.get(str(p["page_no"]))
            p["docling_confidence"] = c["grade"] if c else None
    steps.done("postprocess", engine_pages=len(covered), postprocess_error=post_error, summary=layer["summary"])
    if covered != all_pages:
        layer["gaps"].insert(0, {"code": "extractor_degraded", "extractor": cfg.primary, "status": outcome.status,
                                 "pages": "all" if not covered else sorted(all_pages - covered),
                                 "detail": (post_error or "; ".join(outcome.errors)
                                            or f"status {outcome.status}")[:500]})
    layer["steps"] = [
        {"step": "layout", "tool": cfg.primary, "version": info.version if info else None,
         "params": info.params if info else cfg.docling.engine_params(), "models": pins,
         "deps": deps.closure() if info else None, "cache_key": outcome.cache_key, "status": outcome.status,
         "ready": info.ready if info else None, "forced": bool(force and info and not info.ready),
         "failed_pages": sorted(all_pages - covered), "postprocess_error": post_error},
        {"step": "regions", "tool": "kb_intake.postprocess", "version": __version__,
         "params": {"layout_model": layout_model, "layout_version": layout_version},
         "counts": dict(sorted(Counter(r["type"] for r in layer["regions"]).items()))},
        {"step": "headings", "tool": "kb_intake.headings", "version": __version__, "params": layer.pop("heading_summary")},
        {"step": "tables", "tool": "kb_intake.checks", "version": __version__,
         "params": {"primary": cfg.primary, "secondary": cfg.secondary}, "summary": layer["summary"]},
    ]
    layer["run"] = {"cached": outcome.cached, "seconds": outcome.seconds, "cache_written": outcome.cache_written}
    return layer


def _finish(primary, secondary_, regions, summary, gaps, lines, profiles, page_words) -> dict:
    """Heading candidates (corroborated by the engine's headings, if any), the tables.jsonl rows with
    context_above (headings and page furniture end a table's context) and the tables_secondary.jsonl rows.
    context_above is computed table by table; a failure leaves that table's context empty with a gap."""
    gaps = list(gaps)
    engine_heads = [r for r in regions if r["type"] in ("title", "heading")]
    try:
        heading_rows, heading_summary = headings.headings(lines, engine_heads, profiles)
    except Exception as e:  # noqa: BLE001
        heading_rows, heading_summary = [], {"error": f"{type(e).__name__}: {e}"[:300]}
        gaps.append({"code": "extractor_degraded", "extractor": "headings", "detail": heading_summary["error"]})
    stops: dict[int, list] = {}
    for h in heading_rows:
        stops.setdefault(h["page_no"], []).append(h["bbox"])
    for r in regions:
        if r["type"] in ("page_header", "page_footer", "heading", "title"):
            stops.setdefault(r["page_no"], []).append(r["bbox"])
    tables = []
    lines_by_page: dict[int, list] = {}
    for t in primary:
        page = t["pages"][0]
        try:
            if page not in lines_by_page:
                lines_by_page[page] = post.text_lines(page_words(page))
            others = [o["bboxes"][0] for o in primary if o is not t and o["pages"][0] == page]
            context = post.context_above(t["bboxes"][0], lines_by_page[page], others, stops.get(page, []))
        except Exception as e:  # noqa: BLE001 - one table's context never fails the file
            context = []
            gaps.append({"code": "extractor_degraded", "extractor": "context_above", "page_no": page,
                         "table_id": t["table_id"], "detail": f"{type(e).__name__}: {e}"[:300]})
        tables.append(checks.row(t, context))
    return {"regions": regions, "tables": tables, "tables_secondary": [checks.row(t, []) for t in secondary_],
            "headings": heading_rows, "heading_summary": heading_summary, "gaps": gaps, "summary": summary}


def _assemble(pdf, engine, data: bytes, doc_id: str, covered: set[int], ft_tables, words, page_words, font_heads,
              layout_model: str, layout_version: str):
    lr = engine.interpret(data, doc_id)

    def page_text(page_no, box):
        return sum(1 for c in pdf[page_no - 1].get_text(clip=pymupdf.Rect(box)) if not c.isspace())

    regions, refs = post.regions(lr.regions, doc_id, page_text, layout_model, layout_version)
    blocks = {i + 1: [b for b in page.get_text("blocks") if b[6] == 0 and b[4].strip()] for i, page in enumerate(pdf)}
    regions += post.native_only(regions, blocks, doc_id, len(regions), layout_model, layout_version)
    engine_tables, gaps = post.tables(lr.tables, words, page_words)
    heads = sorted(((h["page_no"], h["bbox"][1], h["text"]) for h in font_heads
                    if not (h["furniture"] and h["level"] == 1)), key=lambda h: (h[0], h[1]))
    body = [r for r in regions if r["content_layer"] == "body" and r["type"] in ("text", "list", "caption", "heading", "title")]

    def context(t):
        """(heading above the table on its page, else None; the heading the table sits under; whether other content
        stands between that heading, or the page top, and the table). Only "(continued)" lines may stand between."""
        page, top = t["pages"][0], t["bboxes"][0][1]
        before = [h for h in heads if (h[0], h[1]) < (page, top)]
        own = next((h for h in reversed(before) if h[0] == page), None)
        floor = own[1] + 1 if own else 0.0
        between = any(r["page_no"] == page and r["bbox"][1] > floor and r["bbox"][3] <= top + 1
                      and not (own and abs(r["bbox"][1] - own[1]) < 3)
                      and not post.CONTINUED.search(pdf[page - 1].get_text(clip=pymupdf.Rect(r["bbox"])))
                      for r in body)
        return (own[2] if own else None), (before[-1][2] if before else None), between
    engine_tables = post.merge_continued(engine_tables, context)
    primary, secondary_, gaps, summary = checks.combine(doc_id, engine_tables, ft_tables, covered, regions, refs, words,
                                                        gaps)          # header / dropped-cell gaps get their table_id
    return primary, secondary_, regions, summary, gaps
