import pymupdf
import pytest

from pipeline.tree import bookmark_tree, flatten, page_labels


def _pdf(path, pages, toc=None, footer=None, labels=None):
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 100), f"body text of page {i + 1} " * 3)
        if footer is not None:
            page.insert_text((540, page.rect.height - 20), str(i + 1 - footer))
    if toc:
        doc.set_toc(toc)
    if labels:
        doc.set_page_labels(labels)
    doc.save(path)
    return str(path)


TREE = {
    "structure": [
        {"title": "A", "node_id": "0000", "start_index": 1, "end_index": 4, "nodes": [
            {"title": "A1", "node_id": "0001", "start_index": 2, "end_index": 2},
            {"title": "A2", "node_id": "0002", "start_index": 2, "end_index": 4, "nodes": []},
        ]},
        {"title": "B", "node_id": "0003", "start_index": 5, "end_index": 6},
    ]
}


def test_flatten_ids_hierarchy_and_pages():
    rows = flatten(TREE, doc_id="d1", tree_source="toc_flash", is_primary=True)
    assert [r["node_id"] for r in rows] == [
        "d1:toc_flash:0000", "d1:toc_flash:0001", "d1:toc_flash:0002", "d1:toc_flash:0003"]
    a, a1, a2, b = rows
    assert a["parent_id"] is None and a["ancestors"] == [] and a["depth"] == 1 and a["ord"] == 0
    assert a1["parent_id"] == "d1:toc_flash:0000" and a1["ancestors"] == ["d1:toc_flash:0000"]
    assert (a1["depth"], a1["ord"], a2["ord"], b["ord"]) == (2, 0, 1, 1)
    assert (a2["start_page"], a2["end_page"]) == (2, 4)  # 闭区间，兄弟可共用边界页
    assert all(r["is_primary"] and r["tree_source"] == "toc_flash" and r["doc_id"] == "d1" for r in rows)
    assert a["profile"] == "generic" and a["ext"] == {}


def test_flatten_rejects_bad_ranges_and_sources():
    bad = {"structure": [{"title": "X", "node_id": "0000", "start_index": 3, "end_index": 2}]}
    with pytest.raises(ValueError):
        flatten(bad, doc_id="d1", tree_source="toc_flash", is_primary=False)
    with pytest.raises(ValueError):
        flatten(TREE, doc_id="d1", tree_source="nope", is_primary=False)


def test_bookmark_tree_ranges(tmp_path):
    pdf = _pdf(tmp_path / "b.pdf", 6, toc=[[1, "A", 1], [2, "A1", 2], [2, "A2", 3], [1, "B", 5]])
    tree = bookmark_tree(pdf)
    a, b = tree["structure"]
    assert (a["title"], a["start_index"], a["end_index"]) == ("A", 1, 4)
    assert [(n["title"], n["start_index"], n["end_index"]) for n in a["nodes"]] == [("A1", 2, 2), ("A2", 3, 4)]
    assert (b["start_index"], b["end_index"]) == (5, 6)
    assert [n["node_id"] for n in (a, *a["nodes"], b)] == ["0000", "0001", "0002", "0003"]


def test_bookmark_tree_empty_when_no_outline(tmp_path):
    assert bookmark_tree(_pdf(tmp_path / "n.pdf", 3))["structure"] == []


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
