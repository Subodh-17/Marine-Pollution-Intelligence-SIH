"""Tests for Multiscale Segmentation Safety, Large-Slick Recovery, and Boundary Preservation (Phase 2)."""
from __future__ import annotations

import numpy as np
import pytest

from app.ml.detector import get_ml_engine
from app.ml.tiling import (
    _generate_tile_weights,
    compute_tile_bounds,
    stitch_multiscale_segmentation,
    stitch_tiled_segmentation,
)


class TestTilingAndBoundaryFeathering:
    def test_compute_tile_bounds_covers_entire_scene(self):
        h, w = 600, 700
        tile_size = 256
        overlap = 32
        bounds = compute_tile_bounds(h, w, tile_size, overlap)

        coverage = np.zeros((h, w), dtype=int)
        for y0, y1, x0, x1 in bounds:
            assert (y1 - y0) <= tile_size
            assert (x1 - x0) <= tile_size
            coverage[y0:y1, x0:x1] += 1

        assert np.all(coverage >= 1), "Every pixel must be covered by at least one tile"

    def test_cosine_taper_weights_preserves_outer_edges(self):
        tile_h, tile_w = 256, 256
        overlap = 32

        # Interior tile
        w_interior = _generate_tile_weights(tile_h, tile_w, overlap, False, False, False, False)
        assert w_interior[0, 0] < 0.1
        assert w_interior[128, 128] == 1.0

        # Top-left corner tile (exterior scene edge)
        w_corner = _generate_tile_weights(tile_h, tile_w, overlap, True, False, True, False)
        # Exterior scene edges must remain 1.0 (no edge attenuation)
        assert w_corner[0, 0] == 1.0
        assert w_corner[0, 128] == 1.0
        # Interior edge must taper
        assert w_corner[-1, -1] < 0.1


class TestMultiscaleSafetyAndLargeSlickRecovery:
    def test_clean_sea_produces_zero_false_positives(self):
        dummy = np.zeros((2, 256, 256), dtype=np.float32)
        clean_model = lambda x: np.full(x.shape[1:], 0.01, dtype=np.float32)

        fused, ms_applied = stitch_multiscale_segmentation(
            clean_model, dummy, 256, 32, fits_direct=True, fusion_strategy="size_gated_closure"
        )
        assert ms_applied is True
        assert np.max(fused) < 0.05
        assert np.sum(fused > 0.5) == 0

    def test_small_slick_blooming_prevention(self):
        # Small circular slick of radius 10px (~314 px)
        dummy = np.zeros((2, 256, 256), dtype=np.float32)
        yy, xx = np.ogrid[:256, :256]
        mask = (yy - 128) ** 2 + (xx - 128) ** 2 <= 10 ** 2

        def small_slick_model(x):
            h, w = x.shape[1], x.shape[2]
            y, x_idx = np.ogrid[:h, :w]
            cy, cx = h // 2, w // 2
            r = 10 if h == 256 else 5
            prob = np.where((y - cy) ** 2 + (x_idx - cx) ** 2 <= r ** 2, 0.95, 0.01)
            return prob.astype(np.float32)

        prob_base, _ = stitch_multiscale_segmentation(
            small_slick_model, dummy, 256, 32, fits_direct=True, fusion_strategy="base_only"
        )
        prob_fused, _ = stitch_multiscale_segmentation(
            small_slick_model, dummy, 256, 32, fits_direct=True, fusion_strategy="size_gated_closure"
        )

        base_count = np.sum(prob_base > 0.5)
        fused_count = np.sum(prob_fused > 0.5)

        # Size-gated closure should not bloom on small slicks
        area_expansion_ratio = fused_count / max(1, base_count)
        assert area_expansion_ratio <= 1.05

    def test_large_continuous_slick_interior_recovery(self):
        # Large continuous slick with hollow center (receptive field limitation)
        dummy = np.zeros((2, 256, 256), dtype=np.float32)

        def hollow_large_model(x):
            h, w = x.shape[1], x.shape[2]
            prob = np.full((h, w), 0.01, dtype=np.float32)
            if h == 256:
                # Full scale model detects perimeter of 180x180 slick, but hollow center
                prob[38:218, 38:218] = 0.95
                prob[78:178, 78:178] = 0.05  # 100x100 hollow center
            else:
                # Coarse scale model (128x128) sees the whole slick without hollow
                prob[19:109, 19:109] = 0.92
            return prob

        prob_base, _ = stitch_multiscale_segmentation(
            hollow_large_model, dummy, 256, 32, fits_direct=True, fusion_strategy="base_only"
        )
        prob_fused, ms_applied = stitch_multiscale_segmentation(
            hollow_large_model, dummy, 256, 32, fits_direct=True, fusion_strategy="size_gated_closure"
        )

        assert ms_applied is True
        base_tp = np.sum(prob_base[78:178, 78:178] > 0.5)
        fused_tp = np.sum(prob_fused[78:178, 78:178] > 0.5)

        # Hollow interior should be recovered by size-gated infill
        assert base_tp == 0
        assert fused_tp > 8000, f"Expected >8000 recovered hollow pixels, got {fused_tp}"

    def test_fail_closed_fallback_on_coarse_exception(self):
        dummy = np.zeros((2, 256, 256), dtype=np.float32)

        def failing_model(x):
            if x.shape[1] != 256:
                raise RuntimeError("Simulated coarse-pass GPU out-of-memory error")
            return np.full((256, 256), 0.85, dtype=np.float32)

        # When coarse pass fails, should cleanly fall back to base without raising
        prob_fused, ms_applied = stitch_multiscale_segmentation(
            failing_model, dummy, 256, 32, fits_direct=True, fusion_strategy="size_gated_closure"
        )
        assert ms_applied is False
        assert np.allclose(prob_fused, 0.85)
