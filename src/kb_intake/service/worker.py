"""One job at a time, in a background thread of the server process (intake-design §3: no queue, no concurrency).

A job scans a file set (an upload) or a directory on the server and records every step of every file as an event,
so the run view can show progress while it happens.
"""

import threading
import traceback
from pathlib import Path

import yaml

from kb_intake import scan
from kb_intake.service.store import Store


class Busy(RuntimeError):
    pass


class Worker:
    def __init__(self, store: Store, packages: Path, config_loader):
        self.store, self.packages, self.config_loader = store, packages, config_loader
        self._lock = threading.Lock()
        self.current: str | None = None
        self.thread: threading.Thread | None = None

    def submit(self, job_id: str, target: Path, declaration_text: str | None, options: dict) -> None:
        """Start the job now, or raise Busy when another job runs."""
        with self._lock:
            if self.current is not None:
                raise Busy(f"job {self.current} is running")
            self.current = job_id
        self.thread = threading.Thread(target=self._run, args=(job_id, target, declaration_text, options),
                                       name=f"intake-job-{job_id}", daemon=True)
        self.thread.start()

    def _run(self, job_id: str, target: Path, declaration_text: str | None, options: dict) -> None:
        store = self.store
        try:
            store.set_status(job_id, "running")
            decl = yaml.safe_load(declaration_text) if declaration_text else None
            cfg = self.config_loader()
            failed = 0

            def progress(file, step, status, info):
                store.event(job_id, file, step, status, info)
            roots = cfg.scan_roots if options.get("directory_job") else None
            for r in scan.scan(target, decl, self.packages, cfg, replace=bool(options.get("replace")),
                               recursive=bool(options.get("recursive")), progress=progress, roots=roots):
                store.result(job_id, r)
                failed += r["status"] == "failed"
            store.set_status(job_id, "failed" if failed else "done",
                             error=f"{failed} file(s) failed" if failed else None)
        except Exception as e:  # noqa: BLE001 - recorded on the job; the server keeps running
            store.event(job_id, None, "job", "failed", {"error": f"{type(e).__name__}: {e}",
                                                        "trace": traceback.format_exc()[-1500:]})
            store.set_status(job_id, "failed", error=f"{type(e).__name__}: {e}"[:500])
        finally:
            with self._lock:
                self.current = None
