from __future__ import annotations

import os
import pytest
from starlette.testclient import TestClient
from app.main import app

client = TestClient(app)

SAMPLE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "sample_sar")
REAL_OIL_SCENE = os.path.join(SAMPLE_DIR, "demo_oil_scene_03.tif")
NO_OIL_SCENE = os.path.join(SAMPLE_DIR, "demo_no_oil_scene_01.tif")
LOOKALIKE_SCENE = os.path.join(SAMPLE_DIR, "demo_lookalike_scene_01.tif")


class TestMLDeterministicPipeline:
    def test_strict_ml_mode_on_real_zenodo_scene_returns_genuine_ml_prediction(self):
        with open(REAL_OIL_SCENE, "rb") as f:
            r = client.post(
                "/api/satellite/upload?detector=ml&allow_fallback=false&center_lat=18.92&center_lon=72.825",
                files={"file": ("demo_oil_scene_03.tif", f, "image/tiff")},
            )
        assert r.status_code == 200
        data = r.json()
        sat = data["satellite"]
        assert sat["detector_provenance"] == "ML_REAL_ZENODO_TRAINED"
        assert sat["fallback_used"] is False
        assert sat["fallback_reason"] is None
        assert "ml_real" in sat["actual_detector"]
        assert "ml_real" in sat["method"]
        assert sat["detector_mode"] == "ml"
        assert sat["scene_classification"] is not None
        assert sat["scene_classification"]["label"] == "Oil"
        assert sat["confidence"] > 0.9
        assert sat["geometry"] is not None
        assert sat["geometry"]["area_km2"] > 0
        assert len(data["candidates"]) > 0

    def test_strict_ml_mode_on_single_band_fails_with_422(self, tmp_path):
        import numpy as np
        from PIL import Image
        png_path = tmp_path / "single_band.png"
        Image.fromarray((np.random.rand(128, 128) * 255).astype("uint8")).save(png_path)
        with open(png_path, "rb") as f:
            r = client.post(
                "/api/satellite/upload?detector=ml&allow_fallback=false&center_lat=18.92&center_lon=72.825",
                files={"file": ("single_band.png", f, "image/png")},
            )
        assert r.status_code == 422
        assert "ML detector requires a dual-polarization (VV+VH) SAR image" in r.json()["detail"]

    def test_strict_ml_mode_when_engine_unavailable_fails_with_422(self, monkeypatch):
        from app.ml import detector
        engine = detector.get_ml_engine()
        monkeypatch.setattr(engine, "available", False)
        monkeypatch.setattr(engine, "load_errors", ["Model weights failed integrity check"])
        with open(REAL_OIL_SCENE, "rb") as f:
            r = client.post(
                "/api/satellite/upload?detector=ml&allow_fallback=false&center_lat=18.92&center_lon=72.825",
                files={"file": ("demo_oil_scene_03.tif", f, "image/tiff")},
            )
        assert r.status_code == 422
        assert "ML detector is unavailable" in r.json()["detail"]

    def test_strict_ml_mode_on_empty_mask_fails_with_422(self):
        with open(NO_OIL_SCENE, "rb") as f:
            r = client.post(
                "/api/satellite/upload?detector=ml&allow_fallback=false&center_lat=18.92&center_lon=72.825",
                files={"file": ("demo_no_oil_scene_01.tif", f, "image/tiff")},
            )
        assert r.status_code == 422
        assert "empty/invalid mask" in r.json()["detail"]

    def test_auto_mode_on_dual_pol_scene_executes_ml(self):
        with open(REAL_OIL_SCENE, "rb") as f:
            r = client.post(
                "/api/satellite/upload?detector=auto&center_lat=18.92&center_lon=72.825",
                files={"file": ("demo_oil_scene_03.tif", f, "image/tiff")},
            )
        assert r.status_code == 200
        data = r.json()
        sat = data["satellite"]
        assert sat["detector_provenance"] == "ML_REAL_ZENODO_TRAINED"
        assert sat["fallback_used"] is False
        assert "ml_real" in sat["actual_detector"]
        assert sat["detector_mode"] == "auto"

    def test_auto_mode_on_single_band_scene_executes_classical_cleanly(self, tmp_path):
        import numpy as np
        from PIL import Image
        png_path = tmp_path / "single_band.png"
        Image.fromarray((np.random.rand(128, 128) * 255).astype("uint8")).save(png_path)
        with open(png_path, "rb") as f:
            r = client.post(
                "/api/satellite/upload?detector=auto&center_lat=18.92&center_lon=72.825",
                files={"file": ("single_band.png", f, "image/png")},
            )
        assert r.status_code == 200
        data = r.json()
        sat = data["satellite"]
        assert sat["detector_provenance"] == "CLASSICAL_DARK_SPOT"
        assert sat["fallback_used"] is False
        assert sat["actual_detector"] == "classical_dark_spot"
        assert sat["detector_mode"] == "auto"
        assert any("single-band" in n for n in sat["detection_notes"])

    def test_auto_mode_on_dual_pol_with_ml_failure_falls_back_honestly(self, monkeypatch):
        from app.ml import detector
        engine = detector.get_ml_engine()
        monkeypatch.setattr(engine, "available", False)
        monkeypatch.setattr(engine, "load_errors", ["Engine offline"])
        with open(REAL_OIL_SCENE, "rb") as f:
            r = client.post(
                "/api/satellite/upload?detector=auto&center_lat=18.92&center_lon=72.825",
                files={"file": ("demo_oil_scene_03.tif", f, "image/tiff")},
            )
        assert r.status_code == 200
        data = r.json()
        sat = data["satellite"]
        assert sat["detector_provenance"] == "CLASSICAL_DARK_SPOT_ML_FALLBACK"
        assert sat["method"] == "classical_dark_spot_ml_fallback"
        assert sat["fallback_used"] is True
        assert "Engine offline" in (sat["fallback_reason"] or "")

    def test_clean_sea_under_ml_mode_falls_back_and_abstains_attribution(self):
        with open(NO_OIL_SCENE, "rb") as f:
            r = client.post(
                "/api/satellite/upload?detector=ml&center_lat=18.92&center_lon=72.825",
                files={"file": ("demo_no_oil_scene_01.tif", f, "image/tiff")},
            )
        assert r.status_code == 200
        data = r.json()
        sat = data["satellite"]
        assert sat["detector_provenance"] == "CLASSICAL_DARK_SPOT_ML_FALLBACK"
        assert sat["fallback_used"] is True
        assert sat["attribution_status"].startswith("ABSTAINED_")

    def test_lookalike_anomaly_under_ml_mode_falls_back_and_abstains_attribution(self):
        with open(LOOKALIKE_SCENE, "rb") as f:
            r = client.post(
                "/api/satellite/upload?detector=ml&center_lat=18.92&center_lon=72.825",
                files={"file": ("demo_lookalike_scene_01.tif", f, "image/tiff")},
            )
        assert r.status_code == 200
        data = r.json()
        sat = data["satellite"]
        assert sat["detector_provenance"] == "CLASSICAL_DARK_SPOT_ML_FALLBACK"
        assert sat["fallback_used"] is True
        assert sat["attribution_status"].startswith("ABSTAINED_")

    def test_sample_endpoint_zenodo_scene_returns_real_ml_detection(self):
        r = client.post(
            "/api/satellite/sample?sample_id=demo_oil_scene_03.tif&detector=ml&allow_fallback=false&center_lat=18.92&center_lon=72.825"
        )
        assert r.status_code == 200
        data = r.json()
        sat = data["satellite"]
        assert sat["detector_provenance"] == "ML_REAL_ZENODO_TRAINED"
        assert sat["confidence"] > 0.9
        assert sat["scene_classification"]["label"] == "Oil"

    def test_samples_catalog_contains_zenodo_dual_pol_scene(self):
        r = client.get("/api/satellite/samples")
        assert r.status_code == 200
        samples = r.json()
        zenodo_sample = next((s for s in samples if s["id"] == "demo_oil_scene_03.tif"), None)
        assert zenodo_sample is not None
        assert zenodo_sample["type"] == "oil"
        assert "Dual-Pol" in zenodo_sample["name"]
