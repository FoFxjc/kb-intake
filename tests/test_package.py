"""kb_intake.package: how packages and work files are written."""


def test_written_files_get_the_mode_of_an_ordinary_file(tmp_path):
    """mkstemp creates 0600: a package written that way could not be read by a consumer running as another user."""
    from kb_intake.package import write_json, write_jsonl
    plain = tmp_path / "plain"
    plain.write_text("x")
    write_jsonl(tmp_path / "a.jsonl", [{"a": 1}])
    write_json(tmp_path / "b.json", {"b": 2})
    want = plain.stat().st_mode & 0o777
    assert (tmp_path / "a.jsonl").stat().st_mode & 0o777 == want
    assert (tmp_path / "b.json").stat().st_mode & 0o777 == want
