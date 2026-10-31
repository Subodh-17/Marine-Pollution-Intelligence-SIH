"""
Test suite verifying correct classification and attribution safety gating
for the 12 non-Zenodo genuine Sentinel-1 SAR evaluation images:
- 6 clean-sea (0% oil) scenes from CSIRO (Java / Great Barrier Reef).
- 6 documented oil-lookalike scenes from DARTIS via PANGAEA.
"""
import os
import pytest
from app.investigation.orchestrator import run_investigation
from app.satellite.real_image import load_real_scene
from app.satellite.detection import detect_oil_slick
from app.satellite.preprocessing import lee_filter, normalize, simple_sea_mask

CSIRO_DIR = r"D:\Marine_SAR_Test_Images\No_Oil"
DARTIS_DIR = r"D:\Marine_SAR_Test_Images\Lookalike_Oil"

CSIRO_IMAGES = [
    "0_0_0_img_0EVoS7GsB3g8BDRK_GBR_cls_0.jpg",
    "0_0_0_img_0NQG13XLKgWxrBhZ_JAV_cls_0.jpg",
    "0_0_0_img_0qbwdQCHFehdh1bz_JAV_cls_0.jpg",
    "0_0_0_img_0t9JBHeOeBHgaLmJ_JAV_cls_0.jpg",
    "0_0_0_img_0XNpLu6NngRGgyFC_JAV_cls_0.jpg",
    "0_0_0_img_1s5xSTBuBFsYJqxV_JAV_cls_0.jpg",
]

DARTIS_IMAGES = [
    "nw-0165-00-000165.jpg",
    "nw-0446-02-000103.jpg",
    "nw-0651-03-000148.jpg",
    "nw-1203-07-000125.jpg",
    "nw-1331-08-000082.jpg",
    "nw-1883-11-000117.jpg",
]


@pytest.mark.skipif(not os.path.exists(CSIRO_DIR), reason="CSIRO test directory not found")
class TestCSIROCleanSeaClassification:
    @pytest.mark.parametrize("fname", CSIRO_IMAGES)
    def test_csiro_clean_sea_is_classified_as_no_oil(self, fname):
        fpath = os.path.join(CSIRO_DIR, fname)
        if not os.path.exists(fpath):
            pytest.skip(f"Image {fname} not found")

        # 1. Verify load_real_scene detects flat sea dynamic range
        scene = load_real_scene(fpath)
        assert scene.is_flat_scene is True

        # 2. Verify detector direct call
        norm = normalize(lee_filter(scene.image, window=7))
        sea_mask = simple_sea_mask(norm)
        det = detect_oil_slick(norm, sea_mask, is_flat_scene=scene.is_flat_scene)
        sc = det.scene_classification

        assert det.success is True
        assert det.mask.sum() == 0
        assert sc["oil_status"] == "NO_OIL"
        assert sc["label"] == "No Oil"
        assert sc["confidence"] >= 0.90
        assert sc["scores"]["No Oil"] > sc["scores"]["Oil"]
        assert sc["scores"]["No Oil"] > sc["scores"]["Lookalike"]

    @pytest.mark.parametrize("fname", CSIRO_IMAGES)
    def test_csiro_end_to_end_investigation(self, fname):
        fpath = os.path.join(CSIRO_DIR, fname)
        if not os.path.exists(fpath):
            pytest.skip(f"Image {fname} not found")

        inv = run_investigation(mode="real_satellite", image_path=fpath, detector="auto")
        d = inv.to_dict()
        sat = d["satellite"]
        sc = sat["scene_classification"]

        assert sc["oil_status"] == "NO_OIL"
        assert sc["label"] == "No Oil"
        assert sat["detector_provenance"] == "CLASSICAL_DARK_SPOT"
        assert len(d.get("candidates", [])) == 0


@pytest.mark.skipif(not os.path.exists(DARTIS_DIR), reason="DARTIS test directory not found")
class TestDARTISLookalikeClassification:
    @pytest.mark.parametrize("fname", DARTIS_IMAGES)
    def test_dartis_lookalike_is_classified_as_lookalike(self, fname):
        fpath = os.path.join(DARTIS_DIR, fname)
        if not os.path.exists(fpath):
            pytest.skip(f"Image {fname} not found")

        scene = load_real_scene(fpath)
        norm = normalize(lee_filter(scene.image, window=7))
        sea_mask = simple_sea_mask(norm)
        det = detect_oil_slick(norm, sea_mask, is_flat_scene=scene.is_flat_scene)
        sc = det.scene_classification

        assert det.success is True
        assert sc["oil_status"] == "LOOKALIKE"
        assert sc["label"] == "Lookalike"
        assert sc["scores"]["Lookalike"] > sc["scores"]["Oil"]
        assert sc["scores"]["Lookalike"] > sc["scores"]["No Oil"]

    @pytest.mark.parametrize("fname", DARTIS_IMAGES)
    def test_dartis_end_to_end_investigation_abtains_attribution(self, fname):
        fpath = os.path.join(DARTIS_DIR, fname)
        if not os.path.exists(fpath):
            pytest.skip(f"Image {fname} not found")

        inv = run_investigation(mode="real_satellite", image_path=fpath, detector="auto")
        d = inv.to_dict()
        sat = d["satellite"]
        sc = sat["scene_classification"]

        assert sc["oil_status"] == "LOOKALIKE"
        assert sc["label"] == "Lookalike"
        assert sat["detector_provenance"] == "CLASSICAL_DARK_SPOT"
        # Safety gate must prevent attribution when classified as Lookalike
        ais = d.get("ais", {})
        assert ais.get("attribution_status") == "ABSTAINED_LOOKALIKE" or len(d.get("candidates", [])) == 0


@pytest.mark.skipif(not os.path.exists(CSIRO_DIR) or not os.path.exists(DARTIS_DIR), reason="Test directories not found")
class TestConfidenceIntegrityAndDiversity:
    def test_csiro_images_produce_distinct_physically_derived_confidences(self):
        """Verifies that clean-sea images produce distinct confidences derived from physical dynamic range,
        never repeating hardcoded 0.95 constants across different images."""
        confidences = []
        for fname in CSIRO_IMAGES:
            fpath = os.path.join(CSIRO_DIR, fname)
            inv = run_investigation(mode="real_satellite", image_path=fpath, detector="auto")
            sc = inv.to_dict()["satellite"]["scene_classification"]
            confidences.append(sc["confidence"])
            assert sc["score_type"] == "heuristic_score"
            assert "0.9500" not in f"{sc['confidence']:.4f}" or sc["scores"]["No Oil"] != 0.9000

        # All 6 clean-sea scenes must have unique, non-identical confidences based on backscatter DR
        assert len(set(confidences)) == len(CSIRO_IMAGES), f"Confidences contained duplicate values: {confidences}"

    def test_dartis_images_produce_distinct_confidences(self):
        """Verifies that lookalike scenes produce distinct confidences reflecting their individual geometry."""
        confidences = []
        for fname in DARTIS_IMAGES:
            fpath = os.path.join(DARTIS_DIR, fname)
            inv = run_investigation(mode="real_satellite", image_path=fpath, detector="auto")
            sc = inv.to_dict()["satellite"]["scene_classification"]
            confidences.append(sc["confidence"])
            assert sc["score_type"] == "heuristic_score"

        assert len(set(confidences)) == len(DARTIS_IMAGES), f"Confidences contained duplicate values: {confidences}"

    def test_heuristic_scores_are_never_labeled_as_calibrated_probabilities(self):
        """Verifies that classical detector scores are labeled as heuristic scores, never calibrated probabilities."""
        sample = os.path.join(CSIRO_DIR, CSIRO_IMAGES[0])
        inv = run_investigation(mode="real_satellite", image_path=sample, detector="auto")
        sc = inv.to_dict()["satellite"]["scene_classification"]
        assert sc["score_type"] == "heuristic_score"
        assert sc.get("is_calibrated") is not True
        assert sc.get("calibration_method") is None


class TestOilSpillClassificationIntegrity:
    def test_sample_oil_scene_is_classified_as_oil(self):
        sample_path = os.path.join(os.path.dirname(__file__), "..", "data", "sample_sar", "demo_oil_scene_01.tif")
        if not os.path.exists(sample_path):
            pytest.skip("Sample oil scene not found")
        inv = run_investigation(mode="real_satellite", image_path=sample_path, detector="classical")
        d = inv.to_dict()
        sc = d["satellite"]["scene_classification"]
        assert sc["oil_status"] == "LIKELY_OIL"
        assert sc["label"] == "Oil"
        assert sc["confidence"] >= 0.70
        assert sc["scores"]["Oil"] > sc["scores"]["Lookalike"]
        assert sc["scores"]["Oil"] > sc["scores"]["No Oil"]

    def test_single_band_oil_slick_classified_as_oil(self):
        """Verifies that an elongated, high-damping oil slick in single-band format
        is reliably classified as LIKELY_OIL (not misclassified as LOOKALIKE)."""
        import numpy as np
        h, w = 300, 300
        img = np.full((h, w), 0.7, dtype=np.float32)
        y, x = np.ogrid[:h, :w]
        # Elongated ellipse (oil slick): length 160, width 30
        mask = (((x - w // 2) / 80) ** 2 + ((y - h // 2) / 15) ** 2) <= 1.0
        img[mask] = 0.2
        det = detect_oil_slick(img, None)
        sc = det.scene_classification
        assert det.success is True
        assert sc["oil_status"] == "LIKELY_OIL"
        assert sc["label"] == "Oil"
        assert sc["confidence"] >= 0.75
        assert det.mask.sum() > 0

    def test_clean_sea_with_wave_clutter_classified_as_no_oil(self):
        """Verifies that clean sea with background texture / speckle (>20 DN dynamic range)
        is classified as NO_OIL, never incorrectly defaulting to LOOKALIKE."""
        import numpy as np
        h, w = 256, 256
        # Background sea with moderate variance but no coherent macroscopic slick
        np.random.seed(42)
        clutter = np.random.normal(loc=0.5, scale=0.04, size=(h, w)).astype(np.float32)
        clutter = np.clip(clutter, 0.0, 1.0)
        sea_mask = np.ones((h, w), dtype=bool)
        det = detect_oil_slick(clutter, sea_mask, is_flat_scene=False)
        sc = det.scene_classification
        assert sc["oil_status"] == "NO_OIL"
        assert sc["label"] == "No Oil"
        assert det.mask.sum() == 0
        assert sc["confidence"] >= 0.85
