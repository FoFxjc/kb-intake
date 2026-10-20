"""Per-page classification (kb ocr-design §2, O-1): native, scanned, mixed, blank; image regions routed by area and
native text. Unit tests on synthetic pages."""

import io

import pymupdf
from PIL import Image

from kb_intake import detect, pages

TEXT = ("The Group reported net profit of $7,422 million for 2025. Net interest income fell 6% to $9,150 million "
        "as benchmark rates declined, while non-interest income rose to a record. ") * 6


def png(w: int, h: int, colour=(40, 90, 160)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), colour).save(buf, format="PNG")
    return buf.getvalue()


def make(build) -> pymupdf.Page:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    build(page)
    return doc, page


def kind(build):
    doc, page = make(build)
    return pages.classify(page)


def test_native_page():
    r = kind(lambda p: p.insert_textbox(pymupdf.Rect(50, 50, 545, 800), TEXT, fontsize=10))
    assert r["kind"] == "native" and r["chars"] > 500 and r["regions"] == []


def test_full_page_image_without_text_is_scanned():
    r = kind(lambda p: p.insert_image(p.rect, stream=png(400, 560)))
    assert r["kind"] == "scanned" and r["chars"] == 0
    assert [g["status"] for g in r["regions"]] == ["pending_ocr"] and r["regions"][0]["area"] > 0.85


def test_large_image_without_text_makes_a_mixed_page():
    def build(p):
        p.insert_textbox(pymupdf.Rect(50, 50, 545, 400), TEXT, fontsize=10)
        p.insert_image(pymupdf.Rect(100, 450, 495, 750), stream=png(300, 220))
    r = kind(build)
    assert r["kind"] == "mixed"
    g = r["regions"][0]
    assert g["status"] == "pending_ocr" and 0.03 <= g["area"] < 0.85 and g["text_chars"] == 0


def test_small_image_is_discarded():
    def build(p):
        p.insert_textbox(pymupdf.Rect(50, 50, 545, 800), TEXT, fontsize=10)
        p.insert_image(pymupdf.Rect(20, 20, 60, 50), stream=png(40, 30))      # about 0.2% of the page
    r = kind(build)
    assert r["kind"] == "native" and r["regions"] == [] and r["discarded"] == 1


def test_image_with_native_text_on_top_is_not_pending():
    def build(p):
        p.insert_image(pymupdf.Rect(50, 300, 545, 700), stream=png(300, 240))
        p.insert_textbox(pymupdf.Rect(60, 310, 535, 690), TEXT, fontsize=10)  # infographic: native text on the image
    r = kind(build)
    assert r["kind"] == "native" and [g["status"] for g in r["regions"]] == ["native_text"]


def test_blank_and_intentionally_blank_pages():
    assert kind(lambda p: None)["kind"] == "blank"
    assert kind(lambda p: p.insert_text((200, 400), "This page has been intentionally left blank."))["kind"] == "blank"


def test_classification_is_deterministic():
    def build(p):
        p.insert_textbox(pymupdf.Rect(50, 50, 545, 400), TEXT, fontsize=10)
        p.insert_image(pymupdf.Rect(100, 450, 495, 750), stream=png(300, 220))
    assert kind(build) == kind(build)


def test_detect_reports_page_kinds(tmp_path):
    doc = pymupdf.open()
    p1 = doc.new_page(width=595, height=842)
    p1.insert_textbox(pymupdf.Rect(50, 50, 545, 800), TEXT, fontsize=10)
    p2 = doc.new_page(width=595, height=842)
    p2.insert_image(p2.rect, stream=png(400, 560))
    doc.new_page(width=595, height=842)
    pdf = tmp_path / "x.pdf"
    doc.save(pdf)
    d, rows = detect.detect_pdf(str(pdf), "en")
    assert d["page_kinds"] == {"native": 1, "scanned": 1, "mixed": 0, "blank": 1}
    assert d["image_regions"]["pending_ocr"] == 1 and d["page_count"] == 3
    assert [r["kind"] for r in rows] == ["native", "scanned", "blank"]


def test_cjk_text_over_an_image_counts_as_native_text():
    """A CJK line comes back as one "word" whose box sticks out of the image region; count characters by clipping
    the region (found in the inventory: four background images on 2024 zh p11)."""
    def build(p):
        p.insert_textbox(pymupdf.Rect(50, 50, 545, 300), TEXT, fontsize=10)
        p.insert_image(pymupdf.Rect(60, 400, 300, 560), stream=png(200, 130, (245, 245, 245)))
        p.insert_text((40, 480), "\u5e7f\u6cdb\u4e1a\u52a1\u7684\u72ec\u7279\u6027\u3002\u603b\u6536\u5165\u521b\u65b0\u9ad8145\u4ebf\u5143  7%\u51c0\u5229\u6da6\u521b\u65b0\u9ad875\u4ebf9000\u4e07\u5143  8%", fontname="china-s", fontsize=11)
    r = kind(build)
    assert [g["status"] for g in r["regions"]] == ["native_text"] and r["kind"] == "native"


def test_short_cjk_label_over_an_image_counts_as_native_text():
    """Financial highlight card: a background image with only a dozen characters on it still has native text."""
    def build(p):
        p.insert_textbox(pymupdf.Rect(50, 50, 545, 300), TEXT, fontsize=10)
        p.insert_image(pymupdf.Rect(60, 400, 300, 560), stream=png(200, 130, (245, 245, 245)))
        p.insert_text((80, 450), "\u51c0\u5229\u6da6\u521b\u65b0\u9ad8", fontname="china-s", fontsize=11)
        p.insert_text((80, 480), "75\u4ebf9000\u4e07\u5143  8%", fontname="china-s", fontsize=11)
    assert [g["status"] for g in kind(build)["regions"]] == ["native_text"]


def test_large_photo_with_only_a_title_overlay_is_still_pending():
    """A big photo with only a title on it (2023 en p30 "Our Board"): the native character count is scaled by the
    region area, and is not enough here, so the region still goes to OCR (blind label s06)."""
    def build(p):
        p.insert_image(pymupdf.Rect(0, 0, 595, 560), stream=png(400, 380, (90, 80, 70)))
        p.insert_text((40, 60), "Our Board", fontsize=24)
        p.insert_textbox(pymupdf.Rect(50, 580, 545, 820), TEXT, fontsize=9)
    r = kind(build)
    assert [g["status"] for g in r["regions"]] == ["pending_ocr"] and r["kind"] == "mixed"


# ---------- after review ----------

def test_rotated_full_page_scan_is_scanned():
    """Review I1: page.rect of a rotated page is the rotated size while image boxes are unrotated; clip with the
    unrotated page size."""
    doc, page = make(lambda p: p.insert_image(p.rect, stream=png(400, 560)))
    page.set_rotation(90)
    r = pages.classify(page)
    assert r["kind"] == "scanned" and r["regions"][0]["area"] > 0.95


def test_scan_cut_into_strips_or_tiles_is_scanned_not_blank():
    """Review I2: a page cut into 40 strips (about 2.5% each) or 6x6 tiles must not turn blank because every piece
    is under 3% and gets discarded."""
    def strips(p):
        h = p.rect.height / 40
        for i in range(40):
            p.insert_image(pymupdf.Rect(0, i * h, p.rect.width, (i + 1) * h), stream=png(200, 5), keep_proportion=False)
    def tiles(p):
        w, h = p.rect.width / 6, p.rect.height / 6
        for i in range(6):
            for j in range(6):
                p.insert_image(pymupdf.Rect(i * w, j * h, (i + 1) * w, (j + 1) * h), stream=png(30, 40))
    assert kind(strips)["kind"] == "scanned" and kind(tiles)["kind"] == "scanned"


def test_blank_needs_few_characters():
    """Review I3: a text page that contains "... intentionally left blank" is not a blank page."""
    r = kind(lambda p: p.insert_textbox(pymupdf.Rect(50, 50, 545, 800), TEXT + " Page 12 has been intentionally left blank.", fontsize=10))
    assert r["kind"] == "native"


def test_mixed_requires_native_text():
    """Review M3: no native text and one medium image is not a mixed page (by the rules it is scanned content)."""
    r = kind(lambda p: p.insert_image(pymupdf.Rect(50, 50, 545, 500), stream=png(300, 280)))
    assert r["kind"] == "scanned"


def test_detect_survives_a_classification_error(tmp_path, monkeypatch):
    """Review M4: when classification fails, detection degrades and records the error instead of failing."""
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Hello world " * 20)
    pdf = tmp_path / "x.pdf"
    doc.save(pdf)
    monkeypatch.setattr(detect.pages, "classify", lambda page: 1 / 0)
    d, rows = detect.detect_pdf(str(pdf), "en")
    assert d["has_text_layer"] and "ZeroDivisionError" in d["page_kinds_error"] and rows == []
