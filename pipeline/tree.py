"""统一树 schema：书签树导出、树拍平为 node 记录、印刷页码（page_label）。

统一树格式沿用 PageIndex 输出：{"structure": [{"title", "node_id", "start_index", "end_index", "nodes"}]}，
页码为物理页、从 1 开始、闭区间（含末页）。拍平后的 node 记录字段见 docs/architecture.md §3，
node_id = doc_id:tree_source:local_id（D15）。

用法：uv run python -m pipeline.tree   # 导出全部文档的书签树、拍平主树与对照树、写页码标签
"""

import json
import re
from collections import Counter
from pathlib import Path

import pymupdf

TREE_SOURCES = {"bookmark", "pageindex_flash", "toc_flash", "en_mapped", "pageindex_llm"}
PROFILE, PROFILE_VERSION, PIPELINE_VERSION = "generic", "0", "p0"
_FOOTER_NUM = re.compile(r"^\d{1,3}$")


def bookmark_tree(pdf_path: str) -> dict:
    """PDF 书签 → 统一树。节点止于下一个同级或更高级书签的前一页（同页起始时止于本页）。"""
    doc = pymupdf.open(pdf_path)
    n = len(doc)
    toc = [(lvl, title.strip(), page) for lvl, title, page in doc.get_toc(simple=True) if page >= 1]
    flat = []
    for i, (lvl, title, page) in enumerate(toc):
        nxt = next((p for l2, _, p in toc[i + 1:] if l2 <= lvl), n + 1)
        flat.append({"level": lvl, "node": {"title": title, "start_index": page,
                                            "end_index": max(page, nxt - 1), "nodes": []}})
    roots, stack = [], []
    for item in flat:
        while stack and stack[-1]["level"] >= item["level"]:
            stack.pop()
        (stack[-1]["node"]["nodes"] if stack else roots).append(item["node"])
        stack.append(item)
    counter = 0
    for node in _walk(roots):
        node["node_id"] = f"{counter:04d}"
        counter += 1
    return {"doc_name": Path(pdf_path).name, "toc_source": "bookmarks", "structure": roots}


def _walk(nodes):
    for node in nodes:
        yield node
        yield from _walk(node.get("nodes") or [])


def flatten(tree: dict, doc_id: str, tree_source: str, is_primary: bool) -> list[dict]:
    """统一树 → node 记录（先序）。富化字段（summary、*_tok、向量、概念、对齐）留空，由后续阶段填写。"""
    if tree_source not in TREE_SOURCES:
        raise ValueError(f"unknown tree_source: {tree_source}")
    rows: list[dict] = []

    def visit(nodes, parent_id, ancestors, depth):
        for ord_, node in enumerate(nodes):
            local_id = node.get("node_id")
            start, end = node.get("start_index"), node.get("end_index")
            if not local_id or not isinstance(start, int) or not isinstance(end, int) or not 1 <= start <= end:
                raise ValueError(f"invalid node {local_id!r}: pages {start}-{end}")
            node_id = f"{doc_id}:{tree_source}:{local_id}"
            rows.append({
                "node_id": node_id, "doc_id": doc_id, "parent_id": parent_id,
                "ancestors": list(ancestors), "depth": depth, "ord": ord_,
                "title": node["title"], "summary": None,
                "start_page": start, "end_page": end,
                "tree_source": tree_source, "is_primary": is_primary,
                "profile": PROFILE, "profile_version": PROFILE_VERSION,
                "pipeline_version": PIPELINE_VERSION, "ext": {},
            })
            visit(node.get("nodes") or [], node_id, ancestors + [node_id], depth + 1)

    visit(tree["structure"], None, [], 1)
    return rows


def page_labels(pdf_path: str) -> tuple[list[str | None], dict]:
    """每个物理页的印刷页码：优先 PDF PageLabels；否则用页脚数字求物理页与印刷页的众数偏移；都没有则为空。"""
    doc = pymupdf.open(pdf_path)
    if doc.get_page_labels():
        return [doc[i].get_label() or None for i in range(len(doc))], {"method": "page_labels"}
    offsets = Counter()
    for i, page in enumerate(doc):
        h = page.rect.height
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                text = "".join(s["text"] for s in line["spans"]).strip()
                if _FOOTER_NUM.match(text) and int(text) >= 1 and line["bbox"][1] > h * 0.93:
                    offsets[i + 1 - int(text)] += 1
    if not offsets:
        return [None] * len(doc), {"method": "none"}
    off, hits = offsets.most_common(1)[0]
    labels = [str(p - off) if p - off >= 1 else None for p in range(1, len(doc) + 1)]
    return labels, {"method": "footer_offset", "offset": off, "pages_agreeing": hits}


def main() -> None:
    manifest = json.load(open("data/manifest.json"))
    out = Path("data/t4")
    out.mkdir(parents=True, exist_ok=True)
    primary = {"ocbc-ar-2025-en": "toc_flash", "ocbc-ar-2025-zh": "toc_flash"}
    summary = {}
    for rec in (r for r in manifest if r["ingest"]):
        doc_id, pdf = rec["doc_id"], rec["local_path"]
        bm = bookmark_tree(pdf)
        (out / f"{doc_id}.bookmark.json").write_text(json.dumps(bm, ensure_ascii=False, indent=1))
        trees = {"bookmark": bm,
                 "pageindex_flash": json.load(open(f"data/t3/{doc_id}.flash.json")),
                 "toc_flash": json.load(open(f"data/t3/{doc_id}.toc_flash.json"))}
        rows = []
        for source, tree in trees.items():
            rows += flatten(tree, doc_id, source, is_primary=(primary.get(doc_id) == source))
        with open(out / f"{doc_id}.nodes.jsonl", "w") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        labels, method = page_labels(pdf)
        (out / f"{doc_id}.page_labels.json").write_text(json.dumps({"method": method, "labels": labels},
                                                                   ensure_ascii=False))
        summary[doc_id] = {"nodes": dict(Counter(r["tree_source"] for r in rows)),
                           "primary": primary.get(doc_id), "page_label_method": method,
                           "labelled_pages": sum(1 for x in labels if x), "pages": len(labels)}
        print(doc_id, summary[doc_id])
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
