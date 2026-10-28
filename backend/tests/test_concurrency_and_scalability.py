"""
Concurrency and Scalability Regression Tests (Phase 8).

Verifies multi-user performance, thread safety, bounded job queueing,
and SQLite WAL mode concurrency under simultaneous load.
"""
import concurrent.futures
import threading
import time
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.ml.detector import MLDetectorEngine
from app.persistence import db as _db, jobs as jobs_module

CLIENT = TestClient(app)


class TestConcurrentApiLoad:
    def test_concurrent_investigation_requests(self):
        """Simulates 5 concurrent users requesting demo investigations simultaneously."""
        def run_one(i):
            t0 = time.perf_counter()
            r = CLIENT.post("/api/spill/detect", params={"mode": "demo"})
            dt = time.perf_counter() - t0
            assert r.status_code == 200
            inv = r.json()
            assert inv["id"]
            assert inv["satellite"]["scene_classification"]["scores"]
            return dt

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
            latencies = list(ex.map(run_one, range(5)))

        assert len(latencies) == 5
        # Verify all requests succeeded promptly
        assert all(dt < 10.0 for dt in latencies)

    def test_concurrent_sqlite_wal_reads_and_writes(self):
        """Verifies that WAL mode prevents database lock errors when readers and writers collide."""
        def writer(i):
            with _db._connect() as conn:
                conn.execute(
                    "INSERT INTO jobs (id, job_type, status, owner, created_at) VALUES (?, ?, ?, ?, ?)",
                    (f"wal-test-{i}-{time.time()}", "test", "queued", "test-user", "2026-10-28T12:00:00Z"),
                )

        def reader(i):
            with _db._connect() as conn:
                rows = conn.execute("SELECT COUNT(*) AS cnt FROM jobs").fetchall()
                assert len(rows) >= 1

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
            futures = []
            for i in range(15):
                futures.append(ex.submit(writer, i))
                futures.append(ex.submit(reader, i))
            for f in concurrent.futures.as_completed(futures):
                f.result()


class TestModelConcurrency:
    def test_concurrent_ml_inference_thread_safety(self):
        """Verifies MLDetectorEngine is thread-safe across concurrent worker threads."""
        eng = MLDetectorEngine()
        img = np.zeros((128, 128), dtype=np.float32)

        def run_inference(i):
            res = eng.detect(img, img)
            assert res.scene_classification is not None
            assert res.scene_classification["oil_status"] == "NO_OIL"
            return res.confidence

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            confidences = list(ex.map(run_inference, range(4)))

        assert len(confidences) == 4
        assert all(isinstance(c, float) for c in confidences)


class TestJobQueueBounding:
    def test_bounded_queue_rejects_overflow_with_429(self):
        """Verifies that when queue capacity is reached, 429 Too Many Requests is returned."""
        saved_cap = jobs_module.MAX_CONCURRENT_ACTIVE_JOBS
        jobs_module.MAX_CONCURRENT_ACTIVE_JOBS = 2

        try:
            with jobs_module._JOBS_LOCK:
                for j in list(jobs_module._JOBS.values()):
                    if j.status in ("queued", "running"):
                        j.status = "cancelled"

            blockers = []
            stop_event = threading.Event()

            def slow_task():
                stop_event.wait(timeout=2.0)
                class Res:
                    id = "res-id"
                return Res()

            # Submit 2 active jobs to fill capacity
            b1 = jobs_module.submit_job("flood_test", slow_task)
            b2 = jobs_module.submit_job("flood_test", slow_task)
            blockers.extend([b1, b2])

            # The 3rd should be rejected with 429
            r = CLIENT.post("/api/jobs/investigation", params={"mode": "demo"})
            assert r.status_code == 429
            assert "Retry-After" in r.headers
            assert "capacity" in r.json()["detail"].lower()

            stop_event.set()
            concurrent.futures.wait([b._future for b in blockers if b._future], timeout=2.0)
        finally:
            stop_event.set()
            jobs_module.MAX_CONCURRENT_ACTIVE_JOBS = saved_cap

    def test_synchronized_simultaneous_submissions_cannot_exceed_capacity(self):
        """Uses a threading.Barrier to release multiple threads simultaneously,
        proving that atomic capacity checking prevents concurrent jobs from exceeding
        configured capacity."""
        saved_cap = jobs_module.MAX_CONCURRENT_ACTIVE_JOBS
        target_cap = 3
        jobs_module.MAX_CONCURRENT_ACTIVE_JOBS = target_cap

        with jobs_module._JOBS_LOCK:
            for j in list(jobs_module._JOBS.values()):
                if j.status in ("queued", "running"):
                    j.status = "cancelled"

        num_threads = 10
        barrier = threading.Barrier(num_threads)
        release_workers = threading.Event()

        def blocking_worker():
            release_workers.wait(timeout=5.0)
            class Res:
                id = "barrier-inv"
            return Res()

        results = []

        def worker_attempt():
            # Synchronize so all threads hit submit_job at the exact same instant
            barrier.wait()
            try:
                job = jobs_module.submit_job("barrier_test", blocking_worker)
                results.append(("success", job.id))
            except jobs_module.JobQueueCapacityExceeded:
                results.append(("rejected", None))

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as ex:
                futures = [ex.submit(worker_attempt) for _ in range(num_threads)]
                concurrent.futures.wait(futures)

            successes = [r for r in results if r[0] == "success"]
            rejections = [r for r in results if r[0] == "rejected"]

            # Exactly target_cap must succeed, and remainder must be rejected
            assert len(successes) == target_cap
            assert len(rejections) == num_threads - target_cap
        finally:
            release_workers.set()
            futures_to_wait = [
                jobs_module._JOBS[jid]._future
                for _, jid in successes
                if jid and jid in jobs_module._JOBS and jobs_module._JOBS[jid]._future
            ]
            concurrent.futures.wait(futures_to_wait, timeout=3.0)
            jobs_module.MAX_CONCURRENT_ACTIVE_JOBS = saved_cap
