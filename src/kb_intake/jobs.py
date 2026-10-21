"""Jobs and their states (intake-design-v2 §2), in one SQLite file shared by the CLI and the server.

    uploaded -> labeled -> planned -> approved -> extracted -> in_review -> confirmed -> delivered

- `status` is always one of these eight states. A step that is running is recorded in `running_op` ("label",
  "extract", "deliver"); it is set with a compare-and-set, so two processes (the CLI and `serve`) can never run the
  same step of a job twice.
- A failing step leaves the job in the state it started from, with `last_error`; running it again is the retry.
- `recover()` (at server start) clears a `running_op` left by a process that stopped, recording "interrupted".
  The state is unchanged, so the step can simply be run again; Docling results are cached, so a conversion that had
  completed is not redone.
- Each job has a directory `work_dir/jobs/<job_id>/` with `source.pdf` and the outputs of each step.
"""

import json
import shutil
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from kb_intake.package import sha_file

STATES = ("uploaded", "labeled", "planned", "approved", "extracted", "in_review", "confirmed", "delivered")
NEXT = {"uploaded": "labeled", "labeled": "planned", "planned": "approved", "approved": "extracted",
        "extracted": "in_review", "in_review": "confirmed", "confirmed": "delivered"}
# the state a running step starts from
OPS = {"label": "uploaded", "extract": "approved", "deliver": "confirmed"}

SCHEMA = """
create table if not exists jobs (
  id text primary key, created_at text not null, updated_at text not null, status text not null,
  running_op text, last_error text, sha256 text not null, doc_id text not null, source_name text not null,
  declaration text not null, approved_by text, approved_at text, workflow_version integer,
  confirmed_by text, confirmed_at text, package text);
create table if not exists workflows (
  job_id text not null, version integer not null, created_at text not null, text text not null,
  primary key (job_id, version));
create table if not exists review_ops (
  job_id text not null, seq integer not null, op text not null, primary key (job_id, seq));
create table if not exists events (
  job_id text not null, seq integer not null, at text not null, step text not null, status text not null,
  info text, primary key (job_id, seq));
"""


class JobError(ValueError):
    pass


class WrongState(JobError):
    pass


class Busy(JobError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class Job:
    id: str
    status: str
    running_op: str | None
    last_error: str | None
    sha256: str
    doc_id: str
    source_name: str
    declaration: dict
    created_at: str
    updated_at: str
    approved_by: str | None
    approved_at: str | None
    workflow_version: int | None
    confirmed_by: str | None
    confirmed_at: str | None
    package: str | None
    dir: Path

    @property
    def source(self) -> Path:
        return self.dir / "source.pdf"

    def to_dict(self) -> dict:
        d = {k: getattr(self, k) for k in self.__dataclass_fields__ if k != "dir"}
        return {**d, "dir": str(self.dir)}


class Jobs:
    def __init__(self, work_dir: Path):
        self.work_dir = work_dir
        work_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.db = sqlite3.connect(work_dir / "state.db", check_same_thread=False, isolation_level=None, timeout=30)
        self.db.row_factory = sqlite3.Row
        with self._lock:
            self.db.execute("pragma journal_mode=wal")
            self.db.executescript(SCHEMA)

    # ---------- reading ----------

    def _job(self, r) -> Job:
        return Job(id=r["id"], status=r["status"], running_op=r["running_op"], last_error=r["last_error"],
                   sha256=r["sha256"], doc_id=r["doc_id"], source_name=r["source_name"],
                   declaration=json.loads(r["declaration"]), created_at=r["created_at"], updated_at=r["updated_at"],
                   approved_by=r["approved_by"], approved_at=r["approved_at"], workflow_version=r["workflow_version"],
                   confirmed_by=r["confirmed_by"], confirmed_at=r["confirmed_at"], package=r["package"],
                   dir=self.work_dir / "jobs" / r["id"])

    def get(self, job_id: str) -> Job:
        with self._lock:
            r = self.db.execute("select * from jobs where id = ?", (job_id,)).fetchone()
        if r is None:
            raise KeyError(job_id)
        return self._job(r)

    def recent(self, limit: int = 100) -> list[Job]:
        with self._lock:
            rows = self.db.execute("select * from jobs order by created_at desc, id desc limit ?", (limit,)).fetchall()
        return [self._job(r) for r in rows]

    # ---------- writing ----------

    @contextmanager
    def _tx(self):
        """One immediate transaction (a write lock across processes)."""
        with self._lock:
            self.db.execute("begin immediate")
            try:
                yield self.db
                self.db.execute("commit")
            except BaseException:
                self.db.execute("rollback")
                raise

    def create(self, source: Path, declaration: dict) -> tuple[Job, bool]:
        """(job, created). A file whose sha256 already has a job that is not delivered returns that job."""
        sha = sha_file(source)
        with self._lock:
            r = self.db.execute("select id from jobs where sha256 = ? and status != 'delivered' "
                                "order by created_at limit 1", (sha,)).fetchone()
        if r is not None:
            return self.get(r["id"]), False
        job_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
        d = self.work_dir / "jobs" / job_id
        d.mkdir(parents=True)
        shutil.copyfile(source, d / "source.pdf")
        t = now()
        with self._tx() as db:
            db.execute("insert into jobs (id, created_at, updated_at, status, sha256, doc_id, source_name, declaration)"
                       " values (?, ?, ?, 'uploaded', ?, ?, ?, ?)",
                       (job_id, t, t, sha, declaration["doc_id"], source.name, json.dumps(declaration)))
        return self.get(job_id), True

    def update_declaration(self, job_id: str, declaration: dict) -> None:
        """Only the label step uses this (an omitted lang is filled from detection)."""
        with self._tx() as db:
            db.execute("update jobs set declaration = ?, updated_at = ? where id = ?",
                       (json.dumps(declaration), now(), job_id))

    def _require(self, db, job_id: str, states: tuple[str, ...], idle: bool = True):
        r = db.execute("select * from jobs where id = ?", (job_id,)).fetchone()
        if r is None:
            raise KeyError(job_id)
        if r["status"] not in states:
            raise WrongState(f"job {job_id} is {r['status']}; this needs {' or '.join(states)}")
        if idle and r["running_op"]:
            raise Busy(f"job {job_id} is running {r['running_op']}")
        return r

    @contextmanager
    def running(self, job_id: str, op: str):
        """Mark `op` as running on the job (it must be in OPS[op] and idle) for the duration of the block.
        An exception records last_error and leaves the status as it was; it is re-raised."""
        with self._tx() as db:
            self._require(db, job_id, (OPS[op],))
            db.execute("update jobs set running_op = ?, last_error = null, updated_at = ? where id = ?",
                       (op, now(), job_id))
        try:
            yield self.get(job_id)
        except BaseException as e:
            with self._tx() as db:
                db.execute("update jobs set running_op = null, last_error = ?, updated_at = ? where id = ?",
                           (f"{op}: {type(e).__name__}: {e}"[:1000], now(), job_id))
            raise
        with self._tx() as db:
            db.execute("update jobs set running_op = null, updated_at = ? where id = ?", (now(), job_id))

    def advance(self, job_id: str, to: str, op: str | None = None, **fields) -> Job:
        """Move the job to the next state `to` (only NEXT transitions), setting extra columns in the same write.
        A running step passes its own `op`; any other running step makes this Busy."""
        allowed = {"approved_by", "approved_at", "workflow_version", "confirmed_by", "confirmed_at", "package"}
        if set(fields) - allowed:
            raise ValueError(f"unknown job fields {sorted(set(fields) - allowed)}")
        frm = next(s for s, n in NEXT.items() if n == to)
        with self._tx() as db:
            r = self._require(db, job_id, (frm,), idle=False)
            if r["running_op"] and r["running_op"] != op:
                raise Busy(f"job {job_id} is running {r['running_op']}")
            sets = ", ".join(f"{k} = ?" for k in fields)
            db.execute(f"update jobs set status = ?, updated_at = ?{', ' + sets if sets else ''} where id = ?",
                       (to, now(), *fields.values(), job_id))
        return self.get(job_id)

    def recover(self) -> list[str]:
        """Clear the running_op of steps whose process stopped; return those job ids."""
        with self._tx() as db:
            rows = db.execute("select id, running_op from jobs where running_op is not null").fetchall()
            for r in rows:
                db.execute("update jobs set running_op = null, last_error = ?, updated_at = ? where id = ?",
                           (f"{r['running_op']}: interrupted (the process stopped); run it again", now(), r["id"]))
        return [r["id"] for r in rows]

    # ---------- workflow (§3) ----------

    def save_workflow(self, job_id: str, text: str, op: str | None = None) -> int:
        """Store a new version of the job's workflow (YAML text, validated by the caller); only before approval.
        The label step stores the suggested draft while it runs and passes op="label"."""
        with self._tx() as db:
            r = self._require(db, job_id, ("labeled", "planned"), idle=False)
            if r["running_op"] and r["running_op"] != op:
                raise Busy(f"job {job_id} is running {r['running_op']}")
            v = db.execute("select coalesce(max(version), 0) + 1 from workflows where job_id = ?",
                           (job_id,)).fetchone()[0]
            db.execute("insert into workflows values (?, ?, ?, ?)", (job_id, v, now(), text))
        return v

    def workflow(self, job_id: str, version: int | None = None) -> dict | None:
        """{"version", "created_at", "text"} of the given or latest version; None when there is none."""
        with self._lock:
            if version is None:
                r = self.db.execute("select * from workflows where job_id = ? order by version desc limit 1",
                                    (job_id,)).fetchone()
            else:
                r = self.db.execute("select * from workflows where job_id = ? and version = ?",
                                    (job_id, version)).fetchone()
        return None if r is None else {"version": r["version"], "created_at": r["created_at"], "text": r["text"]}

    def approve(self, job_id: str, by: str, version: int | None = None) -> Job:
        if not by.strip():
            raise JobError("approval needs a name (by)")
        wf = self.workflow(job_id, version)
        if wf is None:
            raise JobError(f"job {job_id} has no workflow version {version or ''}".strip())
        return self.advance(job_id, "approved", approved_by=by, approved_at=now(), workflow_version=wf["version"])

    # ---------- review (§4) ----------

    def add_ops(self, job_id: str, ops: list[dict]) -> None:
        """Append review operations (already applied by kb_intake.review); only while in review."""
        with self._tx() as db:
            self._require(db, job_id, ("in_review",))
            seq = db.execute("select coalesce(max(seq), 0) from review_ops where job_id = ?", (job_id,)).fetchone()[0]
            for i, op in enumerate(ops, 1):
                db.execute("insert into review_ops values (?, ?, ?)", (job_id, seq + i, json.dumps(op)))

    def ops(self, job_id: str) -> list[dict]:
        with self._lock:
            rows = self.db.execute("select op from review_ops where job_id = ? order by seq", (job_id,)).fetchall()
        return [json.loads(r["op"]) for r in rows]

    def confirm(self, job_id: str, by: str) -> Job:
        if not by.strip():
            raise JobError("confirmation needs a name (by)")
        return self.advance(job_id, "confirmed", confirmed_by=by, confirmed_at=now())

    # ---------- progress events (UI) ----------

    def event(self, job_id: str, step: str, status: str, info: dict | None = None) -> None:
        with self._tx() as db:
            seq = db.execute("select coalesce(max(seq), 0) + 1 from events where job_id = ?", (job_id,)).fetchone()[0]
            db.execute("insert into events values (?, ?, ?, ?, ?, ?)",
                       (job_id, seq, now(), step, status, json.dumps(info or {}, ensure_ascii=False)))

    def events(self, job_id: str, after: int = 0) -> list[dict]:
        with self._lock:
            rows = self.db.execute("select * from events where job_id = ? and seq > ? order by seq",
                                   (job_id, after)).fetchall()
        return [{"seq": r["seq"], "at": r["at"], "step": r["step"], "status": r["status"],
                 "info": json.loads(r["info"] or "{}")} for r in rows]
