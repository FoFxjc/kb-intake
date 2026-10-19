"""来源登记（knowledge-model §3.3；api-contract §8 的管理接口所用）：Postgres source / document 为权威表。"""

import hashlib
import json
import os
import re
from datetime import date
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

import profiles

ROOT = Path(__file__).resolve().parents[1]
PG_DSN = os.environ.get("KB_PG_DSN", "postgresql://kb:kb@127.0.0.1:5432/kb")
REQUIRED = ("issuer_id", "doc_type", "period_end", "lang", "source_url", "completeness")
OPTIONAL = ("translation_of", "publisher", "published_at")
STAGES = ("detect", "tree", "build", "load")


class DeclarationError(ValueError):
    def __init__(self, param: str, message: str, allowed=None):
        super().__init__(message)
        self.param, self.allowed = param, allowed


def connect() -> psycopg.Connection:
    return psycopg.connect(PG_DSN, row_factory=dict_row)


def doc_id_of(decl: dict) -> str:
    return f"{decl['issuer_id']}_{profiles.abbr(decl['doc_type'])}_{int(str(decl['period_end'])[:4])}_{decl['lang']}"


def validate(decl: dict) -> dict:
    if not isinstance(decl, dict):
        raise DeclarationError("declaration", "declaration must be a JSON object")
    missing = [k for k in REQUIRED if decl.get(k) in (None, "")]
    if missing:
        raise DeclarationError(missing[0], f"declaration missing field(s): {missing}", list(REQUIRED))
    unknown = sorted(set(decl) - set(REQUIRED) - set(OPTIONAL))
    if unknown:
        raise DeclarationError(unknown[0], f"unknown declaration field(s): {unknown}", list(REQUIRED + OPTIONAL))
    # 已注册的类型按各自的类型配置入库；其他类型走 generic 配置（D43）
    if not re.fullmatch(r"[a-z][a-z0-9_]*", str(decl["doc_type"])):
        raise DeclarationError("doc_type", "doc_type must match [a-z][a-z0-9_]*", sorted(profiles.registry()))
    if decl["lang"] not in ("en", "zh"):
        raise DeclarationError("lang", "lang must be en or zh", ["en", "zh"])
    if not re.fullmatch(r"[a-z0-9-]+", decl["issuer_id"]):
        raise DeclarationError("issuer_id", "issuer_id must match [a-z0-9-]+")
    try:
        date.fromisoformat(str(decl["period_end"]))
    except ValueError:
        raise DeclarationError("period_end", "period_end must be an ISO date (YYYY-MM-DD)")
    comp = decl["completeness"]
    if not isinstance(comp, dict) or not isinstance(comp.get("full"), bool):
        raise DeclarationError("completeness", 'completeness must be {"full": bool, "sections": [...] | null}')
    return dict(decl)


def register(decl: dict, pdf: bytes) -> tuple[dict, bool]:
    """登记来源；返回 (来源记录, 是否新建)。同一 doc_id、同一文件重复登记为幂等；文件不同则拒绝（替代版本另议）。"""
    decl = validate(decl)
    if not pdf.startswith(b"%PDF-"):
        raise DeclarationError("file", "file must be a PDF")
    doc_id = doc_id_of(decl)
    sha = hashlib.sha256(pdf).hexdigest()
    rel = f"data/raw/{doc_id}.pdf"
    with connect() as pg:
        row = pg.execute("SELECT d.sha256 FROM document d WHERE d.source_id = %s", (doc_id,)).fetchone()
        if row:
            if row["sha256"] != sha:
                raise DeclarationError("file", f"{doc_id} already registered with a different file")
            return get(doc_id), False
        if decl.get("translation_of") and not pg.execute("SELECT 1 FROM source WHERE source_id = %s",
                                                          (decl["translation_of"],)).fetchone():
            raise DeclarationError("translation_of", f"unknown source {decl['translation_of']}")
        (ROOT / rel).parent.mkdir(parents=True, exist_ok=True)
        (ROOT / rel).write_bytes(pdf)
        pg.execute("INSERT INTO source (source_id, source_type, publisher, published_at, status) "
                   "VALUES (%s, 'document', %s, %s, 'pending')",
                   (doc_id, decl.get("publisher"), decl.get("published_at")))
        pg.execute("INSERT INTO document (source_id, issuer_id, doc_type, period_end, fiscal_year, lang, source_url, "
                   "completeness, translation_of, parallel_group_id, sha256, path) "
                   "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                   (doc_id, decl["issuer_id"], decl["doc_type"], decl["period_end"], int(str(decl["period_end"])[:4]),
                    decl["lang"], decl["source_url"], Jsonb(decl["completeness"]), decl.get("translation_of"),
                    doc_id.rsplit("_", 1)[0], sha, rel))
    return get(doc_id), True


def declaration(doc_id: str) -> dict | None:
    """权威表中的声明，供入库管道使用（与 ref/documents.yaml 中的条目同形）。"""
    with connect() as pg:
        r = pg.execute("SELECT * FROM document WHERE source_id = %s", (doc_id,)).fetchone()
    if not r:
        return None
    return {"doc_id": doc_id, "issuer_id": r["issuer_id"], "doc_type": r["doc_type"], "period_end": r["period_end"],
            "lang": r["lang"], "source_url": r["source_url"], "completeness": r["completeness"],
            "translation_of": r["translation_of"], "path": r["path"]}


def upsert_detected(pg, doc: dict, status: str) -> None:
    """入库完成后把推导字段写回权威表（来自 JSONL 的 document 记录）。"""
    pg.execute("UPDATE document SET page_count = %s, has_text_layer = %s, page_label_source = %s, accept_level = %s, "
               "primary_tree_source = %s, pipeline_version = %s, sha256 = %s, parallel_group_id = %s, "
               "profile = %s, profile_version = %s WHERE source_id = %s",
               (doc["page_count"], doc["has_text_layer"], doc["page_label_source"], doc["accept_level"],
                doc["primary_tree_source"], doc["pipeline_version"], doc["sha256"], doc["parallel_group_id"],
                doc["profile"], doc["profile_version"], doc["doc_id"]))
    pg.execute("UPDATE source SET status = %s, updated_at = now() WHERE source_id = %s", (status, doc["doc_id"]))


def runs(pg, doc_id: str) -> list[dict]:
    return pg.execute("SELECT run_id, stage, status, started_at, finished_at, error, detail FROM ingest_run "
                      "WHERE source_id = %s ORDER BY started_at NULLS LAST", (doc_id,)).fetchall()


def latest_stages(pg, doc_id: str) -> tuple[str | None, dict]:
    """最近一次入库运行的各阶段状态。"""
    last = pg.execute("SELECT run_id FROM ingest_run WHERE source_id = %s ORDER BY started_at DESC NULLS LAST LIMIT 1",
                      (doc_id,)).fetchone()
    if not last:
        return None, {s: {"status": "not_run"} for s in STAGES}
    rows = {r["stage"]: r for r in pg.execute("SELECT * FROM ingest_run WHERE run_id = %s", (last["run_id"],))}
    out = {}
    for s in STAGES:
        r = rows.get(s)
        out[s] = {"status": r["status"], "started_at": r["started_at"], "finished_at": r["finished_at"],
                  "duration_ms": int((r["finished_at"] - r["started_at"]).total_seconds() * 1000)
                  if r and r["started_at"] and r["finished_at"] else None,
                  "error": r["error"], "detail": r["detail"]} if r else {"status": "not_run"}
    return str(last["run_id"]), out


def get(doc_id: str) -> dict | None:
    with connect() as pg:
        src = pg.execute("SELECT * FROM source WHERE source_id = %s", (doc_id,)).fetchone()
        if not src:
            return None
        doc = pg.execute("SELECT * FROM document WHERE source_id = %s", (doc_id,)).fetchone()
        cov = {r["metric"]: r["value"] for r in pg.execute(
            "SELECT metric, value FROM coverage WHERE scope_type = 'document' AND scope_id = %s", (doc_id,))}
        run_id, stages = latest_stages(pg, doc_id)
    return {"source_id": doc_id, "source_type": src["source_type"], "status": src["status"],
            "declaration": {k: doc[k] for k in ("issuer_id", "doc_type", "period_end", "lang", "source_url",
                                               "completeness", "translation_of")},
            "detection": {k: doc[k] for k in ("fiscal_year", "page_count", "has_text_layer", "page_label_source",
                                             "sha256", "path", "parallel_group_id")},
            "accept_level": doc["accept_level"], "primary_tree_source": doc["primary_tree_source"],
            "tree_sample_accuracy": (cov.get("tree_sample_accuracy") or {}).get("value"),
            "coverage": cov, "latest_run_id": run_id, "stages": stages,
            "created_at": src["created_at"], "updated_at": src["updated_at"]}


def list_ids() -> list[str]:
    with connect() as pg:
        return [r["source_id"] for r in pg.execute("SELECT source_id FROM source ORDER BY source_id")]


def jsonable(obj):
    return json.loads(json.dumps(obj, default=str))
