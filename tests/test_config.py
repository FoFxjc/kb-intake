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
