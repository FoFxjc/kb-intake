"""Step 7 of the pipeline (intake-design §3.1, §9.2.6): the three checks, and which table enters the package.

1. Text conservation: every number word of the text layer inside a table's box must appear in its cells as often,
   and every number in the cells must be in the text layer. Missing words -> `table_text_unaccounted` (with page and
   word box); numbers only in the cells -> `table_cross_mismatch` (source "text_layer").
2. Engine vs find_tables: tables matched by box (IoU >= 0.5 or one inside the other); different numbers ->
   `table_cross_mismatch` (source "find_tables"). The find_tables candidate stays in the package (role secondary).
3. Missed tables: a find_tables candidate no engine table matches is routed by what the engine says is there:
   a picture, chart or diagram -> the model wins and the candidate is marked as a misread; text or nothing, and the
   candidate passes text conservation -> it is entered (extract_method find_tables) with `table_missed`; otherwise
   only `table_missed`. Not implemented: tables neither extractor found (aligned columns of numbers in text).

Pages the engine did not convert take every find_tables candidate of the page as primary, with the ids and the form
they have without an engine, and are not checked; the document gets one `extractor_degraded` gap (pipeline).
"""

from collections import Counter

from kb_intake import postprocess as post


def ft_ok(t: dict) -> bool:
    """find_tables candidates worth comparing: markdown with at least 3 lines."""
    return bool(t["markdown"]) and t["markdown"].count("\n") >= 2


def conservation(t: dict, words_by_page: dict[int, list]) -> tuple[list[list], list[str]]:
    """(text-layer number words not found in the cells, cell numbers not found in the text layer)."""
    layer_words = [w for page, box in zip(t["pages"], t["bboxes"])
                   for w in words_by_page.get(page, []) if post.center_in(w, box)]
    layer = Counter()
    by_token: dict[str, list] = {}
    for w in layer_words:
        for tok in post.numbers(w[4]):
            layer[tok] += 1
            by_token.setdefault(tok, []).append(w)
    cells = Counter(tok for c in t["cells"] + t.get("merged_header_cells", []) for tok in post.numbers(c["text"]))
    missing = []
    for tok, n in (layer - cells).items():
        missing += by_token[tok][-n:]
    extra = sorted((cells - layer).elements())
    return missing, extra


def cross_compare(a: dict, b: dict, page_no: int) -> tuple[list[str], list[str]]:
    """Numbers only in a / only in b, counted over the cells inside the overlap of the two tables on that page
    (find_tables often leaves the header row outside its box; that is not a disagreement about the numbers)."""
    box_a = next(box for p, box in zip(a["pages"], a["bboxes"]) if p == page_no)
    box_b = b["bboxes"][0]
    both = [max(box_a[0], box_b[0]), max(box_a[1], box_b[1]), min(box_a[2], box_b[2]), min(box_a[3], box_b[3])]

    def nums(t):
        return Counter(tok for c in t["cells"] if c.get("page_no", page_no) == page_no and c["bbox"]
                       and post.center_in([*c["bbox"], ""], both) for tok in post.numbers(c["text"]))
    na, nb = nums(a), nums(b)
    return sorted((na - nb).elements()), sorted((nb - na).elements())


def region_at(regions: list[dict], page_no: int, box) -> dict | None:
    """The region that overlaps the box most (on that page)."""
    best, best_i = None, 0.0
    for r in regions:
        if r["page_no"] != page_no:
            continue
        i = post._inter(r["bbox"], box)
        if i > best_i:
            best, best_i = r, i
    return best if best_i / max(1e-9, post._area(box)) >= 0.2 else None


def combine(doc_id: str, engine_tables: list[dict], ft_tables: list[dict], covered: set[int], regions: list[dict],
            refs: dict[str, str], words: dict[int, list],
            gaps: list[dict] | None = None) -> tuple[list[dict], list[dict], list[dict], dict]:
    """(primary tables, secondary tables, gaps, summary). `covered`: pages the engine converted. `gaps`: earlier
    gaps about engine tables (with `ref`); they get the final table_id like the gaps raised here."""
    gaps = list(gaps or [])
    final: list[dict] = []
    secondary: list[dict] = []
    fallback_pages: list[dict] = []             # find_tables tables of pages the engine did not convert
    for t in engine_tables:
        t["checks"]["matched_find_tables"] = []
    for t in ft_tables:
        page_no = t["pages"][0]
        if page_no not in covered:
            fallback_pages.append(t)            # as without an engine: same id, primary, kept even if small
            continue
        t = {**t, "role": "secondary", "checks": {}}
        fid = t["table_id"].replace(":t", ":f", 1)
        t["checks"]["find_tables_id"] = t["table_id"]
        t["table_id"] = fid
        match = next((d for d in engine_tables for p, box in zip(d["pages"], d["bboxes"])
                      if p == page_no and post.same_box(box, t["bboxes"][0])), None)
        if match:
            only_engine, only_ft = cross_compare(match, t, page_no)
            match["checks"]["matched_find_tables"].append(fid)
            t["checks"]["matched_engine"] = match["engine_ref"]
            if only_engine or only_ft:
                gaps.append({"code": "table_cross_mismatch", "source": "find_tables", "page_no": page_no,
                             "ref": match["engine_ref"], "find_tables_id": fid,
                             "only_engine": only_engine[:20], "only_find_tables": only_ft[:20]})
            secondary.append(t)
            continue
        if not ft_ok(t):
            secondary.append(t)
            continue
        region = region_at(regions, page_no, t["bboxes"][0])
        rtype = region["type"] if region else None
        if rtype in ("picture", "chart", "diagram"):
            t["checks"]["misread"] = f"engine region {region['region_id']} is {rtype}"
            secondary.append(t)
            continue
        missing, extra = conservation(t, words)
        if not missing and not extra:
            t["role"], t["checks"]["fallback"] = "primary", f"engine region: {rtype or 'none'}"
            final.append(t)
            gaps.append({"code": "table_missed", "page_no": page_no, "find_tables_id": fid, "resolution": "find_tables",
                         "engine_region": region["region_id"] if region else None, "_table": t})
        else:
            secondary.append(t)
            gaps.append({"code": "table_missed", "page_no": page_no, "find_tables_id": fid, "resolution": "not_entered",
                         "engine_region": region["region_id"] if region else None,
                         "detail": f"find_tables candidate fails text conservation ({len(missing)} missing, "
                                   f"{len(extra)} extra)"})
    final += [t for t in engine_tables if t["pages"][0] in covered]

    # ids on converted pages: in page order, then by position (find_tables candidates keep their "f" ids)
    final.sort(key=lambda t: (t["pages"][0], t["bboxes"][0][1], t["bboxes"][0][0]))
    per_page = Counter()
    tid_of: dict[str, str] = {}
    for t in final:
        per_page[t["pages"][0]] += 1
        tid = f"{doc_id}:t{t['pages'][0]:04d}_{per_page[t['pages'][0]]:02d}"
        for ref in t.get("merged_refs", [t["engine_ref"]] if "engine_ref" in t else []):
            tid_of[ref] = tid                    # every part of a merged table points at the merged table
            if ref in refs:
                t.setdefault("region_ids", []).append(refs[ref])
        t["table_id"] = tid
    for g in gaps:
        if g.get("ref") in tid_of:
            g["table_id"] = tid_of[g["ref"]]
        if "_table" in g:                        # a find_tables table entered as primary: its final id
            g["table_id"] = g.pop("_table")["table_id"]
    for r in regions:
        if r["type"] == "table":
            for ref, rid in refs.items():
                if rid == r["region_id"] and ref in tid_of:
                    r["ref"] = tid_of[ref]

    # check 1: text conservation on every primary table of a converted page
    for t in final:
        missing, extra = conservation(t, words)
        t["checks"]["text_conservation"] = {"missing": len(missing), "extra": len(extra)}
        for w in missing:
            gaps.append({"code": "table_text_unaccounted", "page_no": next(
                (p for p, b in zip(t["pages"], t["bboxes"]) if post.center_in(w, b)), t["pages"][0]),
                "table_id": t["table_id"], "text": w[4], "bbox": list(w[:4])})
        for tok in extra:
            gaps.append({"code": "table_cross_mismatch", "source": "text_layer", "page_no": t["pages"][0],
                         "table_id": t["table_id"], "detail": f"cell number {tok!r} not in the text layer"})

    primary = sorted(final + fallback_pages, key=lambda t: (t["pages"][0], t["table_id"]))
    summary = {"tables_primary": len(primary), "tables_secondary": len(secondary),
               "by_method": dict(Counter(t["extract_method"] for t in primary)),
               "gaps": dict(sorted(Counter(g["code"] for g in gaps).items())),
               "engine_pages": len(covered), "fallback_tables": len(fallback_pages)}
    return primary, secondary, gaps, summary


def row(t: dict, context: list[dict]) -> dict:
    """A tables.jsonl row (contract v1 key order; role and context_above are additions within v1)."""
    return {"table_id": t["table_id"], "region_ids": t.get("region_ids", []), "pages": t["pages"], "bboxes": t["bboxes"],
            "n_rows": t["n_rows"], "n_cols": t["n_cols"], "cells": t["cells"], "header_rows": t["header_rows"],
            "markdown": t.get("markdown") or post.markdown(t["n_rows"], t["n_cols"], t["cells"]),
            "extract_method": t["extract_method"], "continued_from": t.get("continued_from"),
            "checks": t["checks"], "role": t.get("role", "primary"), "context_above": context}
