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

import json
import re
import threading
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
_JOB_HANDLERS: dict[str, Callable[[dict], Any]] = {}

VALID_STATUSES = ("queued", "running", "completed", "failed", "cancelled", "interrupted")
MAX_CONCURRENT_ACTIVE_JOBS = 20


class JobQueueCapacityExceeded(Exception):
    """Raised when the background job runner is at capacity with active queued/running jobs."""
    pass


def register_job_handler(job_type: str, handler: Callable[[dict], Any]) -> None:
    """Registers an idempotent handler callable capable of re-executing a job from its stored payload."""
    _JOB_HANDLERS[job_type] = handler


def _sanitize_error_message(err: str) -> str:
    """Strips stack traces, sensitive filesystem paths, and credentials from user-facing error messages."""
    if not err:
        return "Internal job execution error."
    msg = str(err).splitlines()[-1] if "\n" in str(err) else str(err)
    msg = re.sub(r"[A-Za-z]:\\[^:\s]+", "<path>", msg)
    msg = re.sub(r"/[^:\s]+/[^:\s]+", "<path>", msg)
    msg = _db._redact_credentials(msg)
    return msg[:300]


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="investigation-job")
        return _executor


def shutdown_executor(wait: bool = False) -> None:
    """Shuts down the background thread pool, cancelling any pending futures."""
    global _executor
    with _executor_lock:
        if _executor is not None:
            _executor.shutdown(wait=wait, cancel_futures=True)
            _executor = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Job:
    id: str
    job_type: str
    status: str = "queued"
    owner: str = "anonymous"
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    started_at: str | None = None
    finished_at: str | None = None
    progress_stage: str = "queued"
    result_investigation_id: str | None = None
    error: str | None = None
    retry_count: int = 0
    max_retries: int = 0
    payload_json: str | None = None
    _future: Future | None = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "job_type": self.job_type,
            "status": self.status,
            "owner": self.owner,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "progress_stage": self.progress_stage,
            "result_investigation_id": self.result_investigation_id,
            "error": self.error,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
        }


def _persist(job: Job) -> None:
    """Best-effort - a job status write failing should never crash the job
    itself, mirroring app/persistence/db.py's own save_investigation()."""
    try:
        with _db._connect() as conn:
            conn.execute(
                """INSERT INTO jobs (id, job_type, status, owner, created_at, updated_at, started_at,
                                      finished_at, progress_stage, result_investigation_id, error,
                                      retry_count, max_retries, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     status=excluded.status, updated_at=excluded.updated_at, started_at=excluded.started_at,
                     finished_at=excluded.finished_at, progress_stage=excluded.progress_stage,
                     result_investigation_id=excluded.result_investigation_id, error=excluded.error,
                     retry_count=excluded.retry_count, max_retries=excluded.max_retries,
                     payload_json=excluded.payload_json""",
                (job.id, job.job_type, job.status, job.owner, job.created_at, job.updated_at, job.started_at,
                 job.finished_at, job.progress_stage, job.result_investigation_id, job.error,
                 job.retry_count, job.max_retries, job.payload_json),
            )
    except Exception as e:
        _log.warning("job_persist_failed", extra={"job_id": job.id, "error": str(e)})


def _run(job_id: str, fn: Callable[[], Any]) -> None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None or job.status == "cancelled":
            return
        now_ts = _now()
        job.status = "running"
        job.started_at = now_ts
        job.updated_at = now_ts
        job.progress_stage = "running"
    _persist(job)
    try:
        result_inv = fn()
        with _JOBS_LOCK:
            now_ts = _now()
            job.status = "completed"
            job.finished_at = now_ts
            job.updated_at = now_ts
            job.progress_stage = "completed"
            job.result_investigation_id = getattr(result_inv, "id", None)
    except Exception as e:
        safe_msg = _sanitize_error_message(str(e))
        _log.warning("job_failed", extra={"job_id": job_id, "error": safe_msg})
        with _JOBS_LOCK:
            now_ts = _now()
            job.status = "failed"
            job.finished_at = now_ts
            job.updated_at = now_ts
            job.progress_stage = "failed"
            job.error = safe_msg
    _persist(job)


def update_job_progress(job_id: str, stage: str) -> None:
    """Updates the progress_stage of a running job and persists to DB."""
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is not None and job.status == "running":
            job.progress_stage = stage
            job.updated_at = _now()
            _persist(job)


def submit_job(
    job_type: str,
    fn: Callable[[], Any],
    owner: str = "anonymous",
    job_id: str | None = None,
    payload: dict | None = None,
    max_retries: int = 0,
) -> Job:
    """Submits `fn` (a zero-arg callable - callers close over their real
    arguments) to the shared background executor and returns immediately
    with a Job whose id can be polled via get_job()/GET /api/jobs/{id}."""
    with _JOBS_LOCK:
        active_count = sum(1 for j in _JOBS.values() if j.status in ("queued", "running"))
        if active_count >= MAX_CONCURRENT_ACTIVE_JOBS:
            raise JobQueueCapacityExceeded(
                f"Background investigation queue is at capacity ({active_count} active jobs). Please retry shortly."
            )
        if job_id is None:
            job_id = str(uuid.uuid4())
        payload_str = json.dumps(payload) if payload is not None else None
        now_ts = _now()
        job = Job(
            id=job_id,
            job_type=job_type,
            owner=owner,
            created_at=now_ts,
            updated_at=now_ts,
            max_retries=max_retries,
            payload_json=payload_str,
        )
        _JOBS[job_id] = job
    _persist(job)
    try:
        future = _get_executor().submit(_run, job_id, fn)
        job._future = future
    except Exception:
        with _JOBS_LOCK:
            _JOBS.pop(job_id, None)
        raise
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
    except Exception:
        return None
    if row is None:
        return None
    row = dict(row)
    status = row["status"]
    now_ts = _now()
    finished_at = row.get("finished_at")
    err = row.get("error")
    if status in ("queued", "running"):
        status = "interrupted"
        err = err or "Analysis was interrupted by a server restart. Please retry your investigation."
        finished_at = finished_at or now_ts
        try:
            with _db._connect() as conn:
                conn.execute(
                    "UPDATE jobs SET status = ?, error = ?, finished_at = ?, updated_at = ? WHERE id = ?",
                    (status, err, finished_at, now_ts, job_id),
                )
        except Exception:
            pass

    return Job(
        id=row["id"],
        job_type=row["job_type"],
        status=status,
        owner=row.get("owner") or "anonymous",
        created_at=row["created_at"],
        updated_at=row.get("updated_at") or now_ts,
        started_at=row.get("started_at"),
        finished_at=finished_at,
        progress_stage=row.get("progress_stage") or status,
        result_investigation_id=row.get("result_investigation_id"),
        error=err,
        retry_count=row.get("retry_count") or 0,
        max_retries=row.get("max_retries") or 0,
        payload_json=row.get("payload_json"),
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
            now_ts = _now()
            job.status = "cancelled"
            job.finished_at = now_ts
            job.updated_at = now_ts
            job.progress_stage = "cancelled"
    if cancelled:
        _persist(job)
    return cancelled


def recover_interrupted_jobs() -> dict[str, int]:
    """Scans persisted jobs at process startup to recover or cleanly fail
    jobs that were left in 'queued' or 'running' state when the previous
    process terminated.
    """
    summary = {"recovered_retried": 0, "marked_interrupted": 0}
    stale_jobs = []
    try:
        with _db._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status IN ('queued', 'running')"
            ).fetchall()
            for r in rows:
                stale_jobs.append(dict(r))
    except Exception as e:
        _log.warning("recover_interrupted_jobs_query_failed", extra={"error": str(e)})
        return summary

    for row in stale_jobs:
        job_id = row["id"]
        job_type = row["job_type"]
        retry_count = int(row.get("retry_count") or 0)
        max_retries = int(row.get("max_retries") or 0)
        payload_raw = row.get("payload_json")
        payload = json.loads(payload_raw) if payload_raw else None

        handler = _JOB_HANDLERS.get(job_type)
        can_retry = (
            max_retries > 0
            and retry_count < max_retries
            and handler is not None
            and payload is not None
        )

        now_ts = _now()
        if can_retry:
            new_retry_count = retry_count + 1
            with _JOBS_LOCK:
                job = Job(
                    id=job_id,
                    job_type=job_type,
                    status="queued",
                    owner=row.get("owner") or "anonymous",
                    created_at=row["created_at"],
                    updated_at=now_ts,
                    progress_stage="requeued_after_restart",
                    retry_count=new_retry_count,
                    max_retries=max_retries,
                    payload_json=payload_raw,
                )
                _JOBS[job_id] = job
            _persist(job)
            try:
                def _make_retry_work(h, p):
                    return lambda: h(p)

                future = _get_executor().submit(_run, job_id, _make_retry_work(handler, payload))
                job._future = future
                summary["recovered_retried"] += 1
                _log.info("job_recovered_and_retried", extra={"job_id": job_id, "attempt": new_retry_count})
            except Exception as e:
                _log.error("job_retry_submit_failed", extra={"job_id": job_id, "error": str(e)})
        else:
            err_msg = "Analysis was interrupted by a server restart. Please retry your investigation."
            with _JOBS_LOCK:
                job = Job(
                    id=job_id,
                    job_type=job_type,
                    status="interrupted",
                    owner=row.get("owner") or "anonymous",
                    created_at=row["created_at"],
                    started_at=row.get("started_at"),
                    finished_at=now_ts,
                    updated_at=now_ts,
                    progress_stage="interrupted",
                    result_investigation_id=row.get("result_investigation_id"),
                    error=err_msg,
                    retry_count=retry_count,
                    max_retries=max_retries,
                    payload_json=payload_raw,
                )
                _JOBS[job_id] = job
            _persist(job)
            summary["marked_interrupted"] += 1
            _log.info("job_marked_interrupted_after_restart", extra={"job_id": job_id})

    return summary


def cleanup_abandoned_jobs(max_age_hours: float = 24.0) -> int:
    """Removes finished (completed/failed/cancelled/interrupted) jobs older than
    `max_age_hours` from the in-memory dict to bound its size over a long
    process lifetime. Rows already written to SQLite are left alone (they
    are small and useful as an audit trail); this only prunes the
    in-memory `_JOBS` cache. Returns the number of jobs removed."""
    cutoff = datetime.now(timezone.utc).timestamp() - max_age_hours * 3600
    removed = 0
    with _JOBS_LOCK:
        for jid in list(_JOBS.keys()):
            job = _JOBS[jid]
            if job.status in ("completed", "failed", "cancelled", "interrupted") and job.finished_at:
                try:
                    finished_ts = datetime.fromisoformat(job.finished_at).timestamp()
                except ValueError:
                    continue
                if finished_ts < cutoff:
                    del _JOBS[jid]
                    removed += 1
    return removed
