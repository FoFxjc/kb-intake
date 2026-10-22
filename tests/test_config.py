"""intake.yaml loading."""

from pathlib import Path

import pytest

from kb_intake import config

ROOT = Path(__file__).resolve().parents[1]


def test_the_example_configuration_loads(tmp_path):
    cfg = config.load(ROOT / "intake.example.yaml", env={})
    assert cfg.low_confidence == 0.5 and cfg.docling.table_mode == "accurate"
    assert cfg.work_dir == Path("~/.kb-intake/work").expanduser() and cfg.state_db.name == "state.db"


def test_unknown_keys_and_bad_values_are_refused(tmp_path):
    p = tmp_path / "c.yaml"
    for text in ("tables: {primary: docling}\n", "headings: {font_profiles: []}\n", "low_confidence: 2\n"):
        p.write_text(text)
        with pytest.raises(config.ConfigError):
            config.load(p, env={})


def test_environment_overrides_the_directories(tmp_path):
    cfg = config.load(None, env={"INTAKE_WORK_DIR": str(tmp_path / "w"), "INTAKE_DELIVERY_DIR": str(tmp_path / "d")})
    assert cfg.work_dir == tmp_path / "w" and cfg.delivery_dir == tmp_path / "d"


# ---------- llm (intake-ui-llm-design-v1 §4.1) ----------

def test_llm_config_defaults_off():
    cfg = config.load(None, env={})
    assert cfg.llm.enabled is False and cfg.llm.model == "MiniMax-M3"
    assert cfg.llm.base_url == "https://api.minimax.io/v1" and cfg.llm.api_key_env == "INTAKE_LLM_API_KEY"
    assert cfg.llm.features == {"summary": True, "describe": True, "chart_table": False}
    info = config.public_llm_info(cfg, env={})
    assert info == {"enabled": False, "configured": False, "key_present": False, "remote": True,
                    "provider": "openai_compatible", "model": "MiniMax-M3", "base_url": "https://api.minimax.io/v1",
                    "images": None, "features": {"summary": True, "describe": True, "chart_table": False}}


def test_llm_is_enabled_only_with_config_and_key(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("llm: {enabled: true, features: {chart_table: true}}\n")
    cfg = config.load(p, env={})
    assert cfg.llm.features == {"summary": True, "describe": True, "chart_table": True}
    assert config.public_llm_info(cfg, env={})["enabled"] is False
    on = config.public_llm_info(cfg, env={"INTAKE_LLM_API_KEY": "sk-secret"})
    assert on["enabled"] is True and on["key_present"] is True and "sk-secret" not in repr(on)


def test_llm_config_rejects_unknown_or_bad_values(tmp_path):
    p = tmp_path / "c.yaml"
    for text in ("llm: {api_key: x}\n", "llm: {features: {ocr: true}}\n", "llm: {provider: other}\n",
                 "llm: {enabled: maybe}\n", "llm: {features: {summary: 1}}\n", "llm: []\n"):
        p.write_text(text)
        with pytest.raises(config.ConfigError):
            config.load(p, env={})


def test_llm_remote_false_needs_a_loopback_endpoint(tmp_path):
    """`remote` is what the UI and manifest show; it cannot claim local for an endpoint on another host."""
    p = tmp_path / "c.yaml"
    for text in ("llm: {enabled: true, remote: false}\n",
                 "llm: {enabled: true, remote: false, base_url: 'https://llm.example.com/v1'}\n"):
        p.write_text(text)
        with pytest.raises(config.ConfigError, match="remote"):
            config.load(p, env={})
    for host in ("127.0.0.1:8000", "localhost:8000", "[::1]:8000"):
        p.write_text(f"llm: {{enabled: true, remote: false, base_url: 'http://{host}/v1'}}\n")
        assert config.load(p, env={}).llm.remote is False


def test_llm_endpoint_on_another_host_needs_https(tmp_path):
    p = tmp_path / "c.yaml"
    for url in ("http://llm.example.com/v1", "ftp://llm.example.com/v1", "api.minimax.io/v1"):
        p.write_text(f"llm: {{base_url: '{url}'}}\n")
        with pytest.raises(config.ConfigError, match="base_url"):
            config.load(p, env={})
    p.write_text("llm: {base_url: 'http://127.0.0.1:9/v1'}\n")
    assert config.load(p, env={}).llm.base_url == "http://127.0.0.1:9/v1"


# ---------- .env (the key's file, gitignored) ----------

def test_dotenv_sets_only_missing_intake_variables(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# comment\n\nexport INTAKE_LLM_API_KEY='sk-file'\nINTAKE_WORK_DIR=\"/tmp/w x\"\n"
                 "INTAKE_DELIVERY_DIR=/from/file\nOTHER_SECRET=nope\nnot a line\nINTAKE_EMPTY=\n")
    env = {"INTAKE_DELIVERY_DIR": "/from/env"}
    assert config.load_dotenv(p, env) == ["INTAKE_LLM_API_KEY", "INTAKE_WORK_DIR", "INTAKE_EMPTY"]
    assert env == {"INTAKE_DELIVERY_DIR": "/from/env", "INTAKE_LLM_API_KEY": "sk-file", "INTAKE_WORK_DIR": "/tmp/w x",
                   "INTAKE_EMPTY": ""}


def test_dotenv_missing_file_is_nothing(tmp_path):
    env = {}
    assert config.load_dotenv(tmp_path / ".env", env) == [] and env == {}


def test_the_example_env_file_has_no_value():
    text = (ROOT / ".env.example").read_text()
    assert "INTAKE_LLM_API_KEY=\n" in text
    assert ".env" in (ROOT / ".gitignore").read_text().splitlines()


def test_dotenv_comments_bom_and_bad_bytes(tmp_path, capsys):
    p = tmp_path / ".env"
    p.write_bytes("﻿INTAKE_A=sk-abc # my key\nINTAKE_B=\"sk-q\" # c\nINTAKE_C=a#b\n".encode("utf-8"))
    env = {}
    config.load_dotenv(p, env)
    assert env == {"INTAKE_A": "sk-abc", "INTAKE_B": "sk-q", "INTAKE_C": "a#b"}
    p.write_bytes(b"INTAKE_A=\xff\xfe\n")
    env = {}
    assert config.load_dotenv(p, env) == [] and env == {}
    assert "not UTF-8" in capsys.readouterr().err


# ---------- the LLM and the directories from the environment (.env, setup.sh) ----------

def test_llm_from_the_environment_needs_the_explicit_switch(tmp_path):
    """INTAKE_LLM_BASE_URL / _MODEL / _API_KEY configure it; only INTAKE_LLM_ENABLED=true switches it on."""
    env = {"INTAKE_LLM_BASE_URL": "https://llm.corp.example/v1", "INTAKE_LLM_MODEL": "qwen-72b",
           "INTAKE_LLM_API_KEY": "k"}
    cfg = config.load(None, env=env)
    assert (cfg.llm.base_url, cfg.llm.model, cfg.llm.enabled) == ("https://llm.corp.example/v1", "qwen-72b", False)
    assert config.public_llm_info(cfg, env)["enabled"] is False
    on = config.load(None, env={**env, "INTAKE_LLM_ENABLED": "true"})
    assert on.llm.enabled and config.public_llm_info(on, env)["enabled"] is True
    assert on.llm.remote is True                                    # another host: data leaves this machine
    with pytest.raises(config.ConfigError, match="INTAKE_LLM_ENABLED"):
        config.load(None, env={**env, "INTAKE_LLM_ENABLED": "maybe"})


def test_llm_describe_can_be_switched_off_from_the_environment():
    cfg = config.load(None, env={"INTAKE_LLM_ENABLED": "true", "INTAKE_LLM_DESCRIBE": "false"})
    assert cfg.llm.features["describe"] is False and cfg.llm.features["summary"] is True


def test_a_localhost_endpoint_is_not_remote_and_http_elsewhere_needs_allow_http():
    local = config.load(None, env={"INTAKE_LLM_BASE_URL": "http://127.0.0.1:8000/v1"})
    assert local.llm.remote is False
    with pytest.raises(config.ConfigError, match="INTAKE_LLM_ALLOW_HTTP"):
        config.load(None, env={"INTAKE_LLM_BASE_URL": "http://llm.corp.example:8000/v1"})
    ok = config.load(None, env={"INTAKE_LLM_BASE_URL": "http://llm.corp.example:8000/v1", "INTAKE_LLM_ALLOW_HTTP": "true"})
    assert ok.llm.base_url.startswith("http://") and ok.llm.remote is True


def test_data_dir_gives_cache_work_and_delivery_below_it(tmp_path):
    cfg = config.load(None, env={"INTAKE_DATA_DIR": str(tmp_path / "d"), "INTAKE_WORK_DIR": str(tmp_path / "w")})
    assert cfg.cache_dir == tmp_path / "d" / "cache" and cfg.delivery_dir == tmp_path / "d" / "delivery"
    assert cfg.work_dir == tmp_path / "w"                          # a specific directory variable wins


def test_dotenv_relative_paths_are_relative_to_the_file_and_network_variables_pass(tmp_path):
    p = tmp_path / ".env"
    p.write_text("INTAKE_MODELS_DIR=./models\nINTAKE_DATA_DIR=data\nSSL_CERT_FILE=certs/corp.pem\n"
                 "HTTPS_PROXY=http://proxy:8080\nPIP_INDEX_URL=https://pypi.corp/simple\n")
    env = {}
    config.load_dotenv(p, env)
    assert env["INTAKE_MODELS_DIR"] == str(tmp_path / "models") and env["INTAKE_DATA_DIR"] == str(tmp_path / "data")
    assert env["SSL_CERT_FILE"] == str(tmp_path / "certs" / "corp.pem") and env["HTTPS_PROXY"] == "http://proxy:8080"
    assert "PIP_INDEX_URL" not in env                              # setup.sh's business, not kb-intake's
