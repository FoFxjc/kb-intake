"""Cache and fault isolation of an engine run (kb_intake.runner, v1 §9.3)."""

import pytest
from fakes import fake_engine

from kb_intake import runner

SHA = "a" * 64


@pytest.fixture
def pdf(tmp_path):
    p = tmp_path / "x.pdf"
    p.write_bytes(b"%PDF-1.7")
    return p


def test_success_is_cached_and_replayed_byte_for_byte(tmp_path, pdf):
    eng = fake_engine(tmp_path)
    first = runner.run(eng, pdf, SHA, tmp_path / "cache", 60)
    again = runner.run(eng, pdf, SHA, tmp_path / "cache", 60)
    assert first.complete and first.cache_written and not first.cached
    assert again.cached and again.data == first.data and again.clusters == first.clusters
    assert (tmp_path / "calls.txt").read_text().count("ok") == 1


def test_stages_do_not_share_cache_entries(tmp_path, pdf):
    a = runner.run(fake_engine(tmp_path, stage="label"), pdf, SHA, tmp_path / "cache", 60)
    b = runner.run(fake_engine(tmp_path, stage="extract"), pdf, SHA, tmp_path / "cache", 60)
    assert a.cache_key != b.cache_key and not b.cached


def test_a_hang_is_killed_at_the_wall_clock_limit(tmp_path, pdf):
    out = runner.run(fake_engine(tmp_path, "hang"), pdf, SHA, tmp_path / "cache", 3)
    assert out.status == "timeout" and out.data is None and not out.cache_written


@pytest.mark.parametrize("behaviour,status", [("crash", "crashed"), ("silent_exit", "crashed"), ("raise", "error")])
def test_a_failing_child_is_reported_not_raised(tmp_path, pdf, behaviour, status):
    out = runner.run(fake_engine(tmp_path, behaviour), pdf, SHA, tmp_path / "cache", 60)
    assert out.status == status and out.data is None


def test_partial_results_and_unready_engines_are_not_cached(tmp_path, pdf):
    part = runner.run(fake_engine(tmp_path, "failed_page"), pdf, SHA, tmp_path / "cache", 60)
    assert part.status == "partial_success" and part.failed_pages == [2] and not part.cache_written
    nr = runner.run(fake_engine(tmp_path, "not_ready"), pdf, SHA, tmp_path / "cache", 60)
    assert nr.status == "not_ready" and nr.data is None
