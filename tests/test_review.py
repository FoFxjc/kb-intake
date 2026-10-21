"""Review (kb_intake.review, intake-design-v2 §2 step 7, §4): reclassify and exclude regions of an extracted job,
all or nothing per call, re-extracting with the workflow handler of the new type; and the page view for the UI.

Jobs come from the real label and extract steps with the fake Docling engine (tests/jobflow.py)."""

import pytest
from jobflow import P106_TABLE, TABLE_BOX, build_pdf, config, engine, in_review, rid

from kb_intake import contract, label, review
from kb_intake.jobs import Jobs, WrongState
from kb_intake.package import read_jsonl


def files(job):
    d = job.dir
    return {"regions": read_jsonl(d / "extract" / "regions.jsonl"),
            "tables": read_jsonl(d / "extract" / "tables.jsonl"),
            "extract_gaps": read_jsonl(d / "extract" / "gaps.jsonl"),
            "review_gaps": read_jsonl(d / "review" / "gaps.jsonl") if (d / "review" / "gaps.jsonl").is_file() else []}


def region_of(job, region_id):
    return next(r for r in files(job)["regions"] if r["region_id"] == region_id)


def assert_valid(job):
    f = files(job)
    for kind, rows in (("region", f["regions"]), ("table", f["tables"]), ("gap", f["extract_gaps"]),
                       ("gap", f["review_gaps"])):
        for r in rows:
            assert contract.schema_errors(kind, r, kind) == []


# ---------- reclassify ----------

def test_reclassify_text_to_table_uses_find_tables_and_is_recorded(tmp_path):
    f = in_review(tmp_path)
    p106 = rid(2, 3)
    assert region_of(f.job, p106)["type"] == "text"
    applied = review.apply(f.jobs, f.job.id, [{"action": "reclassify", "region_id": p106, "to": "table",
                                               "note": "missed table"}], "peter", f.cfg)
    reg = region_of(f.job, p106)
    assert (reg["type"], reg["subtype"], reg["label_source"], reg["score"]) == ("table", None, "user", None)
    assert reg["status"] == "extracted" and reg["handler"] == "table_structure"
    tables = files(f.job)["tables"]
    assert [t["table_id"] for t in tables] == ["demo-ar-2025-en:t0001_01", "demo-ar-2025-en:t0002_01"]
    t = tables[1]
    assert t["region_id"] == p106 and reg["table_id"] == t["table_id"] and t["extract_method"] == "find_tables"
    assert t["bbox"] == list(P106_TABLE)
    assert [c["text"] for c in t["cells"]] == ["Overdue", "2025", "90 days", "5,678"]
    [op] = f.jobs.ops(f.job.id)
    assert op == applied[0]
    assert {k: op[k] for k in ("action", "region_id", "from", "to", "by", "note")} == {
        "action": "reclassify", "region_id": p106, "from": "text", "to": "table", "by": "peter",
        "note": "missed table"}
    assert op["at"] and contract.schema_errors("review", op, "op") == []
    assert_valid(f.job)


def test_reclassify_to_table_reuses_the_docling_grid_of_a_label_table(tmp_path):
    f = in_review(tmp_path)
    table = rid(1, 3)
    review.apply(f.jobs, f.job.id, [{"action": "reclassify", "region_id": table, "to": "text"}], "peter", f.cfg)
    reg = region_of(f.job, table)
    assert reg["type"] == "text" and reg["table_id"] is None and reg["text"] == "Item\n2025\nLoans\n1,234"
    assert files(f.job)["tables"] == []
    review.apply(f.jobs, f.job.id, [{"action": "reclassify", "region_id": table, "to": "table"}], "peter", f.cfg)
    [t] = files(f.job)["tables"]
    assert t["extract_method"] == "docling" and t["bbox"] == list(TABLE_BOX) and t["cells"][0]["is_header"]
    assert region_of(f.job, table)["table_id"] == t["table_id"] == "demo-ar-2025-en:t0001_01"
    assert [(o["from"], o["to"]) for o in f.jobs.ops(f.job.id)] == [("table", "text"), ("text", "table")]


def test_reclassify_to_table_where_find_tables_finds_nothing_is_unhandled(tmp_path):
    f = in_review(tmp_path)
    body = rid(2, 2)                                  # plain body text, no ruling
    review.apply(f.jobs, f.job.id, [{"action": "reclassify", "region_id": body, "to": "table"}], "peter", f.cfg)
    reg = region_of(f.job, body)
    assert reg["status"] == "unhandled" and reg["table_id"] is None and reg["text"] is None
    [gap] = [g for g in files(f.job)["review_gaps"] if g["region_id"] == body]
    assert gap["code"] == "unhandled" and gap["detail"] == "find_tables found no table in the region"
    assert_valid(f.job)


def test_reclassify_uses_the_handler_of_the_new_type(tmp_path):
    f = in_review(tmp_path)
    review.apply(f.jobs, f.job.id, [{"action": "reclassify", "region_id": rid(1, 2), "to": "picture"},
                                    {"action": "reclassify", "region_id": rid(1, 1), "to": "signature"}],
                 "peter", f.cfg)
    assert (region_of(f.job, rid(1, 2))["status"], region_of(f.job, rid(1, 2))["text"]) == ("discarded", None)
    assert region_of(f.job, rid(1, 1))["status"] == "recorded" and region_of(f.job, rid(1, 1))["handler"] == "record"


def test_reextraction_replaces_the_extract_gaps_of_the_region(tmp_path):
    f = in_review(tmp_path)
    body = rid(2, 2)
    review.apply(f.jobs, f.job.id, [{"action": "reclassify", "region_id": body, "to": "unknown"}], "peter", f.cfg)
    assert [g["code"] for g in files(f.job)["review_gaps"] if g["region_id"] == body] == ["unhandled"]
    review.apply(f.jobs, f.job.id, [{"action": "reclassify", "region_id": body, "to": "text"}], "peter", f.cfg)
    assert [g for g in files(f.job)["review_gaps"] if g["region_id"] == body] == []
    assert region_of(f.job, body)["status"] == "extracted"


def test_label_gaps_stay(tmp_path):
    f = in_review(tmp_path)
    before = read_jsonl(f.job.dir / "label" / "gaps.jsonl")
    review.apply(f.jobs, f.job.id, [{"action": "exclude", "region_id": rid(2, 3)}], "peter", f.cfg)
    assert read_jsonl(f.job.dir / "label" / "gaps.jsonl") == before
    assert any(g["code"] == "table_suspected" for g in before)


# ---------- exclude ----------

def test_exclude_removes_content_and_table(tmp_path):
    f = in_review(tmp_path)
    table = rid(1, 3)
    review.apply(f.jobs, f.job.id, [{"action": "exclude", "region_id": table, "note": "not needed"}], "peter", f.cfg)
    reg = region_of(f.job, table)
    assert reg["status"] == "excluded" and reg["text"] is None and reg["text_source"] is None
    assert reg["table_id"] is None and reg["type"] == "table"
    assert files(f.job)["tables"] == []
    [op] = f.jobs.ops(f.job.id)
    assert (op["action"], op["from"], op["to"], op["note"]) == ("exclude", "extracted", "excluded", "not needed")
    assert_valid(f.job)


# ---------- refusals ----------

BAD = {
    "unknown region": {"action": "reclassify", "region_id": "demo-ar-2025-en:p0009:r001", "to": "table"},
    "unknown type": {"action": "reclassify", "region_id": rid(1, 2), "to": "spreadsheet"},
    "same type": {"action": "reclassify", "region_id": rid(1, 2), "to": "text"},
    "unknown action": {"action": "edit_text", "region_id": rid(1, 2)},
    "unknown key": {"action": "exclude", "region_id": rid(1, 2), "text": "new text"},
    "missing to": {"action": "reclassify", "region_id": rid(1, 2)},
    "not a mapping": "exclude",
}


@pytest.mark.parametrize("bad", list(BAD))
def test_ops_are_all_or_nothing(tmp_path, bad):
    f = in_review(tmp_path)
    before = files(f.job)
    with pytest.raises(review.ReviewError):
        review.apply(f.jobs, f.job.id, [{"action": "reclassify", "region_id": rid(2, 3), "to": "table"},
                                        BAD[bad]], "peter", f.cfg)
    assert files(f.job) == before and f.jobs.ops(f.job.id) == []


def test_the_same_region_twice_in_one_call_is_refused(tmp_path):
    f = in_review(tmp_path)
    with pytest.raises(review.ReviewError, match="more than once"):
        review.apply(f.jobs, f.job.id, [{"action": "reclassify", "region_id": rid(2, 3), "to": "table"},
                                        {"action": "reclassify", "region_id": rid(2, 3), "to": "list"}],
                     "peter", f.cfg)
    assert f.jobs.ops(f.job.id) == []


def test_an_excluded_region_cannot_be_excluded_or_reclassified(tmp_path):
    f = in_review(tmp_path)
    review.apply(f.jobs, f.job.id, [{"action": "exclude", "region_id": rid(2, 3)}], "peter", f.cfg)
    before = files(f.job)
    for op in ({"action": "exclude", "region_id": rid(2, 3)},
               {"action": "reclassify", "region_id": rid(2, 3), "to": "table"}):
        with pytest.raises(review.ReviewError, match="excluded"):
            review.apply(f.jobs, f.job.id, [op], "peter", f.cfg)
    assert files(f.job) == before and len(f.jobs.ops(f.job.id)) == 1


def test_a_region_on_an_excluded_page_cannot_be_reclassified(tmp_path):
    from kb_intake import workflow
    wf = workflow.load_template()
    wf["exclude_pages"] = [2]
    f = in_review(tmp_path, workflow_text=workflow.dump(wf))
    assert region_of(f.job, rid(2, 3))["status"] == "excluded"
    with pytest.raises(review.ReviewError, match="excluded"):
        review.apply(f.jobs, f.job.id, [{"action": "reclassify", "region_id": rid(2, 3), "to": "table"}],
                     "peter", f.cfg)


def test_review_needs_a_name_ops_and_a_job_in_review(tmp_path):
    f = in_review(tmp_path)
    op = {"action": "exclude", "region_id": rid(1, 2)}
    with pytest.raises(review.ReviewError):
        review.apply(f.jobs, f.job.id, [op], "  ", f.cfg)
    with pytest.raises(review.ReviewError):
        review.apply(f.jobs, f.job.id, [], "peter", f.cfg)
    f.jobs.confirm(f.job.id, "peter")
    with pytest.raises(WrongState):
        review.apply(f.jobs, f.job.id, [op], "peter", f.cfg)
    assert f.jobs.ops(f.job.id) == []


# ---------- page view ----------

def test_page_view_before_and_after_extraction(tmp_path):
    cfg = config(tmp_path)
    jobs = Jobs(cfg.work_dir)
    job, _ = jobs.create(build_pdf(tmp_path / "demo.pdf"), {"doc_id": "demo-ar-2025-en", "lang": "en"})
    label.run(jobs, job.id, cfg, engine=engine(tmp_path, "label"))
    view = review.page_view(jobs, job.id, 2)
    assert (view["page_no"], view["width"], view["height"], view["kind"], view["excluded"]) == (2, 595.0, 842.0,
                                                                                               "native", False)
    assert "page_label" in view
    assert [r["region_id"] for r in view["regions"]] == [rid(2, 1), rid(2, 2), rid(2, 3)]
    assert "status" not in view["regions"][0] and view["tables"] == []
    assert [g["code"] for g in view["gaps"]] == ["table_suspected"]

    jobs.approve(job.id, "peter")
    from kb_intake import extract
    extract.run(jobs, job.id, cfg, engine=engine(tmp_path, "extract"))
    view = review.page_view(jobs, job.id, 1)
    assert [r["status"] for r in view["regions"]] == ["extracted"] * 3
    assert [t["table_id"] for t in view["tables"]] == ["demo-ar-2025-en:t0001_01"]
    assert view["gaps"] == []
    review.apply(jobs, job.id, [{"action": "reclassify", "region_id": rid(2, 2), "to": "table"}], "peter", cfg)
    view = review.page_view(jobs, job.id, 2)
    assert [g["code"] for g in view["gaps"]] == ["table_suspected", "unhandled"]
    with pytest.raises(KeyError):
        review.page_view(jobs, job.id, 3)
