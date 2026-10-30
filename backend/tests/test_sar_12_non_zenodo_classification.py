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
