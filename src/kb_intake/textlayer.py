"""The text layer of a native PDF (PyMuPDF): every text in a package comes from here, read by box (intake-design-v2
§1). Docling only says where regions and table cells are.

Coordinates are PDF points with a TOPLEFT origin, full precision. The only change made to the text is removing the
control characters \\x07 and \\x03 that some PDFs (OCBC's) carry in their text layer; dashes, quotes and spacing
are kept as they are.

A word is [x0, y0, x1, y1, text, block_no, line_no, word_no] (PyMuPDF "words" order); layout.jsonl keeps the first
five fields.
"""

from collections import Counter

import pymupdf

CONTROL = str.maketrans("", "", "\x07\x03")
BOLD_FLAG = 16


def clean(text: str | None) -> str | None:
    return None if text is None else text.translate(CONTROL)


def words(page: pymupdf.Page) -> list[list]:
    out = []
    for w in page.get_text("words"):
        t = clean(w[4])
        if t.strip():
            out.append([w[0], w[1], w[2], w[3], t, w[5], w[6], w[7]])
    return out


def spans(page: pymupdf.Page) -> list[list]:
    """Text spans: [x0, y0, x1, y1, font, size, flags, text]."""
    out = []
    for b in page.get_text("dict")["blocks"]:
        for line in b.get("lines", []):
            for s in line["spans"]:
                t = clean(s["text"])
                if t.strip():
                    out.append([*s["bbox"], s["font"], s["size"], s["flags"], t])
    return out


def page_record(page: pymupdf.Page, page_no: int, label: str | None, kind: str | None) -> dict:
    """One pages.jsonl row without `excluded` (set from the workflow at delivery)."""
    return {"page_no": page_no, "page_label": label, "width": page.rect.width, "height": page.rect.height,
            "kind": kind, "text": clean(page.get_text()), "text_source": "native"}


def layout_record(page_no: int, page_words: list[list]) -> dict:
    return {"page_no": page_no, "words": [w[:5] for w in page_words]}


def centre_in(w, box, pad: float = 0.0) -> bool:
    cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
    return box[0] - pad <= cx <= box[2] + pad and box[1] - pad <= cy <= box[3] + pad


def words_in(page_words: list[list], box, pad: float = 0.0) -> list[list]:
    """The words whose centre lies in `box`, in text-layer order."""
    return [w for w in page_words if centre_in(w, box, pad)]


def text_of(ws: list[list]) -> str | None:
    """Words joined in text-layer order: a space within a line, a newline between lines. None when there are none."""
    if not ws:
        return None
    lines: list[list[str]] = []
    last = None
    for w in ws:
        key = (w[5], w[6])
        if key != last:
            lines.append([])
            last = key
        lines[-1].append(w[4])
    return "\n".join(" ".join(line) for line in lines)


def font_of(page_spans: list[list], box) -> dict | None:
    """The main font of a box: the (name, size, bold) carrying most characters of the spans centred in it."""
    weight: Counter = Counter()
    for s in page_spans:
        if centre_in(s, box):
            bold = bool(s[6] & BOLD_FLAG) or "bold" in s[4].lower()
            weight[(s[4], round(s[5], 2), bold)] += len(s[7].strip())
    if not weight:
        return None
    (name, size, bold), _ = max(weight.items(), key=lambda kv: (kv[1], kv[0]))
    return {"name": name, "size": size, "bold": bold}


def text_box(page_words: list, region) -> tuple[list[float], str]:
    """The contract's cell bbox and bbox_source, for a cell with text whose own box is `region`:
    - "text":  the box of the words whose centre lies in the region, clipped to the region;
    - "words": no word is centred there; the box of the words that overlap the region, clipped to it;
    - "cell":  no word touches the region; the region itself.
    Empty cells get bbox null and bbox_source null from the callers."""
    def overlaps(w):
        return w[0] < region[2] and w[2] > region[0] and w[1] < region[3] and w[3] > region[1]
    inside, source = words_in(page_words, region), "text"
    if not inside:
        inside, source = [w for w in page_words if overlaps(w)], "words"
    if not inside:
        return list(region), "cell"
    return [max(region[0], min(w[0] for w in inside)), max(region[1], min(w[1] for w in inside)),
            min(region[2], max(w[2] for w in inside)), min(region[3], max(w[3] for w in inside))], source
