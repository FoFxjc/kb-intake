"""The job state machine and its SQLite store (intake-design-v2 §2)."""

import pytest

from kb_intake.jobs import Busy, JobError, Jobs, WrongState

DECL = {"doc_id": "demo-ar-2025-en"}


@pytest.fixture
def jobs(tmp_path):
    return Jobs(tmp_path / "work")


@pytest.fixture
def src(tmp_path):
    p = tmp_path / "a.pdf"
    p.write_bytes(b"%PDF-1.7 demo")
    return p


def _to_review(jobs, job_id):
    with jobs.running(job_id, "label"):
        jobs.advance(job_id, "labeled", op="label")
        jobs.save_workflow(job_id, "name: default\n", op="label")
        jobs.advance(job_id, "planned", op="label")
    jobs.approve(job_id, "peter")
    with jobs.running(job_id, "extract"):
        jobs.advance(job_id, "extracted", op="extract")
        jobs.advance(job_id, "in_review", op="extract")


def test_create_copies_the_source_and_deduplicates_by_sha(jobs, src):
    job, created = jobs.create(src, DECL)
    assert created and job.status == "uploaded" and job.source.read_bytes() == src.read_bytes()
    again, created2 = jobs.create(src, DECL)
    assert not created2 and again.id == job.id


def test_the_happy_path_walks_all_eight_states(jobs, src):
    job, _ = jobs.create(src, DECL)
    _to_review(jobs, job.id)
    assert jobs.get(job.id).status == "in_review"
    jobs.add_ops(job.id, [{"action": "exclude", "region_id": "x"}])
    j = jobs.confirm(job.id, "peter")
    assert j.status == "confirmed" and j.confirmed_by == "peter" and j.confirmed_at
    with jobs.running(job.id, "deliver"):
        jobs.advance(job.id, "delivered", op="deliver", package="demo@abc")
    j = jobs.get(job.id)
    assert j.status == "delivered" and j.package == "demo@abc" and j.workflow_version == 1
    assert jobs.ops(job.id) == [{"action": "exclude", "region_id": "x"}]


def test_a_failing_step_keeps_the_state_and_records_the_error(jobs, src):
    job, _ = jobs.create(src, DECL)
    with pytest.raises(RuntimeError):
        with jobs.running(job.id, "label"):
            raise RuntimeError("boom")
    j = jobs.get(job.id)
    assert j.status == "uploaded" and j.running_op is None and "boom" in j.last_error
    with jobs.running(job.id, "label"):          # the retry
        jobs.advance(job.id, "labeled", op="label")
    assert jobs.get(job.id).last_error is None


def test_a_step_cannot_run_twice_or_in_the_wrong_state(jobs, src):
    job, _ = jobs.create(src, DECL)
    with jobs.running(job.id, "label"):
        with pytest.raises(Busy):
            with jobs.running(job.id, "label"):
                pass
    with pytest.raises(WrongState):
        with jobs.running(job.id, "extract"):
            pass


def test_approval_needs_a_workflow_and_a_name(jobs, src):
    job, _ = jobs.create(src, DECL)
    with jobs.running(job.id, "label"):
        jobs.advance(job.id, "labeled", op="label")
        jobs.advance(job.id, "planned", op="label")
    with pytest.raises(JobError):
        jobs.approve(job.id, "peter")
    jobs.save_workflow(job.id, "v1")
    jobs.save_workflow(job.id, "v2")
    with pytest.raises(JobError):
        jobs.approve(job.id, " ")
    j = jobs.approve(job.id, "peter")
    assert j.workflow_version == 2 and jobs.workflow(job.id, 1)["text"] == "v1"
    with pytest.raises(WrongState):
        jobs.save_workflow(job.id, "v3")        # the approved workflow is frozen


def test_review_ops_only_while_in_review_and_confirm_freezes(jobs, src):
    job, _ = jobs.create(src, DECL)
    with pytest.raises(WrongState):
        jobs.add_ops(job.id, [{"action": "exclude"}])
    _to_review(jobs, job.id)
    jobs.confirm(job.id, "peter")
    with pytest.raises(WrongState):
        jobs.add_ops(job.id, [{"action": "exclude"}])


def test_recover_clears_steps_of_a_stopped_process(tmp_path, src):
    a = Jobs(tmp_path / "work")
    job, _ = a.create(src, DECL)
    a.db.execute("update jobs set running_op = 'label' where id = ?", (job.id,))     # the process died here
    b = Jobs(tmp_path / "work")                                                       # a restarted server
    assert b.recover() == [job.id]
    j = b.get(job.id)
    assert j.status == "uploaded" and j.running_op is None and "interrupted" in j.last_error


def test_two_stores_on_one_file_see_each_other(tmp_path, src):
    a, b = Jobs(tmp_path / "work"), Jobs(tmp_path / "work")      # the CLI and the server
    job, _ = a.create(src, DECL)
    with a.running(job.id, "label"):
        with pytest.raises(Busy):
            with b.running(job.id, "label"):
                pass


def test_events_are_ordered(jobs, src):
    job, _ = jobs.create(src, DECL)
    jobs.event(job.id, "label", "running")
    jobs.event(job.id, "label", "done", {"seconds": 1.5})
    ev = jobs.events(job.id)
    assert [e["status"] for e in ev] == ["running", "done"] and jobs.events(job.id, after=1)[0]["info"]["seconds"] == 1.5


def test_finish_label_and_multi_step_advance_are_one_transaction(jobs, src, monkeypatch):
    job, _ = jobs.create(src, DECL)
    with jobs.running(job.id, "label"):
        j = jobs.finish_label(job.id, "name: default\n", {**DECL, "lang": "en"})
    assert j.status == "planned" and j.declaration["lang"] == "en" and jobs.workflow(job.id)["version"] == 1
    jobs.approve(job.id, "peter")
    with pytest.raises(ValueError):
        jobs.advance(job.id, ("extracted", "confirmed"), op="extract")     # not consecutive
    with jobs.running(job.id, "extract"):
        assert jobs.advance(job.id, ("extracted", "in_review"), op="extract").status == "in_review"


def test_a_failing_finish_label_leaves_the_job_uploaded(jobs, src):
    job, _ = jobs.create(src, DECL)
    with pytest.raises(Exception):
        with jobs.running(job.id, "label"):
            jobs.finish_label(job.id, None)          # NOT NULL text: the insert fails after the status update
    j = jobs.get(job.id)
    assert j.status == "uploaded" and jobs.workflow(job.id) is None


def test_recover_leaves_steps_of_a_live_process_or_another_host(tmp_path, src):
    import socket
    import subprocess
    import sys
    a = Jobs(tmp_path / "work")
    live, _ = a.create(src, DECL)
    other = tmp_path / "b.pdf"
    other.write_bytes(b"%PDF-1.7 other")
    remote, _ = a.create(other, DECL)
    dead_src = tmp_path / "c.pdf"
    dead_src.write_bytes(b"%PDF-1.7 dead")
    dead, _ = a.create(dead_src, DECL)
    cli = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])     # a CLI process still running
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    try:
        host = socket.gethostname()
        for job, owner in ((live, f"{host}:{cli.pid}"), (remote, "elsewhere:1"), (dead, f"{host}:{gone.pid}")):
            a.db.execute("update jobs set running_op = 'label', running_owner = ? where id = ?", (owner, job.id))
        assert Jobs(tmp_path / "work").recover() == [dead.id]
        assert a.get(live.id).running_op == "label" and a.get(remote.id).running_op == "label"
    finally:
        cli.kill()


def test_a_state_db_without_running_owner_is_upgraded(tmp_path, src):
    import sqlite3
    (tmp_path / "work").mkdir()
    db = sqlite3.connect(tmp_path / "work" / "state.db")
    db.executescript(SCHEMA_WITHOUT_OWNER)
    db.close()
    jobs = Jobs(tmp_path / "work")
    job, _ = jobs.create(src, DECL)
    with jobs.running(job.id, "label"):
        assert jobs.db.execute("select running_owner from jobs").fetchone()[0]


SCHEMA_WITHOUT_OWNER = """
create table jobs (
  id text primary key, created_at text not null, updated_at text not null, status text not null,
  running_op text, last_error text, sha256 text not null, doc_id text not null, source_name text not null,
  declaration text not null, approved_by text, approved_at text, workflow_version integer,
  confirmed_by text, confirmed_at text, package text);
"""
