"""The LLM uses wired into the steps (intake-ui-llm-design-v1 §4): extraction, review, the summary, delivery, the
API. The model is a local OpenAI-compatible server (tests/fake_llm.py), so every call is a real HTTP round trip."""

import json

import jobflow
import pytest
from fake_llm import FakeLlm
from fastapi.testclient import TestClient
from jobflow import rid

from kb_intake import contract, deliver, llm, llm_client, queue, review, search, workflow
from kb_intake.package import read_jsonl
from kb_intake.search import Filters

KEY = "sk-flow-SENTINEL"


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setenv("INTAKE_LLM_API_KEY", KEY)
    monkeypatch.setattr(llm_client, "_sleep", lambda s: None)
    f = FakeLlm()
    yield f
    f.close()


def chart_region(f):
    return next(r for r in review.current(f.job.dir)["regions"] if r["type"] == "chart")


def test_suggested_workflow_describes_charts_only_when_the_llm_is_on(tmp_path, fake, monkeypatch):
    on = jobflow.in_review(tmp_path / "on", chart=True, llm_url=fake.url)
    assert workflow.parse(on.jobs.workflow(on.job.id)["text"])["handlers"]["chart"] == "describe"
    monkeypatch.delenv("INTAKE_LLM_API_KEY")
    off = jobflow.in_review(tmp_path / "off", chart=True, llm_url=fake.url)
    assert workflow.parse(off.jobs.workflow(off.job.id)["text"])["handlers"]["chart"] == "text"


def test_extraction_describes_the_chart(tmp_path, fake):
    f = jobflow.in_review(tmp_path, chart=True, llm_url=fake.url)
    chart = chart_region(f)
    assert chart["handler"] == "describe" and chart["status"] == "extracted"
    rows = read_jsonl(f.job.dir / "llm" / "llm.jsonl")
    assert [(r["kind"], r["region_id"]) for r in rows] == [("describe", chart["region_id"])]
    assert rows[0]["output"]["text"] == "A bar chart of revenue by year, in SGD m."     # reasoning removed
    assert all(contract.schema_errors("llm", r, "r") == [] for r in rows)
    assert len(fake.requests) == 1 and fake.keys == {f"Bearer {KEY}"}
    assert "llm" not in json.dumps(read_jsonl(f.job.dir / "extract" / "regions.jsonl"))       # core rows untouched


def test_chart_table_when_switched_on_in_the_workflow(tmp_path, fake):
    base = jobflow.in_review(tmp_path / "probe", chart=True, llm_url=fake.url)
    wf = workflow.parse(base.jobs.workflow(base.job.id)["text"])
    text = workflow.dump({**wf, "llm": {"chart_table": True}})
    f = jobflow.in_review(tmp_path / "t", chart=True, llm_url=fake.url, workflow_text=text)
    # the configuration keeps chart_table off by default: the workflow switch alone does not turn it on
    assert [r["kind"] for r in read_jsonl(f.job.dir / "llm" / "llm.jsonl")] == ["describe"]


def test_describe_without_llm_is_text(tmp_path, monkeypatch):
    monkeypatch.delenv("INTAKE_LLM_API_KEY", raising=False)
    plain = jobflow.in_review(tmp_path / "plain", chart=True)
    wf = workflow.parse(plain.jobs.workflow(plain.job.id)["text"])
    text = workflow.dump({**wf, "handlers": {**wf["handlers"], "chart": "describe"}})
    f = jobflow.in_review(tmp_path / "d", chart=True, workflow_text=text)
    chart = chart_region(f)
    assert chart["handler"] == "describe" and chart["status"] == "extracted"
    assert not (f.job.dir / "llm" / "llm.jsonl").is_file()
    assert not [g for g in review.all_gaps(f.job.dir) if g["code"] == "llm_failed"]
    pkg = deliver.confirm(f.jobs, f.job.id, "anna", f.cfg)
    assert not (pkg / "llm.jsonl").exists()
    assert json.loads((pkg / "manifest.json").read_text())["llm"] == {"used": False}


def test_endpoint_down_degrades_with_gaps(tmp_path, fake):
    fake.status = 503
    f = jobflow.in_review(tmp_path, chart=True, llm_url=fake.url)
    chart = chart_region(f)
    assert chart["status"] == "extracted" and f.job.status == "in_review"
    [gap] = [g for g in review.all_gaps(f.job.dir) if g["code"] == "llm_failed"]
    assert (gap["region_id"], gap["kind"], gap["reason"]) == (chart["region_id"], "describe", "http_503")
    assert len(fake.requests) == 3                                      # one call, two retries
    assert KEY not in json.dumps(gap)
    failed = llm.summarize(f.jobs, f.job.id, f.cfg)                     # a document-level failure too (OCBC run 1)
    assert failed["code"] == "llm_failed" and "region_id" not in failed
    pkg = deliver.confirm(f.jobs, f.job.id, "anna", f.cfg)
    check = contract.check(pkg)
    assert check["ok"], check["errors"]


def test_summary_is_cached_and_lands_first(tmp_path, fake):
    f = jobflow.in_review(tmp_path, chart=True, llm_url=fake.url)
    row = llm.summarize(f.jobs, f.job.id, f.cfg)
    assert row["kind"] == "summary" and row["output"]["text"].startswith("An annual report")
    assert json.loads((f.job.dir / "llm" / "summary.json").read_text()) == {"status": "done"}
    assert [r["kind"] for r in read_jsonl(f.job.dir / "llm" / "llm.jsonl")] == ["summary", "describe"]
    n = len(fake.requests)
    llm.summarize(f.jobs, f.job.id, f.cfg)
    assert len(fake.requests) == n                                      # cached


def test_review_recomputes_llm_rows_of_changed_regions(tmp_path, fake):
    f = jobflow.in_review(tmp_path, chart=True, llm_url=fake.url)
    chart = chart_region(f)
    review.apply(f.jobs, f.job.id, [{"action": "reclassify", "region_id": chart["region_id"], "to": "picture"}],
                 "peter", f.cfg)                                        # picture: discard in the suggested workflow
    assert read_jsonl(f.job.dir / "llm" / "llm.jsonl") == []
    review.apply(f.jobs, f.job.id, [{"action": "reclassify", "region_id": chart["region_id"], "to": "chart"}],
                 "peter", f.cfg)
    assert [r["kind"] for r in read_jsonl(f.job.dir / "llm" / "llm.jsonl")] == ["describe"]
    assert len(fake.requests) == 1                                      # the same crop and text: from the cache


def test_package_carries_llm_jsonl_and_manifest_llm(tmp_path, fake):
    f = jobflow.in_review(tmp_path, chart=True, llm_url=fake.url)
    llm.summarize(f.jobs, f.job.id, f.cfg)
    pkg = deliver.confirm(f.jobs, f.job.id, "anna", f.cfg)
    check = contract.check(pkg)
    assert check["ok"], check["errors"]
    assert [r["kind"] for r in read_jsonl(pkg / "llm.jsonl")] == ["summary", "describe"]
    manifest = json.loads((pkg / "manifest.json").read_text())
    assert manifest["llm"] == {"used": True, "provider": "openai_compatible", "model": "MiniMax-M3", "remote": True,
                               "counts": {"summary": 1, "describe": 1, "chart_table": 0, "failed": 0}}
    assert manifest["run"]["llm"]["calls"] == 2 and KEY not in (pkg / "manifest.json").read_text()


def test_second_run_hits_cache_and_package_is_identical(tmp_path, fake):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent / "regression"))
    import same_packages
    pkgs = []
    for name in ("one", "two"):
        f = jobflow.in_review(tmp_path, work=f"work-{name}", delivery=f"delivery-{name}", chart=True,
                              llm_url=fake.url)
        llm.summarize(f.jobs, f.job.id, f.cfg)
        pkgs.append(deliver.confirm(f.jobs, f.job.id, "anna", f.cfg))
        if name == "one":
            first = len(fake.requests)
    assert len(fake.requests) == first == 2                             # the second run made no call
    assert same_packages.differences(pkgs[0], pkgs[1]) == []


def test_search_queue_and_ack_cover_llm_output(tmp_path, fake):
    f = jobflow.in_review(tmp_path, chart=True, llm_url=fake.url)
    chart = chart_region(f)
    hit = search.find(f.jobs, f.job.id, Filters(q="revenue sgd"))["items"]
    assert [(i["region_id"], i["match"]["source"]) for i in hit] == [(chart["region_id"], "llm")]
    assert hit[0]["llm"] == ["describe"]
    [item] = [i for i in queue.items(f.jobs, f.job.id) if i["group"] == "llm_check"]
    assert item["item_id"] == f"llm:describe:{chart['region_id']}" and item["status"] == "open"
    review.apply(f.jobs, f.job.id, [{"action": "ack", "item_id": item["item_id"], "verdict": "wrong"}], "peter",
                 f.cfg)
    [item] = [i for i in queue.items(f.jobs, f.job.id) if i["group"] == "llm_check"]
    assert item["status"] == "resolved"
    pkg = deliver.confirm(f.jobs, f.job.id, "anna", f.cfg)
    assert contract.check(pkg)["ok"]


def test_api_summary_llm_and_background_summary(tmp_path, fake):
    from kb_intake.service.app import create_app
    f = jobflow.in_review(tmp_path, chart=True, llm_url=fake.url)
    with TestClient(create_app(f.cfg), base_url="http://127.0.0.1") as c:
        assert c.get(f"/v1/jobs/{f.job.id}/summary").json()["status"] == "off"      # not run for this job yet
        llm.summarize(f.jobs, f.job.id, f.cfg)
        s = c.get(f"/v1/jobs/{f.job.id}/summary").json()
        assert s["status"] == "done" and s["text"].startswith("An annual report") and s["model"] == "MiniMax-M3"
        out = c.get(f"/v1/jobs/{f.job.id}/llm").json()
        assert out["enabled"] is True and [i["kind"] for i in out["items"]] == ["summary", "describe"]
        assert out["progress"]["describe"] == {"total": 1, "done": 1, "cached": 0, "failed": 0}
        only = c.get(f"/v1/jobs/{f.job.id}/llm", params={"kind": "describe"}).json()["items"]
        assert [i["kind"] for i in only] == ["describe"]
        regions = c.get(f"/v1/jobs/{f.job.id}/regions", params={"type": "chart"}).json()["items"]
        assert regions[0]["llm"] == ["describe"]
        assert KEY not in c.get(f"/v1/jobs/{f.job.id}/llm").text


def test_service_runs_the_summary_after_label_without_holding_the_step_slot(tmp_path, fake):
    from kb_intake.service.app import create_app
    cfg = jobflow.config(tmp_path, llm_url=fake.url)
    pdf = jobflow.build_pdf(tmp_path / "demo.pdf")
    engines = {"label": jobflow.engine(tmp_path, "label", chart=True),
               "extract": jobflow.engine(tmp_path, "extract", chart=True)}
    app = create_app(cfg, engines=engines)
    decl = "\n".join(f"{k}: {json.dumps(v)}" for k, v in jobflow.DECL.items())
    with TestClient(app, base_url="http://127.0.0.1") as c:
        r = c.post("/v1/jobs", files={"file": ("demo.pdf", pdf.read_bytes(), "application/pdf")},
                   data={"declaration": decl})
        job_id = r.json()["job_id"]
        assert app.state.runner.wait(60)
        assert c.get(f"/v1/jobs/{job_id}").json()["job"]["status"] == "planned"
        # never "off" once labelling is done (OCBC UI walk-through: a page that asked then never asked again)
        assert c.get(f"/v1/jobs/{job_id}/summary").json()["status"] in ("pending", "done")
        assert app.state.summaries.wait(60)
        assert c.get(f"/v1/jobs/{job_id}/summary").json()["status"] == "done"



def test_summary_and_descriptions_written_at_the_same_time_keep_both(tmp_path, fake, monkeypatch):
    """earn-it, stage L: the background summary and the describe pass both rewrite llm/llm.jsonl. The describe pass
    reads the files, waits seconds for the model, then saves: a summary saved in between must survive."""
    import threading

    import pymupdf
    f = jobflow.in_review(tmp_path, chart=True, llm_url=fake.url)
    (f.cfg.cache_dir / "llm").rename(f.cfg.cache_dir / "llm-old")      # the describe call goes to the model again
    started, release = threading.Event(), threading.Event()
    real = llm_client.chat

    def slow_describe(*a, **kw):
        if "PDF document" in a[3][-1]["text"]:
            started.set()
            release.wait(10)                         # the describe call is in flight while the summary saves
        return real(*a, **kw)
    monkeypatch.setattr(llm_client, "chat", slow_describe)
    wf = workflow.parse(f.jobs.workflow(f.job.id)["text"])
    regions = review.current(f.job.dir)["regions"]

    def describe():
        with pymupdf.open(f.job.source) as pdf:
            llm.describe_regions(f.job.dir, f.cfg, wf, regions, pdf, only={chart_region(f)["region_id"]})
    t = threading.Thread(target=describe)
    t.start()
    assert started.wait(10)
    llm.summarize(f.jobs, f.job.id, f.cfg)           # saves while the describe pass waits
    release.set()
    t.join(10)
    assert sorted(r["kind"] for r in read_jsonl(f.job.dir / "llm" / "llm.jsonl")) == ["describe", "summary"]


def test_a_summary_left_pending_by_a_crash_is_failed_at_start(tmp_path, fake):
    from kb_intake.service.app import create_app
    f = jobflow.in_review(tmp_path, chart=True, llm_url=fake.url)
    (f.job.dir / "llm" / "summary.json").write_text('{"status": "pending"}')
    with TestClient(create_app(f.cfg), base_url="http://127.0.0.1") as c:
        assert c.get(f"/v1/jobs/{f.job.id}/summary").json()["status"] == "failed"
    assert json.loads((f.job.dir / "llm" / "summary.json").read_text()) == {"status": "failed",
                                                                             "reason": "interrupted"}


def test_an_unexpected_error_in_the_summary_does_not_leave_it_pending(tmp_path, fake, monkeypatch):
    f = jobflow.in_review(tmp_path, chart=True, llm_url=fake.url)
    monkeypatch.setattr(llm, "summary_input", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        llm.summarize(f.jobs, f.job.id, f.cfg)
    assert json.loads((f.job.dir / "llm" / "summary.json").read_text()) == {"status": "failed",
                                                                             "reason": "error_RuntimeError"}
