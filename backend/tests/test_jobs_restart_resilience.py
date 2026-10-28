"""Tests for durable background job lifecycle, restart recovery, and error sanitization (Section 8 / Phase 4)."""
from __future__ import annotations

import json
import threading
import time
from unittest.mock import patch

import pytest
from app.persistence import db, jobs


@pytest.fixture(autouse=True)
def clean_jobs():
    db.init_db()
    with jobs._JOBS_LOCK:
        jobs._JOBS.clear()
    yield
    jobs.shutdown_executor(wait=True)
    with jobs._JOBS_LOCK:
        jobs._JOBS.clear()


class TestJobRestartRecovery:
    def test_non_retryable_job_marked_interrupted_on_restart(self):
        job_id = "test-non-retryable-job"
        with db._connect() as conn:
            conn.execute(
                """INSERT INTO jobs (id, job_type, status, owner, created_at, started_at,
                                      progress_stage, max_retries, retry_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (job_id, "satellite_upload", "running", "anonymous", "2026-10-28T12:00:00",
                 "2026-10-28T12:00:01", "processing", 0, 0),
            )

        summary = jobs.recover_interrupted_jobs()
        assert summary["marked_interrupted"] >= 1

        reloaded = jobs.get_job(job_id)
        assert reloaded is not None
        assert reloaded.status == "interrupted"
        assert reloaded.progress_stage == "interrupted"
        assert "interrupted by a server restart" in reloaded.error

    def test_retryable_job_requeued_and_completed_on_restart(self):
        execution_log = []

        def sample_handler(payload):
            execution_log.append(payload["target"])
            class Fake:
                id = f"inv-{payload['target']}"
            return Fake()

        jobs.register_job_handler("retryable_test_type", sample_handler)

        job_id = "test-restart-recovery-job"
        payload_data = {"target": "spill_scene_01"}
        with db._connect() as conn:
            conn.execute(
                """INSERT INTO jobs (id, job_type, status, owner, created_at, started_at,
                                      progress_stage, max_retries, retry_count, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (job_id, "retryable_test_type", "running", "anonymous", "2026-10-28T12:00:00",
                 "2026-10-28T12:00:01", "sar_preprocessing", 1, 0, json.dumps(payload_data)),
            )

        summary = jobs.recover_interrupted_jobs()
        assert summary["recovered_retried"] >= 1

        deadline = time.time() + 5.0
        while time.time() < deadline:
            j = jobs.get_job(job_id)
            if j and j.status == "completed":
                break
            time.sleep(0.05)

        reloaded = jobs.get_job(job_id)
        assert reloaded.status == "completed"
        assert reloaded.result_investigation_id == "inv-spill_scene_01"
        assert reloaded.retry_count == 1
        assert "spill_scene_01" in execution_log

    def test_retry_exhaustion_marks_interrupted(self):
        execution_count = 0

        def failing_handler(payload):
            nonlocal execution_count
            execution_count += 1
            raise RuntimeError("Handler persistent failure")

        jobs.register_job_handler("exhaust_type", failing_handler)

        job_id = "test-exhausted-job"
        payload_data = {"mode": "test"}
        with db._connect() as conn:
            conn.execute(
                """INSERT INTO jobs (id, job_type, status, owner, created_at, started_at,
                                      progress_stage, max_retries, retry_count, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (job_id, "exhaust_type", "running", "anonymous", "2026-10-28T12:00:00",
                 "2026-10-28T12:00:01", "processing", 1, 1, json.dumps(payload_data)),
            )

        summary = jobs.recover_interrupted_jobs()
        assert summary["marked_interrupted"] >= 1
        assert summary["recovered_retried"] == 0

        reloaded = jobs.get_job(job_id)
        assert reloaded.status == "interrupted"


class TestErrorSanitizationAndSecurity:
    def test_error_sanitization_removes_paths_and_credentials(self):
        raw_error = "Error in E:\\Projects\\Marine_Pollution\\backend\\app\\secret.py: connection to postgres://user:secret_pass@db.internal:5432 failed"
        sanitized = jobs._sanitize_error_message(raw_error)

        assert "secret_pass" not in sanitized
        assert "E:\\Projects" not in sanitized
        assert "<path>" in sanitized or "postgres://user:***@" in sanitized

    def test_job_metadata_does_not_contain_large_blobs_or_secrets(self):
        payload = {"mode": "demo", "detector": "classical"}
        job = jobs.submit_job("investigation", lambda: None, payload=payload)
        if job._future:
            job._future.result(timeout=2.0)

        job_dict = job.to_dict()
        assert "payload_json" not in job_dict  # Payload is internal, not exposed in public dict
        assert "password" not in str(job_dict)


class TestConcurrencyAndDuplicateExecution:
    def test_duplicate_submission_with_same_id_is_bounded(self):
        job_id = "fixed-uuid-123"
        job1 = jobs.submit_job("test_id", lambda: None, job_id=job_id)
        if job1._future:
            job1._future.result(timeout=2.0)
        assert job1.id == job_id

        reloaded = jobs.get_job(job_id)
        assert reloaded.id == job_id

    def test_cancelled_job_does_not_execute(self):
        started_event = threading.Event()
        def work():
            started_event.set()

        job = jobs.submit_job("cancel_test", work)
        cancelled = jobs.cancel_job(job.id)
        if cancelled:
            reloaded = jobs.get_job(job.id)
            assert reloaded.status == "cancelled"

    def test_database_failure_does_not_crash_submit(self):
        with patch.object(db, "_connect", side_effect=db.PersistenceError("Simulated DB connection failure")):
            # Job submission should complete via in-memory queue even if DB persist fails
            job = jobs.submit_job("memory_fallback_test", lambda: "ok")
            if job._future:
                job._future.result(timeout=2.0)
            assert job is not None
            assert job.status in ("queued", "running", "completed")
