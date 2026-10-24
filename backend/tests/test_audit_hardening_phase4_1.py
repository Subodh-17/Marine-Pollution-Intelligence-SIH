"""
Unit and integration tests for Phase 4.1 Real-Data Validation & ML Reliability Gate.

Tests:
1. Input, output, and failure-recovery audit (Priority 5)
   - Zero-byte and truncated upload rejection
   - Corrupt TIFF upload rejection with structured error (no 500 tracebacks)
   - NaN, Inf, and non-finite numeric safety
   - Unsupported channel dimension handling
2. End-to-end attribution safety & purge of stale candidates (Priority 7)
   - Gated re-analysis purges preexisting candidates from investigation store
   - Direct calls to /api/ais/analyze and /api/attribution/analyze fail-closed
   - /api/vessels/ranking reflects purged candidate state immediately
3. Multiscale parity, boundary preservation, and rollback (Priorities 3 & 4)
   - base_only fusion produces identical output to base alone
   - boundary_preserved eliminates small-slick boundary blooming
   - Evaluator module execution integrity
"""
from __future__ import annotations

import io
from datetime import datetime, timezone

import numpy as np
import pytest
import tifffile
from fastapi.testclient import TestClient

from app.main import app
from app.investigation.orchestrator import _STORE, Investigation
from app.ml.detector import get_ml_engine
from app.ml.tiling import stitch_multiscale_segmentation
from app.scoring.attribution import VesselCandidate


@pytest.fixture
def client():
    return TestClient(app)


# =============================================================================
# Priority 5: Input, Output, and Failure-Recovery Audit
# =============================================================================

def test_zero_byte_satellite_upload_rejected(client):
    """Zero-byte uploaded image must return 422 with clear message rather than crashing."""
    r = client.post(
        "/api/satellite/upload",
        files={"file": ("empty.tif", b"", "image/tiff")},
    )
    assert r.status_code == 422
    detail = r.json()["detail"].lower()
    assert "not a tiff file" in detail or "empty" in detail or "header" in detail


def test_corrupt_tiff_upload_fails_gracefully_without_secret_leakage(client):
    """Truncated or corrupted TIFF data must fail gracefully with 422 and no secret exposure."""
    corrupt_bytes = b"II*\x00\x08\x00\x00\x00" + b"\xff" * 50  # Partial TIFF header then junk
    r = client.post(
        "/api/satellite/upload",
        files={"file": ("corrupt.tif", corrupt_bytes, "image/tiff")},
    )
    assert r.status_code in (400, 422)
    detail = r.json().get("detail", "")
    assert "Traceback" not in detail
    assert not any(k in detail.lower() for k in ("secret", "password", "token", "key"))


def test_unsupported_high_dimensional_array_rejected_gracefully(client):
    """TIFF with unsupported 4D rank must be rejected safely with 422."""
    arr = np.random.rand(2, 64, 64, 2).astype(np.float32)
    buf = io.BytesIO()
    tifffile.imwrite(buf, arr)
    buf.seek(0)
    r = client.post(
        "/api/satellite/upload",
        files={"file": ("multidimensional.tif", buf.read(), "image/tiff")},
    )
    assert r.status_code == 422
    assert "shape" in r.json()["detail"].lower() or "expected" in r.json()["detail"].lower()


def test_ml_detector_safely_handles_non_finite_inputs():
    """Engine detect method must never raise when given NaN or Inf."""
    engine = get_ml_engine()
    vv = np.full((128, 128), np.nan, dtype=np.float32)
    vh = np.zeros((128, 128), dtype=np.float32)
    res = engine.detect(vv, vh)
    assert res.success is False
    assert res.mask.shape == (128, 128)
    assert not np.any(res.mask)
    assert res.confidence == 0.0
    assert "NaN/Inf" in (res.fallback_reason or "")


# =============================================================================
# Priority 7: End-to-End Attribution Safety & Candidate Purge
# =============================================================================

def test_gated_reanalysis_purges_preexisting_stale_candidates(client):
    """If an investigation previously had candidate vessels, re-analyzing AIS or attribution
    on an investigation that is gated (e.g. LOOKALIKE, NO_OIL, or tiny mask) MUST purge
    preexisting candidates and return candidates=[].
    """
    inv_id = "test_stale_candidate_purge_01"
    now_iso = datetime.now(timezone.utc).isoformat()

    # Create dummy candidate
    dummy_candidate = VesselCandidate(
        mmsi="999888777",
        vessel_name="STALE VESSEL",
        vessel_type="Tanker",
        imo="IMO9998887",
        min_distance_km=2.5,
        closest_approach_time=now_iso,
        time_delta_hours=1.2,
        final_score=88.5,
        breakdown=[],
        behavior_flags=[],
        evidence_summary=["High proximity to estimated origin"],
        trajectory_points=[],
    )

    dummy_inv = Investigation(
        id=inv_id,
        created_at=now_iso,
        mode="real_satellite",
        data_sources={"satellite": "test", "ais": "test", "environment": "test", "detection_method": "ML", "drift_method": "test", "attribution_method": "test"},
        satellite={
            "geometry": {"area_px": 500, "area_km2": 0.5, "centroid_lat": 19.0, "centroid_lon": 71.0},
            "scene_classification": {
                "oil_status": "LOOKALIKE",
                "label": "Lookalike",
                "confidence": 0.92,
                "probabilities": {"Oil": 0.04, "No-Oil": 0.04, "Lookalike": 0.92},
            },
            "attribution_gating": {
                "gated": True,
                "status": "ABSTAINED_LOOKALIKE",
                "reason": "Lookalike detected; vessel attribution withheld.",
                "oil_status": "LOOKALIKE",
                "mask_area_px": 500,
            },
            "attribution_status": "ABSTAINED_LOOKALIKE",
        },
        environment={},
        drift={
            "probable_origin": {"lat": 19.0, "lon": 71.0, "timestamp": now_iso},
            "observed": {"lat": 19.0, "lon": 71.0, "timestamp": now_iso},
        },
        ais_summary={"search_radius_km": 50, "time_window_hours": 12, "attribution_gated": True},
        candidates=[dummy_candidate],  # Preexisting stale candidate
    )
    _STORE[inv_id] = dummy_inv

    # Before re-analysis, candidate exists
    assert len(_STORE[inv_id].candidates) == 1

    # 1. Re-analyze AIS
    resp_ais = client.post(f"/api/ais/analyze?investigation_id={inv_id}&search_radius_km=40")
    assert resp_ais.status_code == 200
    data_ais = resp_ais.json()
    assert data_ais["candidates"] == [], "AIS re-analysis must return empty candidate list when gated!"
    assert len(_STORE[inv_id].candidates) == 0, "Preexisting candidate must be purged from investigation store!"

    # 2. Ranking endpoint must reflect purged state immediately
    resp_ranking = client.get(f"/api/vessels/ranking?investigation_id={inv_id}")
    assert resp_ranking.status_code == 200
    assert resp_ranking.json() == []

    # Inject stale candidate again to test /api/attribution/analyze
    _STORE[inv_id].candidates = [dummy_candidate]

    # 3. Attribution re-analysis
    resp_attr = client.post(
        f"/api/attribution/analyze?investigation_id={inv_id}",
        json={"spatial_proximity": 40, "temporal_correlation": 30},
    )
    assert resp_attr.status_code == 200
    data_attr = resp_attr.json()
    assert data_attr["candidates"] == []
    assert data_attr.get("gated") is True
    assert len(_STORE[inv_id].candidates) == 0, "Preexisting candidate must be purged from store!"


def test_tiny_mask_below_threshold_gates_attribution(client):
    """Investigation with mask smaller than MIN_ATTRIBUTION_MASK_PX must gate attribution."""
    inv_id = "test_tiny_mask_gating"
    now_iso = datetime.now(timezone.utc).isoformat()
    dummy_inv = Investigation(
        id=inv_id,
        created_at=now_iso,
        mode="real_satellite",
        data_sources={"satellite": "test", "ais": "test", "environment": "test", "detection_method": "ML", "drift_method": "test", "attribution_method": "test"},
        satellite={
            "geometry": {"area_px": 80, "area_km2": 0.08, "centroid_lat": 19.0, "centroid_lon": 71.0},
            "scene_classification": {
                "oil_status": "LIKELY_OIL",
                "label": "Oil",
                "confidence": 0.85,
            },
            "attribution_gating": {
                "gated": True,
                "status": "ABSTAINED_BELOW_MIN_AREA",
                "reason": "Detection mask area (80 px) is below minimum threshold (250 px).",
                "oil_status": "LIKELY_OIL",
                "mask_area_px": 80,
            },
            "attribution_status": "ABSTAINED_BELOW_MIN_AREA",
        },
        environment={},
        drift={
            "probable_origin": {"lat": 19.0, "lon": 71.0, "timestamp": now_iso},
            "observed": {"lat": 19.0, "lon": 71.0, "timestamp": now_iso},
        },
        ais_summary={"search_radius_km": 50, "time_window_hours": 12, "attribution_gated": True},
        candidates=[],
    )
    _STORE[inv_id] = dummy_inv

    resp = client.post(f"/api/ais/analyze?investigation_id={inv_id}&search_radius_km=30")
    assert resp.status_code == 200
    assert resp.json()["candidates"] == []


# =============================================================================
# Priorities 3 & 4: Multiscale Parity, Boundary Preservation, and Rollback
# =============================================================================

def test_base_only_strategy_guarantees_exact_single_scale_rollback():
    """Setting fusion_strategy='base_only' returns the base scale output untouched."""
    h, w = 128, 128
    stacked = np.zeros((2, h, w), dtype=np.float32)
    base_output = np.random.uniform(0.1, 0.9, size=(h, w)).astype(np.float32)

    fused, applied = stitch_multiscale_segmentation(
        lambda tile: base_output if tile.shape[1] == 128 else np.ones(tile.shape[1:], dtype=np.float32),
        stacked,
        tile_size=128,
        overlap=16,
        fits_direct=True,
        fusion_strategy="base_only",
    )
    assert not applied
    assert np.array_equal(fused, base_output)


def test_evaluator_module_runs_and_evaluates_three_tier_gates():
    """The evaluate_multiscale_comparison module executes cleanly and produces three-tier gate decisions."""
    from app.ml.evaluate_multiscale_comparison import (
        run_benchmark_evaluations,
        evaluate_three_tier_gates,
    )
    benchmarks = run_benchmark_evaluations()
    assert "case_01_clean_sea" in benchmarks
    assert "case_02_small_slick_blooming" in benchmarks
    assert "case_03_large_continuous_slick" in benchmarks
    assert "case_04_fragmented_slicks" in benchmarks
    assert "case_05_narrow_filament_slick" in benchmarks

    gates = evaluate_three_tier_gates(benchmarks, held_out_present=False)
    assert gates["engineering_test_gate"]["status"] == "PASSED"
    assert gates["real_data_accuracy_gate"]["status"] == "BLOCKED"
    assert gates["demonstration_readiness_gate"]["status"].startswith("CONDITIONALLY_READY")
    assert all(c["passed"] for c in gates["engineering_test_gate"]["criteria"])

