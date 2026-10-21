"""Similar-region groups (intake-ui-llm-design-v1 §3.3): deterministic rules, no vector model. A group only helps
the reviewer act on its members together; it changes no data (an action on a group is recorded per member).

| kind               | members                                                     | key                                      |
|--------------------|-------------------------------------------------------------|------------------------------------------|
| repeated_text      | text-like regions whose vertical centre lies in the top band | type, normalised text, band, font name,  |
|                    | (< `top` of the page height) or the bottom band (> `bottom`) | font size rounded to 1 pt; >= 2 pages     |
| same_shape         | icon, logo, signature, stamp, picture, chart, diagram       | type, subtype, round(2 log2 w), round(2   |
|                    |                                                             | log2 h) (w, h in PDF points)              |
| same_table_header  | tables whose first row has text                             | n_cols, normalised first-row cells        |
| same_gap           | regions with an open gap                                    | gap code                                  |

Text is normalised with `normalise` (digits become `#`, so page numbers and year headers group). Excluded and
absorbed regions are left out. Only groups of two or more are returned; ids are `kind:` + 12 hex of the key's sha1.
"""

import hashlib
import json
import math
import re
import unicodedata
from statistics import median

from kb_intake.search import Rows
from kb_intake.vocab import GAP_LABELS

KINDS = ("repeated_text", "same_shape", "same_table_header", "same_gap")
SHAPE_TYPES = ("icon", "logo", "signature", "stamp", "picture", "chart", "diagram")
NOT_TEXT = ("table", "formula", *SHAPE_TYPES)
LEFT_OUT = ("excluded", "absorbed")


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    text = re.sub(r"\d+", "#", text)
    return re.sub(r"\s+", " ", text).strip()


def _cluster(kind: str, key, label: str, basis: dict, members: list[dict]) -> dict:
    members = sorted(members, key=lambda r: r["reading_order"])
    digest = hashlib.sha1(json.dumps([kind, key], sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
    return {"cluster_id": f"{kind}:{digest}", "kind": kind, "label": label, "basis": basis, "n": len(members),
            "pages": sorted({r["page_no"] for r in members}), "members": [r["region_id"] for r in members]}


def _band(r: dict, pages: dict, top: float, bottom: float) -> str | None:
    cy = (r["bbox"][1] + r["bbox"][3]) / 2 / pages[r["page_no"]]["height"]
    return "top" if cy < top else "bottom" if cy > bottom else None


def _repeated_text(regions, pages, top, bottom) -> list[dict]:
    groups: dict[tuple, list[dict]] = {}
    for r in regions:
        if r["type"] in NOT_TEXT or not r.get("text"):
            continue
        norm, band = normalise(r["text"]), _band(r, pages, top, bottom)
        if not norm or band is None:
            continue
        font = r.get("font") or {}
        size = round(font["size"]) if font.get("size") is not None else None
        groups.setdefault((r["type"], norm, band, font.get("name"), size), []).append(r)
    out = []
    for (type_, norm, band, name, size), members in groups.items():
        if len({m["page_no"] for m in members}) < 2:
            continue
        first = min(members, key=lambda m: m["reading_order"])
        out.append(_cluster("repeated_text", [type_, norm, band, name, size], " ".join(first["text"].split())[:80],
                            {"type": type_, "text": norm, "band": band, "font": {"name": name, "size": size}},
                            members))
    return out


def _same_shape(regions) -> list[dict]:
    groups: dict[tuple, list[dict]] = {}
    for r in regions:
        if r["type"] not in SHAPE_TYPES:
            continue
        w, h = max(r["bbox"][2] - r["bbox"][0], 1.0), max(r["bbox"][3] - r["bbox"][1], 1.0)
        groups.setdefault((r["type"], r["subtype"], round(2 * math.log2(w)), round(2 * math.log2(h))), []).append(r)
    out = []
    for (type_, subtype, bw, bh), members in groups.items():
        if len(members) < 2:
            continue
        mw = median(m["bbox"][2] - m["bbox"][0] for m in members)
        mh = median(m["bbox"][3] - m["bbox"][1] for m in members)
        out.append(_cluster("same_shape", [type_, subtype, bw, bh], f"{type_.capitalize()}s about {mw:.0f}×{mh:.0f} pt",
                            {"type": type_, "subtype": subtype, "size_bucket": [bw, bh]}, members))
    return out


def _same_table_header(regions, tables) -> list[dict]:
    by_region = {r["region_id"]: r for r in regions if r["type"] == "table"}
    groups: dict[tuple, list[tuple[dict, list[str]]]] = {}
    for t in tables:
        r = by_region.get(t["region_id"])
        if r is None:
            continue
        first = [c.get("text") or "" for c in sorted((c for c in t["cells"] if c["r"] == 0), key=lambda c: c["c"])]
        if not any(x.strip() for x in first):
            continue
        groups.setdefault((t["n_cols"], " | ".join(normalise(x) for x in first)), []).append((r, first))
    out = []
    for (n_cols, row), pairs in groups.items():
        if len(pairs) < 2:
            continue
        first_member = min(pairs, key=lambda p: p[0]["reading_order"])
        shown = " | ".join(" ".join(x.split()) for x in first_member[1] if x.strip())
        out.append(_cluster("same_table_header", [n_cols, row], f'Tables headed "{shown}"',
                            {"n_cols": n_cols, "first_row": row}, [p[0] for p in pairs]))
    return out


def _same_gap(regions, gaps) -> list[dict]:
    by_id = {r["region_id"]: r for r in regions}
    groups: dict[str, dict[str, dict]] = {}
    for g in gaps:
        r = by_id.get(g.get("region_id") or "")
        if r is not None and not g.get("resolved"):
            groups.setdefault(g["code"], {})[r["region_id"]] = r
    return [_cluster("same_gap", [code], GAP_LABELS.get(code, code), {"code": code}, list(members.values()))
            for code, members in groups.items() if len(members) >= 2]


def build(rows: Rows, pages: dict[int, dict], gaps: list[dict], top: float = 0.12, bottom: float = 0.88) -> list[dict]:
    """The groups of two or more members, ordered by kind, size (largest first) and label."""
    regions = [r for r in rows.regions if r.get("status") not in LEFT_OUT]
    out = (_repeated_text(regions, pages, top, bottom) + _same_shape(regions)
           + _same_table_header(regions, rows.tables) + _same_gap(regions, gaps))
    return sorted(out, key=lambda c: (KINDS.index(c["kind"]), -c["n"], c["label"], c["cluster_id"]))


def for_job(jobs, job_id: str, kind: str | None = None) -> list[dict]:
    """The groups of a job (label-stage regions before extraction, current after). KeyError when not labelled."""
    from kb_intake import review, search
    from kb_intake.package import read_jsonl
    rows = search.load_rows(jobs, job_id)
    d = jobs.get(job_id).dir
    pages = {p["page_no"]: p for p in read_jsonl(d / "label" / "pages.jsonl")}
    if rows.stage == "current":
        gaps = review.all_gaps(d)
    else:
        gaps = read_jsonl(d / "label" / "gaps.jsonl") if (d / "label" / "gaps.jsonl").is_file() else []
    out = build(rows, pages, gaps)
    return [c for c in out if kind is None or c["kind"] == kind]
