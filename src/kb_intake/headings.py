"""Heading candidates (intake-design §9.2 step 7): font + numbering rules first, Docling headings as corroboration.

Only the file is used, so this belongs to intake; building the tree from the candidates stays in kb.
- A font profile (profiles/fonts/*.yaml) maps (font, size) to heading levels for one document series. The profile is
  chosen by the fonts the document uses; without a matching profile the levels are inferred from the font x size
  distribution and the numbering ("N." / "N.N"), and the manifest says so.
- A line's style is its dominant (font, size) by character count; consecutive lines of the same style in one text
  block are one heading (multi-line titles).
- Furniture: a chapter title repeated on the following pages is a running header, and a "(continued)" title repeats
  an earlier heading. Both are kept with furniture = true, so kb can drop them.
"""

import re
from collections import Counter
from pathlib import Path

import pymupdf
import yaml

NOTE = re.compile(r"^\d{1,3}\.\s+\S")
SUBNOTE = re.compile(r"^\d{1,3}\.\d{1,3}(?:\.\d+)*\s+\S")
CONTINUED = re.compile(r"\(continued\)|\uff08\u7eed\uff09", re.I)
PROFILE_DIR = Path(__file__).resolve().parents[2] / "profiles" / "fonts"


def _font(name: str) -> str:
    return name.split("+")[-1]


def lines(pdf: pymupdf.Document) -> list[dict]:
    """Text lines with their dominant style: {page_no, bbox, text, font, size, block}. Pieces on the same baseline
    in the same style are one line ("21." and "Debt Issued" are separate PDF lines); exact duplicates (text drawn
    twice for a bold effect) are dropped."""
    out = []
    for i, page in enumerate(pdf):
        raw = _raw_lines(page, i + 1)
        raw.sort(key=lambda ln: (round(ln["bbox"][1]), ln["bbox"][0]))
        merged: list[dict] = []
        for ln in raw:
            prev = merged[-1] if merged else None
            if prev and (prev["font"], prev["size"]) == (ln["font"], ln["size"]) and abs(prev["bbox"][1] - ln["bbox"][1]) < 1 \
                    and abs(prev["bbox"][3] - ln["bbox"][3]) < 1:
                if ln["text"] == prev["text"] and ln["bbox"] == prev["bbox"]:
                    continue
                if 0 <= ln["bbox"][0] - prev["bbox"][2] <= 4 * ln["size"]:
                    prev["text"] = f"{prev['text']} {ln['text']}"
                    prev["bbox"] = [prev["bbox"][0], min(prev["bbox"][1], ln["bbox"][1]), ln["bbox"][2],
                                    max(prev["bbox"][3], ln["bbox"][3])]
                    continue
            merged.append(ln)
        merged.sort(key=lambda ln: (ln["block"], ln["bbox"][1], ln["bbox"][0]))
        out += merged
    return out


def _raw_lines(page: pymupdf.Page, page_no: int) -> list[dict]:
    out = []
    if True:
        for b_no, block in enumerate(page.get_text("dict")["blocks"]):
            for line in block.get("lines", []):
                spans = [s for s in line["spans"] if s["text"].strip()]
                if not spans:
                    continue
                style = Counter()
                for s in spans:
                    style[(_font(s["font"]), round(s["size"], 1))] += len(s["text"].strip())
                (font, size), _ = style.most_common(1)[0]
                text = " ".join("".join(s["text"] for s in line["spans"]).replace("\x07", " ").replace("\x03", " ").split())
                out.append({"page_no": page_no, "bbox": list(line["bbox"]), "text": text, "font": font, "size": size,
                            "block": b_no})
    return out


def load_profiles(directory: Path = PROFILE_DIR) -> list[dict]:
    return [yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("*.yaml"))]


def pick_profile(all_lines: list[dict], profiles: list[dict]) -> dict | None:
    fonts = {ln["font"] for ln in all_lines}
    for p in profiles:
        if set(p["match"]["fonts_all"]) <= fonts:
            return p
    return None


def infer_profile(all_lines: list[dict]) -> dict:
    """No configured profile: level 2 = the style that most "N." lines use, level 3 = the style of "N.N" lines,
    level 1 = the largest style (>= 14 pt) that starts many pages near the top."""
    note = Counter((ln["font"], ln["size"]) for ln in all_lines if NOTE.match(ln["text"]))
    sub = Counter((ln["font"], ln["size"]) for ln in all_lines if SUBNOTE.match(ln["text"]))
    top = Counter((ln["font"], ln["size"]) for ln in all_lines if ln["size"] >= 14 and ln["bbox"][1] < 120)
    levels = []
    if top:
        (f, s), _ = max(top.items(), key=lambda kv: (kv[1] >= 3, kv[0][1], kv[1]))
        levels.append({"level": 1, "kind": "chapter", "font": f, "size": s})
    if note:
        (f, s), _ = note.most_common(1)[0]
        levels.append({"level": 2, "kind": "note", "font": f, "size": s, "numbering": NOTE.pattern})
    if sub:
        (f, s), _ = sub.most_common(1)[0]
        levels.append({"level": 3, "kind": "subnote", "font": f, "size": s, "numbering": SUBNOTE.pattern})
    return {"name": "auto", "version": 1, "levels": levels}


def _numbering(text: str) -> str | None:
    m = SUBNOTE.match(text) or NOTE.match(text)
    return m.group(0).split()[0] if m else None


def candidates(all_lines: list[dict], profile: dict) -> list[dict]:
    rules = [(lv, re.compile(lv["numbering"]) if lv.get("numbering") else None) for lv in profile["levels"]]
    found: list[dict] = []
    for ln in all_lines:
        rule = next((lv for lv, num in rules if lv["font"] == ln["font"] and abs(lv["size"] - ln["size"]) < 0.25
                     and (num is None or num.match(ln["text"]))), None)
        if rule is None:
            continue
        prev = found[-1] if found else None
        if (prev and prev["page_no"] == ln["page_no"] and prev["level"] == rule["level"]
                and -0.5 * ln["size"] <= ln["bbox"][1] - prev["bbox"][3] < ln["size"] * 0.8
                and min(prev["bbox"][2], ln["bbox"][2]) > max(prev["bbox"][0], ln["bbox"][0])
                and not (rule.get("numbering") and _numbering(ln["text"]))):        # next line of the same title
            prev["text"] = f"{prev['text']} {ln['text']}"
            prev["bbox"] = [min(prev["bbox"][0], ln["bbox"][0]), prev["bbox"][1],
                            max(prev["bbox"][2], ln["bbox"][2]), ln["bbox"][3]]
            continue
        found.append({"page_no": ln["page_no"], "bbox": ln["bbox"], "text": ln["text"], "level": rule["level"],
                      "kind": rule["kind"], "_block": ln["block"]})
    return found


def mark_furniture(found: list[dict]) -> None:
    last_chapter: dict[str, str] = {}
    for h in found:
        h["numbering"] = _numbering(h["text"])
        h["furniture"] = False
        if h["kind"] == "chapter":
            if last_chapter.get("text") == h["text"]:      # the chapter title again (running header on later pages)
                h["furniture"] = True
            last_chapter["text"] = h["text"]
        elif CONTINUED.search(h["text"]):
            h["furniture"] = True                           # "(continued)" repeats an earlier heading


def headings(pdf: pymupdf.Document, docling_heads: list[dict], profiles: list[dict] | None = None) -> tuple[list[dict], dict]:
    """headings.jsonl rows and a summary for the manifest. docling_heads: regions of type title/heading."""
    all_lines = lines(pdf)
    profile = pick_profile(all_lines, load_profiles() if profiles is None else profiles)
    source = "profile"
    if profile is None:
        profile, source = infer_profile(all_lines), "auto"
    found = candidates(all_lines, profile)
    mark_furniture(found)
    rows = []
    for h in found:
        box = h["bbox"]
        match = next((r for r in docling_heads if r["page_no"] == h["page_no"] and _overlap(r["bbox"], box) >= 0.5), None)
        sources = ["font_rule"] + (["numbering"] if h["numbering"] else []) + (["docling"] if match else [])
        rows.append({"region_id": match["region_id"] if match else None, "page_no": h["page_no"], "bbox": box,
                     "text": h["text"], "level": h["level"], "kind": h["kind"], "numbering": h["numbering"],
                     "sources": sources, "furniture": h["furniture"]})
    summary = {"profile": profile["name"], "profile_version": profile.get("version"), "source": source,
               "levels": profile["levels"], "candidates": len(rows),
               "by_level": dict(sorted(Counter(r["level"] for r in rows if not r["furniture"]).items())),
               "furniture": sum(r["furniture"] for r in rows),
               "with_docling": sum("docling" in r["sources"] for r in rows)}
    return rows, summary


def _overlap(a, b) -> float:
    i = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return i / small if small > 0 else 0.0
