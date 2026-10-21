"""Jobs and their states (intake-design-v2 §2), in one SQLite file shared by the CLI and the server.

    uploaded -> labeled -> planned -> approved -> extracted -> in_review -> confirmed -> delivered

- `status` is always one of these eight states. A step that is running is recorded in `running_op` ("label",
  "extract", "deliver"); it is set with a compare-and-set, so two processes (the CLI and `serve`) can never run the
  same step of a job twice.
- A failing step leaves the job in the state it started from, with `last_error`; running it again is the retry.
- `running_owner` is "<host>:<pid>" of the process running the step. `recover()` (at server start) clears a
  `running_op` whose owner process is gone (same host, pid not alive), recording "interrupted"; a step that a live
  CLI process is running is left alone.
  The state is unchanged, so the step can simply be run again; Docling results are cached, so a conversion that had
  completed is not redone.
- Each job has a directory `work_dir/jobs/<job_id>/` with `source.pdf` and the outputs of each step.
- Progress events: the step modules (label, extract, deliver) write them with `event()`; a caller's `progress`
  callback is only for live display (the CLI) and must not write events again.
"""

import json
import os
import shutil
import socket
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
OPS = {"label": "uploaded", "extract": "approved", "review": "in_review", "deliver": "confirmed"}

SCHEMA = """
create table if not exists jobs (
  id text primary key, created_at text not null, updated_at text not null, status text not null,
  running_op text, running_owner text, last_error text, sha256 text not null, doc_id text not null, source_name text not null,
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


class AlreadyDelivered(JobError):
    pass


def owner() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _owner_gone(owner_: str | None) -> bool:
    if not owner_:
        return True
    host, _, pid = owner_.rpartition(":")
    if host != socket.gethostname() or not pid.isdigit():
        return False                    # another machine sharing the work directory: cannot tell
    if int(pid) == os.getpid():
        return True                     # this process is starting up: nothing of it is running yet
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


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
            cols = {r[1] for r in self.db.execute("pragma table_info(jobs)")}
            if "running_owner" not in cols:          # a state.db written before the column existed
                self.db.execute("alter table jobs add column running_owner text")

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

    def create(self, source: Path, declaration: dict, delivery_dir: Path | None = None) -> tuple[Job, bool]:
        """(job, created). A file whose sha256 already has a job that is not delivered returns that job (and
        `declaration` is not used). A file already delivered (a delivered job here, or a package of this sha256 in
        `delivery_dir`) raises AlreadyDelivered: a delivered package is never replaced, so a new job could not end."""
        sha = sha_file(source)
        with self._lock:
            r = self.db.execute("select id from jobs where sha256 = ? and status != 'delivered' "
                                "order by created_at limit 1", (sha,)).fetchone()
            done = self.db.execute("select id, package from jobs where sha256 = ? and status = 'delivered' "
                                   "limit 1", (sha,)).fetchone()
        if r is not None:
            return self.get(r["id"]), False
        if done is not None:
            raise AlreadyDelivered(f"this file was already delivered as {done['package']} (job {done['id']})")
        if delivery_dir is not None:
            for p in sorted(delivery_dir.glob(f"*@{sha[:12]}")):
                if p.is_dir() and not p.name.startswith("."):
                    raise AlreadyDelivered(f"this file was already delivered as {p.name}")
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
            db.execute("update jobs set running_op = ?, running_owner = ?, last_error = null, updated_at = ? "
                       "where id = ?", (op, owner(), now(), job_id))
        try:
            yield self.get(job_id)
        except BaseException as e:
            with self._tx() as db:
                db.execute("update jobs set running_op = null, running_owner = null, last_error = ?, updated_at = ? "
                           "where id = ?", (f"{op}: {type(e).__name__}: {e}"[:1000], now(), job_id))
            raise
        with self._tx() as db:
            db.execute("update jobs set running_op = null, running_owner = null, updated_at = ? where id = ?",
                       (now(), job_id))

    def advance(self, job_id: str, to: str | tuple[str, ...], op: str | None = None, **fields) -> Job:
        """Move the job along NEXT to `to` (one state, or consecutive states passed through in one transaction, so a
        failure can never leave the job in between), setting extra columns in the same write. A running step passes
        its own `op`; any other running step makes this Busy."""
        path = (to,) if isinstance(to, str) else tuple(to)
        allowed = {"approved_by", "approved_at", "workflow_version", "confirmed_by", "confirmed_at", "package"}
        if set(fields) - allowed:
            raise ValueError(f"unknown job fields {sorted(set(fields) - allowed)}")
        with self._tx() as db:
            self._advance(db, job_id, path, op, fields)
        return self.get(job_id)

    def _advance(self, db, job_id: str, path: tuple[str, ...], op: str | None, fields: dict) -> None:
        frm = next((s for s, n in NEXT.items() if n == path[0]), None)
        if frm is None or any(NEXT.get(a) != b for a, b in zip(path, path[1:])):
            raise ValueError(f"not a path of consecutive states: {path}")
        r = self._require(db, job_id, (frm,), idle=False)
        if r["running_op"] and r["running_op"] != op:
            raise Busy(f"job {job_id} is running {r['running_op']}")
        sets = ", ".join(f"{k} = ?" for k in fields)
        db.execute(f"update jobs set status = ?, updated_at = ?{', ' + sets if sets else ''} where id = ?",
                   (path[-1], now(), *fields.values(), job_id))

    def finish_label(self, job_id: str, workflow_text: str, declaration: dict | None = None) -> Job:
        """The end of the label step in one transaction: the filled declaration (if any), uploaded -> labeled, the
        suggested workflow as version 1 (or the next version), -> planned."""
        with self._tx() as db:
            self._advance(db, job_id, ("labeled", "planned"), "label", {})
            if declaration is not None:
                db.execute("update jobs set declaration = ? where id = ?", (json.dumps(declaration), job_id))
            v = db.execute("select coalesce(max(version), 0) + 1 from workflows where job_id = ?",
                           (job_id,)).fetchone()[0]
            db.execute("insert into workflows values (?, ?, ?, ?)", (job_id, v, now(), workflow_text))
        return self.get(job_id)

    def recover(self) -> list[str]:
        """Clear the running_op of steps whose process stopped; return those job ids. A step owned by a live process
        on this host (e.g. a CLI run), or by another host, is left alone."""
        cleared = []
        with self._tx() as db:
            rows = db.execute("select id, running_op, running_owner from jobs where running_op is not null").fetchall()
            for r in rows:
                if not _owner_gone(r["running_owner"]):
                    continue
                db.execute("update jobs set running_op = null, running_owner = null, last_error = ?, updated_at = ? "
                           "where id = ?",
                           (f"{r['running_op']}: interrupted (the process stopped); run it again", now(), r["id"]))
                cleared.append(r["id"])
        return cleared

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
        if self.get(job_id).status != "planned":
            raise WrongState(f"job {job_id} is {self.get(job_id).status}; approval needs planned")
        wf = self.workflow(job_id, version)
        if wf is None:
            raise JobError(f"job {job_id} has no workflow version {version or ''}".strip())
        return self.advance(job_id, "approved", approved_by=by, approved_at=now(), workflow_version=wf["version"])

    # ---------- review (§4) ----------

    def add_ops(self, job_id: str, ops: list[dict], op: str | None = None) -> None:
        """Append review operations (applied by kb_intake.review, which passes op="review"); only while in review."""
        with self._tx() as db:
            r = self._require(db, job_id, ("in_review",), idle=False)
            if r["running_op"] and r["running_op"] != op:
                raise Busy(f"job {job_id} is running {r['running_op']}")
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
