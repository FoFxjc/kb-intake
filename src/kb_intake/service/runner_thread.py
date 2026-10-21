"""One background thread for the steps the server starts (label, extract, deliver): one step at a time across all
jobs; starting another while one runs is refused with jobs.Busy (409).

The step modules record their own progress events and, through `jobs.running`, the job's running_op and
last_error, so this thread writes nothing to the job store. It only remembers the outcome of the last step for
/v1/health (an exception raised before a step could mark its job, e.g. Busy because the CLI runs that job).
"""

import threading
import traceback
from collections.abc import Callable

from kb_intake.jobs import Busy


class StepRunner:
    def __init__(self):
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.current: dict | None = None        # {"job_id", "step"} while a step runs
        self.last: dict | None = None           # {"job_id", "step", "ok", "error"} of the last finished step

    def busy(self) -> dict | None:
        with self._lock:
            return dict(self.current) if self.current else None

    def start(self, step: str, prepare: Callable[[], tuple[str, Callable[[], object]]]) -> str:
        """Run `prepare()` -> (job_id, work) while holding the slot, then `work()` in the background thread.
        Raises Busy when a step is running; anything `prepare` raises propagates and nothing starts."""
        with self._lock:
            if self.current is not None:
                raise Busy(f"job {self.current['job_id']} is running {self.current['step']}; one step at a time")
            job_id, work = prepare()
            self.current = {"job_id": job_id, "step": step}
            self._thread = threading.Thread(target=self._run, args=(job_id, step, work), daemon=True,
                                            name=f"kb-intake-{step}")
            self._thread.start()
        return job_id

    def _run(self, job_id: str, step: str, work: Callable[[], object]) -> None:
        last = {"job_id": job_id, "step": step, "ok": True, "error": None}
        try:
            work()
        except Exception as e:  # noqa: BLE001 - recorded on the job by the step itself; kept here for /health
            last.update(ok=False, error=f"{type(e).__name__}: {e}"[:1000])
            traceback.print_exc()
        finally:
            with self._lock:
                self.current, self.last = None, last

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for the running step (tests, shutdown); True when nothing runs any more."""
        t = self._thread
        if t is not None:
            t.join(timeout)
        return self.busy() is None


class Background:
    """Work that must not hold the step slot (the LLM summary after labelling, intake-ui-llm-design-v1 §4.2): each
    `start` runs on its own daemon thread; failures are printed, the work records its own outcome."""

    def __init__(self):
        self._threads: list[threading.Thread] = []

    def start(self, name: str, work: Callable[[], object]) -> None:
        def run():
            try:
                work()
            except Exception:  # noqa: BLE001 - the summary marks itself failed in llm/summary.json before raising
                traceback.print_exc()
        t = threading.Thread(target=run, daemon=True, name=f"kb-intake-{name}")
        self._threads.append(t)
        t.start()

    def wait(self, timeout: float | None = None) -> bool:
        for t in list(self._threads):
            t.join(timeout)
        return not any(t.is_alive() for t in self._threads)
