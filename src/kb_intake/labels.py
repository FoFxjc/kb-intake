"""Printed page labels: physical page <-> printed page number.

Moved from kb `pipeline/tree.page_labels()` in the D49 split; logic unchanged.
"""

import re
from collections import Counter

import pymupdf

_FOOTER_NUM = re.compile(r"^\d{1,3}$")


def page_labels(pdf_path: str) -> tuple[list[str | None], dict]:
    """Printed label of every physical page. Prefer the PDF PageLabels; otherwise take the most common
    offset between physical pages and footer numbers; otherwise no labels."""
    with pymupdf.open(pdf_path) as doc:
        return _labels(doc)


def _labels(doc: pymupdf.Document) -> tuple[list[str | None], dict]:
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
