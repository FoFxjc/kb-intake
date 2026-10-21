"""Local state of the service: jobs, their step events and per-file results, in one SQLite file."""

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
create table if not exists jobs (
  id text primary key, created_at text not null, kind text not null, input text not null,
  declaration text, options text, status text not null, started_at text, finished_at text, error text);
create table if not exists events (
  job_id text not null, seq integer not null, at text not null, file text, step text not null,
  status text not null, info text, primary key (job_id, seq));
create table if not exists results (
  job_id text not null, seq integer not null, file text, status text not null, package text, detail text,
  primary key (job_id, seq));
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        with self.lock:
            self.db.executescript(SCHEMA)
            # a job left "running" by a stopped server did not finish
            self.db.execute("update jobs set status = 'interrupted', finished_at = ? where status in ('queued', 'running')",
                            (now(),))

    def create_job(self, kind: str, input_: str, declaration: str | None, options: dict) -> str:
        job_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
        with self.lock:
            self.db.execute("insert into jobs (id, created_at, kind, input, declaration, options, status) "
                            "values (?, ?, ?, ?, ?, ?, 'queued')",
                            (job_id, now(), kind, input_, declaration, json.dumps(options)))
        return job_id

    def set_status(self, job_id: str, status: str, error: str | None = None) -> None:
        col = "started_at" if status == "running" else "finished_at"
        with self.lock:
            self.db.execute(f"update jobs set status = ?, {col} = ?, error = coalesce(?, error) where id = ?",
                            (status, now(), error, job_id))

    def event(self, job_id: str, file: str | None, step: str, status: str, info: dict | None = None) -> None:
        with self.lock:
            seq = self.db.execute("select coalesce(max(seq), 0) + 1 from events where job_id = ?", (job_id,)).fetchone()[0]
            self.db.execute("insert into events values (?, ?, ?, ?, ?, ?, ?)",
                            (job_id, seq, now(), file, step, status, json.dumps(info or {}, ensure_ascii=False)))

    def result(self, job_id: str, r: dict) -> None:
        with self.lock:
            seq = self.db.execute("select coalesce(max(seq), 0) + 1 from results where job_id = ?", (job_id,)).fetchone()[0]
            self.db.execute("insert into results values (?, ?, ?, ?, ?, ?)",
                            (job_id, seq, r.get("file"), r["status"], r.get("package"),
                             json.dumps(r, ensure_ascii=False)))

    def jobs(self, limit: int = 50) -> list[dict]:
        with self.lock:
            rows = self.db.execute("select * from jobs order by created_at desc limit ?", (limit,)).fetchall()
        return [_job(r) for r in rows]

    def job(self, job_id: str) -> dict | None:
        with self.lock:
            r = self.db.execute("select * from jobs where id = ?", (job_id,)).fetchone()
            if r is None:
                return None
            events = self.db.execute("select * from events where job_id = ? order by seq", (job_id,)).fetchall()
            results = self.db.execute("select * from results where job_id = ? order by seq", (job_id,)).fetchall()
        out = _job(r)
        out["events"] = [{"seq": e["seq"], "at": e["at"], "file": e["file"], "step": e["step"], "status": e["status"],
                          "info": json.loads(e["info"] or "{}")} for e in events]
        out["results"] = [json.loads(x["detail"]) for x in results]
        return out


def _job(r) -> dict:
    return {"job_id": r["id"], "created_at": r["created_at"], "kind": r["kind"], "input": r["input"],
            "declaration": r["declaration"], "options": json.loads(r["options"] or "{}"), "status": r["status"],
            "started_at": r["started_at"], "finished_at": r["finished_at"], "error": r["error"]}
