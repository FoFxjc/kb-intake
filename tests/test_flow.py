"""End to end with every real step (label, extract, review, deliver) on the fake Docling engine: once through the
command line, once through the HTTP API (intake-design-v2 §7, the demo acceptance on a synthetic report).

The source (tests/jobflow.py) has a table Docling sees on page 1 and, on page 2, a ruled table Docling only sees as
part of a text item (OCBC p106). Review reclassifies that text region to `table`; the package must then hold a
find_tables table for it and the review operation.
"""

import sys
import time
from functools import partial
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from jobflow import DOC, DECL, build_pdf, engine

from kb_intake import cli, contract, extract, label
from kb_intake.config import Config
from kb_intake.package import read_json, read_jsonl
from kb_intake.service.app import create_app

sys.path.insert(0, str(Path(__file__).parent / "regression"))
from same_packages import differences  # noqa: E402

P106_REGION = f"{DOC}:p0002:r003"


def _cfg_file(tmp: Path, name: str) -> str:
    p = tmp / f"{name}.yaml"
    p.write_text(yaml.safe_dump({"work_dir": str(tmp / name / "work"), "delivery_dir": str(tmp / name / "delivery"),
                                 "cache_dir": str(tmp / name / "cache"), "models_dir": str(tmp / "models")}))
    return str(p)


def _cli(capsys, *argv) -> dict:
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    assert code == 0, err
    return [__import__("json").loads(x) for x in out.splitlines() if x.strip()][-1]


def _deliver_by_cli(tmp: Path, name: str, capsys, monkeypatch) -> Path:
    monkeypatch.setattr(label, "run", partial(label.run, engine=engine(tmp, "label")))
    monkeypatch.setattr(extract, "run", partial(extract.run, engine=engine(tmp, "extract")))
    cfg = _cfg_file(tmp, name)
    pdf = tmp / "report.pdf"
    if not pdf.exists():
        build_pdf(pdf)
    (tmp / "decl.yaml").write_text(yaml.safe_dump(DECL))
    res = _cli(capsys, "label", str(pdf), "--decl", str(tmp / "decl.yaml"), "--config", cfg)
    job_id = res["job_id"]
    assert res["status"] == "planned" and res["stats"]["table_suspected"] == 1
    _cli(capsys, "approve", job_id, "--by", "peter", "--config", cfg)
    _cli(capsys, "extract", job_id, "--config", cfg)
    (tmp / "ops.yaml").write_text(yaml.safe_dump([{"action": "reclassify", "region_id": P106_REGION, "to": "table",
                                                   "note": "NPAs by Period Overdue"}]))
    _cli(capsys, "review", job_id, "--apply", str(tmp / "ops.yaml"), "--by", "peter", "--config", cfg)
    out = _cli(capsys, "confirm", job_id, "--by", "peter", "--config", cfg)
    assert out["check"]["ok"], out["check"]
    return Path(out["path"])


def _assert_p106_package(pkg: Path) -> None:
    assert contract.check(pkg)["ok"]
    tables = read_jsonl(pkg / "tables.jsonl")
    p106 = [t for t in tables if t["region_id"] == P106_REGION]
    assert len(p106) == 1 and p106[0]["extract_method"] == "find_tables"
    assert [c["text"] for c in p106[0]["cells"]] == ["Overdue", "2025", "90 days", "5,678"]   # from the text layer
    region = next(r for r in read_jsonl(pkg / "regions.jsonl") if r["region_id"] == P106_REGION)
    assert region["type"] == "table" and region["label_source"] == "user" and region["table_id"] == p106[0]["table_id"]
    ops = read_jsonl(pkg / "review.jsonl")
    assert [(o["action"], o["region_id"], o["from"], o["to"]) for o in ops] == [
        ("reclassify", P106_REGION, "text", "table"), ("confirm", None, None, None)]
    m = read_json(pkg / "manifest.json")
    assert m["status"]["state"] == "confirmed" and m["workflow"]["approved_by"] == "peter"
    assert sorted(p.relative_to(pkg).as_posix() for p in pkg.rglob("*") if p.is_file()) == sorted(
        ["manifest.json", *m["files"]])


def test_cli_end_to_end_twice_gives_the_same_package(tmp_path, capsys, monkeypatch):
    a = _deliver_by_cli(tmp_path, "a", capsys, monkeypatch)
    b = _deliver_by_cli(tmp_path, "b", capsys, monkeypatch)
    _assert_p106_package(a)
    assert a.name == b.name and a.parent != b.parent
    assert differences(a, b) == []
    assert not [p for p in a.parent.iterdir() if p.name.startswith(".tmp-")]


def _wait(client: TestClient, job_id: str, status: str, timeout: float = 60) -> dict:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        job = client.get(f"/v1/jobs/{job_id}").json()
        j = job.get("job", job)
        if j["status"] == status and not j.get("running_op"):
            return j
        if j.get("last_error"):
            pytest.fail(j["last_error"])
        time.sleep(0.05)
    pytest.fail(f"job {job_id} did not reach {status}")


def test_http_end_to_end(tmp_path):
    cfg = Config(models_dir=tmp_path / "models", cache_dir=tmp_path / "cache", work_dir=tmp_path / "work",
                 delivery_dir=tmp_path / "delivery")
    app = create_app(cfg, engines={"label": engine(tmp_path, "label"), "extract": engine(tmp_path, "extract")})
    pdf = build_pdf(tmp_path / "report.pdf")
    with TestClient(app) as client:
        r = client.post("/v1/jobs", files={"file": ("report.pdf", pdf.read_bytes(), "application/pdf")},
                        data={"declaration": yaml.safe_dump(DECL)})
        assert r.status_code == 202, r.text
        job_id = r.json()["job_id"]
        _wait(client, job_id, "planned")
        gaps = client.get(f"/v1/jobs/{job_id}/gaps").json()
        assert any(g["code"] == "table_suspected" for g in (gaps.get("gaps", gaps) if isinstance(gaps, dict) else gaps))
        assert client.post(f"/v1/jobs/{job_id}/approve", json={"by": "peter"}).status_code in (200, 202)
        _wait(client, job_id, "in_review")
        page = client.get(f"/v1/jobs/{job_id}/pages/2").json()
        assert any(r["region_id"] == P106_REGION for r in page["regions"])
        img = client.get(f"/v1/jobs/{job_id}/pages/2/image")
        assert img.status_code == 200 and img.content[:8] == b"\x89PNG\r\n\x1a\n"
        r = client.post(f"/v1/jobs/{job_id}/review", json={"by": "peter", "ops": [
            {"action": "reclassify", "region_id": P106_REGION, "to": "table"}]})
        assert r.status_code == 200, r.text
        bad = client.post(f"/v1/jobs/{job_id}/review", json={"by": "peter", "ops": [
            {"action": "reclassify", "region_id": P106_REGION, "to": "table"}]})
        assert bad.status_code == 400                                    # same type again: refused
        assert client.post(f"/v1/jobs/{job_id}/confirm", json={"by": "peter"}).status_code in (200, 202)
        j = _wait(client, job_id, "delivered")
        pkgs = client.get("/v1/packages").json()
        listed = pkgs.get("packages", pkgs) if isinstance(pkgs, dict) else pkgs
        assert [p["check"]["ok"] for p in listed] == [True]
    _assert_p106_package(cfg.delivery_dir / j["package"])
