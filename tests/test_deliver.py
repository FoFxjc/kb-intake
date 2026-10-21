"""Confirmation and delivery (kb_intake.deliver, intake-design-v2 §2 steps 8-9, §5): the package is built in a
`.tmp-*` directory of the delivery folder, checked against contract v2, and renamed into place; packages of the same
source, workflow and review ops are identical apart from the exempt fields (tests/regression/same_packages.py)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from jobflow import DOC, in_review, rid

from kb_intake import contract, deliver, review, workflow
from kb_intake.jobs import Busy, WrongState
from kb_intake.package import read_json, read_jsonl, write_jsonl

sys.path.insert(0, str(Path(__file__).parent / "regression"))
import same_packages  # noqa: E402

OPS = [{"action": "reclassify", "region_id": rid(2, 3), "to": "table", "note": "p106"},
       {"action": "exclude", "region_id": rid(2, 2)}]


def reviewed(tmp_path, **kw):
    f = in_review(tmp_path, **kw)
    review.apply(f.jobs, f.job.id, OPS, "peter", f.cfg)
    return f


def test_delivery_is_atomic_and_checked(tmp_path, monkeypatch):
    f = reviewed(tmp_path)
    final = f.cfg.delivery_dir / f"{DOC}@{f.job.sha256[:12]}"
    real_check, seen = contract.check, []

    def check(pkg):
        assert not final.exists()
        assert pkg.name == final.name and pkg.parent.name.startswith(f".tmp-{final.name}-")
        seen.append(pkg)
        return real_check(pkg)
    monkeypatch.setattr(contract, "check", check)
    path = deliver.confirm(f.jobs, f.job.id, "anna", f.cfg)
    assert path == final and seen
    assert real_check(final)["ok"], real_check(final)["errors"]
    job = f.jobs.get(f.job.id)
    assert job.status == "delivered" and job.package == final.name and job.confirmed_by == "anna"
    assert sorted(p.name for p in f.cfg.delivery_dir.iterdir()) == [final.name]
    steps = [(e["step"], e["status"]) for e in f.jobs.events(f.job.id) if e["step"] == "deliver"]
    assert steps == [("deliver", "started"), ("deliver", "done")]


def test_package_contents(tmp_path):
    wf = workflow.load_template()
    wf["exclude_pages"] = [1]
    f = in_review(tmp_path, workflow_text=workflow.dump(wf))
    review.apply(f.jobs, f.job.id, [OPS[0]], "peter", f.cfg)
    pkg = deliver.confirm(f.jobs, f.job.id, "anna", f.cfg)
    assert sorted(p.relative_to(pkg).as_posix() for p in pkg.rglob("*") if p.is_file()) == [
        "gaps.jsonl", "layout.jsonl", "manifest.json", "pages.jsonl", "raw/docling.extract.json", "raw/docling.json",
        "regions.jsonl", "review.jsonl", "source.pdf", "tables.jsonl", "workflow.json"]
    assert (pkg / "source.pdf").read_bytes() == f.src.read_bytes()
    assert [p["excluded"] for p in read_jsonl(pkg / "pages.jsonl")] == [True, False]
    work = f.job.dir
    assert read_jsonl(pkg / "regions.jsonl") == read_jsonl(work / "extract" / "regions.jsonl")
    assert read_jsonl(pkg / "layout.jsonl") == read_jsonl(work / "label" / "layout.jsonl")
    gaps = read_jsonl(pkg / "gaps.jsonl")
    assert sorted(g["code"] for g in gaps) == ["table_suspected"]
    [t] = read_jsonl(pkg / "tables.jsonl")
    assert t["extract_method"] == "find_tables" and t["page_no"] == 2
    ops = read_jsonl(pkg / "review.jsonl")
    assert [o["action"] for o in ops] == ["reclassify", "confirm"]
    job = f.jobs.get(f.job.id)
    assert ops[1] == {"action": "confirm", "region_id": None, "from": None, "to": None, "by": "anna",
                      "at": job.confirmed_at, "note": None}
    w = read_json(pkg / "workflow.json")
    assert w == {"workflow": wf, "approved_by": "peter", "approved_at": job.approved_at}
    m = read_json(pkg / "manifest.json")
    assert m["contract_version"] == "2" and m["package"] == pkg.name and m["doc_id"] == DOC
    assert m["source"] == {"filename": "demo.pdf", "sha256": job.sha256, "bytes": f.src.stat().st_size,
                           "media_type": "application/pdf"}
    assert m["metadata"]["page_count"] == 2 and m["metadata"]["lang"] == "en"
    assert m["metadata"]["page_kinds"]["native"] == 2 and m["metadata"]["has_text_layer"] is True
    assert m["stats"]["labeled_by_type"] == {"heading": 2, "table": 1, "text": 3}
    assert m["stats"]["regions_by_type"] == {"heading": 2, "table": 2, "text": 2}
    assert m["stats"]["regions_by_status"] == {"excluded": 3, "extracted": 3} and m["stats"]["tables"] == 1
    assert m["workflow"] == {"name": "default", "version": 1, "approved_by": "peter",
                             "approved_at": job.approved_at}
    assert m["status"] == {"state": "confirmed", "confirmed_by": "anna", "confirmed_at": job.confirmed_at}
    assert set(m["engines"]["params"]) == {"label", "extract"}
    assert m["engines"]["stages"]["label"]["status"] == "success"
    assert m["files"]["regions.jsonl"]["rows"] == 6 and m["files"]["source.pdf"]["rows"] is None
    assert "manifest.json" not in m["files"]
    assert m["run"]["job_id"] == job.id and "deliver_seconds" in m["run"]
    assert contract.check(pkg)["ok"]


def test_existing_package_is_refused_and_stale_tmp_is_cleaned(tmp_path):
    f = reviewed(tmp_path)
    name = f"{DOC}@{f.job.sha256[:12]}"
    stale = f.cfg.delivery_dir / f".tmp-{name}-0123abcd"
    (stale / name).mkdir(parents=True)
    (stale / name / "regions.jsonl").write_text("partial\n")
    other = f.cfg.delivery_dir / f".tmp-other@000000000000-0123abcd"
    other.mkdir()
    f.jobs.confirm(f.job.id, "anna")
    deliver.deliver(f.jobs, f.job.id, f.cfg)
    assert not stale.exists() and other.exists()

    g = in_review(tmp_path, work="work2", delivery="delivery")          # same source, same delivery folder
    review.apply(g.jobs, g.job.id, OPS, "peter", g.cfg)
    with pytest.raises(deliver.DeliveryError, match="already exists"):
        deliver.confirm(g.jobs, g.job.id, "anna", g.cfg)
    job = g.jobs.get(g.job.id)
    assert job.status == "confirmed" and job.running_op is None and "already exists" in job.last_error
    assert [e["status"] for e in g.jobs.events(g.job.id) if e["step"] == "deliver"] == ["started", "failed"]
    assert sorted(p.name for p in g.cfg.delivery_dir.iterdir()) == [other.name, name]


def test_a_failed_check_leaves_no_package_and_the_retry_delivers(tmp_path, monkeypatch):
    f = reviewed(tmp_path)
    real_check = contract.check
    monkeypatch.setattr(contract, "check", lambda pkg: {"ok": False, "errors": ["broken"], "counts": {}})
    with pytest.raises(deliver.DeliveryError, match="broken"):
        deliver.confirm(f.jobs, f.job.id, "anna", f.cfg)
    assert list(f.cfg.delivery_dir.iterdir()) == []
    assert f.jobs.get(f.job.id).status == "confirmed"
    monkeypatch.setattr(contract, "check", real_check)
    path = deliver.confirm(f.jobs, f.job.id, "someone else", f.cfg)       # retry: only delivers
    job = f.jobs.get(f.job.id)
    assert job.status == "delivered" and job.confirmed_by == "anna"
    assert read_json(path / "manifest.json")["status"]["confirmed_by"] == "anna"


def test_a_package_renamed_into_place_before_a_crash_completes_the_retry(tmp_path, monkeypatch):
    f = reviewed(tmp_path)
    f.jobs.confirm(f.job.id, "anna")
    real_advance = f.jobs.advance

    def crash(*a, **kw):
        raise OSError("power cut")
    monkeypatch.setattr(f.jobs, "advance", crash)
    with pytest.raises(OSError, match="power cut"):
        deliver.deliver(f.jobs, f.job.id, f.cfg)
    final = f.cfg.delivery_dir / f"{DOC}@{f.job.sha256[:12]}"
    assert final.is_dir() and f.jobs.get(f.job.id).status == "confirmed"
    monkeypatch.setattr(f.jobs, "advance", real_advance)
    assert deliver.deliver(f.jobs, f.job.id, f.cfg) == final
    assert f.jobs.get(f.job.id).status == "delivered"


def test_a_stale_tmp_is_cleaned_when_the_package_is_already_in_place(tmp_path, monkeypatch):
    f = reviewed(tmp_path)
    f.jobs.confirm(f.job.id, "anna")
    real_advance = f.jobs.advance

    def crash(*a, **kw):
        raise OSError("power cut")
    monkeypatch.setattr(f.jobs, "advance", crash)
    with pytest.raises(OSError, match="power cut"):
        deliver.deliver(f.jobs, f.job.id, f.cfg)
    name = f"{DOC}@{f.job.sha256[:12]}"
    stale = f.cfg.delivery_dir / f".tmp-{name}-deadbeef"         # left by a kill before the tmp was removed
    stale.mkdir()
    monkeypatch.setattr(f.jobs, "advance", real_advance)
    deliver.deliver(f.jobs, f.job.id, f.cfg)
    assert sorted(p.name for p in f.cfg.delivery_dir.iterdir()) == [name]

    g = in_review(tmp_path, work="work2", delivery="delivery")          # refused: the stale tmp still goes
    review.apply(g.jobs, g.job.id, OPS, "peter", g.cfg)
    stale.mkdir()
    with pytest.raises(deliver.DeliveryError, match="already exists"):
        deliver.confirm(g.jobs, g.job.id, "anna", g.cfg)
    assert not stale.exists()


def test_confirm_needs_a_job_in_review(tmp_path):
    f = in_review(tmp_path)
    f.jobs.confirm(f.job.id, "anna")
    deliver.confirm(f.jobs, f.job.id, "anna", f.cfg)
    with pytest.raises(WrongState):
        deliver.confirm(f.jobs, f.job.id, "anna", f.cfg)
    with pytest.raises(WrongState):
        deliver.deliver(f.jobs, f.job.id, f.cfg)


def test_two_deliveries_differ_only_in_exempt_fields(tmp_path):
    a = reviewed(tmp_path, work="wa", delivery="da")
    b = reviewed(tmp_path, work="wb", delivery="db")
    pa = deliver.confirm(a.jobs, a.job.id, "anna", a.cfg)
    pb = deliver.confirm(b.jobs, b.job.id, "anna", b.cfg)
    assert a.job.id != b.job.id
    assert read_json(pa / "manifest.json")["run"] != read_json(pb / "manifest.json")["run"]
    assert same_packages.differences(pa, pb) == []
    for f in ("regions.jsonl", "tables.jsonl", "gaps.jsonl", "pages.jsonl", "layout.jsonl", "source.pdf"):
        assert (pa / f).read_bytes() == (pb / f).read_bytes(), f


def test_same_packages_reports_a_difference_outside_the_exempt_fields(tmp_path):
    a = reviewed(tmp_path, work="wa", delivery="da")
    pa = deliver.confirm(a.jobs, a.job.id, "anna", a.cfg)
    import shutil
    pb = tmp_path / "copy" / pa.name
    shutil.copytree(pa, pb)
    assert same_packages.differences(pa, pb) == []
    ops = read_jsonl(pb / "review.jsonl")
    ops[0]["note"] = "changed"
    (pb / "review.jsonl").write_text("".join(json.dumps(o) + "\n" for o in ops))
    m = read_json(pb / "manifest.json")
    m["status"]["confirmed_by"] = "mallory"
    (pb / "manifest.json").write_text(json.dumps(m))
    (pb / "raw" / "docling.json").write_bytes(b"{}")
    diff = same_packages.differences(pa, pb)
    assert any("review.jsonl" in d for d in diff) and any("status" in d for d in diff)
    assert any("raw/docling.json" in d for d in diff)


def test_a_second_delivery_of_a_running_job_is_busy(tmp_path):
    f = reviewed(tmp_path)
    f.jobs.confirm(f.job.id, "anna")
    with f.jobs.running(f.job.id, "deliver"):
        with pytest.raises(Busy):
            deliver.deliver(f.jobs, f.job.id, f.cfg)
        with pytest.raises(Busy):
            deliver.confirm(f.jobs, f.job.id, "anna", f.cfg)
    assert not f.cfg.delivery_dir.exists() or list(f.cfg.delivery_dir.iterdir()) == []


def test_compare_snapshot_reads_v2_packages(tmp_path):
    f = reviewed(tmp_path)
    pkg = deliver.confirm(f.jobs, f.job.id, "anna", f.cfg)
    snap = tmp_path / "snapshot"
    (snap / "tables").mkdir(parents=True)
    (snap / "tables" / "t01_p1.csv").write_text('Item,2025\nLoans,"1,234"\n')
    (snap / "tables" / "t02_p2.csv").write_text('Overdue,2025\n90 days,"5,678"\n')
    (snap / "tables" / "tables_check.json").write_text(json.dumps([{"idx": 1, "shape": [1, 2], "header_rows": 1},
                                                                   {"idx": 2, "shape": [1, 2], "header_rows": 1}]))
    (snap / "tables_extra").mkdir()
    (snap / "tables_extra" / "tables_check.json").write_text("[]")
    script = Path(__file__).parent / "regression" / "compare_snapshot.py"
    res = subprocess.run([sys.executable, str(script), "--package", str(pkg), "--snapshot", str(snap)],
                         capture_output=True, text=True)
    out = json.loads(res.stdout)
    assert [(t["snapshot"], t["numbers_ok"]) for t in out["tables"]] == [("t01_p1", True), ("t02_p2", True)]
    assert out["tables"][0]["text_diffs"] == [] and out["tables"][0]["header_diffs"] == []
    assert out["tables"][1]["extract_method"] == "find_tables"
    assert res.returncode == 1 and out["p217_continues_p216"] is None       # the demo has no p106 / p216-217


def test_delivery_refuses_work_files_that_do_not_match_the_review_record(tmp_path):
    """The known crash window of review.apply: ops recorded, the work files not replaced."""
    f = reviewed(tmp_path)
    regions = read_jsonl(f.job.dir / "extract" / "regions.jsonl")
    op = f.jobs.ops(f.job.id)[0]
    rows = [{**r, "type": "text", "label_source": "model"} if r["region_id"] == op["region_id"] else r
            for r in regions]
    write_jsonl(f.job.dir / "extract" / "regions.jsonl", rows)      # as if the crash came before this write
    with pytest.raises(deliver.DeliveryError, match="do not match the review record"):
        deliver.confirm(f.jobs, f.job.id, "anna", f.cfg)
    assert f.jobs.get(f.job.id).status == "confirmed" and not list(f.cfg.delivery_dir.glob("*@*"))


def test_check_review_wants_the_last_op_of_each_region():
    regions = [{"region_id": "a", "type": "table", "label_source": "user", "status": "extracted"},
               {"region_id": "b", "type": "text", "label_source": "model", "status": "excluded"}]
    ops = [{"action": "reclassify", "region_id": "a", "to": "text"}, {"action": "reclassify", "region_id": "a",
                                                                       "to": "table"},
           {"action": "exclude", "region_id": "b"}]
    assert deliver.check_review(ops, regions) == []
    assert deliver.check_review(ops[:1], regions) == ["a was reclassified to text, but is table (user)"]


def test_a_delivered_file_cannot_start_a_new_job(tmp_path):
    from kb_intake.jobs import AlreadyDelivered, Jobs
    f = reviewed(tmp_path)
    deliver.confirm(f.jobs, f.job.id, "anna", f.cfg)
    with pytest.raises(AlreadyDelivered):
        f.jobs.create(f.src, dict(f.job.declaration))                     # a delivered job of this sha256
    other = Jobs(tmp_path / "other-work")                                 # another work dir, same delivery folder
    with pytest.raises(AlreadyDelivered):
        other.create(f.src, dict(f.job.declaration), f.cfg.delivery_dir)


def test_delivery_accepts_ack_rows(tmp_path):
    f = in_review(tmp_path, p106_split=True)
    review.apply(f.jobs, f.job.id, [{"action": "ack", "item_id": f"gap:table_suspected:2:{rid(2, 3)}",
                                     "verdict": "ok"}], "peter", f.cfg)
    review.apply(f.jobs, f.job.id, [{"action": "exclude", "region_id": rid(1, 2)}], "peter", f.cfg)
    pkg = deliver.confirm(f.jobs, f.job.id, "anna", f.cfg)
    assert contract.check(pkg)["ok"], contract.check(pkg)["errors"]
    rows = read_jsonl(pkg / "review.jsonl")
    assert [r["action"] for r in rows] == ["ack", "exclude", "confirm"]
    gap = next(g for g in read_jsonl(pkg / "gaps.jsonl") if g["code"] == "table_suspected")
    assert gap["resolved"] == {"action": "ack", "review_seq": 1}


def test_check_review_ignores_ack_for_the_last_op_and_checks_its_gap():
    regions = [{"region_id": "a", "type": "text", "label_source": "user", "status": "extracted"}]
    target = {"kind": "gap", "code": "low_confidence", "page_no": 1, "region_id": "a", "table_id": None}
    ops = [{"action": "reclassify", "region_id": "a", "to": "table"},
           {"action": "ack", "region_id": "a", "target": target}]
    gaps = [{"code": "low_confidence", "page_no": 1, "region_id": "a", "resolved": {"action": "ack", "review_seq": 2}}]
    assert deliver.check_review(ops, regions, gaps) == ["a was reclassified to table, but is text (user)"]
    assert deliver.check_review(ops[1:], regions, gaps) == []
    assert deliver.check_review(ops[1:], regions, [{**gaps[0], "resolved": None}]) == \
        ["ack 1 of gap low_confidence on a: the gap is not resolved"]


def test_ack_of_a_gap_that_a_later_reclassify_removed_does_not_block_delivery(tmp_path):
    """Independent review of stage S: the extract gap an ack answered disappears when its region is reclassified;
    delivery must not refuse the package for it."""
    f = in_review(tmp_path, p106_split=True)
    p = f.job.dir / "extract" / "gaps.jsonl"
    write_jsonl(p, read_jsonl(p) + [{"code": "no_text_layer", "page_no": 1, "region_id": rid(1, 2),
                                     "detail": "no text-layer words in the region"}])
    review.apply(f.jobs, f.job.id, [{"action": "ack", "item_id": f"gap:no_text_layer:1:{rid(1, 2)}"}], "peter", f.cfg)
    review.apply(f.jobs, f.job.id, [{"action": "reclassify", "region_id": rid(1, 2), "to": "picture"}], "peter", f.cfg)
    pkg = deliver.confirm(f.jobs, f.job.id, "anna", f.cfg)
    assert contract.check(pkg)["ok"]
