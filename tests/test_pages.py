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


def test_cjk_text_over_an_image_counts_as_native_text():
    """中文按词取坐标时一整行是一个“词”，词框会超出图片区域；按区域裁取文字计数（盘点发现：2024 中文版 p11 的四张底图）。"""
    def build(p):
        p.insert_textbox(pymupdf.Rect(50, 50, 545, 300), TEXT, fontsize=10)
        p.insert_image(pymupdf.Rect(60, 400, 300, 560), stream=png(200, 130, (245, 245, 245)))
        p.insert_text((40, 480), "广泛业务的独特性。总收入创新高145亿元  7%净利润创新高75亿9000万元  8%", fontname="china-s", fontsize=11)
    r = kind(build)
    assert [g["status"] for g in r["regions"]] == ["native_text"] and r["kind"] == "native"


def test_short_cjk_label_over_an_image_counts_as_native_text():
    """财务重点卡片：底图上只压着十几个字（“净利润创新高 75亿9000万元 8%”），也算图上有原生文字。"""
    def build(p):
        p.insert_textbox(pymupdf.Rect(50, 50, 545, 300), TEXT, fontsize=10)
        p.insert_image(pymupdf.Rect(60, 400, 300, 560), stream=png(200, 130, (245, 245, 245)))
        p.insert_text((80, 450), "净利润创新高", fontname="china-s", fontsize=11)
        p.insert_text((80, 480), "75亿9000万元  8%", fontname="china-s", fontsize=11)
    assert [g["status"] for g in kind(build)["regions"]] == ["native_text"]


def test_large_photo_with_only_a_title_overlay_is_still_pending():
    """大照片上只压着一个标题（2023 英 p30 “Our Board”）：原生字数按区域面积折算，不够就仍交给 OCR（盲标 s06）。"""
    def build(p):
        p.insert_image(pymupdf.Rect(0, 0, 595, 560), stream=png(400, 380, (90, 80, 70)))
        p.insert_text((40, 60), "Our Board", fontsize=24)
        p.insert_textbox(pymupdf.Rect(50, 580, 545, 820), TEXT, fontsize=9)
    r = kind(build)
    assert [g["status"] for g in r["regions"]] == ["pending_ocr"] and r["kind"] == "mixed"


# ---------- 审查之后 ----------

def test_rotated_full_page_scan_is_scanned():
    """审查 I1：旋转页的 page.rect 是转过之后的尺寸，图片框是未旋转的坐标；按未旋转的页面尺寸裁剪。"""
    doc, page = make(lambda p: p.insert_image(p.rect, stream=png(400, 560)))
    page.set_rotation(90)
    r = pages.classify(page)
    assert r["kind"] == "scanned" and r["regions"][0]["area"] > 0.95


def test_scan_cut_into_strips_or_tiles_is_scanned_not_blank():
    """审查 I2：整页切成 40 条（每条约 2.5%）或 6×6 小块，不能因为每块都小于 3% 被丢弃后判成空白。"""
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
    """审查 I3：正文页里出现一句“……intentionally left blank”，不是空白页。"""
    r = kind(lambda p: p.insert_textbox(pymupdf.Rect(50, 50, 545, 800), TEXT + " Page 12 has been intentionally left blank.", fontsize=10))
    assert r["kind"] == "native"


def test_mixed_requires_native_text():
    """审查 M3：没有原生文字、只有一张中等大小的图，不算混合页（按规则是待识别的扫描内容）。"""
    r = kind(lambda p: p.insert_image(pymupdf.Rect(50, 50, 545, 500), stream=png(300, 280)))
    assert r["kind"] == "scanned"


def test_detect_survives_a_classification_error(tmp_path, monkeypatch):
    """审查 M4：分类或写中间产物出错时，检测阶段降级记录错误，不让整份文档因此被拒。"""
    from pipeline import ingest
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Hello world " * 20)
    pdf = tmp_path / "x.pdf"
    doc.save(pdf)
    monkeypatch.setattr(ingest, "PAGES_DIR", tmp_path / "pages")
    monkeypatch.setattr(ingest.pages, "classify", lambda page: 1 / 0)
    d = ingest.detect({"doc_id": "x_doc_2025_en", "path": str(pdf), "lang": "en"})
    assert d["has_text_layer"] and "ZeroDivisionError" in d["page_kinds_error"]
