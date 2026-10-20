"""Compare intake packages with the kb baseline (intake-design §6, step 2 acceptance).

For every document of the baseline, item by item:
- pages:   text and page_label of every page equal kb data/jsonl/page; page_count, sha256, has_text_layer and the
           page label method equal kb data/jsonl/document
- kinds:   kind, chars, image regions and discarded count equal kb data/artifacts/pages (kb #29)
- words:   kb data/artifacts/layout rounds to 0.1 pt; package words rounded the same way must be identical, in order
- tables:  the find_tables candidates that kb keeps (markdown with at least 3 lines) are exactly the baseline
           table_block rows: same table_id, same markdown, same bbox once rounded like kb
Cells are not in the baseline; they are checked in step 3, where kb computes `structure` from the package and
must reproduce the baseline byte for byte.

Reads only JSON files; imports nothing from kb.

usage: python scripts/compare_baseline.py --packages $KB_PACKAGES_DIR --baseline ../kb/data/baseline/pre-intake-baseline
"""

import argparse
import json
import sys
from pathlib import Path

LABEL_SOURCE = {"page_labels": "pdf_labels", "footer_offset": "footer_offset", "none": "none"}


def jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def r1(x: float) -> float:
    return round(x, 1)


def compare(pkg: Path, base: Path, doc_id: str) -> dict:
    m = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
    pages = jsonl(pkg / "pages.jsonl")
    layout = jsonl(pkg / "layout.jsonl")
    tables = jsonl(pkg / "tables.jsonl")
    b_doc = jsonl(base / "jsonl" / "document" / f"{doc_id}.jsonl")[0]
    b_pages = jsonl(base / "jsonl" / "page" / f"{doc_id}.jsonl")
    b_layout = jsonl(base / "artifacts" / "layout" / f"{doc_id}.jsonl")
    b_kinds = jsonl(base / "artifacts" / "pages" / f"{doc_id}.jsonl")
    b_tables = jsonl(base / "jsonl" / "table_block" / f"{doc_id}.jsonl")
    out: dict = {}

    def item(name, total, bad):
        out[name] = {"compared": total, "different": len(bad), "examples": bad[:5]}

    d = m["detection"]
    doc_fields = {"page_count": (d["page_count"], b_doc["page_count"]),
                  "sha256": (m["source"]["sha256"], b_doc["sha256"]),
                  "has_text_layer": (d["has_text_layer"], b_doc["has_text_layer"]),
                  "page_label_source": (LABEL_SOURCE[d["page_label_method"]["method"]], b_doc["page_label_source"])}
    item("document", len(doc_fields), [k for k, (a, b) in doc_fields.items() if a != b])

    item("page_count", 1, [] if len(pages) == len(b_pages) == len(layout) else [[len(pages), len(b_pages), len(layout)]])
    item("page_text", len(b_pages), [b["page_no"] for a, b in zip(pages, b_pages) if a["text"] != b["text"]])
    item("page_label", len(b_pages), [b["page_no"] for a, b in zip(pages, b_pages) if a["page_label"] != b["page_label"]])
    item("page_kind", len(b_kinds), [b["page_no"] for a, b in zip(pages, b_kinds)
                                     if (a["kind"], a.get("chars"), a.get("image_regions"), a.get("images_discarded"))
                                     != (b["kind"], b["chars"], b["regions"], b["discarded"])])

    bad_words, bad_size, n_words = [], [], 0
    for a, b in zip(layout, b_layout):
        n_words += len(b["words"])
        if (r1(a["width"]), r1(a["height"])) != (b["width"], b["height"]):
            bad_size.append(b["page_no"])
        rounded = [[r1(w[0]), r1(w[1]), r1(w[2]), r1(w[3]), w[4]] for w in a["words"]]
        if rounded != b["words"]:
            bad_words.append(b["page_no"])
    item("page_size", len(b_layout), bad_size)
    item("words_pages", len(b_layout), bad_words)
    out["words_total"] = n_words

    kept = [t for t in tables if t["markdown"] and t["markdown"].count("\n") >= 2]
    by_id = {t["table_id"]: t for t in kept}
    ids_pkg, ids_base = [t["table_id"] for t in kept], [t["table_id"] for t in b_tables]
    item("table_ids", len(ids_base), [] if ids_pkg == ids_base else
         sorted(set(ids_pkg) ^ set(ids_base)) or ["same ids, different order"])
    bad_md, bad_box = [], []
    for b in b_tables:
        a = by_id.get(b["table_id"])
        if a is None:
            continue
        if a["markdown"] != b["markdown"]:
            bad_md.append(b["table_id"])
        if json.dumps([r1(x) for x in a["bboxes"][0]]) != b["bbox"] or a["pages"] != [b["page_start"]]:
            bad_box.append(b["table_id"])
    item("table_markdown", len(b_tables), bad_md)
    item("table_bbox", len(b_tables), bad_box)
    out["tables_in_package"] = len(tables)
    out["cells_in_package"] = sum(len(t["cells"]) for t in tables)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--packages", required=True)
    ap.add_argument("--baseline", required=True)
    args = ap.parse_args()
    base, root = Path(args.baseline), Path(args.packages)
    doc_ids = sorted(p.stem for p in (base / "jsonl" / "document").glob("*.jsonl"))
    report, ok = {}, True
    for doc_id in doc_ids:
        pkgs = sorted(p for p in root.glob(f"{doc_id}@*") if p.is_dir())
        if len(pkgs) != 1:
            report[doc_id] = {"error": f"expected one package, found {len(pkgs)}"}
            ok = False
            continue
        report[doc_id] = compare(pkgs[0], base, doc_id)
        ok &= all(v["different"] == 0 for v in report[doc_id].values() if isinstance(v, dict))
    print(json.dumps({"ok": ok, "documents": report}, ensure_ascii=False, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
