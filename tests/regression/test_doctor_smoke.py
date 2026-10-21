"""kb-intake doctor --smoke with the real models: the generated PDF's table is found by Docling, extracted, read and
delivered. Skipped unless the pinned weights are installed ($INTAKE_MODELS_DIR, else ~/.kb-intake/models)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from kb_intake import models

MODELS = Path(os.environ.get("INTAKE_MODELS_DIR", "~/.kb-intake/models")).expanduser()


def _installed() -> bool:
    try:
        models.verify(MODELS)
        return True
    except models.ModelsError:
        return False


@pytest.mark.skipif(not _installed(), reason=f"pinned model weights not installed in {MODELS}")
def test_doctor_smoke_with_the_real_models(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("INTAKE_")}
    env.update(INTAKE_MODELS_DIR=str(MODELS), INTAKE_DATA_DIR=str(tmp_path / "data"))
    r = subprocess.run([sys.executable, "-m", "kb_intake.cli", "doctor", "--smoke"], cwd=tmp_path, env=env,
                       capture_output=True, text=True, timeout=1800)
    report = json.loads((tmp_path / "kb-intake-doctor.json").read_text())
    status = {c["name"]: c["status"] for c in report["checks"]}
    assert r.returncode == 0, r.stderr[-2000:]
    assert all(status[n] == "PASS" for n in ("smoke_label", "smoke_approve", "smoke_extract", "smoke_package"))
    assert status["llm"] == "SKIP"
