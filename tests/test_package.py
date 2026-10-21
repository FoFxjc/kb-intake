"""Building and checking intake packages (flow A) on a synthetic PDF, without a layout engine (the fully degraded
package: text layer + find_tables). The engine paths are in test_pipeline.py."""

import json
import zipfile
from pathlib import Path

import pymupdf
import pytest

from kb_intake import cli, contract, declaration, package, pipeline, secondary, textlayer
from kb_intake.config import Config

NO_LAYOUT = Config(layout_enabled=False)

DECL = {"doc_id": "acme_ar_2025_en", "issuer_id": "acme", "doc_type": "annual_report", "period_end": "2025-12-31",
        "lang": "en", "source_url": "https://example.com/ar2025.pdf", "completeness": {"full": True, "sections": None}}


def make_pdf(path: Path, note: str = "") -> Path:
    doc = pymupdf.open()
    for i in range(3):
        p = doc.new_page(width=595, height=842)
        p.insert_text((72, 80), f"Annual report page {i + 1} {note}", fontsize=12)
        if i == 1:                                  # a ruled 3x3 table with a nil cell and a negative number
            xs, ys = [72, 250, 400, 520], [200, 230, 260, 290]
            for x in xs:
                p.draw_line((x, ys[0]), (x, ys[-1]))
            for y in ys:
                p.draw_line((xs[0], y), (xs[-1], y))
            cells = [["Item", "2025", "2024"], ["Net profit", "7,422", "(1,234)"], ["Other", "-", "12.5"]]
            for r, row in enumerate(cells):
                for c, text in enumerate(row):
                    p.insert_text((xs[c] + 4, ys[r] + 20), text, fontsize=10)
        p.insert_text((540, 820), str(i + 1), fontsize=9)
    doc.save(path)
    return path


@pytest.fixture
def pdf(tmp_path):
    return make_pdf(tmp_path / "ar.pdf")


def run(pdf, decl, out, **kw):
    return pipeline.process(pdf, decl, out, NO_LAYOUT, **kw)


DEGRADED = {"code": "extractor_degraded", "extractor": "docling", "status": "disabled", "pages": "all",
            "detail": "layout engine disabled (--no-layout)"}


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_run_writes_a_package_that_passes_the_contract(pdf, tmp_path):
    pkg = run(pdf, dict(DECL), tmp_path / "out")
    assert pkg.name.startswith("acme_ar_2025_en@") and len(pkg.name.split("@")[1]) == 12
    result = contract.check_path(pkg)
    assert result["ok"], result["errors"]
    assert result["counts"]["pages.jsonl"] == 3 and result["counts"]["layout.jsonl"] == 3
    m = json.loads((pkg / "manifest.json").read_text())
    assert m["flow"] == "A" and m["detection"]["page_label_method"]["method"] == "footer_offset"
    assert (pkg / "working.pdf").read_bytes() == pdf.read_bytes() == (pkg / "source.pdf").read_bytes()
    assert json.loads((pkg / "checks.json").read_text())["ok"] is True


def test_pages_layout_and_tables_keep_full_precision(pdf, tmp_path):
    pkg = run(pdf, dict(DECL), tmp_path / "out")
    page = pymupdf.open(pdf)[1]
    layout = read_jsonl(pkg / "layout.jsonl")[1]
    assert layout["words"] == [[w[0], w[1], w[2], w[3], w[4], "native", None] for w in page.get_text("words")]
    assert read_jsonl(pkg / "pages.jsonl")[1]["text"] == page.get_text()
    (t,) = read_jsonl(pkg / "tables.jsonl")
    found = page.find_tables().tables[0]
    assert t["table_id"] == "acme_ar_2025_en:t0002_01" and t["pages"] == [2] and t["bboxes"] == [list(found.bbox)]
    assert (t["n_rows"], t["n_cols"]) == (3, 3) and len(t["cells"]) == 9
    assert all(c["grid_bbox"] == list(found.rows[c["r"]].cells[c["c"]]) for c in t["cells"])
    words = page.get_text("words")
    for c in t["cells"]:          # bbox: the box of the text in the cell, inside its grid box
        g, b = c["grid_bbox"], c["bbox"]
        assert g[0] <= b[0] < b[2] <= g[2] and g[1] <= b[1] < b[3] <= g[3]
        assert any(abs(w[0] - b[0]) < 1e-9 and w[4] in c["text"] for w in words)
    by = {(c["r"], c["c"]): c for c in t["cells"]}
    assert by[(1, 1)]["value"] == 7422 and by[(1, 2)]["value"] == -1234
    assert by[(2, 1)]["nil"] is True and by[(2, 1)]["value"] is None and by[(2, 2)]["value"] == 12.5


def test_rerun_is_byte_identical_except_manifest_run(pdf, tmp_path):
    a = run(pdf, dict(DECL), tmp_path / "a")
    b = run(pdf, dict(DECL), tmp_path / "b")
    for f in sorted(p.name for p in a.iterdir()):
        if f == "manifest.json":
            ma, mb = (json.loads((x / f).read_text()) for x in (a, b))
            ma.pop("run"), mb.pop("run")
            assert ma == mb
        else:
            assert (a / f).read_bytes() == (b / f).read_bytes(), f


def test_same_file_is_idempotent_and_a_different_file_is_refused(pdf, tmp_path):
    out = tmp_path / "out"
    first = run(pdf, dict(DECL), out)
    assert run(pdf, dict(DECL), out) == first
    other = make_pdf(tmp_path / "other.pdf", note="revised")
    with pytest.raises(package.IntakeError, match="different file"):
        run(other, dict(DECL), out)
    replaced = run(other, dict(DECL), out, replace=True)
    assert [p.name for p in out.iterdir() if not p.name.startswith(".")] == [replaced.name]


def test_check_catches_tampering(pdf, tmp_path):
    pkg = run(pdf, dict(DECL), tmp_path / "out")
    with (pkg / "pages.jsonl").open("a") as f:
        f.write('{"page_no": 4}\n')
    errors = contract.check_path(pkg)["errors"]
    assert any("sha256 does not match" in e for e in errors)
    assert any("pages.jsonl:4" in e for e in errors)


def test_tables_secondary_is_optional_for_older_packages(pdf, tmp_path):
    """tables_secondary.jsonl was added within v1: a package written before it (stage A) still passes."""
    pkg = run(pdf, dict(DECL), tmp_path / "out")
    m = json.loads((pkg / "manifest.json").read_text())
    assert "tables_secondary.jsonl" in m["files"]
    del m["files"]["tables_secondary.jsonl"]
    (pkg / "tables_secondary.jsonl").unlink()
    (pkg / "manifest.json").write_text(json.dumps(m))
    assert contract.check_path(pkg)["ok"]


def test_check_reads_a_zip(pdf, tmp_path):
    pkg = run(pdf, dict(DECL), tmp_path / "out")
    z = tmp_path / "pkg.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for p in sorted(pkg.rglob("*")):
            zf.write(p, Path(pkg.name) / p.relative_to(pkg))
    assert contract.check_path(z)["ok"]


@pytest.mark.parametrize("field,value,param", [
    ("doc_id", "ACME_ar_2025_en", "doc_id"),
    ("doc_id", "-x", "doc_id"),
    ("doc_id", "a/b", "doc_id"),
    ("lang", "eng", "lang"),
    ("lang", "EN", "lang"),
    ("period_end", "31/12/2025", "period_end"),
    ("completeness", {"sections": []}, "completeness"),
    ("extra", 1, "extra"),
])
def test_declaration_errors_name_the_field(field, value, param):
    with pytest.raises(declaration.DeclarationError) as e:
        declaration.validate({**DECL, field: value})
    assert e.value.param == param


def test_non_pdf_is_refused(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("hello")
    with pytest.raises(declaration.DeclarationError, match="unsupported"):
        run(f, dict(DECL), tmp_path / "out")


def test_parse_value():
    assert textlayer.parse_value("1,234") == (1234, False)
    assert textlayer.parse_value("(56.5)") == (-56.5, False)
    assert textlayer.parse_value("-") == (None, True) and textlayer.parse_value("\u2013") == (None, True)
    assert textlayer.parse_value("\x07 12 \x07") == (12, False)
    assert textlayer.parse_value("Net profit") == (None, False) and textlayer.parse_value(None) == (None, False)
    assert textlayer.parse_value("(12") == (None, False) and textlayer.parse_value("1,234\n5,678") == (None, False)


def test_cli_scan_and_check(pdf, tmp_path, monkeypatch, capsys):
    decl = tmp_path / "decl.yaml"
    decl.write_text("\n".join(f"{k}: {json.dumps(v)}" for k, v in DECL.items()))
    monkeypatch.setenv("INTAKE_PACKAGES_DIR", str(tmp_path / "pk"))
    assert cli.main(["scan", str(pdf), "--decl", str(decl), "--no-layout"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "ok" and out["package"].startswith("acme_ar_2025_en@")
    assert cli.main(["check", str(tmp_path / "pk" / out["package"])]) == 0
    assert json.loads(capsys.readouterr().out)["ok"]


# ---------- after review ----------

def test_parse_value_never_merges_two_numbers():
    """Review B1: "2023 2024" or "7 13.4" are two numbers in one cell, not 20232024 or 713.4."""
    for text in ("2023 2024", "7 13.4", "295 297", "1, 234", "(-5)", "12.5%", "1,23", "(1,234"):
        assert textlayer.parse_value(text) == (None, False), text
    assert textlayer.parse_value("−5") == (-5, False)
    assert textlayer.parse_value("-1,234.5") == (-1234.5, False) and textlayer.parse_value("+7") == (7, False)


def test_cells_carry_their_page(pdf, tmp_path):
    pkg = run(pdf, dict(DECL), tmp_path / "out")
    (t,) = read_jsonl(pkg / "tables.jsonl")
    assert {c["page_no"] for c in t["cells"]} == {2} and all((c["rs"], c["cs"]) == (1, 1) for c in t["cells"])


def test_cli_stdout_is_pure_json(pdf, tmp_path):
    """Review S2: PyMuPDF's one-off layout recommendation must not reach the CLI's stdout."""
    import subprocess
    import sys
    decl = tmp_path / "decl.yaml"
    decl.write_text("\n".join(f"{k}: {json.dumps(v)}" for k, v in DECL.items()))
    r = subprocess.run([sys.executable, "-m", "kb_intake.cli", "scan", str(pdf), "--decl", str(decl),
                        "--out", str(tmp_path / "pk"), "--no-layout"], capture_output=True, text=True, check=True)
    assert json.loads(r.stdout)["status"] == "ok"


def test_cli_errors_are_json_not_tracebacks(pdf, tmp_path, capsys):
    assert cli.main(["scan", str(pdf), "--decl", str(tmp_path / "missing.yaml"), "--out", str(tmp_path)]) == 2
    assert json.loads(capsys.readouterr().err)["error"] == "declaration"
    pkg = run(pdf, dict(DECL), tmp_path / "out")
    (pkg / "checks.json").write_text("{bad")
    assert cli.main(["check", str(pkg)]) == 1
    assert any("checks.json is not JSON" in e for e in json.loads(capsys.readouterr().out)["errors"])
    (pkg / "manifest.json").write_text("[]")
    assert contract.check_path(pkg)["errors"][-1] == "manifest.json is not an object"


def test_files_listed_outside_the_package_are_rejected(pdf, tmp_path):
    """Review S3: a manifest must not make a package "own" a path outside its directory."""
    pkg = run(pdf, dict(DECL), tmp_path / "out")
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    m = json.loads((pkg / "manifest.json").read_text())
    m["files"]["../../outside.txt"] = {"sha256": contract._sha(outside), "bytes": 1, "rows": None}
    (pkg / "manifest.json").write_text(json.dumps(m))
    errors = contract.check_path(pkg)["errors"]
    assert any("outside the package" in e for e in errors) and any("files" in e for e in errors)


def test_one_bad_table_degrades_instead_of_failing(pdf, tmp_path, monkeypatch):
    """Review S4 / intake-design §9.3.5: a table that cannot be read becomes an extractor_degraded gap."""
    monkeypatch.setattr(secondary, "_table", lambda *a: (_ for _ in ()).throw(ValueError("irregular find_tables grid")))
    pkg = run(pdf, dict(DECL), tmp_path / "out")
    gaps = read_jsonl(pkg / "gaps.jsonl")
    assert read_jsonl(pkg / "tables.jsonl") == []
    assert gaps == [{"code": "extractor_degraded", "page_no": 2, "extractor": "find_tables", "table_index": 1,
                     "detail": "ValueError: irregular find_tables grid"}, DEGRADED]
    assert json.loads((pkg / "manifest.json").read_text())["degraded"] is True
