"""The HTTP API (intake-design §3.2) on the fake engine: submit, progress, page review data, page image, package
zip, contract check of an uploaded zip, configuration, one job at a time."""

import io
import json
import time
import zipfile
from functools import partial

import pytest
from fastapi.testclient import TestClient

import fakes
from kb_intake import pipeline
from kb_intake.service.app import create_app
from test_package import DECL, make_pdf
from test_pipeline import CELLS, XS, YS


@pytest.fixture
def client(tmp_path, monkeypatch):
    doc = json.dumps(fakes.document(3, 2, CELLS, XS, YS))
    monkeypatch.setitem(pipeline.LAYOUT, "docling", partial(fakes.FakeEngine, mode="ok", doc=doc))
    app = create_app(tmp_path / "data")
    with TestClient(app) as c:
        yield c


def _wait(client, job_id, timeout=60):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        j = client.get(f"/v1/jobs/{job_id}").json()
        if j["status"] not in ("queued", "running"):
            return j
        time.sleep(0.2)
    raise AssertionError("job did not finish")


def _decl_text() -> str:
    return "\n".join(f"{k}: {json.dumps(v)}" for k, v in DECL.items())


def test_upload_run_review_download_and_check(client, tmp_path):
    pdf = make_pdf(tmp_path / "ar.pdf")
    r = client.post("/v1/jobs", files=[("files", ("ar.pdf", pdf.read_bytes(), "application/pdf"))],
                    data={"declaration": _decl_text()})
    assert r.status_code == 202, r.text
    j = _wait(client, r.json()["job_id"])
    assert j["status"] == "done" and j["results"][0]["status"] == "ok"
    steps = [(e["step"], e["status"]) for e in j["events"]]
    for s in ("detect", "text_layer", "tables_secondary", "layout", "postprocess", "package"):
        assert (s, "running") in steps and (s, "done") in steps, s
    layout = next(e for e in j["events"] if e["step"] == "layout" and e["status"] == "done")
    assert layout["info"]["status"] == "success" and layout["info"]["cached"] is False
    (p,) = j["packages"]
    assert p["check_ok"] is True and p["layout_status"] == "success"

    page = client.get(f"/v1/jobs/{j['job_id']}/pages/2").json()
    assert page["page_no"] == 2 and {r["type"] for r in page["regions"]} >= {"table", "text"}
    (t,) = [t for t in page["tables"] if t["role"] == "primary"]
    assert t["extract_method"] == "docling" and all(c["bbox_source"] in ("text", "words", "cell") for c in t["cells"]
                                                    if c["bbox"])
    assert page["words"] and page["image"].endswith("/pages/2/image")
    img = client.get(page["image"])
    assert img.status_code == 200 and img.content[:8] == b"\x89PNG\r\n\x1a\n"

    z = client.get(f"/v1/packages/{p['package']}")
    assert z.status_code == 200 and zipfile.is_zipfile(io.BytesIO(z.content))
    checked = client.post("/v1/packages/check", files={"file": ("p.zip", z.content, "application/zip")}).json()
    assert checked["ok"] is True
    assert client.get("/v1/packages").json()["packages"][0]["package"] == p["package"]
    m = client.get(f"/v1/packages/{p['package']}/manifest").json()
    assert m["checks"]["ok"] and m["manifest"]["doc_id"] == DECL["doc_id"]


def test_a_directory_on_this_machine_and_skips(client, tmp_path):
    d = tmp_path / "in"
    d.mkdir()
    make_pdf(d / "a.pdf")
    decl = "defaults:\n" + "\n".join(f"  {k}: {json.dumps(v)}" for k, v in DECL.items() if k != "doc_id")
    first = _wait(client, client.post("/v1/jobs", json={"directory": str(d), "declaration": decl}).json()["job_id"])
    assert [r["status"] for r in first["results"]] == ["ok"] and first["packages"][0]["package"].startswith("a@")
    again = _wait(client, client.post("/v1/jobs", json={"directory": str(d), "declaration": decl}).json()["job_id"])
    assert [r["status"] for r in again["results"]] == ["skipped_same_sha"]
    assert client.post("/v1/jobs", json={"directory": str(tmp_path / "nope")}).status_code == 422


def test_one_job_at_a_time(client, tmp_path):
    client.app.state.service.worker.current = "other-job"
    pdf = make_pdf(tmp_path / "ar.pdf")
    r = client.post("/v1/jobs", files=[("files", ("ar.pdf", pdf.read_bytes(), "application/pdf"))],
                    data={"declaration": _decl_text()})
    assert r.status_code == 409 and "other-job" in r.json()["detail"]
    client.app.state.service.worker.current = None


def test_configuration_is_validated_before_it_is_saved(client):
    bad = client.put("/v1/config", json={"yaml": "docling:\n  threads: 4\n"})
    assert bad.status_code == 422 and "unknown key" in bad.json()["detail"]
    ok = client.put("/v1/config", json={"yaml": "docling:\n  num_threads: 2\n"}).json()
    assert ok["effective"]["docling"]["num_threads"] == 2 and ok["effective"]["cache_dir"].endswith("/data/cache")
    assert client.get("/v1/health").json()["contract_version"] == "1"
    e = client.get("/v1/engines").json()
    assert e["ready"] is True and e["layout"]["name"] == "docling" and e["dependencies"]["problems"] == []


def test_the_ui_is_served(client):
    assert client.get("/", follow_redirects=False).headers["location"] == "/ui/"
    html = client.get("/ui/").text
    assert "<title>kb-intake</title>" in html and client.get("/ui/app.js").status_code == 200
    assert client.get("/v1/packages/../etc/pages/1").status_code == 404
