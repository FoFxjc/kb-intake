"""Per-page classification (ocr-design §2): native / scanned / mixed / blank, plus routing of the
image regions on a page. Deterministic rules, no model.

Image regions (from PyMuPDF image info, clipped to the page; an image drawn twice at the same place counts once):
- smaller than 3% of the page -> discarded (icons, logos, signatures), only counted;
- native text lies on top of the region -> native_text (the text of an infographic is already in the text layer);
- otherwise -> pending_ocr: goes to OCR; regions without usable OCR text are discarded later (O-3).
Page kinds:
- scanned: very little native text, and images (pending regions plus discarded strips and tiles) cover at least
  85% of the page; or no native text at all but some pending region;
- blank: no image region of 3% or more, images do not fill the page, and no text or only a short
  "intentionally left blank" note;
- mixed: native text plus pending regions;
- native: everything else.
Thresholds were measured in O-1 on the six annual reports and the synthetic scan set (ocr-design O-1).
"""

import re

import pymupdf

MIN_REGION = 0.03          # images smaller than this share of the page are discarded
NATIVE_TEXT_MIN = 6        # a region needs at least this many native non-space characters to count as "text on image";
                           # set from short Chinese labels (2024 zh p11: 13-19 characters per card)
NATIVE_TEXT_DENSITY = 100  # and, scaled by area, at least "region share x 100" characters (a big photo with just a
                           # title on it still needs OCR; blind label s06)
SCANNED_COVER = 0.85       # pending regions covering this share of the page may make it a scanned page
SCANNED_MAX_CHARS = 50     # upper bound of native characters on a scanned page
BLANK_MAX_CHARS = 60       # upper bound of characters on a blank page (blank note and page number)
BLANK = re.compile(r"(?:intentionally|deliberately)\s+(?:been\s+)?left\s+blank|this page is blank|"
                   "\u672c\u9875(?:\u7279\u610f|\u523b\u610f)?(?:\u7559\u767d|\u7a7a\u767d)|"
                   "\u6b64\u9875(?:\u7279\u610f|\u523b\u610f)?(?:\u7559\u767d|\u7a7a\u767d)", re.I)


def _area(r) -> float:
    return max(0.0, r[2] - r[0]) * max(0.0, r[3] - r[1])


def classify(page: pymupdf.Page) -> dict:
    """Classify one page: {kind, chars, regions: [{bbox, area, text_chars, status}], discarded}."""
    # Use the unrotated page size: image boxes and text coordinates are unrotated
    # (page.rect of a rotated page is the rotated size; review I1)
    W, H = page.cropbox.width, page.cropbox.height
    pa = W * H or 1.0
    text = page.get_text()
    chars = sum(1 for c in text if not c.isspace())
    regions, discarded, small, seen = [], 0, 0.0, set()
    for info in page.get_image_info():
        x0, y0, x1, y1 = info["bbox"]
        r = (max(x0, 0.0), max(y0, 0.0), min(x1, W), min(y1, H))
        key = tuple(round(v, 1) for v in r)
        if _area(r) <= 0 or key in seen:
            continue
        seen.add(key)
        ratio = _area(r) / pa
        if ratio < MIN_REGION:
            discarded += 1
            small += ratio            # a full-page scan made of strips or tiles: each tile is discarded,
            continue                  # but its area still counts towards coverage (review I2)
        # Count native characters inside the region by clipping: CJK lines come back as one "word" whose
        # box can stick out of the region
        inside = sum(1 for c in page.get_text(clip=pymupdf.Rect(r)) if not c.isspace())
        regions.append({"bbox": list(key), "area": round(ratio, 4), "text_chars": inside,
                        "status": "native_text" if inside >= max(NATIVE_TEXT_MIN, ratio * NATIVE_TEXT_DENSITY) else "pending_ocr"})
    regions.sort(key=lambda g: (g["bbox"][1], g["bbox"][0]))
    pending = [g for g in regions if g["status"] == "pending_ocr"]
    cover = min(1.0, sum(g["area"] for g in pending) + small)
    if chars < SCANNED_MAX_CHARS and (cover >= SCANNED_COVER or (chars == 0 and pending)):
        kind = "scanned"
    elif not regions and cover < SCANNED_COVER and (chars == 0 or (chars <= BLANK_MAX_CHARS and BLANK.search(text))):
        kind = "blank"
    elif pending and chars > 0:
        kind = "mixed"
    else:
        kind = "native"
    return {"kind": kind, "chars": chars, "regions": regions, "discarded": discarded}


def summary(rows: list[dict]) -> dict:
    """Detection summary: page counts per kind and image region counts."""
    kinds = {k: sum(r["kind"] == k for r in rows) for k in ("native", "scanned", "mixed", "blank")}
    regions = {"discarded": sum(r["discarded"] for r in rows),
               "pending_ocr": sum(g["status"] == "pending_ocr" for r in rows for g in r["regions"]),
               "native_text": sum(g["status"] == "native_text" for r in rows for g in r["regions"])}
    return {"page_kinds": kinds, "image_regions": regions}
