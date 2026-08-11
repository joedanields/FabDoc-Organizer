"""Background work with progress and cancellation.

Reading a thousand drawings takes minutes. The desktop app runs that on a worker
thread and reports ``[412/968] Assembly: 17172C172.pdf`` into a progress bar with
a Cancel button beside it; a single blocking POST cannot do either, which is why
the browser version used to sit on a spinner with no way out but closing the tab.

So the long operations - building a register, comparing two package folders - are
started here and polled. The engine is untouched: ``build_register`` already
takes a ``progress`` callback and a ``should_cancel`` predicate, and this module
does nothing but wire them to something a browser can read.
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

# Jobs finished this long ago are dropped, so a day of work does not accumulate
# register snapshots in memory. Generous: the page polls every 400ms, and a user
# who walks away should still find the result when they come back.
KEEP_SECONDS = 30 * 60


@dataclass
class Job:
    id: str
    state: str = "running"          # running | done | cancelled | error
    done: int = 0
    total: int = 0
    label: str = "Starting..."
    result: Any = None
    error: str = ""
    trace: str = ""
    finished_at: float = 0.0
    cancel: threading.Event = field(default_factory=threading.Event)

    def progress(self, done: int, total: int, label: str) -> None:
        self.done, self.total, self.label = done, total, label

    def cancelled(self) -> bool:
        return self.cancel.is_set()

    def payload(self) -> dict[str, Any]:
        return {
            "id": self.id, "state": self.state, "done": self.done,
            "total": self.total, "label": self.label,
            "result": self.result, "error": self.error, "trace": self.trace,
        }


_JOBS: dict[str, Job] = {}
_LOCK = threading.Lock()


def _sweep() -> None:
    cutoff = time.time() - KEEP_SECONDS
    for key in [k for k, j in _JOBS.items() if j.finished_at and j.finished_at < cutoff]:
        _JOBS.pop(key, None)


def start(work: Callable[[Job], Any]) -> Job:
    """Run ``work(job)`` on a worker thread and return the job immediately."""
    job = Job(id=uuid.uuid4().hex[:12])
    with _LOCK:
        _sweep()
        _JOBS[job.id] = job

    def run() -> None:
        try:
            result = work(job)
            # A cancel that lands after the work has already finished does not
            # retroactively undo it. Reporting "cancelled" there would tell the
            # engineer nothing happened while a register had in fact been
            # written - possibly over the previous one. The work signals a real
            # early exit by returning nothing.
            if job.cancelled() and not result:
                job.state = "cancelled"
            else:
                job.result = result
                job.state = "done"
        except Exception as exc:                     # surfaced in the page
            job.state = "error"
            job.error = f"{type(exc).__name__}: {exc}"
            job.trace = traceback.format_exc()[-2000:]
        finally:
            job.finished_at = time.time()

    threading.Thread(target=run, daemon=True).start()
    return job


def get(job_id: str) -> Job | None:
    with _LOCK:
        return _JOBS.get(job_id)


def cancel(job_id: str) -> bool:
    job = get(job_id)
    if job is None or job.state != "running":
        return False
    job.cancel.set()
    job.label = "Cancelling..."
    return True
