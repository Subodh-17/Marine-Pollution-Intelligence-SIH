"""
Background job processing (Section 8).

A lightweight, IN-PROCESS job runner using stdlib `concurrent.futures.
ThreadPoolExecutor` - deliberately NOT Redis/Celery/Docker, per the
request's own constraint ("do not require Redis, Celery, or Docker just to
run the SIH demo"). This is additive: the existing synchronous
POST /api/spill/detect and POST /api/satellite/upload endpoints are
UNCHANGED and still work exactly as before (nothing in the existing demo
workflow depends on jobs). These endpoints give callers who want it a way
to kick off the same work asynchronously and poll for status instead.

Job status is persisted to the same SQLite database as investigations (see
app/persistence/db.py) so `GET /api/jobs/{id}` still answers correctly
after a restart - though the actual in-flight work itself is naturally
gone if the process restarts mid-job (there is no cross-process resumption
of a running thread), which is disclosed via the `interrupted` status set
by `recover_interrupted_jobs()` (called at app startup - see main.py).

Threads, not processes, because the actual heavy work here (numpy/SciPy
convolutions, PIL/tifffile decode, small-scale PyTorch CPU inference) is
either I/O-bound or releases the GIL for its hot inner loops - a
ProcessPoolExecutor would add pickling overhead and platform-specific
(Windows spawn) complexity for no real benefit at this scale. A single
shared `ThreadPoolExecutor` also naturally caps concurrent heavy work,
which matters more for this app's memory footprint than raw throughput.
"""
from __future__ import annotations

import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from app.config import get_settings
from app.logging_config import get_logger
from app.persistence import db as _db

_log = get_logger("persistence.jobs")

_executor: ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()

_JOBS: dict[str, "Job"] = {}
_JOBS_LOCK = threading.Lock()

VALID_STATUSES = ("queued", "running", "completed", "failed", "cancelled")


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="investigation-job")
        return _executor


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Job:
    id: str
    job_type: str
    status: str = "queued"
    owner: str = "anonymous"
    created_at: str = field(default_factory=_now)
    started_at: str | None = None
    finished_at: str | None = None
    progress_stage: str = "queued"
    result_investigation_id: str | None = None
    error: str | None = None
    _future: Future | None = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "job_type": self.job_type, "status": self.status,
            "owner": self.owner, "created_at": self.created_at, "started_at": self.started_at,
            "finished_at": self.finished_at, "progress_stage": self.progress_stage,
            "result_investigation_id": self.result_investigation_id, "error": self.error,
        }


def _persist(job: Job) -> None:
    """Best-effort - a job status write failing should never crash the job
    itself, mirroring app/persistence/db.py's own save_investigation()."""
    try:
        with _db._connect() as conn:
            conn.execute(
                """INSERT INTO jobs (id, job_type, status, owner, created_at, started_at,
                                      finished_at, progress_stage, result_investigation_id, error)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     status=excluded.status, started_at=excluded.started_at,
                     finished_at=excluded.finished_at, progress_stage=excluded.progress_stage,
                     result_investigation_id=excluded.result_investigation_id, error=excluded.error""",
                (job.id, job.job_type, job.status, job.owner, job.created_at, job.started_at,
                 job.finished_at, job.progress_stage, job.result_investigation_id, job.error),
            )
    except Exception as e:  # pragma: no cover - defensive
        _log.warning("job_persist_failed", extra={"job_id": job.id, "error": str(e)})


def _run(job_id: str, fn: Callable[[], Any]) -> None:
    with _JOBS_LOCK:
        job = _JOBS[job_id]
        job.status = "running"
        job.started_at = _now()
        job.progress_stage = "running"
    _persist(job)
    try:
        result_inv = fn()
        with _JOBS_LOCK:
            job.status = "completed"
            job.finished_at = _now()
            job.progress_stage = "completed"
            job.result_investigation_id = getattr(result_inv, "id", None)
    except Exception as e:
        _log.warning("job_failed", extra={"job_id": job_id, "error": str(e), "traceback": traceback.format_exc()})
        with _JOBS_LOCK:
            job.status = "failed"
            job.finished_at = _now()
            job.progress_stage = "failed"
            job.error = str(e)
    _persist(job)


def submit_job(job_type: str, fn: Callable[[], Any], owner: str = "anonymous") -> Job:
    """Submits `fn` (a zero-arg callable - callers close over their real
    arguments) to the shared background executor and returns immediately
    with a Job whose id can be polled via get_job()/GET /api/jobs/{id}."""
    job_id = str(uuid.uuid4())
    job = Job(id=job_id, job_type=job_type, owner=owner)
    with _JOBS_LOCK:
        _JOBS[job_id] = job
    _persist(job)
    future = _get_executor().submit(_run, job_id, fn)
    job._future = future
    return job


def get_job(job_id: str) -> Job | None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if job is not None:
        return job
    # Fall back to the database (Section 8: "background job status" must
    # survive a restart, same as investigations - see the module docstring
    # for why the underlying work itself cannot be resumed).
    try:
        with _db._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    except Exception:  # pragma: no cover - defensive
        return None
    if row is None:
        return None
    status = row["status"]
    if status in ("queued", "running"):
        # The process that was running this job is gone (this IS a
        # different process/test, or a real restart) - honestly report
        # that the original job's outcome is unknown rather than pretend
        # it's still progressing.
        status = "interrupted"
    return Job(
        id=row["id"], job_type=row["job_type"], status=status, owner=row["owner"] or "anonymous",
        created_at=row["created_at"], started_at=row["started_at"], finished_at=row["finished_at"],
        progress_stage=row["progress_stage"] or status,
        result_investigation_id=row["result_investigation_id"], error=row["error"],
    )


def cancel_job(job_id: str) -> bool:
    """Best-effort cancellation - only succeeds if the job hasn't started
    running yet (ThreadPoolExecutor cannot forcibly interrupt a thread
    already executing Python code, and this codebase does not attempt the
    unsafe workarounds for that - see Python's own `concurrent.futures`
    docs). Returns True if the job was queued and is now cancelled, False
    otherwise (already running/completed/failed, or not found)."""
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None or job._future is None:
            return False
        cancelled = job._future.cancel()
        if cancelled:
            job.status = "cancelled"
            job.finished_at = _now()
            job.progress_stage = "cancelled"
    if cancelled:
        _persist(job)
    return cancelled


def cleanup_abandoned_jobs(max_age_hours: float = 24.0) -> int:
    """Removes finished (completed/failed/cancelled) jobs older than
    `max_age_hours` from the in-memory dict to bound its size over a long
    process lifetime. Rows already written to SQLite are left alone (they
    are small and useful as an audit trail); this only prunes the
    in-memory `_JOBS` cache. Returns the number of jobs removed."""
    cutoff = datetime.now(timezone.utc).timestamp() - max_age_hours * 3600
    removed = 0
    with _JOBS_LOCK:
        for jid in list(_JOBS.keys()):
            job = _JOBS[jid]
            if job.status in ("completed", "failed", "cancelled") and job.finished_at:
                try:
                    finished_ts = datetime.fromisoformat(job.finished_at).timestamp()
                except ValueError:
                    continue
                if finished_ts < cutoff:
                    del _JOBS[jid]
                    removed += 1
    return removed
