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
                    "features": {"summary": True, "describe": True, "chart_table": False}}


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
