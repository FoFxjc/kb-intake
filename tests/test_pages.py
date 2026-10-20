"""按页分类（OCR 设计 §2，O-1）：原生、扫描、混合、空白；图片区域按面积与是否有原生文字分流。单元测试，用合成页面。"""

import io

import pymupdf
from PIL import Image

from pipeline import pages

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
        p.insert_image(pymupdf.Rect(20, 20, 60, 50), stream=png(40, 30))      # 约 0.2% 的页面
    r = kind(build)
    assert r["kind"] == "native" and r["regions"] == [] and r["discarded"] == 1


def test_image_with_native_text_on_top_is_not_pending():
    def build(p):
        p.insert_image(pymupdf.Rect(50, 300, 545, 700), stream=png(300, 240))
        p.insert_textbox(pymupdf.Rect(60, 310, 535, 690), TEXT, fontsize=10)  # 信息图：图上压着原生文字
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


def test_detect_reports_page_kinds_and_writes_the_artifact(tmp_path, monkeypatch):
    from pipeline import ingest
    doc = pymupdf.open()
    p1 = doc.new_page(width=595, height=842)
    p1.insert_textbox(pymupdf.Rect(50, 50, 545, 800), TEXT, fontsize=10)
    p2 = doc.new_page(width=595, height=842)
    p2.insert_image(p2.rect, stream=png(400, 560))
    doc.new_page(width=595, height=842)
    pdf = tmp_path / "x.pdf"
    doc.save(pdf)
    monkeypatch.setattr(ingest, "PAGES_DIR", tmp_path / "pages")
    d = ingest.detect({"doc_id": "x_doc_2025_en", "path": str(pdf), "lang": "en"})
    assert d["page_kinds"] == {"native": 1, "scanned": 1, "mixed": 0, "blank": 1}
    assert d["image_regions"]["pending_ocr"] == 1 and d["page_count"] == 3
    rows = (tmp_path / "pages" / "x_doc_2025_en.jsonl").read_text().splitlines()
    assert [__import__("json").loads(r)["kind"] for r in rows] == ["native", "scanned", "blank"]
