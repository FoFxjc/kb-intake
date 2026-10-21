"""Search, filters and facets over a job's regions (intake-ui-llm-design-v1 §3.2), on jobs written by the real steps.

The source (tests/jobflow.py): page 1 has a heading, body text and a table whose cells hold Item / 2025 / Loans /
1,234; page 2 has a heading, body text, and two text regions over a table Docling missed (Overdue / 2025, 90 days /
5,678), the first with a `table_suspected` gap.
"""

import hashlib

import jobflow
import pytest
from jobflow import rid

from kb_intake import label, review, search
from kb_intake.jobs import Jobs
from kb_intake.search import Filters


@pytest.fixture
def flow(tmp_path):
    return jobflow.in_review(tmp_path, p106_split=True)


def find(flow, **kw):
    cursor, limit = kw.pop("cursor", None), kw.pop("limit", 100)
    return search.find(flow.jobs, flow.job.id, Filters(**kw), cursor=cursor, limit=limit)


def ids(result) -> list[str]:
    return [i["region_id"] for i in result["items"]]


def digest(d) -> dict:
    return {p.relative_to(d).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(d.rglob("*")) if p.is_file() and "search" not in p.parts}


def test_label_rows_have_text_by_box(tmp_path):
    cfg = jobflow.config(tmp_path)
    jobs = Jobs(cfg.work_dir)
    jobflow.build_pdf(tmp_path / "demo.pdf")
    job, _ = jobs.create(tmp_path / "demo.pdf", dict(jobflow.DECL))
    label.run(jobs, job.id, cfg, engine=jobflow.engine(tmp_path, "label", p106_split=True))
    before = digest(job.dir)
    rows = search.load_rows(jobs, job.id)
    assert rows.stage == "label"
    text = {r["region_id"]: r["text"] for r in rows.regions}
    assert text[rid(1, 1)] == "Section 1" and "Loans" in text[rid(1, 3)] and "Overdue" in text[rid(2, 3)]
    assert all(r["status"] is None and r["handler"] is None for r in rows.regions)
    assert digest(job.dir) == before                         # work files untouched (the cache lives in search/)
    hit = find(SimpleFlow(jobs, job), q="overdue")
    assert ids(hit) == [rid(2, 3)] and hit["items"][0]["gaps"] == ["table_suspected"]


class SimpleFlow:
    def __init__(self, jobs, job):
        self.jobs, self.job = jobs, job


def test_search_finds_region_and_cell_text(flow):
    cell = find(flow, q="loans")["items"]
    assert [i["region_id"] for i in cell] == [rid(1, 3)]
    m = cell[0]["match"]
    assert m["source"] == "cell" and m["table_id"].endswith(":t0001_01") and (m["r"], m["c"]) == (1, 0)
    assert len(m["bbox"]) == 4 and "Loans" in m["snippet"]
    body = find(flow, q="pleased")["items"]
    assert [i["region_id"] for i in body] == [rid(1, 2), rid(2, 2)]
    assert body[0]["match"]["source"] == "region" and body[0]["match"]["bbox"] == body[0]["bbox"]


def test_results_in_reading_order_and_paged(flow):
    whole = find(flow)
    assert whole["total"] == 7 and whole["next_cursor"] is None
    orders = [i["reading_order"] for i in whole["items"]]
    assert orders == sorted(orders)
    got, cursor = [], None
    while True:
        page = find(flow, cursor=cursor, limit=2)
        assert page["total"] == 7
        got += ids(page)
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert got == ids(whole)


def test_filters_combine(flow):
    assert ids(find(flow, q="2025", types=["table"])) == [rid(1, 3)]
    assert ids(find(flow, q="2025", types=["text"])) == [rid(2, 3)]
    assert ids(find(flow, page=2, gaps=["table_suspected"])) == [rid(2, 3)]
    assert ids(find(flow, min_score=0.92)) == [rid(1, 3)]
    f = search.facets(flow.jobs, flow.job.id, Filters(q="2025", types=["table"]))
    assert f["total"] == 1
    assert f["type"] == {"table": 1, "text": 1}               # the type facet ignores the type filter
    assert f["status"] == {"extracted": 1}                    # other facets apply it
    assert f["gap"] == {}


def test_search_treats_query_syntax_as_text(flow):
    loans = [rid(1, 3)]
    for q in ['"loans', 'loans*', '-loans', '(loans', 'loans)', '^loans', 'loans:', "'loans'"]:
        assert ids(find(flow, q=q)) == loans, q                 # operators are part of the word, not syntax
    assert ids(find(flow, q="loans AND")) == []                 # AND is a word every hit must contain
    assert ids(find(flow, q="loans OR overdue")) == []
    for q in ['"', '*', '-', '(', "'", '__', '\u6570\u5b57']:
        assert find(flow, q=q)["items"] == [], q                # nothing searchable, or not in the document
    assert search.fts_query("__ loans") == '"loans"*'           # a word the tokenizer drops is not searched


def test_index_follows_work_files_changed_elsewhere(flow):
    assert ids(find(flow, q="overdue", types=["table"])) == []
    # a review applied outside the server (the CLI): only the work files change
    review.apply(flow.jobs, flow.job.id, [{"action": "reclassify", "region_id": rid(2, 3), "to": "table"}], "cli",
                 flow.cfg)
    hit = find(flow, q="overdue", types=["table"])["items"]
    assert [i["region_id"] for i in hit] == [rid(2, 3)] and hit[0]["match"]["source"] == "cell"


def test_bad_filters_raise_value_error(flow):
    for kw in ({"types": ["nope"]}, {"statuses": ["nope"]}, {"gaps": ["nope"]}):
        with pytest.raises(ValueError):
            find(flow, **kw)


def test_rows_are_reused_until_the_work_files_change(flow):
    a = search.load_rows(flow.jobs, flow.job.id)
    assert search.load_rows(flow.jobs, flow.job.id) is a
    review.apply(flow.jobs, flow.job.id, [{"action": "exclude", "region_id": rid(1, 2)}], "cli", flow.cfg)
    b = search.load_rows(flow.jobs, flow.job.id)
    assert b is not a and next(r for r in b.regions if r["region_id"] == rid(1, 2))["status"] == "excluded"
