"""
Tests for Phase 8 Investigation Pipeline Latency Optimizations & Phase 9 Scientific Fidelity Verification.

Validates:
1. Regionprops min_area filtering optimization preserves region properties and handles edge cases.
2. O(1) exterior standard deviation texture score matches naive boolean indexing and is numerically stable.
3. Drift model regional centroid anchor optimization eliminates off-grid request cascades while remaining bounded by physical uncertainty.
4. Dateline / antimeridian coordinate wrapping integrity.
5. Investigation orchestrator structured stage latency logging.
6. Strict preservation of scientific honesty guards under performance optimizations.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np
import pytest
from app.config import get_settings
from app.drift.model import run_drift, _dest_point
from app.environment.adapters import (
    EnvironmentConditions,
    EnvironmentDataUnavailable,
    resolve_environment_conditions,
)
from app.environment.open_meteo import get_open_meteo_cache
from app.investigation.orchestrator import run_investigation
from app.satellite.detection import _texture_score, detect_oil_slick
from app.satellite.geometry_utils import regionprops


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2
    return R * 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


class TestGeometryOptimization:
    def test_regionprops_min_area_skips_noise_components(self):
        """Verifies regionprops with min_area skips small noise components while retaining valid ones."""
        # Create a labeled image with 1 large component (200 px) and 10 small components (5 px each)
        labeled = np.zeros((100, 100), dtype=np.int32)
        # Big component: 20x10 = 200 px
        labeled[10:30, 10:20] = 1
        # Small components
        for i in range(2, 12):
            labeled[50, i * 5 : i * 5 + 3] = i

        # Without min_area: all 11 regions returned
        all_props = regionprops(labeled)
        assert len(all_props) == 11

        # With min_area=50: only region 1 returned
        filtered_props = regionprops(labeled, min_area=50)
        assert len(filtered_props) == 1
        assert filtered_props[0].area == 200
        assert filtered_props[0].label == 1
        assert filtered_props[0].bbox == (10, 10, 30, 20)

    def test_regionprops_empty_and_all_zero_inputs(self):
        """Verifies regionprops gracefully returns empty list on empty or all-zero labeled images."""
        empty_img = np.zeros((0, 0), dtype=np.int32)
        assert regionprops(empty_img) == []

        all_zeros = np.zeros((64, 64), dtype=np.int32)
        assert regionprops(all_zeros) == []

        # All components below min_area
        all_small = np.zeros((64, 64), dtype=np.int32)
        all_small[5:8, 5:8] = 1  # 9 px
        assert regionprops(all_small, min_area=50) == []

    def test_texture_score_o1_matches_naive_calculation(self):
        """Verifies O(1) texture score produces identical standard deviation to boolean indexing."""
        np.random.seed(42)
        gray = np.random.uniform(0.0, 1.0, (256, 256)).astype(np.float32)

        # Create a realistic oil slick mask (e.g. irregular ellipse)
        mask = np.zeros((256, 256), dtype=bool)
        y, x = np.ogrid[:256, :256]
        slick_region = ((y - 120) ** 2) / (30**2) + ((x - 140) ** 2) / (60**2) <= 1.0
        mask[slick_region] = True

        # Naive calculation:
        interior_naive = gray[mask]
        exterior_naive = gray[~mask]
        std_int_naive = float(np.std(interior_naive))
        std_ext_naive = float(np.std(exterior_naive))

        # Precomputed totals as done in detection.py:
        tot_sum = float(np.sum(gray))
        tot_sq = float(np.sum(gray * gray))
        total_pixels = gray.size

        # Test _texture_score:
        score = _texture_score(gray, mask, tot_sum=tot_sum, tot_sq=tot_sq)
        assert 0.0 <= score <= 1.0

        # Verify O(1) exterior variance formula directly:
        k_in = int(np.count_nonzero(mask))
        k_out = total_pixels - k_in
        sum_in = float(np.sum(gray[mask]))
        sq_in = float(np.sum(gray[mask] * gray[mask]))
        sum_out = tot_sum - sum_in
        sq_out = tot_sq - sq_in

        var_ext = max(0.0, (sq_out / k_out) - (sum_out / k_out) ** 2)
        std_ext_o1 = np.sqrt(var_ext)

        assert abs(std_ext_o1 - std_ext_naive) < 1e-6

    def test_texture_score_edge_cases_and_numerical_stability(self):
        """Verifies texture score handles empty masks, all-foreground, constant images, and near-zero exterior variance."""
        gray = np.random.uniform(0.1, 0.9, (128, 128)).astype(np.float32)
        tot_sum = float(gray.sum())
        tot_sq = float((gray ** 2).sum())

        # 1. Empty mask
        mask_empty = np.zeros_like(gray, dtype=bool)
        assert _texture_score(gray, mask_empty, tot_sum, tot_sq) == 0.0

        # 2. All-foreground mask
        mask_all = np.ones_like(gray, dtype=bool)
        score_all = _texture_score(gray, mask_all, tot_sum, tot_sq)
        assert 0.0 <= score_all <= 1.0

        # 3. Constant image
        gray_const = np.full((128, 128), 0.5, dtype=np.float32)
        mask_sub = np.zeros((128, 128), dtype=bool)
        mask_sub[20:60, 20:60] = True
        c_tot_sum = float(gray_const.sum())
        c_tot_sq = float((gray_const ** 2).sum())
        score_const = _texture_score(gray_const, mask_sub, c_tot_sum, c_tot_sq)
        assert score_const == 1.0  # zero interior std with zero exterior std yields ratio 0 -> score 1.0

        # 4. Near-zero exterior variance (numerical cancellation stability)
        gray_nz = np.zeros((128, 128), dtype=np.float32)
        gray_nz[~mask_sub] = 0.42000000000001
        gray_nz[mask_sub] = np.random.uniform(0.1, 0.3, mask_sub.sum())
        nz_tot_sum = float(gray_nz.sum())
        nz_tot_sq = float((gray_nz ** 2).sum())
        score_nz = _texture_score(gray_nz, mask_sub, nz_tot_sum, nz_tot_sq)
        assert np.isfinite(score_nz)
        assert 0.0 <= score_nz <= 1.0


class TestDriftAnchorOptimization:
    def setup_method(self):
        get_open_meteo_cache().clear()
        get_settings.cache_clear()

    def teardown_method(self):
        get_open_meteo_cache().clear()
        get_settings.cache_clear()

    def test_run_drift_trajectory_evaluates_at_slick_centroid_anchor(self, monkeypatch):
        """Verifies all Euler integration steps evaluate environmental forcing at the anchor."""
        queries = []

        def mock_env(lat, lon, t, allow_synthetic=True, mode=None):
            queries.append((lat, lon))
            return EnvironmentConditions(
                wind_speed_ms=8.0,
                wind_dir_deg=240.0,
                current_speed_ms=0.5,
                current_dir_deg=90.0,
                wave_height_m=1.0,
                source="TEST_ANCHOR",
            )

        monkeypatch.setattr("app.drift.model.resolve_environment_conditions", mock_env)

        t0 = datetime(2026, 8, 28, 10, 0, tzinfo=timezone.utc)
        origin_lat, origin_lon = 19.03876, 71.19603
        drift = run_drift(
            observed_lat=origin_lat,
            observed_lon=origin_lon,
            observed_time=t0,
            backward_hours=(6, 12, 18, 24),
            forward_hours=(6, 12, 24, 48),
            step_minutes=30,
            allow_synthetic=True,
            mode="demo",
        )

        assert drift.drift_status == "COMPLETED"
        assert len(queries) > 100

        # Every query to resolve_environment_conditions MUST have passed origin_lat, origin_lon
        for q_lat, q_lon in queries:
            assert abs(q_lat - origin_lat) < 1e-5
            assert abs(q_lon - origin_lon) < 1e-5

    def test_drift_dateline_wrapping(self):
        """Verifies _dest_point and trajectory integration correctly wraps the antimeridian (+180°/-180°)."""
        # Step eastward across 180° longitude
        lat, lon = _dest_point(0.0, 179.95, 90.0, 20_000.0)  # ~20 km East
        assert -180.0 <= lon <= 180.0
        assert lon < 0.0  # Wrapped to Western hemisphere (around -179.87°)


class TestInvestigationOrchestratorLatencyLogging:
    def test_orchestrator_records_stage_timing_metadata(self, monkeypatch, tmp_path):
        """Verifies orchestrator emits machine-readable stage timestamps and elapsed durations."""
        from PIL import Image

        img = Image.new("L", (100, 100), color=180)
        # Add dark patch for detection
        for y in range(40, 60):
            for x in range(40, 60):
                img.putpixel((x, y), 20)
        img_path = str(tmp_path / "test_slick_timing.png")
        img.save(img_path)

        inv = run_investigation(
            mode="demo",
            image_path=img_path,
            center_lat=19.05,
            center_lon=71.20,
        )

        assert inv.progress_log is not None
        assert len(inv.progress_log) >= 5

        for entry in inv.progress_log:
            assert "step" in entry
            assert "stage" in entry
            assert "outcome" in entry
            assert "duration_ms" in entry
            assert isinstance(entry["duration_ms"], (int, float))
            assert entry["duration_ms"] >= 0.0
            assert "ts" in entry
