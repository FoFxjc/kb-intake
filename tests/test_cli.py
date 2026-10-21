"""The command line (intake-design-v2 §6): label -> approve -> extract -> review -> confirm, one JSON object per
line on stdout, errors as JSON on stderr, exit 0 ok / 1 failed / 2 bad input.

label and extract are the real steps on the fake Docling engine (tests/fakes.py); review and deliver are stand-ins
(tests/stepstubs.py) until Task C is merged.
"""

import json
from functools import partial

import pytest
import stepstubs
import yaml
from fakes import fake_engine
from stepstubs import DOC, build_pdf, decl_yaml

from kb_intake import cli, contract, extract, label, workflow
from kb_intake.jobs import Jobs


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A config file with every directory under tmp_path, real steps on fake engines, stand-in review/deliver."""
    cfg = tmp_path / "intake.yaml"
    cfg.write_text(yaml.safe_dump({"work_dir": str(tmp_path / "work"), "delivery_dir": str(tmp_path / "delivery"),
                                   "cache_dir": str(tmp_path / "cache"), "models_dir": str(tmp_path / "models")}))
    monkeypatch.setattr(label, "run", partial(label.run, engine=fake_engine(tmp_path, stage="label")))
    monkeypatch.setattr(extract, "run", partial(extract.run, engine=fake_engine(tmp_path, stage="extract")))
    stepstubs.install(monkeypatch)
    monkeypatch.setattr(contract, "check_path", lambda p: {"contract_version": "2", "ok": True, "errors": [],
                                                           "counts": {}})
    return tmp_path, str(cfg)


def run(capsys, *argv) -> tuple[int, list[dict], list[dict]]:
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    lines = lambda s: [json.loads(x) for x in s.splitlines() if x.strip()]  # noqa: E731
    return code, lines(out), lines(err)


def test_cli_happy_path(env, capsys):
    tmp, cfg = env
    pdf = build_pdf(tmp / "report.pdf")
    (tmp / "decl.yaml").write_text(decl_yaml())

    code, out, err = run(capsys, "label", str(pdf), "--decl", str(tmp / "decl.yaml"), "--config", cfg)
    assert code == 0, err
    (res,) = out
    assert set(res) == {"job_id", "status", "stats", "workflow", "notes"}
    assert res["status"] == "planned" and res["stats"]["regions"] == 5
    assert workflow.parse(res["workflow"]) == workflow.load_template()
    assert res["notes"] == res["stats"]["workflow_notes"]
    assert any(e.get("step") == "label" and e.get("status") == "done" for e in err), "progress goes to stderr"
    job_id = res["job_id"]

    wf = workflow.load_template()
    wf["handlers"]["picture"] = "record"
    wf["exclude_pages"] = [2]
    (tmp / "wf.yaml").write_text(workflow.dump(wf))
    code, out, err = run(capsys, "approve", job_id, "--workflow", str(tmp / "wf.yaml"), "--by", "alice",
                         "--config", cfg)
    assert code == 0, err
    assert out[0]["status"] == "approved" and out[0]["approved_by"] == "alice" and out[0]["workflow_version"] == 2

    code, out, err = run(capsys, "extract", job_id, "--config", cfg)
    assert code == 0, err
    assert out[0]["status"] == "in_review" and out[0]["summary"]["regions"] == 5

    (tmp / "ops.yaml").write_text(yaml.safe_dump([{"action": "exclude", "region_id": f"{DOC}:p0001:r002"}]))
    code, out, err = run(capsys, "review", job_id, "--apply", str(tmp / "ops.yaml"), "--by", "bob", "--config", cfg)
    assert code == 0, err
    assert out[0]["status"] == "in_review" and out[0]["applied"][0]["by"] == "bob"

    code, out, err = run(capsys, "confirm", job_id, "--by", "carol", "--config", cfg)
    assert code == 0, err
    assert set(out[0]) == {"package", "path", "check"} and out[0]["check"]["ok"] is True
    assert out[0]["package"].startswith(f"{DOC}@")

    code, out, _ = run(capsys, "status", job_id, "--config", cfg)
    assert code == 0 and out[0]["job"]["status"] == "delivered" and out[0]["job"]["confirmed_by"] == "carol"
    assert out[0]["stats"]["regions"] == 5 and out[0]["events"]
    code, out, _ = run(capsys, "jobs", "--config", cfg)
    assert code == 0 and [j["job_id"] for j in out] == [job_id] and out[0]["status"] == "delivered"


def test_label_refuses_a_file_that_is_not_a_pdf(env, capsys):
    tmp, cfg = env
    (tmp / "x.pdf").write_bytes(b"hello")
    (tmp / "decl.yaml").write_text(decl_yaml())
    code, out, err = run(capsys, "label", str(tmp / "x.pdf"), "--decl", str(tmp / "decl.yaml"), "--config", cfg)
    assert code == 2 and out == [] and err[0]["ok"] is False


def test_label_refuses_a_bad_declaration(env, capsys):
    tmp, cfg = env
    pdf = build_pdf(tmp / "report.pdf")
    (tmp / "decl.yaml").write_text(decl_yaml(period_end="last year"))
    code, _, err = run(capsys, "label", str(pdf), "--decl", str(tmp / "decl.yaml"), "--config", cfg)
    assert code == 2 and err[0]["param"] == "period_end"
    assert Jobs(tmp / "work").recent() == []


def _labelled(env, capsys) -> str:
    tmp, cfg = env
    pdf = build_pdf(tmp / "report.pdf")
    (tmp / "decl.yaml").write_text(decl_yaml())
    code, out, _ = run(capsys, "label", str(pdf), "--decl", str(tmp / "decl.yaml"), "--config", cfg)
    assert code == 0
    return out[0]["job_id"]


def test_approve_refuses_a_bad_workflow_and_a_wrong_state(env, capsys):
    tmp, cfg = env
    job_id = _labelled(env, capsys)
    (tmp / "wf.yaml").write_text("name: x\nversion: 1\nhandlers: {}\nexclude_pages: []\n")
    code, _, err = run(capsys, "approve", job_id, "--workflow", str(tmp / "wf.yaml"), "--by", "a", "--config", cfg)
    assert code == 2 and "handlers" in err[0]["message"]
    code, out, _ = run(capsys, "approve", job_id, "--by", "a", "--config", cfg)      # the suggested draft
    assert code == 0 and out[0]["workflow_version"] == 1
    code, _, err = run(capsys, "approve", job_id, "--by", "a", "--config", cfg)
    assert code == 2 and err[0]["error"] == "WrongState"


def test_unknown_job_is_bad_input(env, capsys):
    _, cfg = env
    code, _, err = run(capsys, "status", "nope", "--config", cfg)
    assert code == 2 and err[0]["error"] == "not_found"


def test_review_refuses_bad_ops(env, capsys):
    tmp, cfg = env
    job_id = _labelled(env, capsys)
    assert run(capsys, "approve", job_id, "--by", "a", "--config", cfg)[0] == 0
    assert run(capsys, "extract", job_id, "--config", cfg)[0] == 0
    (tmp / "ops.yaml").write_text(yaml.safe_dump({"ops": [{"action": "exclude", "region_id": "nope"}]}))
    code, _, err = run(capsys, "review", job_id, "--apply", str(tmp / "ops.yaml"), "--by", "b", "--config", cfg)
    assert code == 2 and err[0]["error"] == "ReviewError"
    (tmp / "ops.yaml").write_text("just text\n")
    code, _, err = run(capsys, "review", job_id, "--apply", str(tmp / "ops.yaml"), "--by", "b", "--config", cfg)
    assert code == 2


def test_a_failing_step_exits_1(env, capsys, monkeypatch):
    tmp, cfg = env
    job_id = _labelled(env, capsys)
    assert run(capsys, "approve", job_id, "--by", "a", "--config", cfg)[0] == 0

    def boom(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(extract, "run", boom)
    code, _, err = run(capsys, "extract", job_id, "--config", cfg)
    assert code == 1 and "disk full" in err[0]["message"]


def test_label_of_a_known_source_reports_the_existing_job(env, capsys):
    tmp, cfg = env
    job_id = _labelled(env, capsys)
    code, out, _ = run(capsys, "label", str(tmp / "report.pdf"), "--decl", str(tmp / "decl.yaml"), "--config", cfg)
    assert code == 0 and out[0]["job_id"] == job_id and out[0]["status"] == "planned"
    assert len(Jobs(tmp / "work").recent()) == 1
