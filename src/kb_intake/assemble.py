"""Assemble the 3b extraction layer of flow A (intake-design §9.2): Docling primary, find_tables secondary.

Input: the DoclingDocument (or None when Docling failed), the find_tables candidates, the text layer words and the
PDF. Output: tables (primary and secondary), regions, gaps and a summary for the manifest. Pages Docling could not
convert fall back to find_tables and get an `extractor_degraded` gap (§9.3.5).
"""

import json
from collections import Counter

import pymupdf

from kb_intake import docling_post as post


def _words(layout_rows: list[dict]) -> dict[int, list]:
    return {r["page_no"]: [w[:5] for w in r["words"]] for r in layout_rows}


def _ft_ok(t: dict) -> bool:
    """find_tables candidates worth comparing: the same filter kb applies (markdown with at least 3 lines)."""
    return bool(t["markdown"]) and t["markdown"].count("\n") >= 2


def assemble(doc_id: str, docling_json: bytes | None, failed_pages: list[int], ft_tables: list[dict],
             layout_rows: list[dict], pdf: pymupdf.Document, layout_model: str, layout_version: str,
             confidence_pages: dict, heading_rows: list[dict] | None = None) -> dict:
    """heading_rows: font-rule heading candidates (headings.py); they give the "heading a table sits under" that
    decides whether a table continues on the next page. Running chapter headers (furniture, level 1) are ignored."""
    words = _words(layout_rows)
    gaps: list[dict] = []
    regions: list[dict] = []
    refs: dict[str, str] = {}
    dl_tables: list[dict] = []
    if docling_json:
        doc = json.loads(docling_json)

        def page_text(page_no, box):
            return sum(1 for c in pdf[page_no - 1].get_text(clip=pymupdf.Rect(box)) if not c.isspace())

        regions, refs = post.regions(doc, doc_id, page_text, layout_model, layout_version)
        blocks = {i + 1: [b for b in page.get_text("blocks") if b[6] == 0 and b[4].strip()] for i, page in enumerate(pdf)}
        regions += post.native_only(regions, blocks, doc_id, len(regions), layout_model, layout_version)
        full_words: dict[int, list] = {}

        def page_words(page_no):
            if page_no not in full_words:
                full_words[page_no] = pdf[page_no - 1].get_text("words")
            return full_words[page_no]
        dl_tables, g = post.docling_tables(doc, words, page_words)
        gaps += g
        heads = sorted(((h["page_no"], h["bbox"][1], h["text"]) for h in heading_rows or []
                        if not (h["furniture"] and h["level"] == 1)), key=lambda h: (h[0], h[1]))

        body = [r for r in regions if r["content_layer"] == "body" and r["type"] in ("text", "list", "caption", "heading", "title")]

        def context(t):
            """(heading above the table on its page, else None; the heading the table sits under; whether other
            content stands between that heading, or the page top, and the table)."""
            page, top = t["pages"][0], t["bboxes"][0][1]
            before = [h for h in heads if (h[0], h[1]) < (page, top)]
            own = next((h for h in reversed(before) if h[0] == page), None)
            floor = own[1] + 1 if own else 0.0
            between = any(r["page_no"] == page and r["bbox"][1] > floor and r["bbox"][3] <= top + 1
                          and not (own and abs(r["bbox"][1] - own[1]) < 3)
                          and not post.CONTINUED.search(pdf[page - 1].get_text(clip=pymupdf.Rect(r["bbox"])))
                          for r in body)            # only "(continued)" lines may stand between
            return (own[2] if own else None), (before[-1][2] if before else None), between
        dl_tables, g = post.merge_continued(dl_tables, context)
        gaps += g
    docling_pages = set() if not docling_json else set(range(1, len(pdf) + 1)) - set(failed_pages)

    # find_tables candidates: secondary by default; primary on pages Docling did not cover
    ft_by_page: dict[int, list[dict]] = {}
    for t in ft_tables:
        ft_by_page.setdefault(t["pages"][0], []).append(t)
    final: list[dict] = []
    secondary: list[dict] = []
    for t in dl_tables:
        t["checks"]["matched_find_tables"] = []
    for page_no, cands in sorted(ft_by_page.items()):
        for t in cands:
            t = {**t, "role": "secondary", "checks": {}}
            fid = t["table_id"].replace(":t", ":f", 1)
            t["checks"]["find_tables_id"] = t["table_id"]
            t["table_id"] = fid
            if page_no not in docling_pages:
                if _ft_ok(t):
                    t["role"] = "primary"
                    final.append(t)
                    gaps.append({"code": "extractor_degraded", "page_no": page_no, "extractor": "docling",
                                 "detail": "page not converted by Docling; find_tables used", "table_id": fid})
                continue
            match = next((d for d in dl_tables for p, box in zip(d["pages"], d["bboxes"])
                          if p == page_no and post.same_box(box, t["bboxes"][0])), None)
            if match:
                miss_dl, miss_ft = post.cross_compare(match, t, page_no)   # only in docling / only in find_tables
                match["checks"]["matched_find_tables"].append(fid)
                t["checks"]["matched_docling"] = match["docling_ref"]
                if miss_dl or miss_ft:
                    gaps.append({"code": "table_cross_mismatch", "source": "find_tables", "page_no": page_no,
                                 "ref": match["docling_ref"], "find_tables_id": fid,
                                 "only_docling": miss_dl[:20], "only_find_tables": miss_ft[:20]})
                secondary.append(t)
                continue
            if not _ft_ok(t):
                secondary.append(t)
                continue
            region = post.region_at(regions, page_no, t["bboxes"][0])
            rtype = region["type"] if region else None
            if rtype in ("picture", "chart", "diagram"):
                t["checks"]["misread"] = f"docling region {region['region_id']} is {rtype}"
                secondary.append(t)
                continue
            missing, extra = post.conservation(t, words)
            if not missing and not extra:
                t["role"], t["checks"]["fallback"] = "primary", f"docling region: {rtype or 'none'}"
                final.append(t)
                gaps.append({"code": "table_missed", "page_no": page_no, "find_tables_id": fid, "resolution": "find_tables",
                             "docling_region": region["region_id"] if region else None, "_table": t})
            else:
                secondary.append(t)
                gaps.append({"code": "table_missed", "page_no": page_no, "find_tables_id": fid, "resolution": "not_entered",
                             "docling_region": region["region_id"] if region else None,
                             "detail": f"find_tables candidate fails text conservation ({len(missing)} missing, "
                                       f"{len(extra)} extra)"})
    final += [t for t in dl_tables if t["pages"][0] in docling_pages]

    # ids: primary tables in page order, then by position; find_tables keep their "f" ids
    final.sort(key=lambda t: (t["pages"][0], t["bboxes"][0][1], t["bboxes"][0][0]))
    per_page = Counter()
    rid_of = {}
    for t in final:
        per_page[t["pages"][0]] += 1
        tid = f"{doc_id}:t{t['pages'][0]:04d}_{per_page[t['pages'][0]]:02d}"
        if t["extract_method"] != "find_tables":
            rid_of[t["docling_ref"]] = tid
            for ref in t.get("merged_refs", [t["docling_ref"]]):
                if ref in refs:
                    t.setdefault("region_ids", []).append(refs[ref])
        t["table_id"] = tid
    for g in gaps:
        if g.get("ref") in rid_of:
            g["table_id"] = rid_of[g["ref"]]
        if "_table" in g:                          # a find_tables table entered as primary: its final id
            g["table_id"] = g.pop("_table")["table_id"]
    for r in regions:
        if r["type"] == "table":
            for ref, rid in refs.items():
                if rid == r["region_id"] and ref in rid_of:
                    r["ref"] = rid_of[ref]

    # check 1: text conservation on every primary table
    for t in final:
        missing, extra = post.conservation(t, words)
        t["checks"]["text_conservation"] = {"missing": len(missing), "extra": len(extra)}
        for w in missing:
            gaps.append({"code": "table_text_unaccounted", "page_no": next(
                (p for p, b in zip(t["pages"], t["bboxes"]) if post._center_in(w, b)), t["pages"][0]),
                "table_id": t["table_id"], "text": w[4], "bbox": list(w[:4])})
        for tok in extra:
            gaps.append({"code": "table_cross_mismatch", "source": "text_layer", "page_no": t["pages"][0],
                         "table_id": t["table_id"], "detail": f"cell number {tok!r} not in the text layer"})

    tables = [_row(t) for t in final] + [_row(t) for t in secondary]
    summary = {
        "tables_primary": len(final), "tables_secondary": len(secondary),
        "by_method": dict(Counter(t["extract_method"] for t in final)),
        "gaps": dict(sorted(Counter(g["code"] for g in gaps).items())),
        "docling_pages": len(docling_pages), "docling_failed_pages": sorted(failed_pages),
    }
    return {"tables": tables, "regions": regions, "gaps": gaps, "summary": summary,
            "confidence_pages": confidence_pages}


def _row(t: dict) -> dict:
    return {"table_id": t["table_id"], "region_ids": t.get("region_ids", []), "pages": t["pages"], "bboxes": t["bboxes"],
            "n_rows": t["n_rows"], "n_cols": t["n_cols"], "cells": t["cells"], "header_rows": t["header_rows"],
            "markdown": t.get("markdown") or post._markdown(t["n_rows"], t["n_cols"], t["cells"]),
            "extract_method": t["extract_method"], "continued_from": t.get("continued_from"),
            "role": t.get("role", "primary"), "checks": t["checks"]}
