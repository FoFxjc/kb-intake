"""Turning Docling output into plain dicts (kb_intake.engines.docling), on fake documents shaped like Docling 2.140."""

import json

from fakes import document

from kb_intake.config import DoclingParams
from kb_intake.engines.docling import extract_tables, label_items


def test_label_items_join_cluster_confidence_and_drop_table_cell_texts():
    doc, clusters = document(pages=2, pictures=[(2, (100, 400, 300, 600), [["bar_chart", 0.97], ["other", 0.01]])])
    out = label_items(json.dumps(doc).encode(), clusters)
    labels = [(i["page_no"], i["label"]) for i in out["items"]]
    assert labels == [(1, "section_header"), (1, "text"), (1, "table"), (2, "section_header"), (2, "text"),
                      (2, "picture")]
    assert out["dropped_table_cells"] == 1 and out["unscored"] == 0
    assert all(i["score"] is not None for i in out["items"])
    pic = out["items"][-1]
    assert pic["classes"][0] == ["bar_chart", 0.97] and pic["bbox"] == [100, 400, 300, 600]   # TOPLEFT again
    assert out["items"][0]["classes"] is None and out["page_sizes"][1] == [595.0, 842.0]


def test_an_item_without_cluster_outside_tables_is_kept_unscored():
    doc, clusters = document(pages=1)
    clusters["1"] = [c for c in clusters["1"] if c[4] != "text"]        # the body text's cluster is missing
    out = label_items(json.dumps(doc).encode(), clusters)
    text = next(i for i in out["items"] if i["label"] == "text")
    assert text["score"] is None and out["unscored"] == 1


def test_extract_tables_keep_structure_not_docling_text():
    doc, _ = document(pages=1, stage="extract", grid=(2, 3))
    [t] = extract_tables(json.dumps(doc).encode())
    assert (t["page_no"], t["n_rows"], t["n_cols"], len(t["cells"])) == (1, 2, 3, 6)
    assert "text" not in t["cells"][0] and t["cells"][0]["is_header"] and not t["cells"][3]["is_header"]
    assert t["bbox"] == [72, 200, 400, 300]


def test_stage_params_differ_and_shape_the_cache_key():
    p = DoclingParams()
    label, extract = p.engine_params("label"), p.engine_params("extract")
    assert label["do_picture_classification"] and not label["do_table_structure"]
    assert extract["do_table_structure"] and not extract["do_picture_classification"]
    assert extract["table_mode"] == "accurate" and extract["do_cell_matching"]
