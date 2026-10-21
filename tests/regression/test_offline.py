"""A real Docling run with the network blocked (intake-design §7, §9.3.2). Skipped unless the pinned weights are
installed ($INTAKE_MODELS_DIR, else ~/.kb-intake/models).

`kb-intake scan` runs in a subprocess whose interpreter (and the conversion child it spawns) loads a sitecustomize
that makes every IP connection fail and records the attempt. The package must come out with Docling's status
"success", and no connection may have been attempted.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pymupdf
import pytest

from kb_intake import models

MODELS = Path(os.environ.get("INTAKE_MODELS_DIR", "~/.kb-intake/models")).expanduser()

SITECUSTOMIZE = '''
import os, socket
_log = os.environ["NET_ATTEMPTS"]
with open(os.environ["NET_LOADED"], "a") as f:      # one line per interpreter that runs with the block
    f.write(str(os.getpid()) + "\\n")
_connect = socket.socket.connect
def _blocked(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6):
        with open(_log, "a") as f:
            f.write(repr(address) + "\\n")
        raise OSError("network blocked by test")
    return _connect(self, address)
socket.socket.connect = _blocked
socket.socket.connect_ex = lambda self, address: _blocked(self, address) or 0
'''


def _ready() -> bool:
    try:
        models.verify(MODELS)
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _ready(), reason="pinned weights not installed")
def test_scan_runs_docling_with_the_network_blocked(tmp_path):
    pdf = tmp_path / "t.pdf"
    doc = pymupdf.open()
    p = doc.new_page(width=595, height=842)
    p.insert_text((72, 80), "Selected figures of the group for the year", fontsize=14)
    xs, ys = [72, 250, 400, 520], [200, 230, 260, 290]
    for x in xs:
        p.draw_line((x, ys[0]), (x, ys[-1]))
    for y in ys:
        p.draw_line((xs[0], y), (xs[-1], y))
    for r, row in enumerate([["Item", "2025", "2024"], ["Net profit", "7,422", "6,111"], ["Other", "-", "12.5"]]):
        for c, text in enumerate(row):
            p.insert_text((xs[c] + 4, ys[r] + 20), text, fontsize=10)
    doc.save(pdf)
    (tmp_path / "site").mkdir()
    (tmp_path / "site" / "sitecustomize.py").write_text(SITECUSTOMIZE)
    attempts, loaded = tmp_path / "attempts.txt", tmp_path / "loaded.txt"
    decl = tmp_path / "d.yaml"
    decl.write_text("doc_id: offline-test\nissuer_id: test\ndoc_type: report\nperiod_end: 2025-12-31\nlang: en\n"
                    "source_url: https://example.com\ncompleteness: {full: true}\n")
    env = {**os.environ, "PYTHONPATH": str(tmp_path / "site"), "NET_ATTEMPTS": str(attempts), "NET_LOADED": str(loaded),
           "INTAKE_MODELS_DIR": str(MODELS), "INTAKE_CACHE_DIR": str(tmp_path / "cache")}
    probe = subprocess.run([sys.executable, "-c", "import socket; socket.create_connection(('192.0.2.1', 80), 1)"],
                           env=env, capture_output=True, text=True)
    assert "network blocked by test" in probe.stderr and attempts.exists()        # the block works
    attempts.unlink()
    loaded.unlink()
    r = subprocess.run([sys.executable, "-m", "kb_intake.cli", "scan", str(pdf), "--decl", str(decl),
                        "--out", str(tmp_path / "out")], env=env, capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, r.stderr[-2000:]
    result = json.loads(r.stdout.splitlines()[-1])
    m = json.loads((tmp_path / "out" / result["package"] / "manifest.json").read_text())
    layout = next(s for s in m["steps"] if s["step"] == "layout")
    assert layout["status"] == "success" and m["degraded"] is False, layout
    assert not attempts.exists(), attempts.read_text()
    assert len(set(loaded.read_text().split())) >= 2          # the CLI and the conversion child both had the block
    tables = [json.loads(line) for line in (tmp_path / "out" / result["package"] / "tables.jsonl").open()]
    assert [t["extract_method"] for t in tables if t["role"] == "primary"] == ["docling"]
