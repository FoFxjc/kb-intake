"""The HTTP API and web UI of `kb-intake serve` (intake-design-v2 §6) through fastapi's TestClient.

Every step is the real one, on the fake Docling engine. Steps run in the service's one background thread.
"""

import threading

import pymupdf
import pytest
import samples
from fakes import fake_engine
from fastapi.testclient import TestClient
from samples import DOC, build_pdf, decl_yaml

from kb_intake import contract, label, workflow
from kb_intake.config import Config
from kb_intake.jobs import Jobs
from kb_intake.service.app import create_app


def make_cfg(tmp_path, roots=()) -> Config:
    return Config(models_dir=tmp_path / "models", cache_dir=tmp_path / "cache", work_dir=tmp_path / "work",
                  delivery_dir=tmp_path / "delivery", scan_roots=tuple(r.resolve() for r in roots))


@pytest.fixture
def svc(tmp_path, monkeypatch):
    (tmp_path / "roots" / "inbox").mkdir(parents=True)
    cfg = make_cfg(tmp_path, roots=[tmp_path / "roots"])
    engines = {"label": fake_engine(tmp_path, stage="label"), "extract": fake_engine(tmp_path, stage="extract")}
    app = create_app(cfg, engines=engines)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.tmp, c.cfg, c.runner = tmp_path, cfg, app.state.runner
        yield c


def upload(c, name="report.pdf", decl=None):
    pdf = c.tmp / name
    if not pdf.exists():               # the same bytes on a second upload (a rebuilt PDF has a new creation date)
        build_pdf(pdf)
    return c.post("/v1/jobs", files={"file": (name, pdf.read_bytes(), "application/pdf")},
                  data={"declaration": decl if decl is not None else decl_yaml()})


def settle(c, job_id) -> dict:
    assert c.runner.wait(60), "the background step did not finish"
    return c.get(f"/v1/jobs/{job_id}").json()


def labelled(c) -> str:
    r = upload(c)
    assert r.status_code == 202, r.text
    j = settle(c, r.json()["job_id"])
    assert j["job"]["status"] == "planned", j
    return j["job"]["id"]


# ---------- the plan's list ----------

def test_upload_creates_the_job_and_starts_label(svc):
    r = upload(svc)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["created"] is True and body["started"] is True
    j = settle(svc, body["job_id"])
    assert j["job"]["status"] == "planned" and j["job"]["source_name"] == "report.pdf"
    assert j["stats"]["regions"] == 5
    steps = [(e["step"], e["status"]) for e in j["events"]]
    assert ("label", "done") in steps
    assert len([s for s in steps if s == ("label", "done")]) == 1, "the server must not write events again"
    assert [x["job_id"] for x in svc.get("/v1/jobs").json()["jobs"]] == [body["job_id"]]
    ev = svc.get(f"/v1/jobs/{body['job_id']}/events", params={"after": 1}).json()["events"]
    assert ev and ev[0]["seq"] == 2


def test_upload_refuses_bad_input(svc):
    r = svc.post("/v1/jobs", files={"file": ("x.pdf", b"hello", "application/pdf")}, data={"declaration": decl_yaml()})
    assert r.status_code == 422
    r = upload(svc, decl=decl_yaml(period_end="soon"))
    assert r.status_code == 422 and r.json()["detail"]["param"] == "period_end"
    r = upload(svc, decl="a: [b")
    assert r.status_code == 422
    assert svc.get("/v1/jobs").json()["jobs"] == []


def test_workflow_put_invalid_is_422_and_valid_is_a_new_version(svc):
    job_id = labelled(svc)
    r = svc.put(f"/v1/jobs/{job_id}/workflow", content="name: x\nversion: 1\nhandlers: {}\nexclude_pages: []\n",
                headers={"content-type": "text/yaml"})
    assert r.status_code == 422 and "handlers" in r.json()["detail"]
    wf = svc.get(f"/v1/jobs/{job_id}/workflow").json()
    assert wf["version"] == 1 and wf["workflow"] == workflow.load_template()
    assert "table" in wf["types"] and "discard" in wf["handlers"]
    edited = workflow.load_template()
    edited["exclude_pages"] = [2]
    r = svc.put(f"/v1/jobs/{job_id}/workflow", content=workflow.dump(edited), headers={"content-type": "text/yaml"})
    assert r.status_code == 200 and r.json()["version"] == 2
    assert svc.get(f"/v1/jobs/{job_id}/workflow").json()["workflow"]["exclude_pages"] == [2]


def test_approve_in_the_wrong_state_is_409(svc):
    job_id = labelled(svc)
    assert svc.post(f"/v1/jobs/{job_id}/approve", json={"by": ""}).status_code == 422
    assert svc.post(f"/v1/jobs/{job_id}/approve", json={"by": "alice"}).status_code == 202
    settle(svc, job_id)
    r = svc.post(f"/v1/jobs/{job_id}/approve", json={"by": "alice"})
    assert r.status_code == 409
    r = svc.put(f"/v1/jobs/{job_id}/workflow", content=workflow.dump(workflow.load_template()))
    assert r.status_code == 409


def test_directories_outside_scan_roots_are_403(svc):
    root = svc.tmp / "roots"
    build_pdf(root / "inbox" / "a.pdf")
    (root / "inbox" / "notes.txt").write_text("x")
    r = svc.get("/v1/directories")
    assert r.status_code == 200 and r.json()["roots"] == [str(root.resolve())]
    r = svc.get("/v1/directories", params={"path": str(root / "inbox")})
    assert r.status_code == 200 and [e["name"] for e in r.json()["entries"]] == ["a.pdf"]
    assert svc.get("/v1/directories", params={"path": str(svc.tmp)}).status_code == 403
    assert svc.get("/v1/directories", params={"path": str(root / ".." / "work")}).status_code == 403
    (root / "escape").symlink_to(svc.tmp / "work")
    assert svc.get("/v1/directories", params={"path": str(root / "escape")}).status_code == 403


def test_directories_without_scan_roots_are_403(tmp_path, monkeypatch):
    with TestClient(create_app(make_cfg(tmp_path)), base_url="http://127.0.0.1") as c:
        assert c.get("/v1/directories").status_code == 403
        assert c.post("/v1/jobs", json={"path": str(tmp_path), "declaration": decl_yaml()}).status_code == 403


def test_a_path_under_scan_roots_can_be_submitted(svc):
    pdf = build_pdf(svc.tmp / "roots" / "inbox" / "b.pdf")
    r = svc.post("/v1/jobs", json={"path": str(svc.tmp / "outside.pdf"), "declaration": decl_yaml()})
    assert r.status_code == 403
    r = svc.post("/v1/jobs", json={"path": str(pdf), "declaration": decl_yaml()})
    assert r.status_code == 202, r.text
    assert settle(svc, r.json()["job_id"])["job"]["source_name"] == "b.pdf"


def test_page_image_is_png(svc):
    job_id = labelled(svc)
    r = svc.get(f"/v1/jobs/{job_id}/pages/1/image", params={"dpi": 50})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
    pix = pymupdf.Pixmap(r.content)
    assert abs(pix.width - 595 * 50 / 72) <= 1
    assert svc.get(f"/v1/jobs/{job_id}/pages/3/image").status_code == 404
    assert svc.get(f"/v1/jobs/{job_id}/pages/0").status_code == 404
    assert svc.get("/v1/jobs/nope/pages/1/image").status_code == 404


def test_recover_on_startup_clears_running_op(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path)
    jobs = Jobs(cfg.work_dir)
    job, _ = jobs.create(build_pdf(tmp_path / "a.pdf"), samples.DECL)
    cm = jobs.running(job.id, "label")
    cm.__enter__()                                   # a process that stopped in the middle of the label step
    assert jobs.get(job.id).running_op == "label"
    with TestClient(create_app(cfg), base_url="http://127.0.0.1") as c:
        j = c.get(f"/v1/jobs/{job.id}").json()["job"]
        assert j["running_op"] is None and "interrupted" in j["last_error"] and j["status"] == "uploaded"


# ---------- the whole flow, busy, retry ----------

def test_the_whole_flow(svc):
    job_id = labelled(svc)
    page = svc.get(f"/v1/jobs/{job_id}/pages/1").json()
    assert page["page_no"] == 1 and len(page["regions"]) == 3
    gaps = svc.get(f"/v1/jobs/{job_id}/gaps").json()["gaps"]
    assert isinstance(gaps, list)

    assert svc.post(f"/v1/jobs/{job_id}/review", json={"by": "b", "ops": []}).status_code == 409   # not in review
    assert svc.post(f"/v1/jobs/{job_id}/approve", json={"by": "alice"}).status_code == 202
    j = settle(svc, job_id)
    assert j["job"]["status"] == "in_review" and j["job"]["approved_by"] == "alice"
    assert ("extract", "done") in [(e["step"], e["status"]) for e in j["events"]]

    r = svc.post(f"/v1/jobs/{job_id}/review", json={"by": "bob", "ops": [{"action": "exclude", "region_id": "nope"}]})
    assert r.status_code == 400
    r = svc.post(f"/v1/jobs/{job_id}/review",
                 json={"by": "bob", "ops": [{"action": "exclude", "region_id": f"{DOC}:p0001:r002"}]})
    assert r.status_code == 200 and r.json()["applied"][0]["by"] == "bob"
    assert svc.post(f"/v1/jobs/{job_id}/review", json={"by": "", "ops": []}).status_code == 422

    assert svc.post(f"/v1/jobs/{job_id}/confirm", json={"by": ""}).status_code == 422
    assert svc.post(f"/v1/jobs/{job_id}/confirm", json={"by": "carol"}).status_code == 202
    j = settle(svc, job_id)
    assert j["job"]["status"] == "delivered" and j["job"]["package"].startswith(f"{DOC}@")

    pk = svc.get("/v1/packages").json()["packages"]
    assert [p["package"] for p in pk] == [j["job"]["package"]] and pk[0]["check"]["ok"] is True
    assert svc.post(f"/v1/jobs/{job_id}/confirm", json={"by": "carol"}).status_code == 409


def test_packages_lists_the_contract_check(svc, monkeypatch):
    (svc.cfg.delivery_dir / f"{DOC}@0123456789ab").mkdir(parents=True)
    (svc.cfg.delivery_dir / ".tmp-x").mkdir()
    monkeypatch.setattr(contract, "check", lambda p: {"ok": True, "errors": [], "counts": {}, "contract_version": "2"})
    pk = svc.get("/v1/packages").json()["packages"]
    assert [p["package"] for p in pk] == [f"{DOC}@0123456789ab"] and pk[0]["check"]["ok"] is True


def test_one_step_at_a_time_across_jobs(svc, monkeypatch):
    gate, started = threading.Event(), threading.Event()
    real = label.run

    def slow(*a, **k):
        started.set()
        assert gate.wait(30)
        return real(*a, **k)
    monkeypatch.setattr(label, "run", slow)
    r1 = upload(svc, "one.pdf")
    assert r1.status_code == 202 and started.wait(10)
    pdf2 = build_pdf(svc.tmp / "two.pdf")
    pymupdf_doc = pymupdf.open(pdf2)
    pymupdf_doc[0].insert_text((72, 400), "different", fontsize=9)
    pymupdf_doc.save(svc.tmp / "two-b.pdf")
    pymupdf_doc.close()
    r2 = svc.post("/v1/jobs", files={"file": ("two-b.pdf", (svc.tmp / "two-b.pdf").read_bytes(), "application/pdf")},
                  data={"declaration": decl_yaml(doc_id="other")})
    assert r2.status_code == 409
    assert svc.get("/v1/health").json()["busy"]["job_id"] == r1.json()["job_id"]
    gate.set()
    assert settle(svc, r1.json()["job_id"])["job"]["status"] == "planned"
    assert svc.get("/v1/health").json()["busy"] is None


def test_retry_reruns_the_failed_step(svc, monkeypatch):
    real = label.run
    calls = []

    def flaky(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            with Jobs(svc.cfg.work_dir).running(a[1], "label"):
                raise RuntimeError("model blew up")
        return real(*a, **k)
    monkeypatch.setattr(label, "run", flaky)
    r = upload(svc)
    job_id = r.json()["job_id"]
    j = settle(svc, job_id)
    assert j["job"]["status"] == "uploaded" and "model blew up" in j["job"]["last_error"]
    assert svc.post(f"/v1/jobs/{job_id}/retry").status_code == 202
    assert settle(svc, job_id)["job"]["status"] == "planned"
    assert svc.post(f"/v1/jobs/{job_id}/retry").status_code == 409        # planned: nothing to retry


def test_health_engines_and_ui(svc):
    h = svc.get("/v1/health").json()
    assert h["ok"] is True and h["contract_version"] == "2"
    e = svc.get("/v1/engines").json()
    assert set(e["stages"]) == {"label", "extract"} and "ready" in e
    r = svc.get("/ui/")
    assert r.status_code == 200 and "app.js" in r.text
    assert svc.get("/ui/app.js").status_code == 200
    assert svc.get("/", follow_redirects=False).status_code in (302, 307)
    assert svc.get("/v1/jobs/nope").status_code == 404


def test_ui_has_no_external_resources():
    from kb_intake.service import app as app_mod
    for f in app_mod.STATIC.iterdir():
        text = f.read_text(encoding="utf-8")
        assert "http://" not in text and "https://" not in text.replace("https://example.com", ""), f.name


def test_a_busy_job_is_409_for_the_cli_too(svc):
    """A step the CLI is running on a job (running_op set in the shared store) makes the server's start 409."""
    job_id = labelled(svc)
    jobs = Jobs(svc.cfg.work_dir)
    jobs.approve(job_id, "cli")
    with jobs.running(job_id, "extract"):
        assert svc.post(f"/v1/jobs/{job_id}/retry").status_code == 409


def test_review_checks_the_state_itself(svc, monkeypatch):
    """409 for a job that is not in_review or is busy comes from the service, not from review.apply."""
    import kb_intake
    calls = []
    monkeypatch.setattr(kb_intake.review, "apply", lambda *a, **k: calls.append(a) or [])
    job_id = labelled(svc)
    assert svc.post(f"/v1/jobs/{job_id}/review", json={"by": "b", "ops": []}).status_code == 409   # planned
    jobs = Jobs(svc.cfg.work_dir)
    jobs.approve(job_id, "cli")
    with jobs.running(job_id, "extract"):
        assert svc.post(f"/v1/jobs/{job_id}/review", json={"by": "b", "ops": []}).status_code == 409
    assert calls == []


# ---------- whole-branch review findings ----------

def test_only_local_hosts_and_same_origin_writes_are_served(svc):
    assert svc.get("/v1/health").status_code == 200
    assert svc.get("/v1/health", headers={"host": "evil.example:8700"}).status_code == 403     # DNS rebinding
    assert svc.get("/v1/health", headers={"host": "[::1]:8700"}).status_code == 200
    job_id = labelled(svc)
    cross = svc.post(f"/v1/jobs/{job_id}/approve", json={"by": "x"}, headers={"origin": "https://evil.example"})
    assert cross.status_code == 403
    plain = svc.post(f"/v1/jobs/{job_id}/approve", content='{"by": "attacker"}', headers={"content-type": "text/plain"})
    assert plain.status_code == 415                                   # a cross-site form post cannot pass a gate
    assert svc.get(f"/v1/jobs/{job_id}").json()["job"]["status"] == "planned"
    same = svc.post(f"/v1/jobs/{job_id}/approve", json={"by": "peter"}, headers={"origin": "http://127.0.0.1:8700"})
    assert same.status_code == 202


def test_a_served_address_is_allowed_as_host(tmp_path):
    app = create_app(make_cfg(tmp_path), hosts=("192.168.1.20",))
    with TestClient(app, base_url="http://192.168.1.20:8700") as c:
        assert c.get("/v1/health").status_code == 200


def test_upload_of_the_same_file_reports_the_existing_job(svc):
    first = labelled(svc)
    r = upload(svc, decl=decl_yaml(doc_id="another-id"))
    assert r.status_code == 202 and r.json()["job_id"] == first
    assert r.json()["existing_job"] is True and "not used" in r.json()["existing_note"]


def test_upload_of_a_delivered_file_is_refused(svc):
    job_id = labelled(svc)
    svc.post(f"/v1/jobs/{job_id}/approve", json={"by": "peter"})
    settle(svc, job_id)
    assert svc.post(f"/v1/jobs/{job_id}/confirm", json={"by": "peter"}).status_code in (200, 202)
    assert settle(svc, job_id)["job"]["status"] == "delivered"
    r = upload(svc)
    assert r.status_code == 409 and "already delivered" in r.text


def test_upload_named_dot_dot_and_approval_of_an_unlabelled_job(svc, monkeypatch):
    pdf = build_pdf(svc.tmp / "x.pdf")
    r = svc.post("/v1/jobs", files={"file": ("..", pdf.read_bytes(), "application/pdf")},
                 data={"declaration": decl_yaml()})
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    settle(svc, job_id)
    jobs = Jobs(svc.cfg.work_dir)
    other = svc.tmp / "y.pdf"
    other.write_bytes(pdf.read_bytes() + b"\n%x")
    job, _ = jobs.create(other, {**samples.DECL, "doc_id": "y"})              # uploaded, never labelled
    assert svc.post(f"/v1/jobs/{job.id}/approve", json={"by": "peter"}).status_code == 409
    assert svc.get(f"/v1/jobs/{job.id}/pages/1").status_code == 409


# ---------- intake-ui-llm-design-v1 §3.1 ----------

def test_source_pdf_whole(svc):
    job_id = labelled(svc)
    r = svc.get(f"/v1/jobs/{job_id}/source.pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.headers["accept-ranges"] == "bytes"
    assert r.content == Jobs(svc.cfg.work_dir).get(job_id).source.read_bytes()


def test_source_pdf_range(svc):
    job_id = labelled(svc)
    size = Jobs(svc.cfg.work_dir).get(job_id).source.stat().st_size
    r = svc.get(f"/v1/jobs/{job_id}/source.pdf", headers={"range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100
    assert r.headers["content-range"] == f"bytes 0-99/{size}"


def test_source_pdf_bad_range_and_unknown_job(svc):
    job_id = labelled(svc)
    assert svc.get(f"/v1/jobs/{job_id}/source.pdf", headers={"range": "bytes=999999999-"}).status_code == 416
    assert svc.get("/v1/jobs/nope/source.pdf").status_code == 404


def unlabelled_job(svc):
    pdf = build_pdf(svc.tmp / "u.pdf")
    other = svc.tmp / "u2.pdf"
    other.write_bytes(pdf.read_bytes() + b"\n%u")
    job, _ = Jobs(svc.cfg.work_dir).create(other, {**samples.DECL, "doc_id": "u"})
    return job.id


def test_regions_and_facets(svc):
    job_id = labelled(svc)
    whole = svc.get(f"/v1/jobs/{job_id}/regions").json()
    assert whole["total"] == len(whole["items"]) > 2 and whole["next_cursor"] is None
    first = svc.get(f"/v1/jobs/{job_id}/regions", params={"limit": 2}).json()
    assert len(first["items"]) == 2 and isinstance(first["next_cursor"], str)
    rest = svc.get(f"/v1/jobs/{job_id}/regions", params={"cursor": first["next_cursor"], "limit": 500}).json()
    assert [i["region_id"] for i in first["items"] + rest["items"]] == [i["region_id"] for i in whole["items"]]
    some_type = whole["items"][0]["type"]
    typed = svc.get(f"/v1/jobs/{job_id}/regions", params=[("type", some_type), ("type", "table")]).json()
    assert {i["type"] for i in typed["items"]} <= {some_type, "table"}
    f = svc.get(f"/v1/jobs/{job_id}/facets", params={"type": some_type}).json()
    assert set(f) == {"total", "type", "status", "gap"} and f["total"] == typed["total"] - f["type"].get("table", 0)
    assert svc.get(f"/v1/jobs/{job_id}/regions", params={"q": 'x" OR *'}).status_code == 200


def test_regions_before_label_is_409(svc):
    job_id = unlabelled_job(svc)
    for path in ("regions", "facets"):
        assert svc.get(f"/v1/jobs/{job_id}/{path}").status_code == 409
    assert svc.get("/v1/jobs/nope/regions").status_code == 404


def test_regions_rejects_bad_params(svc):
    job_id = labelled(svc)
    for params in ({"cursor": "zz"}, {"cursor": "LTE"}, {"limit": 0}, {"limit": 501}, {"type": "nope"},
                   {"status": "nope"}, {"gap": "nope"}, {"page": "x"}):
        r = svc.get(f"/v1/jobs/{job_id}/regions", params=params)
        assert r.status_code == 422, (params, r.text)
    assert svc.get(f"/v1/jobs/{job_id}/facets", params={"type": "nope"}).status_code == 422


def test_clusters_endpoint(svc):
    job_id = labelled(svc)
    out = svc.get(f"/v1/jobs/{job_id}/clusters").json()["clusters"]
    heads = [c for c in out if c["kind"] == "repeated_text" and c["basis"]["text"] == "section #"]
    assert len(heads) == 1 and heads[0]["n"] == 2 and heads[0]["pages"] == [1, 2]
    only = svc.get(f"/v1/jobs/{job_id}/clusters", params={"kind": "same_shape"}).json()["clusters"]
    assert all(c["kind"] == "same_shape" for c in only)
    assert svc.get(f"/v1/jobs/{job_id}/clusters", params={"kind": "nope"}).status_code == 422
    assert svc.get(f"/v1/jobs/{unlabelled_job(svc)}/clusters").status_code == 409



@pytest.fixture
def p106(tmp_path):
    """The service on a job in review with a table_suspected gap (tests/jobflow.py, OCBC p106 split)."""
    import jobflow
    flow = jobflow.in_review(tmp_path, p106_split=True)
    with TestClient(create_app(flow.cfg), base_url="http://127.0.0.1") as c:
        c.flow, c.job_id = flow, flow.job.id
        yield c


def test_queue_and_seen(p106, svc):
    c, job_id = p106, p106.job_id
    q = c.get(f"/v1/jobs/{job_id}/queue").json()
    items = [i for g in q["groups"] for i in g["items"]]
    assert q["total"] == len(items) == 1 and items[0]["status"] == "open"
    first = items[0]["item_id"]
    r = c.post(f"/v1/jobs/{job_id}/queue/seen", json={"item_ids": [first]})
    assert r.status_code == 200 and r.json() == {"updated": 1}
    again = c.get(f"/v1/jobs/{job_id}/queue").json()["groups"][0]
    assert again["seen"] == 1 and again["items"][0]["status"] == "seen"
    assert c.post(f"/v1/jobs/{job_id}/queue/seen", json={"item_ids": ["gap:nope:1:-"]}).status_code == 422
    assert c.post(f"/v1/jobs/{job_id}/queue/seen", json={"item_ids": "x"}).status_code == 422
    assert c.post(f"/v1/jobs/{job_id}/queue/seen", content="{}", headers={"content-type": "text/plain"}
                  ).status_code == 415
    planned = labelled(svc)
    assert svc.get(f"/v1/jobs/{planned}/queue").json() == {"total": 0, "done": 0, "groups": []}
    assert svc.post(f"/v1/jobs/{planned}/queue/seen", json={"item_ids": []}).status_code == 409
    assert svc.get(f"/v1/jobs/{unlabelled_job(svc)}/queue").status_code == 409


def test_ack_through_the_api_gaps_resolved_and_review_list(p106):
    c, job_id = p106, p106.job_id
    item = c.get(f"/v1/jobs/{job_id}/queue").json()["groups"][0]["items"][0]
    r = c.post(f"/v1/jobs/{job_id}/review", json={"by": "peter", "ops": [
        {"action": "ack", "item_id": item["item_id"], "verdict": "wrong", "note": "an aligned list"}]})
    assert r.status_code == 200, r.text
    assert r.json()["applied"][0]["target"] == item["target"] and r.json()["applied"][0]["verdict"] == "wrong"
    gaps = c.get(f"/v1/jobs/{job_id}/gaps").json()["gaps"]
    sus = next(g for g in gaps if g["code"] == "table_suspected")
    assert sus["resolved"] == {"action": "ack", "review_seq": 1} and sus["stage"] == "label"
    ops = c.get(f"/v1/jobs/{job_id}/review").json()["ops"]
    assert [o["action"] for o in ops] == ["ack"] and ops[0]["by"] == "peter"
    q = c.get(f"/v1/jobs/{job_id}/queue").json()
    assert q["done"] == 1 and q["groups"][0]["items"][0]["status"] == "resolved"
    again = c.post(f"/v1/jobs/{job_id}/review", json={"by": "peter", "ops": [
        {"action": "ack", "item_id": item["item_id"]}]})
    assert again.status_code == 400 and "resolved" in again.json()["detail"]


def test_llm_endpoints_are_off_without_config(svc, monkeypatch):
    monkeypatch.setenv("INTAKE_LLM_API_KEY", "sk-sentinel-123")
    job_id = labelled(svc)
    s = svc.get(f"/v1/jobs/{job_id}/summary").json()
    assert s == {"status": "off", "provider": "openai_compatible", "model": "MiniMax-M3", "remote": True, "text": None}
    llm = svc.get(f"/v1/jobs/{job_id}/llm").json()
    assert llm["enabled"] is False and llm["items"] == []
    assert set(llm["progress"]) == {"summary", "describe", "chart_table"}
    for path in (f"/v1/jobs/{job_id}/workflow", "/v1/health", "/v1/engines"):
        body = svc.get(path).json()
        assert body["llm"]["enabled"] is False and body["llm"]["key_present"] is True, path
    for path in (f"/v1/jobs/{job_id}/summary", f"/v1/jobs/{job_id}/llm", f"/v1/jobs/{job_id}/workflow", "/v1/health",
                 "/v1/engines", f"/v1/jobs/{job_id}", "/openapi.json"):
        assert "sk-sentinel-123" not in svc.get(path).text, path
    assert svc.get("/v1/jobs/nope/summary").status_code == 404
