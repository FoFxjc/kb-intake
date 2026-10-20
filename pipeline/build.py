"""入库：由原件与树产物生成 JSONL 标准记录（knowledge-model.md §3.1、§3.3–3.4）。

输出 data/jsonl/{document,node,page,table_block}/{doc_id}.jsonl。按 doc_type 取类型配置（profiles/，D43）：
用哪些树、哪棵是主树、章节怎么分类、平行版本与完整性怎么校验、财年与主体怎么定，都由配置决定；
记录里的 profile / profile_version 写实际使用的配置。summary = 标题 + 起始页中标题之后的正文前 300 字（不调用 LLM）。

用法：uv run python -m pipeline.build [doc_id ...]   （默认 ref/documents.yaml 中的全部文档）
"""

import hashlib
import json
import re
import sys
from pathlib import Path

import pymupdf
import yaml

import domains

import profiles
from pipeline import tables as table_base
from pipeline.tokenize import to_field
from pipeline.tree import PIPELINE_VERSION, flatten, page_labels

ROOT = Path(__file__).resolve().parents[1]
JSONL = ROOT / "data" / "jsonl"
SUMMARY_CHARS = 300
PAGE_LABEL_SOURCE = {"page_labels": "pdf_labels", "footer_offset": "footer_offset", "none": "none"}


def load_ref() -> tuple[dict, list[dict], list[dict]]:
    ref = yaml.safe_load((ROOT / "ref" / "documents.yaml").read_text())
    evals = yaml.safe_load((ROOT / "ref" / "tree_eval.yaml").read_text())["evaluations"]
    return ref["issuers"], ref["documents"], evals


def latest_accuracy(evals: list[dict], doc_id: str, tree_source: str) -> float | None:
    rows = [e for e in evals if e["doc_id"] == doc_id and e["tree_source"] == tree_source]
    return rows[-1]["correct"] / rows[-1]["sampled"] if rows else None


def summary_of(title: str, page_text: str) -> str:
    text = re.sub(r"\s+", " ", page_text).strip()
    head = re.sub(r"\s+", "", title.split(";")[0])[:10]
    compact_pos, pos = 0, 0
    if head:  # 在去空白的文本中定位标题，再换算回原文位置
        for i, ch in enumerate(text):
            if not ch.isspace():
                if text.replace(" ", "")[compact_pos:compact_pos + len(head)] == head:
                    pos = i + len(title.split(";")[0])
                    break
                compact_pos += 1
    body = text[pos:pos + SUMMARY_CHARS].strip()
    return f"{title} {body}".strip()


def deepest_covering(nodes: list[dict], page_count: int) -> dict[int, list[str]]:
    """每一页 → 覆盖它的最深主树节点（边界页可有多个；api-contract §6.1 第 6 步、D19）。"""
    children: dict[str, list[dict]] = {}
    for n in nodes:
        children.setdefault(n["parent_id"], []).append(n)
    out: dict[int, list[str]] = {p: [] for p in range(1, page_count + 1)}

    def visit(parent_id):
        for n in children.get(parent_id, []):
            for p in range(n["start_page"], n["end_page"] + 1):
                kids_cover = any(k["start_page"] <= p <= k["end_page"] for k in children.get(n["node_id"], []))
                if not kids_cover:
                    out[p].append(n["node_id"])
            visit(n["node_id"])
    visit(None)
    return out


def tables_of(page: pymupdf.Page, doc_id: str, page_no: int, covering: list[str],
              entity_words: dict[str, list[str]] | None = None) -> list[dict]:
    rows = []
    try:
        found = page.find_tables()
    except Exception:  # noqa: BLE001 — 个别页面版式会让表格检测失败，跳过该页即可
        return rows
    for i, t in enumerate(found.tables):
        md = t.to_markdown(clean=False).strip()
        if not md or md.count("\n") < 2:
            continue
        hint = domains.current().units().table_unit_hint.search(md[:400])   # 单位提示的写法来自领域包（D44）
        rows.append({"doc_id": doc_id, "table_id": f"{doc_id}:t{page_no:04d}_{i + 1:02d}",
                     "node_id": covering[0] if covering else None, "page_start": page_no, "page_end": page_no,
                     "caption": None, "unit_hint": hint.group(1) if hint else None, "markdown": md,
                     "bbox": json.dumps([round(x, 1) for x in t.bbox]), "extract_method": "pymupdf_find_tables",
                     "status": "ok",
                     # 表格的共同底座（内容类型设计 §C）：误识别、拆合并格、表头路径、主体列；原有字段不动
                     "structure": table_base.structure(page, t, entity_words)})
    return rows


def artifact_id(decl: dict) -> str:
    """树等中间产物的文件名：P0 下载的文档沿用 manifest 编号，经管理接口登记的文档用 doc_id。"""
    return decl.get("manifest_id") or decl["doc_id"]


def raw_path(decl: dict, manifest: dict) -> str:
    return decl.get("path") or manifest[decl["manifest_id"]]["local_path"]


def build(decl: dict, issuers: dict, evals: list[dict], manifest: dict) -> dict[str, list[dict]]:
    doc_id, rel_path = decl["doc_id"], raw_path(decl, manifest)
    pdf_path = ROOT / rel_path
    pdf = pymupdf.open(pdf_path)
    texts = [p.get_text() for p in pdf]
    labels, label_method = page_labels(str(pdf_path))
    profile = profiles.get(decl["doc_type"])
    fiscal_year, _entity = profile.period_entity(decl)
    common = {"fiscal_year": fiscal_year, "lang": decl["lang"], "issuer": decl["issuer_id"]}

    nodes: list[dict] = []
    art = artifact_id(decl)
    for tree_source, primary in profile.tree_sources:
        acc = latest_accuracy(evals, doc_id, tree_source)
        tree = json.loads(profiles.tree_path(art, tree_source).read_text())
        section_of: dict[str, tuple] = {}
        if primary:  # section_type 只在主树上赋值（类型配置的章节词表）
            detected = profile.classify_sections(tree["structure"])
            profile.check_completeness(dict(decl, doc_id=doc_id), detected)

            def collect(ns):
                for x in ns:
                    section_of[x["node_id"]] = (x.get("section_type"), x.get("section_rule"))
                    collect(x.get("nodes") or [])
            collect(tree["structure"])
        for n in flatten(tree, doc_id, tree_source, primary, profile=(profile.name, profile.version)):
            st, rule = section_of.get(n["node_id"].rsplit(":", 1)[1], (None, None))
            summary = summary_of(n["title"], texts[n["start_page"] - 1])
            n.update(title_tok=to_field(n["title"]), summary=summary, summary_tok=to_field(summary),
                     page_label_range=[labels[n["start_page"] - 1], labels[n["end_page"] - 1]],
                     tree_confidence="ok" if acc is not None and acc >= 0.85 else "low",
                     section_type=st, section_rule=rule, concept_ids=None, parallel_node_id=None, **common)
            nodes.append(n)
    covering = deepest_covering([n for n in nodes if n["is_primary"]], len(pdf))

    pages, tables = [], []
    layout_path = ROOT / "data" / "artifacts" / "layout" / f"{doc_id}.jsonl"
    layout_path.parent.mkdir(parents=True, exist_ok=True)
    layout = layout_path.open("w")  # 词级坐标（ui-design §7）：中间产物，不进数据库
    for i, page in enumerate(pdf):
        page_no = i + 1
        layout.write(json.dumps({"page_no": page_no, "width": round(page.rect.width, 1),
                                 "height": round(page.rect.height, 1),
                                 "words": [[round(w[0], 1), round(w[1], 1), round(w[2], 1), round(w[3], 1), w[4]]
                                           for w in page.get_text("words")]}, ensure_ascii=False) + "\n")
        pages.append({"doc_id": doc_id, "page_no": page_no, "page_label": labels[i],
                      "page_label_source": PAGE_LABEL_SOURCE[label_method["method"]], **common,
                      "text": texts[i], "text_tok": to_field(texts[i]),
                      "image_path": f"data/pages/{doc_id}/{page_no:04d}.png", "covering_node_ids": covering[page_no]})
        tables += tables_of(page, doc_id, page_no, covering[page_no], profile.table_entity_words())

    layout.close()
    parallel = profile.parallel_group(decl)
    document = {
        "doc_id": doc_id, "issuer_id": decl["issuer_id"],
        "issuer_name": (issuers.get(decl["issuer_id"]) or {}).get("name_en", decl["issuer_id"]),
        "doc_type": decl["doc_type"], "period_end": str(decl["period_end"]), "fiscal_year": common["fiscal_year"],
        "lang": decl["lang"], "source_url": decl["source_url"], "completeness": json.dumps(decl["completeness"], ensure_ascii=False),
        "translation_of": decl.get("translation_of"), "parallel_group_id": parallel,
        "sha256": hashlib.sha256(pdf_path.read_bytes()).hexdigest(), "path": rel_path, "page_count": len(pdf),
        "has_text_layer": any(t.strip() for t in texts), "page_label_source": PAGE_LABEL_SOURCE[label_method["method"]],
        "accept_level": "full", "primary_tree_source": profile.primary_tree,
        "profile": profile.name, "profile_version": profile.version, "pipeline_version": PIPELINE_VERSION, "ext": "{}",
    }
    return {"document": [document], "node": nodes, "page": pages, "table_block": tables}


def load_manifest() -> dict:
    path = ROOT / "data" / "manifest.json"
    return {r["doc_id"]: r for r in json.loads(path.read_text())} if path.exists() else {}


def write_jsonl(doc_id: str, out: dict[str, list[dict]]) -> None:
    for table, rows in out.items():
        path = JSONL / table / f"{doc_id}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")


def main() -> None:
    issuers, decls, evals = load_ref()
    manifest = load_manifest()
    wanted = set(sys.argv[1:])
    for decl in decls:
        if wanted and decl["doc_id"] not in wanted:
            continue
        out = build(decl, issuers, evals, manifest)
        write_jsonl(decl["doc_id"], out)
        print(decl["doc_id"], {t: len(r) for t, r in out.items()})


if __name__ == "__main__":
    main()
