"""
Tests for Phase 11: Asynchronous satellite upload job processing, real-time stage progress reporting,
and bounding-box texture scoring optimization.
"""
from __future__ import annotations

import io
import os
import time
import numpy as np
from fastapi.testclient import TestClient

from app.main import app
from app.investigation.orchestrator import run_investigation
from app.satellite.detection import _texture_score


def _client():
    return TestClient(app)


def _wait_until_done(client, job_id, timeout=25.0):
    deadline = time.time() + timeout
    stages_seen = []
    while time.time() < deadline:
        r = client.get(f"/api/jobs/{job_id}")
        assert r.status_code == 200
        data = r.json()
        stage = data.get("progress_stage")
        if stage and stage not in stages_seen:
            stages_seen.append(stage)
        if data["status"] in ("completed", "failed"):
            return data, stages_seen
        time.sleep(0.15)
    raise AssertionError(f"Job {job_id} did not finish within {timeout}s (stages seen: {stages_seen})")


def test_orchestrator_progress_callback_invoked():
    stages = []

    def on_stage(st: str):
        stages.append(st)

    inv = run_investigation(mode="demo", progress_callback=on_stage)
    assert inv is not None
    assert len(stages) > 0
    assert "sar_preprocessing_completed" in stages or "preprocessing_lee_speckle_filter_&_normalization" in stages or any("preprocessing" in s for s in stages)
    assert any("slick" in s or "detection" in s for s in stages)


def test_satellite_sample_async_job_lifecycle():
    client = _client()
    resp = client.post(
        "/api/satellite/sample",
        params={"sample_name": "demo_oil_scene_01.tif", "detector": "classical", "async_job": True},
    )
    assert resp.status_code == 202
    job_data = resp.json()
    assert job_data["status"] in ("queued", "running", "completed")
    job_id = job_data["id"]

    final_job, stages = _wait_until_done(client, job_id, timeout=30.0)
    assert final_job["status"] == "completed"
    assert final_job["result_investigation_id"]

    inv_resp = client.get(f"/api/spill/{final_job['result_investigation_id']}")
    assert inv_resp.status_code == 200
    inv = inv_resp.json()
    assert inv["mode"] == "real_satellite"
    assert inv["satellite"]["detector_provenance"] == "CLASSICAL_DARK_SPOT"


def test_satellite_upload_async_job_lifecycle():
    client = _client()
    sample_dir = os.path.join(os.path.dirname(__file__), "..", "data", "sample_sar")
    sample_path = os.path.join(sample_dir, "demo_oil_scene_01.tif")

    with open(sample_path, "rb") as f:
        file_bytes = f.read()

    resp = client.post(
        "/api/satellite/upload",
        params={"detector": "classical", "async_job": True},
        files={"file": ("uploaded_oil_scene.tif", io.BytesIO(file_bytes), "image/tiff")},
    )
    assert resp.status_code == 202
    job_data = resp.json()
    assert job_data["id"]
    job_id = job_data["id"]

    final_job, stages = _wait_until_done(client, job_id, timeout=30.0)
    assert final_job["status"] == "completed"
    assert final_job["result_investigation_id"]

    # Verify persisted investigation
    inv_resp = client.get(f"/api/spill/{final_job['result_investigation_id']}")
    assert inv_resp.status_code == 200
    inv = inv_resp.json()
    assert inv["mode"] == "real_satellite"


def test_satellite_upload_sync_backward_compatibility():
    client = _client()
    sample_dir = os.path.join(os.path.dirname(__file__), "..", "data", "sample_sar")
    sample_path = os.path.join(sample_dir, "demo_oil_scene_01.tif")

    with open(sample_path, "rb") as f:
        file_bytes = f.read()

    resp = client.post(
        "/api/satellite/upload",
        params={"detector": "classical", "async_job": False},
        files={"file": ("sync_oil_scene.tif", io.BytesIO(file_bytes), "image/tiff")},
    )
    assert resp.status_code == 200
    inv = resp.json()
    assert inv["id"]
    assert inv["mode"] == "real_satellite"


def test_bbox_texture_scoring_numerical_equivalence():
    np.random.seed(42)
    img = np.random.uniform(0.1, 0.9, size=(256, 256)).astype(np.float64)
    # create a synthetic region
    mask = np.zeros_like(img, dtype=bool)
    mask[50:100, 60:120] = True
    img[50:100, 60:120] *= 0.3  # darker inside

    # Full array texture calculation
    score_full = _texture_score(img, mask)

    # Sub-array calculation with bounding box
    min_r, min_c, max_r, max_c = 50, 60, 100, 120
    sub_labels = mask[min_r:max_r, min_c:max_c]
    sub_gray = img[min_r:max_r, min_c:max_c]
    tot_sum = float(img.sum())
    tot_sq = float((img ** 2).sum())

    score_bbox = _texture_score(img, sub_labels, tot_sum=tot_sum, tot_sq=tot_sq, sub_gray=sub_gray)

    assert abs(score_full - score_bbox) < 1e-7
