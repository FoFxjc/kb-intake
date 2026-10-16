"""入库运行（管理接口“触发入库”）：detect → tree → build → load，每个阶段一行 ingest_run（knowledge-model §3.8）。

- detect：文本层、页数、页码来源、语言与声明是否一致（不一致 → 来源状态 review，运行在此停止）
- tree：PageIndex Flash（summary=False、optimize="merge"，不调 LLM）+ toc_flash 混合树（D22）
- build：生成 JSONL（pipeline.build）；completeness.sections 有声明时须与检测一致
- load：按 doc_id 删除后重放到 Doris，写覆盖率和 Postgres 权威表
"""

import json
import re
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pymupdf
from psycopg.types.json import Jsonb

from pipeline import registry
from pipeline.tree import PIPELINE_VERSION, page_labels

ROOT = Path(__file__).resolve().parents[1]


def _now():
    return datetime.now(timezone.utc)


def _mark(pg, run_id, doc_id, stage, status, started=None, finished=None, error=None, detail=None):
    pg.execute("INSERT INTO ingest_run (run_id, source_id, pipeline_version, stage, status, started_at, finished_at, "
               "error, detail) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
               "ON CONFLICT (run_id, stage) DO UPDATE SET status = EXCLUDED.status, "
               "started_at = COALESCE(EXCLUDED.started_at, ingest_run.started_at), finished_at = EXCLUDED.finished_at, "
               "error = EXCLUDED.error, detail = EXCLUDED.detail",
               (run_id, doc_id, PIPELINE_VERSION, stage, status, started, finished, error,
                Jsonb(detail) if detail is not None else None))
    pg.commit()


def detect(decl: dict) -> dict:
    pdf = pymupdf.open(ROOT / decl["path"])
    text = "".join(p.get_text() for p in pdf)
    chars = [c for c in text if not c.isspace()]
    cjk = sum(1 for c in chars if "一" <= c <= "鿿") / max(1, len(chars))
    detected_lang = "zh" if cjk > 0.2 else "en"
    _, method = page_labels(str(ROOT / decl["path"]))
    return {"page_count": len(pdf), "has_text_layer": bool(chars), "cjk_ratio": round(cjk, 4),
            "detected_lang": detected_lang, "lang_matches": detected_lang == decl["lang"],
            "page_label_method": method}


def tree(decl: dict) -> dict:
    from pageindex.flash.api import flash_rejection_reason, page_index_flash
    from scripts.t3_tocflash import build as build_toc_flash  # toc_flash 规则实现（D22）
    doc_id = decl["doc_id"]
    raw = ROOT / "data" / "raw" / f"{doc_id}.pdf"
    if not raw.exists() or raw.resolve() != (ROOT / decl["path"]).resolve():
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_bytes((ROOT / decl["path"]).read_bytes())
    flash = page_index_flash(str(raw), summary=False, optimize="merge", use_embedded_toc=True)
    (ROOT / "data" / "t3" / f"{doc_id}.flash.json").write_text(json.dumps(flash, ensure_ascii=False, indent=1))
    toc_tree, mapping = build_toc_flash(doc_id)
    (ROOT / "data" / "t3" / f"{doc_id}.toc_flash.json").write_text(json.dumps(toc_tree, ensure_ascii=False, indent=1))

    def count(ns):
        return sum(1 + count(n.get("nodes") or []) for n in ns)
    return {"flash_toc_source": flash.get("toc_source"), "flash_rejection": flash_rejection_reason(flash),
            "flash_nodes": count(flash["structure"]), "toc_flash_nodes": count(toc_tree["structure"]),
            "page_mapping": mapping}


def build(decl: dict) -> dict:
    from pipeline import build as b
    issuers, _, evals = b.load_ref()
    decl = dict(decl, manifest_id=None)  # 经管理接口登记的文档：中间产物以 doc_id 命名
    out = b.build(decl, issuers, evals, b.load_manifest())
    b.write_jsonl(decl["doc_id"], out)
    return {t: len(r) for t, r in out.items()}


def load(decl: dict) -> dict:
    from pipeline import loader
    from pipeline.doris import connect
    conn = connect()
    cur = conn.cursor()
    cur.execute(f"USE {loader.DB}")
    counts = loader.load_doc(cur, decl["doc_id"])
    with registry.connect() as pg:
        loader.write_coverage(pg, decl["doc_id"])
        doc = json.loads((loader.JSONL / "document" / f"{decl['doc_id']}.jsonl").read_text().splitlines()[0])
        registry.upsert_detected(pg, doc, "loaded")
    return counts


def run(doc_id: str, run_id: uuid.UUID | None = None, on_done=None) -> uuid.UUID:
    """同步执行一次入库运行（由管理接口放进后台线程调用）。"""
    run_id = run_id or uuid.uuid4()
    decl = registry.declaration(doc_id)
    with registry.connect() as pg:
        for s in registry.STAGES:
            _mark(pg, run_id, doc_id, s, "pending")
        status = "loaded"
        for stage, fn in (("detect", detect), ("tree", tree), ("build", build), ("load", load)):
            started = _now()
            _mark(pg, run_id, doc_id, stage, "running", started=started)
            try:
                detail = registry.jsonable(fn(decl))
            except Exception as e:  # noqa: BLE001 — 阶段失败要记录下来，不向外抛
                _mark(pg, run_id, doc_id, stage, "error", started, _now(),
                      error=re.sub(r"\s+", " ", f"{type(e).__name__}: {e}")[:500],
                      detail={"traceback": traceback.format_exc()[-2000:]})
                status = "rejected" if stage == "detect" else "review"
                break
            if stage == "detect" and (not detail["has_text_layer"] or not detail["lang_matches"]):
                _mark(pg, run_id, doc_id, stage, "error", started, _now(),
                      error="no text layer" if not detail["has_text_layer"] else "language does not match declaration",
                      detail=detail)
                status = "rejected" if not detail["has_text_layer"] else "review"
                break
            _mark(pg, run_id, doc_id, stage, "ok", started, _now(), detail=detail)
        for s in registry.STAGES:  # 未执行的阶段标为 skipped
            pg.execute("UPDATE ingest_run SET status = 'skipped' WHERE run_id = %s AND stage = %s AND status = 'pending'",
                       (run_id, s))
        pg.execute("UPDATE source SET status = %s, updated_at = now() WHERE source_id = %s", (status, doc_id))
        pg.commit()
    if on_done:
        on_done(doc_id, status)
    return run_id
