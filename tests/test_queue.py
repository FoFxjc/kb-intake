"""The review queue (intake-ui-llm-design-v1 §3.1 `queue`): gaps, weak continuation hints and LLM checks, grouped by
problem, each open / seen / resolved."""

import jobflow
import pytest
from jobflow import rid

from kb_intake import label, queue, review
from kb_intake.jobs import Jobs
from kb_intake.package import read_jsonl, write_jsonl


@pytest.fixture
def flow(tmp_path):
    return jobflow.in_review(tmp_path, p106_split=True)


def hint_on_p1_table(flow, reason="same_first_row"):
    """Give page 1's table a continuation hint (the fake source has no table pair that raises one by itself)."""
    p = flow.job.dir / "extract" / "tables.jsonl"
    tables = read_jsonl(p)
    tables[0]["continued_hint"] = {"table_id": "demo-ar-2025-en:t0000_01", "reason": reason}
    write_jsonl(p, tables)
    return tables[0]


def by_id(flow) -> dict[str, dict]:
    return {i["item_id"]: i for i in queue.items(flow.jobs, flow.job.id)}


SUSPECTED = f"gap:table_suspected:2:{rid(2, 3)}"


def test_queue_groups_gaps_and_weak_hints(flow):
    t = hint_on_p1_table(flow)
    items = by_id(flow)
    assert set(items) == {SUSPECTED, f"hint:{t['region_id']}"}
    gap = items[SUSPECTED]
    assert gap["group"] == "table_suspected" and gap["kind"] == "gap" and gap["status"] == "open"
    assert gap["bbox"] == [72.0, 400.0, 400.0, 500.0]           # the find_tables box the gap carries
    assert gap["target"] == {"kind": "gap", "code": "table_suspected", "page_no": 2, "region_id": rid(2, 3),
                             "table_id": None}
    hint = items[f"hint:{t['region_id']}"]
    assert hint["group"] == "continued_hint" and hint["table_id"] == t["table_id"] and hint["bbox"] == t["bbox"]
    view = queue.view(list(items.values()))
    assert [g["key"] for g in view["groups"]] == ["table_suspected", "continued_hint"]
    assert view["total"] == 2 and view["done"] == 0
    assert view["groups"][0]["label"] == "Suspected missed tables" and view["groups"][0]["open"] == 1


def test_strong_hints_are_not_queued(flow):
    hint_on_p1_table(flow, reason="continued_heading")
    assert set(by_id(flow)) == {SUSPECTED}


def test_item_ids_do_not_depend_on_table_ids():
    a = {"kind": "hint", "code": "same_first_row", "page_no": 3, "region_id": "d:p0003:r002", "table_id": "d:t0003_01"}
    assert queue.item_id(a) == queue.item_id({**a, "table_id": "d:t0003_02"}) == "hint:d:p0003:r002"
    page_gap = {"kind": "gap", "code": "no_text_layer", "page_no": 7, "region_id": None, "table_id": None}
    assert queue.item_id(page_gap) == "gap:no_text_layer:7:-"


def test_seen_is_work_state_only(flow):
    assert flow.jobs.mark_seen(flow.job.id, [SUSPECTED]) == 1
    assert flow.jobs.mark_seen(flow.job.id, [SUSPECTED]) == 0
    assert by_id(flow)[SUSPECTED]["status"] == "seen"
    assert flow.jobs.seen(flow.job.id) == {SUSPECTED}
    assert not any("seen" in p.name for p in flow.job.dir.rglob("*"))


def test_resolved_after_reclassify(flow):
    review.apply(flow.jobs, flow.job.id, [{"action": "reclassify", "region_id": rid(2, 3), "to": "table"}], "peter",
                 flow.cfg)
    item = by_id(flow)[SUSPECTED]
    assert item["status"] == "resolved" and item["resolution"] == {"action": "reclassify", "review_seq": 1}
    assert queue.view([item])["done"] == 1


def test_queue_before_extract_is_empty_and_before_label_raises(tmp_path):
    cfg = jobflow.config(tmp_path)
    jobs = Jobs(cfg.work_dir)
    jobflow.build_pdf(tmp_path / "demo.pdf")
    job, _ = jobs.create(tmp_path / "demo.pdf", dict(jobflow.DECL))
    with pytest.raises(KeyError):
        queue.items(jobs, job.id)
    label.run(jobs, job.id, cfg, engine=jobflow.engine(tmp_path, "label", p106_split=True))
    assert queue.items(jobs, job.id) == []
    assert queue.view([]) == {"total": 0, "done": 0, "groups": []}
