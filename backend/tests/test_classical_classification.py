"""
Regression tests for Classical Detector multi-class classification and scores.

Covers:
1. Synthetic oil-like scene: Oil verdict, LIKELY_OIL, oil has highest score.
2. Synthetic lookalike scenes (circular/low elongation, rough texture, real lookalike scene):
   Lookalike verdict, LOOKALIKE, lookalike has highest score.
3. Synthetic no-oil scenes (clean sea, diffuse wave clutter):
   No-Oil verdict, NO_OIL, no-oil has highest score.
4. Edge cases & input guards: empty array, None, NaN/Inf, zero variance, all dark/bright, tiny spots.
5. Score normalization: scores sum strictly to 1.0 (subject to rounding), bounded in [0, 1].
6. Contract consistency: identical schema between Classical and ML detector scene_classification.
7. Fallback behavior: detector_name updated to 'Classical Detector (ML Fallback)', scores retained.
8. End-to-end investigation with Classical Detector: demo and real-satellite modes via API.
"""
import math
import os
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.satellite.detection import detect_oil_slick
from app.ml.detector import MLDetectorEngine
from app.investigation.orchestrator import run_investigation
from app.main import app

CLIENT = TestClient(app)
SAMPLE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "sample_sar")


def _make_oil_scene(h=300, w=300):
    """Creates a synthetic SAR scene with an elongated, smooth dark slick."""
    img = np.full((h, w), 0.7, dtype=np.float32)
    y, x = np.ogrid[:h, :w]
    # Elongated ellipse: semi-axes 80 and 15 (elongation ~ 5.3:1, area ~ 3770 px)
    mask = (((x - w // 2) / 80) ** 2 + ((y - h // 2) / 15) ** 2) <= 1.0
    img[mask] = 0.2
    return img


def _make_circular_lookalike_scene(h=300, w=300):
    """Creates a synthetic SAR scene with a circular dark spot (elongation ~ 1.0)."""
    img = np.full((h, w), 0.7, dtype=np.float32)
    y, x = np.ogrid[:h, :w]
    # Circular spot: semi-axes 35 and 35 (elongation ~ 1.0, area ~ 3848 px)
    mask = (((x - w // 2) / 35) ** 2 + ((y - h // 2) / 35) ** 2) <= 1.0
    img[mask] = 0.2
    return img


def _make_rough_texture_lookalike_scene(h=300, w=300):
    """Creates a synthetic SAR scene with an elongated dark spot having rough interior texture."""
    img = np.full((h, w), 0.7, dtype=np.float32)
    y, x = np.ogrid[:h, :w]
    mask = (((x - w // 2) / 70) ** 2 + ((y - h // 2) / 20) ** 2) <= 1.0
    noise = np.random.RandomState(42).normal(0, 0.08, (h, w))
    img[mask] = 0.2 + noise[mask]
    return img


def _make_clean_sea_scene(h=300, w=300):
    """Creates a clean sea surface without significant dark anomalies."""
    rng = np.random.RandomState(42)
    return np.clip(0.7 + rng.normal(0, 0.02, (h, w)), 0.0, 1.0).astype(np.float32)


class TestSyntheticOilClassification:
    def test_synthetic_oil_slick_classified_as_oil_with_dominant_score(self):
        img = _make_oil_scene()
        res = detect_oil_slick(img, None)

        assert res.success is True
        sc = res.scene_classification
        assert sc is not None
        assert sc["label"] == "Oil"
        assert sc["oil_status"] == "LIKELY_OIL"
        assert sc["score_type"] == "heuristic_score"
        assert sc["score_type_label"] == "Heuristic score"
        assert sc["detector_name"] == "Classical Detector"
        assert "reasoning" in sc and len(sc["reasoning"]) > 10

        scores = sc["scores"]
        assert scores["Oil"] > scores["Lookalike"]
        assert scores["Oil"] > scores["No Oil"]
        assert scores["Oil"] >= 0.50
        assert sc["confidence"] == scores["Oil"]

    def test_real_bundled_oil_scene_classified_as_oil(self):
        oil_tif = os.path.join(SAMPLE_DIR, "demo_oil_scene_01.tif")
        if not os.path.exists(oil_tif):
            pytest.skip("Sample file demo_oil_scene_01.tif not available")
        from app.satellite.real_image import load_real_scene
        from app.satellite.preprocessing import lee_filter, normalize, simple_sea_mask
        scene = load_real_scene(oil_tif)
        norm = normalize(lee_filter(scene.image, window=7))
        sea_mask = simple_sea_mask(norm)
        res = detect_oil_slick(norm, sea_mask)

        assert res.success is True
        sc = res.scene_classification
        assert sc["label"] == "Oil"
        assert sc["oil_status"] == "LIKELY_OIL"
        assert sc["scores"]["Oil"] > sc["scores"]["Lookalike"]
        assert sc["scores"]["Oil"] > sc["scores"]["No Oil"]


class TestSyntheticLookalikeClassification:
    def test_circular_spot_classified_as_lookalike_due_to_low_elongation(self):
        img = _make_circular_lookalike_scene()
        res = detect_oil_slick(img, None)

        assert res.success is True
        sc = res.scene_classification
        assert sc is not None
        assert sc["label"] == "Lookalike"
        assert sc["oil_status"] == "LOOKALIKE"
        assert sc["score_type"] == "heuristic_score"
        assert sc["score_type_label"] == "Heuristic score"

        scores = sc["scores"]
        assert scores["Lookalike"] > scores["Oil"]
        assert scores["Lookalike"] > scores["No Oil"]
        assert sc["confidence"] == scores["Lookalike"]

    def test_rough_interior_texture_classified_as_lookalike(self):
        img = _make_rough_texture_lookalike_scene()
        res = detect_oil_slick(img, None)

        assert res.success is True
        sc = res.scene_classification
        assert sc["label"] == "Lookalike"
        assert sc["oil_status"] == "LOOKALIKE"

        scores = sc["scores"]
        assert scores["Lookalike"] > scores["Oil"]
        assert scores["Lookalike"] > scores["No Oil"]

    def test_real_bundled_lookalike_scene_classified_as_lookalike(self):
        lookalike_tif = os.path.join(SAMPLE_DIR, "demo_lookalike_scene_01.tif")
        if not os.path.exists(lookalike_tif):
            pytest.skip("Sample file demo_lookalike_scene_01.tif not available")
        from app.satellite.real_image import load_real_scene
        from app.satellite.preprocessing import lee_filter, normalize, simple_sea_mask
        scene = load_real_scene(lookalike_tif)
        norm = normalize(lee_filter(scene.image, window=7))
        sea_mask = simple_sea_mask(norm)
        res = detect_oil_slick(norm, sea_mask)

        assert res.success is True
        sc = res.scene_classification
        assert sc["label"] == "Lookalike"
        assert sc["oil_status"] == "LOOKALIKE"
        assert sc["scores"]["Lookalike"] > sc["scores"]["Oil"]


class TestSyntheticNoOilClassification:
    def test_clean_sea_classified_as_no_oil(self):
        img = _make_clean_sea_scene()
        res = detect_oil_slick(img, None)

        assert res.success is True
        sc = res.scene_classification
        assert sc is not None
        assert sc["label"] == "No Oil"
        assert sc["oil_status"] == "NO_OIL"
        assert sc["score_type"] == "heuristic_score"

        scores = sc["scores"]
        assert scores["No Oil"] > scores["Oil"]
        assert scores["No Oil"] > scores["Lookalike"]
        assert sc["confidence"] == scores["No Oil"]

    def test_real_bundled_diffuse_clutter_classified_as_no_oil(self):
        no_oil_tif = os.path.join(SAMPLE_DIR, "demo_no_oil_scene_01.tif")
        if not os.path.exists(no_oil_tif):
            pytest.skip("Sample file demo_no_oil_scene_01.tif not available")
        from app.satellite.real_image import load_real_scene
        from app.satellite.preprocessing import lee_filter, normalize, simple_sea_mask
        scene = load_real_scene(no_oil_tif)
        norm = normalize(lee_filter(scene.image, window=7))
        sea_mask = simple_sea_mask(norm)
        res = detect_oil_slick(norm, sea_mask)

        assert res.success is True
        sc = res.scene_classification
        assert sc["label"] == "No Oil"
        assert sc["oil_status"] == "NO_OIL"
        assert sc["scores"]["No Oil"] > sc["scores"]["Oil"]


class TestEdgeCasesAndRobustness:
    def test_empty_image_returns_graceful_uncertain(self):
        empty = np.zeros((0, 0), dtype=np.float32)
        res = detect_oil_slick(empty, None)
        assert res.success is False
        sc = res.scene_classification
        assert sc["label"] == "Uncertain"
        assert sc["oil_status"] == "UNCERTAIN"
        assert sc["confidence"] == 0.0

    def test_none_image_returns_graceful_uncertain(self):
        res = detect_oil_slick(None, None)
        assert res.success is False
        sc = res.scene_classification
        assert sc["label"] == "Uncertain"
        assert sc["oil_status"] == "UNCERTAIN"

    def test_nan_and_inf_values_rejected_gracefully(self):
        img_nan = np.full((100, 100), 0.5, dtype=np.float32)
        img_nan[50, 50] = np.nan
        res = detect_oil_slick(img_nan, None)
        assert res.success is False
        assert res.scene_classification["oil_status"] == "UNCERTAIN"

        img_inf = np.full((100, 100), 0.5, dtype=np.float32)
        img_inf[50, 50] = np.inf
        res_inf = detect_oil_slick(img_inf, None)
        assert res_inf.success is False
        assert res_inf.scene_classification["oil_status"] == "UNCERTAIN"

    def test_constant_images_classified_as_no_oil_without_error(self):
        # All-dark zero-variance image
        res_dark = detect_oil_slick(np.zeros((100, 100), dtype=np.float32), None)
        assert res_dark.success is True
        assert res_dark.scene_classification["oil_status"] == "NO_OIL"

        # All-bright zero-variance image
        res_bright = detect_oil_slick(np.ones((100, 100), dtype=np.float32), None)
        assert res_bright.success is True
        assert res_bright.scene_classification["oil_status"] == "NO_OIL"

    def test_tiny_subthreshold_dark_spots_classified_as_no_oil(self):
        img = np.full((300, 300), 0.7, dtype=np.float32)
        rng = np.random.RandomState(42)
        for _ in range(30):
            cy, cx = rng.randint(20, 280, size=2)
            img[cy - 2 : cy + 3, cx - 2 : cx + 3] = 0.1
        res = detect_oil_slick(img, None)
        assert res.scene_classification["oil_status"] == "NO_OIL"
        assert res.scene_classification["scores"]["No Oil"] > 0.80

    def test_3d_and_rank_mismatches_handled_safely(self):
        # 3D multi-channel array (e.g. RGB or multi-polarization)
        res_3d = detect_oil_slick(np.zeros((3, 100, 100), dtype=np.float32), None)
        assert res_3d.success is False
        assert res_3d.scene_classification["oil_status"] == "UNCERTAIN"

        # 3D single-band raster (1, H, W) should be squeezed and succeed as 2D
        res_single_band = detect_oil_slick(np.full((1, 100, 100), 0.7, dtype=np.float32), None)
        assert res_single_band.success is True
        assert res_single_band.scene_classification["oil_status"] == "NO_OIL"


class TestScoreNormalization:
    @pytest.mark.parametrize(
        "scene_fn",
        [
            _make_oil_scene,
            _make_circular_lookalike_scene,
            _make_rough_texture_lookalike_scene,
            _make_clean_sea_scene,
        ],
    )
    def test_synthetic_scene_scores_sum_strictly_to_one(self, scene_fn):
        img = scene_fn()
        res = detect_oil_slick(img, None)
        scores = res.scene_classification["scores"]
        total = scores["Oil"] + scores["Lookalike"] + scores["No Oil"]
        assert total == pytest.approx(1.0, abs=1e-3)
        for cat, val in scores.items():
            assert 0.0 <= val <= 1.0, f"Score for {cat} out of bounds: {val}"
            assert math.isfinite(val)

    def test_probabilities_alias_sums_to_one(self):
        img = _make_oil_scene()
        res = detect_oil_slick(img, None)
        probs = res.scene_classification["probabilities"]
        assert probs["Oil"] + probs["Lookalike"] + probs["No-Oil"] == pytest.approx(1.0, abs=1e-3)


class TestContractConsistency:
    def test_classical_and_ml_scene_classification_share_identical_keys(self):
        classical_res = detect_oil_slick(_make_oil_scene(), None)
        cl_sc = classical_res.scene_classification

        ml_eng = MLDetectorEngine()
        img = np.zeros((256, 256), dtype=np.float32)
        ml_res = ml_eng.detect(img, img)
        ml_sc = ml_res.scene_classification

        required_keys = {
            "label",
            "confidence",
            "oil_status",
            "probabilities",
            "scores",
            "score_type",
            "score_type_label",
            "detector_name",
            "reasoning",
        }
        assert required_keys.issubset(set(cl_sc.keys()))
        assert required_keys.issubset(set(ml_sc.keys()))

        # Verify score keys
        assert set(cl_sc["scores"].keys()) == {"Oil", "Lookalike", "No Oil"}
        assert set(ml_sc["scores"].keys()) == {"Oil", "Lookalike", "No Oil"}

        # Verify probability keys
        assert set(cl_sc["probabilities"].keys()) == {"Oil", "Lookalike", "No-Oil"}
        assert set(ml_sc["probabilities"].keys()) == {"Oil", "Lookalike", "No-Oil"}

        # Verify score types
        assert cl_sc["score_type"] == "heuristic_score"
        assert cl_sc["score_type_label"] == "Heuristic score"
        assert ml_sc["score_type"] == "probability"
        assert ml_sc["score_type_label"] == "Model probability"


class TestMLFallbackClassification:
    def test_single_pol_real_satellite_ml_request_falls_back_with_accurate_provenance(self, tmp_path):
        import numpy as np
        from PIL import Image
        single_pol = tmp_path / "single_pol.png"
        Image.fromarray((np.random.rand(128, 128) * 255).astype("uint8")).save(single_pol)
        inv = run_investigation(mode="real_satellite", image_path=str(single_pol), detector="ml")
        sat = inv.satellite
        sc = sat["scene_classification"]

        assert sat["detector_provenance"] == "CLASSICAL_DARK_SPOT_ML_FALLBACK"
        assert sc["detector_name"] == "Classical Detector (ML Fallback)"
        assert sc["score_type"] == "heuristic_score"
        assert sc["score_type_label"] == "Heuristic score"
        assert "scores" in sc
        assert sc["scores"]["Oil"] + sc["scores"]["Lookalike"] + sc["scores"]["No Oil"] == pytest.approx(1.0, abs=1e-3)


class TestEndToEndClassicalInvestigation:
    def test_demo_investigation_returns_complete_scene_classification(self):
        inv = run_investigation(mode="demo", detector="classical")
        d = inv.to_dict()
        sc = d["satellite"]["scene_classification"]

        assert sc is not None
        assert sc["detector_name"] == "Classical Detector"
        assert sc["score_type"] == "heuristic_score"
        assert set(sc["scores"].keys()) == {"Oil", "Lookalike", "No Oil"}

    def test_api_detect_and_get_preserves_classification(self):
        r = CLIENT.post("/api/spill/detect", params={"mode": "demo"})
        assert r.status_code == 200
        inv = r.json()
        sc = inv["satellite"]["scene_classification"]

        assert sc["detector_name"] == "Classical Detector"
        assert sc["score_type"] == "heuristic_score"
        assert sc["scores"]["Oil"] > 0
        assert sc["scores"]["Lookalike"] > 0
        assert sc["scores"]["No Oil"] > 0

        # Verify GET /api/spill/{id}
        r_get = CLIENT.get(f"/api/spill/{inv['id']}")
        assert r_get.status_code == 200
        sc_get = r_get.json()["satellite"]["scene_classification"]
        assert sc_get == sc
