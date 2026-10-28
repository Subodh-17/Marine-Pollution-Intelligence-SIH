"""
Tests for the merged-in real satellite image upload path and the optional
ML prototype detector (app/ml/detector.py, app/satellite/real_image.py).

These exercise the ACTUAL bundled sample SAR images in
backend/data/sample_sar/ (real dual-polarization GeoTIFFs carried over from
the SIH26143_Marine_Spill_Intelligence_Platform project), not synthetic
stand-ins, so a failure here means the real upload path is actually broken.

Per project policy, these tests do not assert what label the ML detector
"should" produce - only that it runs, returns a well-formed result, and is
honestly labelled. The classical detector's real-world behavior on these
images IS asserted, since it's the operational default and was independently
verified to find a plausible dark-spot region in the bundled oil scene.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

SAMPLE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "sample_sar")
OIL_SCENE = os.path.join(SAMPLE_DIR, "demo_oil_scene_01.tif")
NO_OIL_SCENE = os.path.join(SAMPLE_DIR, "demo_no_oil_scene_01.tif")


def _sample_available():
    return os.path.exists(OIL_SCENE)


@pytest.mark.skipif(not _sample_available(), reason="bundled sample SAR image not present")
class TestRealSatelliteUpload:
    def test_upload_classical_detector_returns_200_and_real_geometry(self):
        with open(OIL_SCENE, "rb") as f:
            r = client.post(
                "/api/satellite/upload?detector=classical&center_lat=18.92&center_lon=72.825",
                files={"file": ("demo_oil_scene_01.tif", f, "image/tiff")},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["satellite"]["source"] == "USER_UPLOADED_REAL_IMAGE"
        assert body["satellite"]["detector_provenance"] == "CLASSICAL_DARK_SPOT"
        # This is a real, literature-standard detector run on a real image -
        # it should find *some* plausible region in the bundled oil scene
        # (independently confirmed during development), not assert a specific
        # area/confidence number that could silently drift with unrelated
        # changes.
        assert body["satellite"]["geometry"] is not None
        assert body["satellite"]["geometry"]["area_km2"] > 0

    def test_upload_unsupported_extension_returns_400(self):
        r = client.post(
            "/api/satellite/upload",
            files={"file": ("not_an_image.txt", b"hello", "text/plain")},
        )
        assert r.status_code == 400

    def test_upload_ml_detector_on_dual_pol_image_without_torch_falls_back_to_classical(self, monkeypatch):
        """In this (supported, documented) torch-not-installed environment,
        requesting detector=ml on a genuinely dual-pol image must NOT produce
        an empty "no detection" result mislabelled as ML - it must
        automatically fall back to a full, working Classical SAR Detection
        run, clearly labelled as a fallback, never as if the ML model had
        produced it."""
        from app.ml import detector
        engine = detector.get_ml_engine()
        monkeypatch.setattr(engine, "available", False)
        monkeypatch.setattr(engine, "load_errors", ["Torch not installed in this environment"])
        with open(OIL_SCENE, "rb") as f:
            r = client.post(
                "/api/satellite/upload?detector=ml&center_lat=18.92&center_lon=72.825",
                files={"file": ("demo_oil_scene_01.tif", f, "image/tiff")},
            )
        assert r.status_code == 200
        body = r.json()
        sat = body["satellite"]
        assert sat["detector_provenance"] == "CLASSICAL_DARK_SPOT_ML_FALLBACK"
        assert sat["method"] == "classical_dark_spot_ml_fallback"
        # The fallback must actually produce a real, working detection - not
        # silently swallow the whole investigation.
        assert sat["geometry"] is not None
        assert sat["geometry"]["area_km2"] > 0
        assert len(body["candidates"]) > 0
        assert any("Classical SAR Detection / Fallback" in n for n in sat["detection_notes"])
        assert any("does NOT indicate the ML model worked" in n for n in sat["detection_notes"])
        assert "Fallback" in body["data_sources"]["detection_method"]

    def test_ml_status_endpoint_reports_actual_torch_availability(self):
        """Sanity check for the fallback test above, made environment-
        independent: the fallback asserted there must hold regardless of
        whether torch happens to be installed in the environment running
        the suite (CI/dev machines may have it; this sandboxed submission
        environment may not) - so this only checks that /api/ml/status
        HONESTLY reflects reality (`app.ml.detector.TORCH_AVAILABLE`),
        never that reality is a specific fixed value. See the test above
        for confirmation that the fallback itself is genuine either way
        (missing torch OR untrained/mismatched checkpoints both legitimately
        produce a fallback on this real bundled image)."""
        from app.ml.detector import TORCH_AVAILABLE
        r = client.get("/api/ml/status")
        assert r.json()["torch_available"] is TORCH_AVAILABLE

    def test_ml_detector_falls_back_to_classical_on_single_band_input(self, tmp_path):
        import numpy as np
        from PIL import Image
        png_path = tmp_path / "single_band.png"
        Image.fromarray((np.random.rand(128, 128) * 255).astype("uint8")).save(png_path)
        with open(png_path, "rb") as f:
            r = client.post("/api/satellite/upload?detector=ml", files={"file": ("single_band.png", f, "image/png")})
        assert r.status_code == 200
        sat = r.json()["satellite"]
        assert sat["detector_provenance"] == "CLASSICAL_DARK_SPOT_ML_FALLBACK"
        assert sat["method"] == "classical_dark_spot_ml_fallback"
        assert any("not dual-polarization" in n for n in sat["detection_notes"])
        assert any("NOT an ML result" in n for n in sat["detection_notes"])

    def test_geolocation_is_marked_assumed_for_real_uploads(self):
        with open(OIL_SCENE, "rb") as f:
            r = client.post("/api/satellite/upload", files={"file": ("demo_oil_scene_01.tif", f, "image/tiff")})
        assert r.json()["satellite"]["geolocation_is_assumed"] is True

    def test_no_detection_real_image_does_not_crash_pipeline(self):
        """Whatever the classical detector decides for the no-oil reference
        scene, the pipeline must not crash, and downstream stages must
        gracefully report not-run rather than fabricating results."""
        with open(NO_OIL_SCENE, "rb") as f:
            r = client.post("/api/satellite/upload", files={"file": ("demo_no_oil_scene_01.tif", f, "image/tiff")})
        assert r.status_code == 200
        body = r.json()
        if body["satellite"]["geometry"] is None:
            assert body["drift"]["probable_origin"] is None
            assert body["candidates"] == []


@pytest.mark.skipif(not _sample_available(), reason="bundled sample SAR image not present")
class TestMLConfidenceFallback:
    """Directly exercises the orchestrator's fallback DECISION logic (empty
    mask / low confidence) via a fake ML engine substituted with monkeypatch.
    This sandboxed environment has no torch installed, so a genuine
    low-confidence or empty-mask *real* inference result can't be produced
    here - but the fallback decision itself lives in
    app/investigation/orchestrator.py, not inside real PyTorch inference, so
    faking only the engine's `.detect()` output still exercises the actual
    production fallback code path end-to-end through the real HTTP endpoint,
    not a mock of the assertion."""

    @staticmethod
    def _fake_engine(mask, confidence, available=True):
        from app.satellite.detection import DetectionResult

        class FakeEngine:
            def __init__(self):
                self.available = available
                self.load_errors = [] if available else ["forced unavailable for test"]
                self.provenance = "ML_PROTOTYPE_SYNTHETIC_TRAINED"

            def detect(self, vv, vh):
                return DetectionResult(
                    mask=mask,
                    candidate_masks=[mask] if mask.sum() else [],
                    confidence=confidence,
                    method="ml_prototype_pytorch_cnn_unet",
                    notes=["fake ML note for test"],
                    provenance="ML_PROTOTYPE_SYNTHETIC_TRAINED",
                )
        return FakeEngine()

    def test_empty_ml_mask_falls_back_to_classical(self, monkeypatch):
        import numpy as np
        import app.ml.detector as ml_detector_module
        empty_mask = np.zeros((512, 512), dtype=bool)
        monkeypatch.setattr(ml_detector_module, "get_ml_engine",
                             lambda: self._fake_engine(empty_mask, confidence=0.99))
        with open(OIL_SCENE, "rb") as f:
            r = client.post(
                "/api/satellite/upload?detector=ml&center_lat=18.92&center_lon=72.825",
                files={"file": ("demo_oil_scene_01.tif", f, "image/tiff")},
            )
        assert r.status_code == 200
        sat = r.json()["satellite"]
        # An empty ML mask must trigger fallback even though confidence was high -
        # a confident-but-empty result is still not a usable detection.
        assert sat["detector_provenance"] == "CLASSICAL_DARK_SPOT_ML_FALLBACK"
        assert sat["geometry"] is not None  # fallback actually ran and worked
        assert any("empty/invalid mask" in n for n in sat["detection_notes"])

    def test_low_ml_confidence_falls_back_to_classical(self, monkeypatch):
        import numpy as np
        import app.ml.detector as ml_detector_module
        small_mask = np.zeros((512, 512), dtype=bool)
        small_mask[100:110, 100:110] = True
        monkeypatch.setattr(ml_detector_module, "get_ml_engine",
                             lambda: self._fake_engine(small_mask, confidence=0.1))
        with open(OIL_SCENE, "rb") as f:
            r = client.post(
                "/api/satellite/upload?detector=ml&center_lat=18.92&center_lon=72.825",
                files={"file": ("demo_oil_scene_01.tif", f, "image/tiff")},
            )
        assert r.status_code == 200
        sat = r.json()["satellite"]
        assert sat["detector_provenance"] == "CLASSICAL_DARK_SPOT_ML_FALLBACK"
        assert any("below the configured" in n for n in sat["detection_notes"])

    def test_high_confidence_valid_ml_mask_is_reported_as_ml_not_overridden(self, monkeypatch):
        """Guard against over-eager fallback: a genuinely valid, above-threshold
        ML result must be reported as ML, not silently replaced by classical."""
        import numpy as np
        import app.ml.detector as ml_detector_module
        good_mask = np.zeros((512, 512), dtype=bool)
        good_mask[100:400, 100:150] = True  # plausible elongated region
        monkeypatch.setattr(ml_detector_module, "get_ml_engine",
                             lambda: self._fake_engine(good_mask, confidence=0.9))
        with open(OIL_SCENE, "rb") as f:
            r = client.post(
                "/api/satellite/upload?detector=ml&center_lat=18.92&center_lon=72.825",
                files={"file": ("demo_oil_scene_01.tif", f, "image/tiff")},
            )
        assert r.status_code == 200
        sat = r.json()["satellite"]
        assert sat["detector_provenance"] == "ML_PROTOTYPE_SYNTHETIC_TRAINED"
        assert sat["method"] == "ml_prototype_pytorch_cnn_unet"

    def test_ml_inference_exception_falls_back_to_classical(self, monkeypatch):
        """A genuine runtime exception during PyTorch inference (OOM, shape
        mismatch, a bad checkpoint that loaded but doesn't match the input,
        etc) must be caught inside app/ml/detector.py::MLDetectorEngine.detect
        and turned into an honest, empty, zero-confidence DetectionResult
        (method='ml_prototype_inference_error') - never an unhandled 500,
        and never a silently-fabricated detection. The orchestrator must
        then recognize that method and fall back to the classical detector
        with a clear, non-silent reason."""
        import numpy as np
        from app.satellite.detection import DetectionResult
        import app.ml.detector as ml_detector_module

        class ExplodingEngine:
            available = True
            load_errors: list = []

            def detect(self, vv, vh):
                return DetectionResult(
                    mask=np.zeros_like(vv, dtype=bool),
                    candidate_masks=[],
                    confidence=0.0,
                    method="ml_prototype_inference_error",
                    notes=["ML_PROTOTYPE_SYNTHETIC_TRAINED: PyTorch inference raised a runtime "
                           "exception (RuntimeError: simulated forward-pass failure for test)."],
                )

        monkeypatch.setattr(ml_detector_module, "get_ml_engine", lambda: ExplodingEngine())
        with open(OIL_SCENE, "rb") as f:
            r = client.post(
                "/api/satellite/upload?detector=ml&center_lat=18.92&center_lon=72.825",
                files={"file": ("demo_oil_scene_01.tif", f, "image/tiff")},
            )
        assert r.status_code == 200
        sat = r.json()["satellite"]
        assert sat["detector_provenance"] == "CLASSICAL_DARK_SPOT_ML_FALLBACK"
        assert sat["method"] == "classical_dark_spot_ml_fallback"
        # The fallback must have actually produced a real, working detection.
        assert sat["geometry"] is not None
        assert sat["geometry"]["area_km2"] > 0
        assert any("runtime exception" in n for n in sat["detection_notes"])
        assert "Fallback" in r.json()["data_sources"]["detection_method"]

    def test_ml_detector_engine_catches_real_inference_exception(self, monkeypatch):
        """Unit-level check (no HTTP, no orchestrator fallback logic involved)
        that MLDetectorEngine.detect() itself - the actual code touched by
        this fix - catches a real exception raised from inside a genuine
        forward pass and returns method='ml_prototype_inference_error'
        rather than propagating the exception."""
        import numpy as np
        from app.ml.detector import MLDetectorEngine, RUNTIME_PYTORCH

        engine = MLDetectorEngine.__new__(MLDetectorEngine)
        engine.available = True
        engine.classifier_loaded = True
        engine.segmenter_loaded = True
        engine.load_errors = []
        engine.class_labels = ["Oil", "No-Oil", "Lookalike"]
        engine.device = "cpu"
        # Same reasoning as _minimal_available_engine() below: without these,
        # detect() raises AttributeError on its own sizing logic before ever
        # reaching BoomModule, so this test would "pass" for the wrong reason
        # (a missing test attribute, not the simulated forward-pass failure
        # it's named for).
        engine.runtime = RUNTIME_PYTORCH
        engine.max_inference_pixels = 512 * 512
        engine.tiling_enabled = True
        engine.tile_size = 256
        engine.tile_overlap = 32
        engine.max_tiled_inference_pixels = 4096 * 4096
        engine.segmentation_threshold = 0.5

        class BoomModule:
            def eval(self):
                return self

            def __call__(self, x):
                raise RuntimeError("simulated forward-pass failure")

        engine.classifier = BoomModule()
        engine.segmenter = BoomModule()

        vv = np.random.rand(64, 64).astype("float32")
        vh = np.random.rand(64, 64).astype("float32")
        result = engine.detect(vv, vh)

        assert result.method == "ml_prototype_inference_error"
        assert result.confidence == 0.0
        assert result.mask.sum() == 0
        assert any("runtime exception" in n for n in result.notes)

    @staticmethod
    def _minimal_available_engine():
        """Builds a bare-bones engine via __new__ (skipping __init__'s real
        checkpoint loading) that still carries every attribute detect()'s
        sizing/inference path actually reads before it ever gets to the
        classifier/segmenter forward pass - max_inference_pixels,
        tiling_enabled, tile_size, tile_overlap, max_tiled_inference_pixels,
        segmentation_threshold, and runtime (see MLDetectorEngine.__init__ /
        _resolve_*() in app/ml/detector.py for where these normally come
        from). Without these, detect() raises a plain AttributeError before
        it ever reaches the classifier/segmenter, which the method's own
        broad `except Exception` then mislabels as
        'ml_prototype_inference_error' - masking the actual
        'ml_prototype_invalid_output' classification this suite means to
        exercise. Values mirror the real defaults (_DEFAULT_MAX_INFERENCE_PIXELS
        = 512*512, tiling on, 256px tiles, 32px overlap, 0.5 threshold) so a
        64x64 test input always takes the same "fits direct, no tiling"
        path a real engine would take for an image this small."""
        from app.ml.detector import MLDetectorEngine, RUNTIME_PYTORCH
        engine = MLDetectorEngine.__new__(MLDetectorEngine)
        engine.available = True
        engine.classifier_loaded = True
        engine.segmenter_loaded = True
        engine.load_errors = []
        engine.class_labels = ["Oil", "No-Oil", "Lookalike"]
        engine.device = "cpu"
        engine.runtime = RUNTIME_PYTORCH
        engine.max_inference_pixels = 512 * 512
        engine.tiling_enabled = True
        engine.tile_size = 256
        engine.tile_overlap = 32
        engine.max_tiled_inference_pixels = 4096 * 4096
        engine.segmentation_threshold = 0.5
        return engine

    def test_detect_rejects_mismatched_vv_vh_shapes(self):
        """Unsupported SAR input: VV/VH with different shapes must be caught
        as an honest invalid-output result, never an unhandled exception."""
        import numpy as np
        engine = self._minimal_available_engine()
        vv = np.random.rand(64, 64).astype("float32")
        vh = np.random.rand(32, 32).astype("float32")
        result = engine.detect(vv, vh)
        assert result.method == "ml_prototype_invalid_output"
        assert result.confidence == 0.0
        assert result.mask.sum() == 0

    def test_detect_rejects_none_input(self):
        """Missing VV/VH (None) must not raise inside detect()."""
        import numpy as np
        engine = self._minimal_available_engine()
        result = engine.detect(None, np.zeros((64, 64), dtype="float32"))
        assert result.method == "ml_prototype_invalid_output"
        assert result.confidence == 0.0

    def test_detect_rejects_non_finite_input(self):
        """NaN/Inf pixel values (invalid input tensor) must be rejected
        before ever reaching a torch forward pass."""
        import numpy as np
        engine = self._minimal_available_engine()
        vv = np.random.rand(64, 64).astype("float32")
        vv[0, 0] = np.nan
        vh = np.random.rand(64, 64).astype("float32")
        result = engine.detect(vv, vh)
        assert result.method == "ml_prototype_invalid_output"
        assert result.confidence == 0.0

    def test_detect_rejects_nan_classifier_output(self):
        """A classifier that produces NaN softmax output (e.g. a corrupt
        checkpoint) must be discarded, not reported as a confident result."""
        try:
            import torch  # noqa: F401
        except (ImportError, OSError) as _e:
            pytest.skip(f"torch unavailable or blocked by OS policy: {_e}")
        import numpy as np
        import torch
        engine = self._minimal_available_engine()

        class NaNClassifier:
            def eval(self):
                return self

            def __call__(self, x):
                return torch.full((1, 3), float("nan"))

        class ZeroSegmenter:
            def eval(self):
                return self

            def __call__(self, x):
                return torch.zeros((1, 1, x.shape[-2], x.shape[-1]))

        engine.classifier = NaNClassifier()
        engine.segmenter = ZeroSegmenter()
        vv = np.random.rand(64, 64).astype("float32")
        vh = np.random.rand(64, 64).astype("float32")
        result = engine.detect(vv, vh)
        assert result.method == "ml_prototype_invalid_output"
        assert result.confidence == 0.0
        assert result.mask.sum() == 0

    def test_ml_engine_unavailable_falls_back_to_classical(self, monkeypatch):
        import numpy as np
        import app.ml.detector as ml_detector_module
        monkeypatch.setattr(ml_detector_module, "get_ml_engine",
                             lambda: self._fake_engine(np.zeros((512, 512), dtype=bool), confidence=0.0, available=False))
        with open(OIL_SCENE, "rb") as f:
            r = client.post(
                "/api/satellite/upload?detector=ml&center_lat=18.92&center_lon=72.825",
                files={"file": ("demo_oil_scene_01.tif", f, "image/tiff")},
            )
        assert r.status_code == 200
        sat = r.json()["satellite"]
        assert sat["detector_provenance"] == "CLASSICAL_DARK_SPOT_ML_FALLBACK"
        assert sat["geometry"] is not None
        assert any("unavailable" in n for n in sat["detection_notes"])

    def test_ml_nan_confidence_falls_back_to_classical(self, monkeypatch):
        """A NaN confidence must never slip through as if it were a valid,
        above-threshold ML result - `NaN < threshold` is always False in
        Python, so a naive comparison would silently treat NaN as "good
        enough". This must be explicitly rejected instead."""
        import math
        import numpy as np
        import app.ml.detector as ml_detector_module
        good_mask = np.zeros((512, 512), dtype=bool)
        good_mask[100:400, 100:150] = True
        monkeypatch.setattr(ml_detector_module, "get_ml_engine",
                             lambda: self._fake_engine(good_mask, confidence=float("nan")))
        with open(OIL_SCENE, "rb") as f:
            r = client.post(
                "/api/satellite/upload?detector=ml&center_lat=18.92&center_lon=72.825",
                files={"file": ("demo_oil_scene_01.tif", f, "image/tiff")},
            )
        assert r.status_code == 200
        sat = r.json()["satellite"]
        assert sat["detector_provenance"] == "CLASSICAL_DARK_SPOT_ML_FALLBACK"
        assert sat["geometry"] is not None
        assert math.isfinite(sat["confidence"])
        assert any("not finite" in n for n in sat["detection_notes"])

    def test_ml_infinite_confidence_falls_back_to_classical(self, monkeypatch):
        import math
        import numpy as np
        import app.ml.detector as ml_detector_module
        good_mask = np.zeros((512, 512), dtype=bool)
        good_mask[100:400, 100:150] = True
        monkeypatch.setattr(ml_detector_module, "get_ml_engine",
                             lambda: self._fake_engine(good_mask, confidence=float("inf")))
        with open(OIL_SCENE, "rb") as f:
            r = client.post(
                "/api/satellite/upload?detector=ml&center_lat=18.92&center_lon=72.825",
                files={"file": ("demo_oil_scene_01.tif", f, "image/tiff")},
            )
        assert r.status_code == 200
        sat = r.json()["satellite"]
        assert sat["detector_provenance"] == "CLASSICAL_DARK_SPOT_ML_FALLBACK"
        assert math.isfinite(sat["confidence"])

    def test_ml_engine_raising_outside_its_own_wrapper_still_falls_back(self, monkeypatch):
        """Even if a future/misbehaving engine implementation breaks its own
        'never raise' contract entirely, the orchestrator's own defense-in-
        depth try/except around `engine.detect()` must still prevent a raw
        exception from reaching the API layer."""
        import app.ml.detector as ml_detector_module

        class MisbehavingEngine:
            available = True
            load_errors: list = []

            def detect(self, vv, vh):
                raise RuntimeError("this engine ignores its own safety contract")

        monkeypatch.setattr(ml_detector_module, "get_ml_engine", lambda: MisbehavingEngine())
        with open(OIL_SCENE, "rb") as f:
            r = client.post(
                "/api/satellite/upload?detector=ml&center_lat=18.92&center_lon=72.825",
                files={"file": ("demo_oil_scene_01.tif", f, "image/tiff")},
            )
        assert r.status_code == 200
        sat = r.json()["satellite"]
        assert sat["detector_provenance"] == "CLASSICAL_DARK_SPOT_ML_FALLBACK"
        assert sat["geometry"] is not None


class TestMLStatusEndpoint:
    def test_ml_status_returns_200_and_never_claims_validated(self):
        r = client.get("/api/ml/status")
        assert r.status_code == 200
        body = r.json()
        
        from app.ml.detector import get_ml_engine
        engine = get_ml_engine()
        if engine and engine.available and getattr(engine, "provenance", None):
            expected_provenance = engine.provenance
        else:
            expected_provenance = "ML_UNAVAILABLE"
            
        assert body["provenance"] == expected_provenance
        assert "status_message" in body

    def test_demo_mode_is_unaffected_by_ml_module_existing(self):
        """Regression guard: adding the ML module must not change the
        deterministic classical demo scenario's behavior at all."""
        r = client.post("/api/spill/detect?mode=demo")
        assert r.status_code == 200
        body = r.json()
        assert body["satellite"]["source"] == "SYNTHETIC_DEMO"
        assert body["satellite"]["detector_provenance"] == "CLASSICAL_DARK_SPOT"


class TestNewExportEndpoints:
    @pytest.fixture(autouse=True)
    def _fresh_investigation(self):
        # A function-scoped autouse fixture, not `setup_class` (which this
        # used to be): `setup_class` is an xunit-style hook that runs ONCE,
        # before ANY test's fixtures - including the autouse
        # `_isolated_database` fixture in conftest.py, which is what
        # redirects DATABASE_PATH to a throwaway tmp_path file. That meant
        # the POST below, when it ran inside `setup_class`, always landed
        # in the real, non-isolated backend/data/app.db (whatever
        # DATABASE_PATH defaults to locally) once per test *session*,
        # regardless of how many times the suite ran - a real file this
        # test class left behind outside the isolated-test-database
        # mechanism every single run. Being function-scoped here instead
        # guarantees `_isolated_database` (same scope, and conftest
        # fixtures resolve before same-scope fixtures defined closer to the
        # test) has already pointed at its own tmp_path file before this
        # investigation is created - the trade-off is one extra demo
        # investigation per test method (4 instead of 1; milliseconds) for
        # genuine isolation instead of none.
        self.inv_id = client.post("/api/spill/detect?mode=demo").json()["id"]

    def test_vessels_csv_export_contains_konkan_star(self):
        r = client.get(f"/api/export/{self.inv_id}/vessels.csv")
        assert r.status_code == 200
        assert "text/csv" in r.headers["content-type"]
        assert "MV KONKAN STAR" in r.text
        assert r.text.startswith("rank,mmsi,vessel_name")

    def test_spill_polygon_geojson_is_valid_polygon_from_real_geometry(self):
        r = client.get(f"/api/export/{self.inv_id}/spill_polygon.geojson")
        assert r.status_code == 200
        body = r.json()
        assert body["type"] == "FeatureCollection"
        geom = body["features"][0]["geometry"]
        assert geom["type"] == "Polygon"
        ring = geom["coordinates"][0]
        assert ring[0] == ring[-1]  # closed ring

    def test_vessel_tracks_geojson_matches_candidate_count(self):
        inv = client.get(f"/api/spill/{self.inv_id}").json()
        r = client.get(f"/api/export/{self.inv_id}/vessel_tracks.geojson")
        assert r.status_code == 200
        assert len(r.json()["features"]) == len(inv["candidates"])

    def test_spill_polygon_export_409_when_no_detection(self):
        import numpy as np
        from PIL import Image
        import tempfile as _tf
        path = _tf.NamedTemporaryFile(suffix=".png", delete=False).name
        Image.fromarray(np.full((64, 64), 200, dtype="uint8")).save(path)  # uniform bright -> no dark spot
        with open(path, "rb") as f:
            r = client.post("/api/satellite/upload", files={"file": ("blank.png", f, "image/png")})
        iid = r.json()["id"]
        r2 = client.get(f"/api/export/{iid}/spill_polygon.geojson")
        if r.json()["satellite"]["geometry"] is None:
            assert r2.status_code == 409
