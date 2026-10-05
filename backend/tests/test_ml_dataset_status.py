"""
Tests for the ML Training page's Dataset Status section:

  GET /api/ml/dataset-status   (new)
  app.ml.dataset.get_dataset_status()   (the function backing it)

Enforces the same honesty contract as the rest of the ML subsystem: every
count reported must come from files genuinely discovered/validated on this
call via the EXISTING discover_pairs()/validate_pairs() logic (never a
second, hardcoded, or sampled counting path) - and the four states
(not_configured / missing / incomplete / validated) must be driven entirely
by what's actually on disk, with dataset presence kept strictly separate
from model-training status.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.ml import dataset as ds

client = TestClient(app)

tifffile = pytest.importorskip("tifffile", reason="dataset-status tests need tifffile (a core dependency)")


@pytest.fixture(autouse=True)
def _clear_dataset_status_cache():
    """Every test gets a clean in-memory dataset-status cache (see
    app.ml.dataset.get_dataset_status_cached) so one test's cached result
    for a given dataset_root can never leak into - or be mistaken for - a
    different test's expectations about freshness."""
    ds.clear_dataset_status_cache()
    yield
    ds.clear_dataset_status_cache()


def _write_tiff(path, arr):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tifffile.imwrite(path, arr)


def _write_part(root, part_dir, prefix, n, oil_positive, size=16, start=1):
    rng = np.random.default_rng(0)
    img_dir = os.path.join(root, part_dir, f"{prefix}_images")
    mask_dir = os.path.join(root, part_dir, f"{prefix}_mask")
    for i in range(start, start + n):
        stem = f"{i:04d}"
        img = rng.normal(size=(size, size, 2)).astype(np.float32)
        mask = np.zeros((size, size), dtype=np.uint8)
        if oil_positive:
            mask[1:3, 1:3] = 1
        _write_tiff(os.path.join(img_dir, f"{stem}.tif"), img)
        _write_tiff(os.path.join(mask_dir, f"{stem}.tif"), mask)


def _write_part3(root, subfolder, n, oil_positive, size=16):
    rng = np.random.default_rng(1)
    base = os.path.join(root, "part3", "02_Test_images_and_ground_truth")
    for i in range(1, n + 1):
        stem = f"{i:04d}"
        img = rng.normal(size=(size, size, 2)).astype(np.float32)
        mask = np.zeros((size, size), dtype=np.uint8)
        if oil_positive:
            mask[1:3, 1:3] = 1
        _write_tiff(os.path.join(base, "Images", subfolder, f"{stem}.tif"), img)
        _write_tiff(os.path.join(base, "Mask", subfolder, f"{stem}.tif"), mask)


class TestNotConfiguredAndMissing:
    def test_empty_dataset_root_is_not_configured(self):
        body = ds.get_dataset_status(dataset_root="")
        assert body["status"] == "not_configured"
        assert body["dataset_root_configured"] is False
        assert body["dataset_root"] is None
        assert body["total_validated_pairs"] == 0
        assert all(not p["detected"] for p in body["parts"].values())

    def test_nonexistent_directory_is_missing(self, tmp_path):
        target = str(tmp_path / "does_not_exist_QA")
        body = ds.get_dataset_status(dataset_root=target)
        assert body["status"] == "missing"
        assert body["dataset_root_configured"] is True
        assert "does not exist" in body["reason"]
        assert body["total_validated_pairs"] == 0

    def test_empty_existing_directory_is_missing(self, tmp_path):
        root = str(tmp_path / "empty_root")
        os.makedirs(root)
        body = ds.get_dataset_status(dataset_root=root)
        assert body["status"] == "missing"
        assert body["total_validated_pairs"] == 0
        assert body["issue_count"] == 0

    def test_never_touches_the_network_or_writes_anything(self, tmp_path):
        # A directory that exists but is unrelated content - the function
        # must only ever read, never attempt to fetch or create the dataset.
        root = str(tmp_path / "unrelated")
        os.makedirs(root)
        with open(os.path.join(root, "notes.txt"), "w") as f:
            f.write("nothing to see here")
        before = set(os.listdir(root))
        ds.get_dataset_status(dataset_root=root)
        after = set(os.listdir(root))
        assert before == after  # nothing written, nothing downloaded


class TestIncompleteAndValidated:
    def test_incomplete_when_part3_missing(self, tmp_path):
        root = str(tmp_path / "zenodo_sar")
        _write_part(root, "part1", "01_Train_Val_Oil_Spill", 5, oil_positive=True)
        _write_part(root, "part2", "01_Train_Val_Lookalike", 3, oil_positive=False)
        _write_part(root, "part2", "01_Train_Val_No_Oil", 3, oil_positive=False)

        body = ds.get_dataset_status(dataset_root=root)
        assert body["status"] == "incomplete"
        assert "part3" in body["reason"]
        assert body["parts"]["part1"]["detected"] is True
        assert body["parts"]["part1"]["validated_pairs"] == 5
        assert body["parts"]["part2"]["validated_pairs"] == 6
        assert body["parts"]["part3"]["detected"] is False
        assert body["parts"]["part3"]["validated_pairs"] == 0
        # Real counts, never fabricated round numbers.
        assert body["category_counts"] == {"Oil": 5, "No-Oil": 3, "Lookalike": 3}
        assert body["total_validated_pairs"] == 11

    def test_validated_when_all_three_parts_present(self, tmp_path):
        root = str(tmp_path / "zenodo_sar")
        _write_part(root, "part1", "01_Train_Val_Oil_Spill", 4, oil_positive=True)
        _write_part(root, "part2", "01_Train_Val_Lookalike", 4, oil_positive=False)
        _write_part(root, "part2", "01_Train_Val_No_Oil", 4, oil_positive=False)
        _write_part3(root, "Oil", 2, oil_positive=True)
        _write_part3(root, "Lookalike", 2, oil_positive=False)
        _write_part3(root, "No_oil", 2, oil_positive=False)

        body = ds.get_dataset_status(dataset_root=root)
        assert body["status"] == "validated"
        assert body["reason"] is None
        for part in ("part1", "part2", "part3"):
            assert body["parts"][part]["detected"] is True
            assert body["parts"][part]["validated_pairs"] > 0
        assert body["total_validated_pairs"] == 18
        assert body["category_counts"] == {"Oil": 6, "No-Oil": 6, "Lookalike": 6}
        assert body["issue_count"] == 0
        # Each part's own DOI is present and matches the module's own DATASET_DOIS mapping.
        assert body["parts"]["part1"]["doi"] == ds.DATASET_DOIS["part1"]

    def test_validated_dataset_says_nothing_about_training(self, tmp_path):
        # Dataset presence and model-training status are strictly separate
        # concerns - this endpoint must never claim anything about training.
        root = str(tmp_path / "zenodo_sar")
        _write_part(root, "part1", "01_Train_Val_Oil_Spill", 2, oil_positive=True)
        _write_part(root, "part2", "01_Train_Val_Lookalike", 2, oil_positive=False)
        _write_part(root, "part2", "01_Train_Val_No_Oil", 2, oil_positive=False)
        _write_part3(root, "Oil", 1, oil_positive=True)
        _write_part3(root, "Lookalike", 1, oil_positive=False)
        _write_part3(root, "No_oil", 1, oil_positive=False)

        body = ds.get_dataset_status(dataset_root=root)
        assert body["status"] == "validated"
        for key in ("model_trained", "provenance", "checkpoint", "training_history"):
            assert key not in body
        assert "training" in body["note"].lower()

    def test_corrupt_file_is_recorded_as_an_issue_not_silently_dropped(self, tmp_path):
        root = str(tmp_path / "zenodo_sar")
        _write_part(root, "part1", "01_Train_Val_Oil_Spill", 3, oil_positive=True)
        _write_part(root, "part2", "01_Train_Val_Lookalike", 2, oil_positive=False)
        _write_part(root, "part2", "01_Train_Val_No_Oil", 2, oil_positive=False)
        _write_part3(root, "Oil", 1, oil_positive=True)
        _write_part3(root, "Lookalike", 1, oil_positive=False)
        _write_part3(root, "No_oil", 1, oil_positive=False)
        # Corrupt one Part I image after writing.
        bad_path = os.path.join(root, "part1", "01_Train_Val_Oil_Spill_images", "0001.tif")
        with open(bad_path, "wb") as f:
            f.write(b"not a real tiff file")

        body = ds.get_dataset_status(dataset_root=root)
        assert body["issue_count"] == 1
        assert body["issues"][0]["kind"] == "corrupt"
        assert bad_path in body["issues"][0]["path"]
        # 2 valid Oil pairs remain (3 written, 1 corrupted) - still validated
        # overall since all three parts still have at least one good pair.
        assert body["parts"]["part1"]["validated_pairs"] == 2
        assert body["status"] == "validated"


class TestApiEndpoint:
    def test_endpoint_shape(self):
        r = client.get("/api/ml/dataset-status")
        assert r.status_code == 200
        body = r.json()
        for key in ("status", "dataset_root", "dataset_root_configured", "reason", "parts",
                    "category_counts", "total_validated_pairs", "issue_count", "issues",
                    "issues_truncated", "dataset_citation", "note"):
            assert key in body
        assert set(body["parts"].keys()) == {"part1", "part2", "part3"}
        assert body["status"] in ("not_configured", "missing", "incomplete", "validated")

    def test_endpoint_works_without_torch(self, monkeypatch):
        # Dataset validation has nothing to do with PyTorch - this must
        # keep working even when the ML detector's torch dependency isn't
        # installed, unlike /api/ml/status et al.
        from app.ml import detector as detector_module
        monkeypatch.setattr(detector_module, "TORCH_AVAILABLE", False)
        r = client.get("/api/ml/dataset-status")
        assert r.status_code == 200
        assert r.json()["status"] in ("not_configured", "missing", "incomplete", "validated")

    def test_endpoint_reflects_a_real_configured_dataset(self, tmp_path, monkeypatch):
        root = str(tmp_path / "zenodo_sar")
        _write_part(root, "part1", "01_Train_Val_Oil_Spill", 2, oil_positive=True)
        _write_part(root, "part2", "01_Train_Val_Lookalike", 2, oil_positive=False)
        _write_part(root, "part2", "01_Train_Val_No_Oil", 2, oil_positive=False)
        _write_part3(root, "Oil", 1, oil_positive=True)
        _write_part3(root, "Lookalike", 1, oil_positive=False)
        _write_part3(root, "No_oil", 1, oil_positive=False)

        from app.config import get_settings
        get_settings.cache_clear()
        monkeypatch.setenv("DATASET_ROOT", root)
        try:
            r = client.get("/api/ml/dataset-status")
            body = r.json()
            assert body["status"] == "validated"
            assert body["total_validated_pairs"] == 9
        finally:
            get_settings.cache_clear()


class TestCachingWrapper:
    """`get_dataset_status_cached()` / `?refresh=true` - the fix for the
    ML Training page's Promise.all() failure mode: the expensive full scan
    must not re-run on every page load, but must always be forceable."""

    def test_second_call_is_served_from_cache(self, tmp_path):
        root = str(tmp_path / "zenodo_sar")
        _write_part(root, "part1", "01_Train_Val_Oil_Spill", 2, oil_positive=True)
        _write_part(root, "part2", "01_Train_Val_Lookalike", 2, oil_positive=False)
        _write_part(root, "part2", "01_Train_Val_No_Oil", 2, oil_positive=False)

        first = ds.get_dataset_status_cached(dataset_root=root)
        assert first["cache"]["cached"] is False
        assert first["total_validated_pairs"] == 6

        # Add a new pair on disk without calling get_dataset_status_cached
        # again with force_refresh - a plain second call must NOT re-scan
        # (and therefore must not see the new file), proving it's genuinely
        # served from cache rather than coincidentally re-computing the
        # same answer.
        _write_part(root, "part1", "01_Train_Val_Oil_Spill", 1, oil_positive=True, start=100)
        second = ds.get_dataset_status_cached(dataset_root=root)
        assert second["cache"]["cached"] is True
        assert second["total_validated_pairs"] == 6  # unchanged - served from cache

    def test_force_refresh_bypasses_cache_and_rescans(self, tmp_path):
        root = str(tmp_path / "zenodo_sar")
        _write_part(root, "part1", "01_Train_Val_Oil_Spill", 2, oil_positive=True)
        _write_part(root, "part2", "01_Train_Val_Lookalike", 2, oil_positive=False)
        _write_part(root, "part2", "01_Train_Val_No_Oil", 2, oil_positive=False)

        first = ds.get_dataset_status_cached(dataset_root=root)
        assert first["total_validated_pairs"] == 6

        _write_part(root, "part1", "01_Train_Val_Oil_Spill", 1, oil_positive=True, start=100)
        refreshed = ds.get_dataset_status_cached(dataset_root=root, force_refresh=True)
        assert refreshed["cache"]["cached"] is False
        assert refreshed["total_validated_pairs"] == 7  # the new file is now picked up

        # ...and the fresh result is what subsequent plain calls now serve.
        third = ds.get_dataset_status_cached(dataset_root=root)
        assert third["cache"]["cached"] is True
        assert third["total_validated_pairs"] == 7

    def test_expired_ttl_triggers_a_rescan(self, tmp_path):
        root = str(tmp_path / "zenodo_sar")
        _write_part(root, "part1", "01_Train_Val_Oil_Spill", 2, oil_positive=True)

        first = ds.get_dataset_status_cached(dataset_root=root, ttl_seconds=0.01)
        assert first["total_validated_pairs"] == 2
        time.sleep(0.05)

        _write_part(root, "part1", "01_Train_Val_Oil_Spill", 1, oil_positive=True, start=100)
        second = ds.get_dataset_status_cached(dataset_root=root, ttl_seconds=0.01)
        assert second["cache"]["cached"] is False
        assert second["total_validated_pairs"] == 3

    def test_different_dataset_roots_never_share_a_cache_entry(self, tmp_path):
        root_a = str(tmp_path / "root_a")
        root_b = str(tmp_path / "root_b")
        _write_part(root_a, "part1", "01_Train_Val_Oil_Spill", 2, oil_positive=True)
        _write_part(root_b, "part1", "01_Train_Val_Oil_Spill", 5, oil_positive=True)

        body_a = ds.get_dataset_status_cached(dataset_root=root_a)
        body_b = ds.get_dataset_status_cached(dataset_root=root_b)
        assert body_a["total_validated_pairs"] == 2
        assert body_b["total_validated_pairs"] == 5

    def test_clear_dataset_status_cache_forces_a_fresh_scan(self, tmp_path):
        root = str(tmp_path / "zenodo_sar")
        _write_part(root, "part1", "01_Train_Val_Oil_Spill", 2, oil_positive=True)

        ds.get_dataset_status_cached(dataset_root=root)
        _write_part(root, "part1", "01_Train_Val_Oil_Spill", 1, oil_positive=True, start=100)
        ds.clear_dataset_status_cache()

        after_clear = ds.get_dataset_status_cached(dataset_root=root)
        assert after_clear["cache"]["cached"] is False
        assert after_clear["total_validated_pairs"] == 3


class TestRefreshQueryParam:
    """`GET /api/ml/dataset-status?refresh=true` - the "Refresh dataset
    status" button on the ML Training page hits this exact endpoint/param."""

    def test_endpoint_response_includes_cache_metadata(self, tmp_path, monkeypatch):
        root = str(tmp_path / "zenodo_sar")
        _write_part(root, "part1", "01_Train_Val_Oil_Spill", 2, oil_positive=True)

        from app.config import get_settings
        get_settings.cache_clear()
        monkeypatch.setenv("DATASET_ROOT", root)
        try:
            r1 = client.get("/api/ml/dataset-status")
            assert r1.status_code == 200
            body1 = r1.json()
            assert body1["cache"]["cached"] is False

            r2 = client.get("/api/ml/dataset-status")
            assert r2.json()["cache"]["cached"] is True

            r3 = client.get("/api/ml/dataset-status?refresh=true")
            assert r3.status_code == 200
            assert r3.json()["cache"]["cached"] is False
        finally:
            get_settings.cache_clear()

    def test_refresh_true_reflects_files_added_since_the_cached_scan(self, tmp_path, monkeypatch):
        root = str(tmp_path / "zenodo_sar")
        _write_part(root, "part1", "01_Train_Val_Oil_Spill", 2, oil_positive=True)

        from app.config import get_settings
        get_settings.cache_clear()
        monkeypatch.setenv("DATASET_ROOT", root)
        try:
            cached_body = client.get("/api/ml/dataset-status").json()
            assert cached_body["total_validated_pairs"] == 2

            _write_part(root, "part1", "01_Train_Val_Oil_Spill", 3, oil_positive=True, start=100)

            stale_body = client.get("/api/ml/dataset-status").json()
            assert stale_body["total_validated_pairs"] == 2  # still cached

            fresh_body = client.get("/api/ml/dataset-status?refresh=true").json()
            assert fresh_body["total_validated_pairs"] == 5  # rescanned
        finally:
            get_settings.cache_clear()
