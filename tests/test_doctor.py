"""kb-intake doctor: each check's PASS / FAIL / SKIP and what it tells the user to do. The LLM checks talk to the
local fake server over HTTP; the smoke test on a real Docling run is in tests/regression/ (it needs the models)."""

import dataclasses
import json
import os
import socket

import pymupdf
import pytest
from fake_llm import FakeLlm

from kb_intake import cli, config, doctor

KEY = "sk-doctor-SENTINEL"


def llm_cfg(tmp_path, url):
    cfg = config.load(None, env={"INTAKE_DATA_DIR": str(tmp_path / "data"), "INTAKE_MODELS_DIR": str(tmp_path / "m")})
    return dataclasses.replace(cfg, llm=dataclasses.replace(cfg.llm, enabled=True, base_url=url, remote=False))


def by_name(checks):
    return {c.name: c for c in checks}


def test_llm_off_is_skipped_and_nothing_is_sent(tmp_path):
    cfg = config.load(None, env={})
    [c] = doctor.check_llm(cfg, {})
    assert (c.name, c.status, c.blocking) == ("llm", doctor.SKIP, False)


def test_llm_enabled_without_a_key_fails(tmp_path):
    fake = FakeLlm()
    try:
        [c] = doctor.check_llm(llm_cfg(tmp_path, fake.url), {})
        assert c.status == doctor.FAIL and "INTAKE_LLM_API_KEY" in c.detail and fake.requests == []
    finally:
        fake.close()


def test_text_and_images_work(tmp_path):
    fake = FakeLlm()
    try:
        cs = by_name(doctor.check_llm(llm_cfg(tmp_path, fake.url), {"INTAKE_LLM_API_KEY": KEY}))
        assert cs["llm"].status == doctor.PASS and "stays on this machine" in cs["llm"].detail
        assert cs["llm_text"].status == doctor.PASS and cs["llm_image"].status == doctor.PASS
        assert any(p.get("type") == "image_url" for p in fake.requests[1]["messages"][0]["content"])
    finally:
        fake.close()


def test_text_only_model_switches_describe_off_through_the_data_dir_not_the_env_file(tmp_path):
    """Peter, 2026-10-21: setup and doctor never change the user's .env. Each run tests images again and records
    the result in the data directory; describe follows the latest result."""
    dotenv = tmp_path / ".env"
    dotenv.write_text("INTAKE_LLM_ENABLED=true\nINTAKE_LLM_API_KEY=x\n")
    before = dotenv.read_bytes()
    fake = FakeLlm(images=False)
    env = {"INTAKE_LLM_API_KEY": KEY}
    cfg = llm_cfg(tmp_path, fake.url)
    try:
        cs = by_name(doctor.check_llm(cfg, env))
    finally:
        fake.close()
    assert cs["llm_text"].status == doctor.PASS
    assert cs["llm_image"].status == doctor.FAIL and cs["llm_image"].blocking is False
    assert "images not accepted: chart and picture descriptions switched off automatically" in cs["llm_image"].detail
    assert dotenv.read_bytes() == before
    state = json.loads(config.capabilities_path(cfg).read_text())
    assert state["images"] is False and state["base_url"] == fake.url and KEY not in json.dumps(state)
    data = {"INTAKE_DATA_DIR": str(tmp_path / "data"), "INTAKE_LLM_ENABLED": "true", "INTAKE_LLM_BASE_URL": fake.url}
    assert config.load(None, env=data).llm.features["describe"] is False            # the next start
    fake = FakeLlm(images=True)                         # the model takes images now: describe comes back
    try:
        cfg2 = llm_cfg(tmp_path, fake.url)
        cs = by_name(doctor.check_llm(cfg2, {"INTAKE_LLM_API_KEY": KEY}))
    finally:
        fake.close()
    assert cs["llm_image"].status == doctor.PASS
    assert config.load(None, env={**data, "INTAKE_LLM_BASE_URL": fake.url}).llm.features["describe"] is True
    assert dotenv.read_bytes() == before


def test_a_result_for_another_endpoint_or_model_does_not_apply(tmp_path):
    data = tmp_path / "data"
    cfg = config.load(None, env={"INTAKE_DATA_DIR": str(data), "INTAKE_LLM_BASE_URL": "https://a.example/v1"})
    config.record_capabilities(cfg, images=False, reason="http_400")
    env = {"INTAKE_DATA_DIR": str(data), "INTAKE_LLM_ENABLED": "true"}
    assert config.load(None, env={**env, "INTAKE_LLM_BASE_URL": "https://a.example/v1"}).llm.features["describe"] is False
    assert config.load(None, env={**env, "INTAKE_LLM_BASE_URL": "https://b.example/v1"}).llm.features["describe"] is True
    assert config.load(None, env={**env, "INTAKE_LLM_BASE_URL": "https://a.example/v1",
                                  "INTAKE_LLM_MODEL": "other"}).llm.features["describe"] is True
    info = config.public_llm_info(config.load(None, env={**env, "INTAKE_LLM_BASE_URL": "https://a.example/v1"}), {})
    assert info["images"] is False


def test_the_users_own_switch_still_counts(tmp_path):
    env = {"INTAKE_DATA_DIR": str(tmp_path / "data"), "INTAKE_LLM_ENABLED": "true", "INTAKE_LLM_DESCRIBE": "false"}
    cfg = config.load(None, env=env)
    config.record_capabilities(cfg, images=True, reason=None)
    assert config.load(None, env=env).llm.features["describe"] is False


def test_unreachable_endpoint_fails_with_what_to_check(tmp_path):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()                                                     # nothing listens there
    cs = by_name(doctor.check_llm(llm_cfg(tmp_path, f"http://127.0.0.1:{port}/v1"), {"INTAKE_LLM_API_KEY": KEY}))
    assert cs["llm_text"].status == doctor.FAIL and cs["llm_text"].blocking
    assert "HTTPS_PROXY" in cs["llm_text"].fix and "INTAKE_LLM_BASE_URL" in cs["llm_text"].fix
    assert cs["llm_image"].status == doctor.SKIP


def test_another_host_says_where_the_data_goes(tmp_path):
    cfg = config.load(None, env={"INTAKE_LLM_ENABLED": "true", "INTAKE_LLM_BASE_URL": "https://llm.corp.example/v1"})
    c = doctor.check_llm(cfg, {"INTAKE_LLM_API_KEY": KEY}, chat=lambda *a, **k: (_ for _ in ()).throw(
        doctor.llm_client.LlmError("network", "x")))[0]
    assert c.detail.endswith("data will be sent to llm.corp.example")


def test_models_are_checked_file_by_file(tmp_path):
    cs = doctor.check_models(tmp_path / "nothing")
    assert [c.status for c in cs] == [doctor.FAIL] * 3
    assert "models import" in cs[0].fix and "huggingface.co/" in cs[0].fix


def test_torch_is_reported_never_failed():
    c = doctor.check_torch()
    assert c.status == doctor.PASS and c.blocking is False and ("CPU build" in c.detail or "CUDA" in c.detail)


def test_smoke_pdf_has_a_title_text_and_the_table(tmp_path):
    doctor.smoke_pdf(tmp_path / "s.pdf")
    with pymupdf.open(tmp_path / "s.pdf") as d:
        assert d.page_count == 3
        words = {w[4] for w in d[1].get_text("words")}
    assert {"1,234", "(567)", "808", "Revenue"} <= words


def test_cli_doctor_writes_the_report_and_never_the_key(tmp_path, monkeypatch, capsys):
    fake = FakeLlm(images=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(f"INTAKE_LLM_ENABLED=true\nINTAKE_LLM_BASE_URL={fake.url}\nINTAKE_LLM_API_KEY={KEY}\n"
                                   f"INTAKE_MODELS_DIR=./models\nINTAKE_DATA_DIR=./data\n")
    # cli.main loads .env into os.environ: a copy here, so nothing it sets outlives the test (monkeypatch.delenv
    # of a variable that was absent restores nothing)
    monkeypatch.setattr(os, "environ", {k: v for k, v in os.environ.items() if not k.startswith("INTAKE_")})
    try:
        rc = cli.main(["doctor"])
    finally:
        fake.close()
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    report = (tmp_path / "kb-intake-doctor.json").read_text()
    assert rc == 1 and out["ok"] is False and set(out["blocking_failures"]) == {"model layout", "model table_structure",
                                                                                  "model picture_classifier"}
    assert KEY not in report and json.loads(report)["checks"]
    assert "INTAKE_LLM_DESCRIBE" not in (tmp_path / ".env").read_text()                 # .env untouched
    assert json.loads((tmp_path / "data" / "work" / "llm-capabilities.json").read_text())["images"] is False


def test_cli_doctor_reports_a_bad_setting_instead_of_crashing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(os, "environ", {**{k: v for k, v in os.environ.items() if not k.startswith("INTAKE_")},
                                        "INTAKE_LLM_BASE_URL": "http://llm.corp.example:8000/v1"})
    assert cli.main(["doctor"]) == 1
    report = json.loads((tmp_path / "kb-intake-doctor.json").read_text())
    assert report["checks"][0]["name"] == "config" and "INTAKE_LLM_ALLOW_HTTP" in report["checks"][0]["detail"]
