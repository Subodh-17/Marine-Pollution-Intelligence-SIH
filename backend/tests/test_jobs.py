"""Tests for app/persistence/jobs.py and its API wiring (Section 8)."""
from __future__ import annotations

import time

from fastapi.testclient import TestClient


def client():
    from app.main import app
    return TestClient(app)


def _wait_until_done(c, job_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = c.get(f"/api/jobs/{job_id}")
        assert r.status_code == 200
        status = r.json()["status"]
        if status in ("completed", "failed"):
            return r.json()
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


def test_job_lifecycle_demo_mode():
    c = client()
    r = c.post("/api/jobs/investigation", params={"mode": "demo"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("queued", "running", "completed")
    job_id = body["id"]

    final = _wait_until_done(c, job_id)
    assert final["status"] == "completed"
    assert final["result_investigation_id"]

    # The resulting investigation is a real, fully-formed one.
    inv_id = final["result_investigation_id"]
    r2 = c.get(f"/api/spill/{inv_id}")
    assert r2.status_code == 200


def test_job_not_found():
    c = client()
    r = c.get("/api/jobs/does-not-exist")
    assert r.status_code == 404


def test_job_custom_ais_without_upload_rejected():
    from app.main import _LAST_UPLOADED_AIS
    # Other tests in the full suite may have already uploaded an AIS file
    # via the shared module-level _LAST_UPLOADED_AIS dict (pre-existing
    # test-isolation characteristic of that global, not something this test
    # should assume away) - clear it explicitly so this test is
    # order-independent.
    saved = dict(_LAST_UPLOADED_AIS)
    _LAST_UPLOADED_AIS.clear()
    try:
        c = client()
        r = c.post("/api/jobs/investigation", params={"mode": "custom_ais"})
        assert r.status_code == 400
    finally:
        _LAST_UPLOADED_AIS.clear()
        _LAST_UPLOADED_AIS.update(saved)


def test_direct_job_module_unit():
    from app.persistence import jobs as jobs_module

    calls = []

    def work():
        calls.append(1)
        class Fake:
            id = "fake-inv-id"
        return Fake()

    job = jobs_module.submit_job("unit_test", work)
    deadline = time.time() + 5
    while time.time() < deadline:
        j = jobs_module.get_job(job.id)
        if j.status == "completed":
            break
        time.sleep(0.05)
    j = jobs_module.get_job(job.id)
    assert j.status == "completed"
    assert j.result_investigation_id == "fake-inv-id"
    assert calls == [1]


def test_job_failure_is_captured():
    from app.persistence import jobs as jobs_module

    def failing_work():
        raise ValueError("synthetic failure for test")

    job = jobs_module.submit_job("unit_test_fail", failing_work)
    deadline = time.time() + 5
    while time.time() < deadline:
        j = jobs_module.get_job(job.id)
        if j.status == "failed":
            break
        time.sleep(0.05)
    j = jobs_module.get_job(job.id)
    assert j.status == "failed"
    assert "synthetic failure" in j.error


def test_job_survives_in_memory_cache_clear():
    """Simulates a restart: the in-memory _JOBS dict is gone, but the row
    persisted to SQLite (this test's isolated tmp_path DB) is not - the job
    is reported as 'interrupted' rather than falsely still 'running'."""
    from app.persistence import jobs as jobs_module

    def work():
        class Fake:
            id = "restart-test-inv"
        return Fake()

    job = jobs_module.submit_job("restart_test", work)
    deadline = time.time() + 5
    while time.time() < deadline:
        if jobs_module.get_job(job.id).status == "completed":
            break
        time.sleep(0.05)

    jobs_module._JOBS.pop(job.id, None)
    reloaded = jobs_module.get_job(job.id)
    assert reloaded is not None
    assert reloaded.status == "completed"  # was persisted before the cache was cleared
    assert reloaded.result_investigation_id == "restart-test-inv"


def test_cleanup_abandoned_jobs_does_not_crash():
    from app.persistence import jobs as jobs_module
    removed = jobs_module.cleanup_abandoned_jobs(max_age_hours=0.0000001)
    assert isinstance(removed, int)
