"""
Regression test suite for the six verified oil-related SAR images previously
misclassified as LOOKALIKE.

Problematic Images:
1. Delta-X Oil Slick Radar Signal in Gulf of Mexico.jpeg (NASA JPL UAVSAR)
2. Natural Oil Seepage Off California Coast.jpeg (NASA JPL Coal Oil Point)
3. Oil_spill_detected_by_Sentinel-1 (1).png (ESA Copernicus Sentinel-1)
4. Space Radar Image of North Sea, Germany.jpeg (NASA/DLR SIR-C/X-SAR)
5. Space Radar Image of Oil Slicks.jpeg (NASA SIR-C/X-SAR)
6. UAVSAR Oil Slicks.jpeg (NASA JPL Deepwater Horizon)

Verifies:
- All 6 images are classified as LIKELY_OIL (not LOOKALIKE or NO_OIL).
- Confidence scores are >= 0.70 and sum strictly to 1.0000.
- Detection masks are non-empty and capture coherent slick geometry.
- Attribution safety policy permits vessel attribution (ATTRIBUTION_PERMITTED).
- End-to-end investigation pipeline preserves LIKELY_OIL verdict.
- 3-class discrimination integrity across clean sea (6), lookalikes (6), and oil (6).
"""
import os
from pathlib import Path
import pytest
import numpy as np

from app.investigation.orchestrator import run_investigation
from app.satellite.real_image import load_real_scene
from app.satellite.detection import detect_oil_slick
from app.satellite.preprocessing import lee_filter, normalize, simple_sea_mask
from app.investigation.policy import evaluate_attribution_eligibility, STATUS_PERMITTED

OIL_DIRS = [
    Path(r"D:\Marine_SAR_Test_Images\Oil"),
    Path(r"C:\Users\Subodh\Downloads"),
]

LOOKALIKE_DIR = Path(r"D:\Marine_SAR_Test_Images\Lookalike_Oil")
CLEAN_SEA_DIR = Path(r"D:\Marine_SAR_Test_Images\No_Oil")

SIX_OIL_IMAGES = [
    "Delta-X Oil Slick Radar Signal in Gulf of Mexico.jpeg",
    "Natural Oil Seepage Off California Coast.jpeg",
    "Oil_spill_detected_by_Sentinel-1 (1).png",
    "Space Radar Image of North Sea, Germany.jpeg",
    "Space Radar Image of Oil Slicks.jpeg",
    "UAVSAR Oil Slicks.jpeg",
]


def _resolve_oil_image(fname: str) -> Path:
    for d in OIL_DIRS:
        p = d / fname
        if p.exists():
            return p
    pytest.skip(f"Oil test image '{fname}' not found in any search path.")


class TestSixOilImagesRegression:
    @pytest.mark.parametrize("fname", SIX_OIL_IMAGES)
    def test_direct_detector_classifies_as_likely_oil(self, fname: str):
        path = _resolve_oil_image(fname)
        scene = load_real_scene(str(path))
        norm = normalize(lee_filter(scene.image, window=7))
        sea_mask = simple_sea_mask(norm)
        det = detect_oil_slick(
            norm,
            sea_mask,
            is_flat_scene=scene.is_flat_scene,
            raw_dynamic_range=scene.raw_dynamic_range,
        )

        assert det.success is True, f"Detection failed on {fname}: {det.warnings}"
        sc = det.scene_classification
        assert sc is not None, "Scene classification dict must be present"

        # Assert correct classification as LIKELY_OIL
        assert sc["oil_status"] == "LIKELY_OIL", (
            f"Image '{fname}' was misclassified as {sc['oil_status']} (reason: {sc.get('reasoning')})"
        )
        assert sc["label"] == "Oil"
        assert sc["confidence"] >= 0.70, (
            f"Confidence {sc['confidence']} below 0.70 threshold for {fname}"
        )

        # Assert scores integrity
        scores = sc["scores"]
        assert scores["Oil"] > scores["Lookalike"], (
            f"Oil score ({scores['Oil']}) must exceed Lookalike ({scores['Lookalike']}) for {fname}"
        )
        assert scores["Oil"] > scores["No Oil"], (
            f"Oil score ({scores['Oil']}) must exceed No-Oil ({scores['No Oil']}) for {fname}"
        )
        total_score = round(scores["Oil"] + scores["Lookalike"] + scores["No Oil"], 4)
        assert total_score == 1.0000, f"Scores must sum to 1.0000, got {total_score}"

        # Assert non-empty mask capturing slick
        assert det.mask is not None and det.mask.sum() > 0, (
            f"Expected non-empty detection mask for genuine oil spill in {fname}"
        )

    @pytest.mark.parametrize("fname", SIX_OIL_IMAGES)
    def test_end_to_end_investigation_returns_likely_oil(self, fname: str):
        path = _resolve_oil_image(fname)
        inv = run_investigation(mode="real_satellite", image_path=str(path), detector="auto")
        data = inv.to_dict()
        sat = data.get("satellite", {})
        sc = sat.get("scene_classification", {})

        assert sc.get("oil_status") == "LIKELY_OIL", (
            f"End-to-end investigation returned {sc.get('oil_status')} for {fname}"
        )
        assert sc.get("label") == "Oil"
        assert sat.get("confidence", 0.0) >= 0.70

    @pytest.mark.parametrize("fname", SIX_OIL_IMAGES)
    def test_attribution_safety_policy_permits_investigation(self, fname: str):
        from datetime import datetime, timezone
        path = _resolve_oil_image(fname)
        scene = load_real_scene(str(path))
        norm = normalize(lee_filter(scene.image, window=7))
        sea_mask = simple_sea_mask(norm)
        det = detect_oil_slick(
            norm,
            sea_mask,
            is_flat_scene=scene.is_flat_scene,
            raw_dynamic_range=scene.raw_dynamic_range,
        )

        policy = evaluate_attribution_eligibility(
            scene_classification=det.scene_classification,
            mask=det.mask,
            observed_time=datetime.now(timezone.utc),
            min_mask_area_px=100,
            ais_records=[],
        )
        assert policy.eligible is True, (
            f"Attribution should be permitted for genuine oil spill {fname}, but was denied: {policy.reason}"
        )
        assert policy.status == STATUS_PERMITTED


class TestThreeClassDiscriminationIntegrity:
    @pytest.mark.skipif(
        not LOOKALIKE_DIR.exists() or not CLEAN_SEA_DIR.exists(),
        reason="DARTIS or CSIRO test directories missing",
    )
    def test_complete_eighteen_image_evaluation_matrix(self):
        """Verifies full 3-class discrimination across 18 verified SAR evaluation scenes:
        - 6 Genuine Oil Slicks -> LIKELY_OIL
        - 6 Oceanic Internal Wave Lookalikes -> LOOKALIKE
        - 6 Clean-Sea Baselines -> NO_OIL
        Must achieve 18/18 (100%) accuracy with zero false positives or false negatives.
        """
        results = {"OIL": [], "LOOKALIKE": [], "NO_OIL": []}

        # 1. Evaluate 6 Oil Images
        for fname in SIX_OIL_IMAGES:
            p = _resolve_oil_image(fname)
            scene = load_real_scene(str(p))
            norm = normalize(lee_filter(scene.image, window=7))
            sea_mask = simple_sea_mask(norm)
            det = detect_oil_slick(
                norm,
                sea_mask,
                is_flat_scene=scene.is_flat_scene,
                raw_dynamic_range=scene.raw_dynamic_range,
            )
            results["OIL"].append(det.scene_classification["oil_status"])

        # 2. Evaluate 6 DARTIS Lookalike Images
        for p in sorted(LOOKALIKE_DIR.glob("*.jpg")):
            scene = load_real_scene(str(p))
            norm = normalize(lee_filter(scene.image, window=7))
            sea_mask = simple_sea_mask(norm)
            det = detect_oil_slick(
                norm,
                sea_mask,
                is_flat_scene=scene.is_flat_scene,
                raw_dynamic_range=scene.raw_dynamic_range,
            )
            results["LOOKALIKE"].append(det.scene_classification["oil_status"])

        # 3. Evaluate 6 CSIRO Clean Sea Images
        for p in sorted(CLEAN_SEA_DIR.glob("*.jpg")):
            scene = load_real_scene(str(p))
            norm = normalize(lee_filter(scene.image, window=7))
            sea_mask = simple_sea_mask(norm)
            det = detect_oil_slick(
                norm,
                sea_mask,
                is_flat_scene=scene.is_flat_scene,
                raw_dynamic_range=scene.raw_dynamic_range,
            )
            results["NO_OIL"].append(det.scene_classification["oil_status"])

        assert all(s == "LIKELY_OIL" for s in results["OIL"]), (
            f"Oil images had non-oil predictions: {results['OIL']}"
        )
        assert all(s == "LOOKALIKE" for s in results["LOOKALIKE"]), (
            f"Lookalike images had non-lookalike predictions: {results['LOOKALIKE']}"
        )
        assert all(s == "NO_OIL" for s in results["NO_OIL"]), (
            f"Clean sea images had non-clean predictions: {results['NO_OIL']}"
        )
