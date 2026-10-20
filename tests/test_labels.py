"""Printed page labels (moved from kb tests/test_tree.py with page_labels())."""

import pymupdf

from kb_intake.labels import page_labels


def _pdf(path, pages, footer=None, labels=None):
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 100), f"body text of page {i + 1} " * 3)
        if footer is not None:
            page.insert_text((540, page.rect.height - 20), str(i + 1 - footer))
    if labels:
        doc.set_page_labels(labels)
    doc.save(path)
    return str(path)


def test_page_labels_prefer_pdf_labels(tmp_path):
    pdf = _pdf(tmp_path / "l.pdf", 4, footer=0,
               labels=[{"startpage": 0, "prefix": "", "style": "r", "firstpagenum": 1},
                       {"startpage": 2, "prefix": "", "style": "D", "firstpagenum": 1}])
    labels, method = page_labels(pdf)
    assert labels == ["i", "ii", "1", "2"] and method["method"] == "page_labels"


def test_page_labels_footer_offset(tmp_path):
    labels, method = page_labels(_pdf(tmp_path / "f.pdf", 5, footer=2))
    assert method == {"method": "footer_offset", "offset": 2, "pages_agreeing": 3}
    assert labels == [None, None, "1", "2", "3"]


def test_page_labels_none(tmp_path):
    labels, method = page_labels(_pdf(tmp_path / "x.pdf", 2))
    assert labels == [None, None] and method["method"] == "none"
