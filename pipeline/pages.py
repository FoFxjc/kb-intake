"""按页分类（OCR 设计 §2，O-1）：原生 / 扫描 / 混合 / 空白，以及页内图片区域的分流。确定性规则，不调用模型。

图片区域（来自 PyMuPDF 的图片信息，裁到页面内；同一位置重复绘制的只算一次）：
- 小于页面 3% → 丢弃（图标、标志、签名），只计数；
- 区域里压着原生文字 → native_text（信息图上的文字已在文字层里）；
- 其余 → pending_ocr：交给 OCR；识别不出可用文字的再丢弃（O-3 执行，O-1 只盘点）。不单独判断“是不是照片”。
页类别：
- scanned：待识别区域合计覆盖页面 85% 以上，且原生字数很少；
- blank：没有达到 3% 的图片区域，且没有文字或只有“本页特意留白”这类说明；
- mixed：有原生文字，同时有待识别区域；
- native：其余。
阈值在 O-1 用 6 份年报与合成扫描集实测后确定（docs/ocr-o1-report.md）。
"""

import re

import pymupdf

MIN_REGION = 0.03          # 小于页面这个比例的图片直接丢弃
NATIVE_TEXT_MIN = 6        # 区域里原生文字（非空白字符）至少这些，才算“图上有原生文字”；按中文短标签定（2024 中文 p11 每张卡片 13–19 字）
NATIVE_TEXT_DENSITY = 100  # 并且按面积折算：至少“区域占页面比例 × 100”个字（大照片上只压一个标题仍要识别，盲标 s06）
SCANNED_COVER = 0.85       # 待识别区域覆盖页面的比例，达到即可能是扫描页
SCANNED_MAX_CHARS = 50     # 扫描页的原生字数上限
BLANK = re.compile(r"(?:intentionally|deliberately)\s+(?:been\s+)?left\s+blank|this page is blank|本页(?:特意|刻意)?(?:留白|空白)|"
                   r"此页(?:特意|刻意)?(?:留白|空白)", re.I)


def _area(r) -> float:
    return max(0.0, r[2] - r[0]) * max(0.0, r[3] - r[1])


def classify(page: pymupdf.Page) -> dict:
    """一页的分类：{kind, chars, regions: [{bbox, area, text_chars, status}], discarded}。"""
    W, H = page.rect.width, page.rect.height
    pa = W * H or 1.0
    text = page.get_text()
    chars = sum(1 for c in text if not c.isspace())
    regions, discarded, seen = [], 0, set()
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
            continue
        # 区域里的原生文字按字符计（裁取区域内的文字）：中文按词取坐标时一整行是一个“词”，词框会超出区域
        inside = sum(1 for c in page.get_text(clip=pymupdf.Rect(r)) if not c.isspace())
        regions.append({"bbox": list(key), "area": round(ratio, 4), "text_chars": inside,
                        "status": "native_text" if inside >= max(NATIVE_TEXT_MIN, ratio * NATIVE_TEXT_DENSITY) else "pending_ocr"})
    regions.sort(key=lambda g: (g["bbox"][1], g["bbox"][0]))
    pending = [g for g in regions if g["status"] == "pending_ocr"]
    cover = min(1.0, sum(g["area"] for g in pending))
    if cover >= SCANNED_COVER and chars < SCANNED_MAX_CHARS:
        kind = "scanned"
    elif not regions and (chars == 0 or BLANK.search(text)):
        kind = "blank"
    elif pending:
        kind = "mixed"
    else:
        kind = "native"
    return {"kind": kind, "chars": chars, "regions": regions, "discarded": discarded}


def classify_pdf(path: str) -> list[dict]:
    """整份 PDF 逐页分类，带物理页码。"""
    with pymupdf.open(path) as pdf:
        return [{"page_no": i + 1, **classify(p)} for i, p in enumerate(pdf)]


def summary(rows: list[dict]) -> dict:
    """检测阶段 detail：各类页数与图片区域计数。"""
    kinds = {k: sum(r["kind"] == k for r in rows) for k in ("native", "scanned", "mixed", "blank")}
    regions = {"discarded": sum(r["discarded"] for r in rows),
               "pending_ocr": sum(g["status"] == "pending_ocr" for r in rows for g in r["regions"]),
               "native_text": sum(g["status"] == "native_text" for r in rows for g in r["regions"])}
    return {"page_kinds": kinds, "image_regions": regions}
