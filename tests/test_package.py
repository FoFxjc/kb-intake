"""Building and checking intake packages (flow A) on a synthetic PDF."""

import json
import zipfile
from pathlib import Path

import pymupdf
import pytest

from kb_intake import cli, contract, declaration, native, package

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


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_run_writes_a_package_that_passes_the_contract(pdf, tmp_path):
    pkg = package.run(pdf, dict(DECL), tmp_path / "out")
    assert pkg.name.startswith("acme_ar_2025_en@") and len(pkg.name.split("@")[1]) == 12
    result = contract.check_path(pkg)
    assert result["ok"], result["errors"]
    assert result["counts"]["pages.jsonl"] == 3 and result["counts"]["layout.jsonl"] == 3
    m = json.loads((pkg / "manifest.json").read_text())
    assert m["flow"] == "A" and m["detection"]["page_label_method"]["method"] == "footer_offset"
    assert (pkg / "working.pdf").read_bytes() == pdf.read_bytes() == (pkg / "source.pdf").read_bytes()
    assert json.loads((pkg / "checks.json").read_text())["ok"] is True


def test_pages_layout_and_tables_keep_full_precision(pdf, tmp_path):
    pkg = package.run(pdf, dict(DECL), tmp_path / "out")
    page = pymupdf.open(pdf)[1]
    layout = read_jsonl(pkg / "layout.jsonl")[1]
    assert layout["words"] == [[w[0], w[1], w[2], w[3], w[4], "native", None] for w in page.get_text("words")]
    assert read_jsonl(pkg / "pages.jsonl")[1]["text"] == page.get_text()
    (t,) = read_jsonl(pkg / "tables.jsonl")
    found = page.find_tables().tables[0]
    assert t["table_id"] == "acme_ar_2025_en:t0002_01" and t["pages"] == [2] and t["bboxes"] == [list(found.bbox)]
    assert (t["n_rows"], t["n_cols"]) == (3, 3) and len(t["cells"]) == 9
    assert all(c["bbox"] == list(found.rows[c["r"]].cells[c["c"]]) for c in t["cells"])
    by = {(c["r"], c["c"]): c for c in t["cells"]}
    assert by[(1, 1)]["value"] == 7422 and by[(1, 2)]["value"] == -1234
    assert by[(2, 1)]["nil"] is True and by[(2, 1)]["value"] is None and by[(2, 2)]["value"] == 12.5


def test_rerun_is_byte_identical_except_manifest_run(pdf, tmp_path):
    a = package.run(pdf, dict(DECL), tmp_path / "a")
    b = package.run(pdf, dict(DECL), tmp_path / "b")
    for f in sorted(p.name for p in a.iterdir()):
        if f == "manifest.json":
            ma, mb = (json.loads((x / f).read_text()) for x in (a, b))
            ma.pop("run"), mb.pop("run")
            assert ma == mb
        else:
            assert (a / f).read_bytes() == (b / f).read_bytes(), f


def test_same_file_is_idempotent_and_a_different_file_is_refused(pdf, tmp_path):
    out = tmp_path / "out"
    first = package.run(pdf, dict(DECL), out)
    assert package.run(pdf, dict(DECL), out) == first
    other = make_pdf(tmp_path / "other.pdf", note="revised")
    with pytest.raises(package.IntakeError, match="different file"):
        package.run(other, dict(DECL), out)
    replaced = package.run(other, dict(DECL), out, replace=True)
    assert [p.name for p in out.iterdir() if not p.name.startswith(".")] == [replaced.name]


def test_check_catches_tampering(pdf, tmp_path):
    pkg = package.run(pdf, dict(DECL), tmp_path / "out")
    with (pkg / "pages.jsonl").open("a") as f:
        f.write('{"page_no": 4}\n')
    errors = contract.check_path(pkg)["errors"]
    assert any("sha256 does not match" in e for e in errors)
    assert any("pages.jsonl:4" in e for e in errors)


def test_check_reads_a_zip(pdf, tmp_path):
    pkg = package.run(pdf, dict(DECL), tmp_path / "out")
    z = tmp_path / "pkg.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for p in sorted(pkg.rglob("*")):
            zf.write(p, Path(pkg.name) / p.relative_to(pkg))
    assert contract.check_path(z)["ok"]


@pytest.mark.parametrize("field,value,param", [
    ("doc_id", "ACME_ar_2025_en", "doc_id"),
    ("doc_id", "acme_ar_2025_zh", "doc_id"),           # must end with the declared lang
    ("doc_id", "other_ar_2025_en", "doc_id"),          # must start with the issuer
    ("lang", "fr", "lang"),
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
        package.run(f, dict(DECL), tmp_path / "out")


def test_parse_value():
    assert native.parse_value("1,234") == (1234, False)
    assert native.parse_value("(56.5)") == (-56.5, False)
    assert native.parse_value("-") == (None, True) and native.parse_value("\u2013") == (None, True)
    assert native.parse_value("\x07 12 \x07") == (12, False)
    assert native.parse_value("Net profit") == (None, False) and native.parse_value(None) == (None, False)
    assert native.parse_value("(12") == (None, False) and native.parse_value("1,234\n5,678") == (None, False)


def test_cli_run_and_check(pdf, tmp_path, monkeypatch, capsys):
    decl = tmp_path / "decl.yaml"
    decl.write_text("\n".join(f"{k}: {json.dumps(v)}" for k, v in DECL.items()))
    monkeypatch.setenv("KB_PACKAGES_DIR", str(tmp_path / "pk"))
    assert cli.main(["run", str(pdf), "--decl", str(decl)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["rows"]["pages.jsonl"] == 3
    assert cli.main(["check", out["package"]]) == 0
    assert json.loads(capsys.readouterr().out)["ok"]
