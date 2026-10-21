"""kb-intake models import: the pinned files found under any directory (a Hugging Face cache, flat files) by name
and sha256, copied into the standard layout and verified. A small fake lock stands in for the real weights."""

import hashlib
import json
import os

import pytest

from kb_intake import cli, models

FILES = {
    "org/layout": {"config.json": b'{"layout": 1}', "model.safetensors": b"LAYOUT-WEIGHTS" * 100},
    "org/tables": {"config.json": b'{"tables": 1}',
                   "model_artifacts/tableformer/accurate/tm_config.json": b'{"mode": "accurate"}',
                   "model_artifacts/tableformer/accurate/weights.safetensors": b"TABLE-WEIGHTS" * 100},
}
REV = {"org/layout": "a" * 40, "org/tables": "b" * 40}
sha = lambda b: hashlib.sha256(b).hexdigest()  # noqa: E731


@pytest.fixture(autouse=True)
def fake_lock(monkeypatch):
    lock = tuple({"repo_id": repo, "revision": REV[repo], "role": repo.split("/")[1],
                  "files": {rel: sha(data) for rel, data in files.items()}} for repo, files in FILES.items())
    monkeypatch.setattr(models, "lock", lambda: lock)
    models._passed.clear()


def hf_cache(root):
    """The layout huggingface_hub writes: models--org--name/{blobs/<sha256>, snapshots/<rev>/<file> -> blob}."""
    for repo, files in FILES.items():
        base = root / ("models--" + repo.replace("/", "--"))
        for rel, data in files.items():
            blob = base / "blobs" / sha(data)
            blob.parent.mkdir(parents=True, exist_ok=True)
            blob.write_bytes(data)
            link = base / "snapshots" / REV[repo] / rel
            link.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(os.path.relpath(blob, link.parent), link)
        (base / "refs").mkdir(exist_ok=True)
        (base / "refs" / "main").write_text(REV[repo])


def test_import_from_a_hugging_face_cache(tmp_path):
    hf_cache(tmp_path / "hub")
    out = models.import_from(tmp_path / "hub", tmp_path / "models")
    assert out["copied"] == 5 and out["missing"] == []
    for repo, files in FILES.items():
        for rel, data in files.items():
            p = tmp_path / "models" / models.folder(repo) / rel
            assert p.read_bytes() == data and not p.is_symlink()
    assert models.verify(tmp_path / "models")


def test_import_from_flat_files_tells_same_names_apart_by_content(tmp_path):
    flat = tmp_path / "downloads"
    (flat / "a").mkdir(parents=True)
    (flat / "b").mkdir()
    (flat / "a" / "config.json").write_bytes(FILES["org/layout"]["config.json"])
    (flat / "b" / "config.json").write_bytes(FILES["org/tables"]["config.json"])
    (flat / "c.json").write_bytes(b"unrelated")
    for files in FILES.values():
        for rel, data in files.items():
            if not rel.endswith("config.json"):
                (flat / rel.rsplit("/", 1)[-1]).write_bytes(data)
    (flat / "tm_config.json").write_bytes(FILES["org/tables"]["model_artifacts/tableformer/accurate/tm_config.json"])
    out = models.import_from(flat, tmp_path / "models")
    assert out["missing"] == [] and models.verify(tmp_path / "models")


def test_a_missing_or_altered_file_is_named_with_where_to_get_it(tmp_path):
    hf_cache(tmp_path / "hub")
    base = tmp_path / "hub" / "models--org--tables"
    snap = base / "snapshots" / REV["org/tables"]
    for rel in ("config.json", "model_artifacts/tableformer/accurate/weights.safetensors"):
        (base / "blobs" / sha(FILES["org/tables"][rel])).unlink()     # a blob is found by its sha256 name too
        (snap / rel).unlink()
    w = snap / "model_artifacts/tableformer/accurate/weights.safetensors"
    w.write_bytes(b"another revision")                       # same name, other content: not the pinned file
    with pytest.raises(models.ModelsError) as e:
        models.import_from(tmp_path / "hub", tmp_path / "models")
    msg = str(e.value)
    assert "org/tables: config.json" in msg and "weights.safetensors" in msg
    assert f"https://huggingface.co/org/tables/tree/{REV['org/tables']}" in msg
    assert (tmp_path / "models" / "org--layout" / "model.safetensors").is_file()   # what was found is kept


def test_import_again_copies_nothing(tmp_path):
    hf_cache(tmp_path / "hub")
    models.import_from(tmp_path / "hub", tmp_path / "models")
    assert models.import_from(tmp_path / "hub", tmp_path / "models")["copied"] == 0
    assert models.import_from(tmp_path / "models", tmp_path / "models")["copied"] == 0   # already the standard layout


def test_cli_models_import(tmp_path, capsys):
    hf_cache(tmp_path / "hub")
    assert cli.main(["models", "import", str(tmp_path / "hub"), "--to", str(tmp_path / "models")]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["ok"] is True and out["copied"] == 5
    assert cli.main(["models", "import", str(tmp_path / "nowhere"), "--to", str(tmp_path / "m2")]) == 1
