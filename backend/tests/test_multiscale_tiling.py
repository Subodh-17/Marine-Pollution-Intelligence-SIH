"""
Tests for multiscale large-slick segmentation recovery (stitch_multiscale_segmentation)
and inference pipeline hardening in app/ml/tiling.py and app/ml/detector.py.
"""
import concurrent.futures
import os
import numpy as np
import pytest

from app.ml.tiling import (
    compute_tile_bounds,
    stitch_tiled_segmentation,
    stitch_multiscale_segmentation,
)
from app.ml.detector import (
    MLDetectorEngine,
    get_ml_engine,
    RUNTIME_ONNX_CPU,
    CHECKPOINT_DIR,
)
from app.ml.onnx_engine import ONNX_AVAILABLE, MANIFEST_FILENAME


_ARTIFACTS_PRESENT = all(
    os.path.exists(os.path.join(CHECKPOINT_DIR, p))
    for p in ("classifier_real_best.onnx", "spill_unet_real_best.onnx", MANIFEST_FILENAME)
)
requires_onnx = pytest.mark.skipif(
    not (ONNX_AVAILABLE and _ARTIFACTS_PRESENT),
    reason="onnxruntime + exported .onnx artifacts required",
)


class TestMultiscaleTilingUnit:
    """Unit tests for stitch_multiscale_segmentation function."""

    def test_invalid_input_shape_raises(self):
        with pytest.raises(ValueError, match="expected"):
            stitch_multiscale_segmentation(lambda x: x[0], np.zeros((3, 100, 100)), 256, 32)
        with pytest.raises(ValueError, match="expected"):
            stitch_multiscale_segmentation(lambda x: x[0], np.zeros((100, 100)), 256, 32)

    def test_small_image_bypasses_coarse_pass_safely(self):
        """Images smaller than 64x64 cannot be downsampled to U-Net divisible dimensions."""
        stacked = np.full((2, 48, 56), 0.3, dtype=np.float32)
        out, applied = stitch_multiscale_segmentation(
            lambda tile: np.full(tile.shape[1:], 0.4, dtype=np.float32),
            stacked,
            tile_size=256,
            overlap=32,
            fits_direct=True,
        )
        assert out.shape == (48, 56)
        assert not applied
        assert np.allclose(out, 0.4)

    def test_non_divisible_dimensions_produce_exact_shape(self):
        h, w = 317, 429
        stacked = np.random.rand(2, h, w).astype(np.float32)

        def mock_segmenter(tile):
            return np.full(tile.shape[1:], 0.65, dtype=np.float32)

        out, applied = stitch_multiscale_segmentation(
            mock_segmenter,
            stacked,
            tile_size=256,
            overlap=32,
            fits_direct=False,
        )
        assert out.shape == (h, w)
        assert applied
        assert np.all(np.isfinite(out))
        assert np.all(out >= 0.0) and np.all(out <= 1.0)

    def test_clean_sea_abstention_preserved(self):
        """Clean sea (flat low probability) must remain low probability after fusion (no false alarms)."""
        h, w = 256, 256
        stacked = np.full((2, h, w), 0.6, dtype=np.float32)

        def mock_clean_detector(tile):
            return np.full(tile.shape[1:], 0.005, dtype=np.float32)

        out, applied = stitch_multiscale_segmentation(
            mock_clean_detector,
            stacked,
            tile_size=256,
            overlap=32,
            fits_direct=True,
        )
        assert applied
        assert out.shape == (h, w)
        # Output must remain well below any detection threshold (e.g. 0.5)
        assert np.max(out) < 0.05
        assert np.all(out < 0.5)

    def test_large_slick_interior_recovery(self):
        """Simulates receptive-field interior failure at base scale and recovery at coarse scale."""
        h, w = 256, 256
        # A large slick covering the center 180x180 region
        stacked = np.zeros((2, h, w), dtype=np.float32)

        def simulated_rf_model(tile):
            th, tw = tile.shape[1], tile.shape[2]
            prob = np.zeros((th, tw), dtype=np.float32)
            # If resolution is full (256x256), edges detect oil (~0.85) but center is hollowed out (~0.10)
            if th == 256 and tw == 256:
                prob[38:218, 38:218] = 0.10  # hollowed out center
                # Ring of 20px around slick boundary detects correctly
                prob[38:58, 38:218] = 0.85
                prob[198:218, 38:218] = 0.85
                prob[38:218, 38:58] = 0.85
                prob[38:218, 198:218] = 0.85
            else:
                # Coarse scale (128x128): receptive field covers the entire slick!
                # Slick in coarse space is ~ 19:109, 19:109
                prob[19:109, 19:109] = 0.90
            return prob

        # Base scale alone
        base_alone = simulated_rf_model(stacked)
        # Center is hollowed out
        assert base_alone[128, 128] == 0.10
        assert np.sum(base_alone > 0.5) < (180 * 180) / 2

        # Multiscale fusion
        out, applied = stitch_multiscale_segmentation(
            simulated_rf_model,
            stacked,
            tile_size=256,
            overlap=32,
            fits_direct=True,
        )
        assert applied
        # Center is now recovered (> 0.8)
        assert out[128, 128] > 0.80
        # Slick mask area is substantially recovered
        assert np.sum(out > 0.5) > (180 * 180 * 0.90)


@requires_onnx
class TestMultiscaleDetectorIntegration:
    """Integration tests with real ONNX engine and environment controls."""

    def test_multiscale_flag_disabled_by_default_in_production(self):
        """Production default is single-scale baseline until independent validation promotes multiscale."""
        engine = MLDetectorEngine()
        assert engine.multiscale_enabled is False

        rng = np.random.default_rng(42)
        vv = rng.random((256, 256)).astype(np.float32)
        vh = rng.random((256, 256)).astype(np.float32)
        result = engine.detect(vv, vh)
        assert result.success
        assert not any("Multiscale large-slick recovery active" in n for n in result.notes)

    def test_multiscale_can_be_enabled_via_env(self, monkeypatch):
        """Experimental candidate size_gated_closure can be explicitly enabled via env flag."""
        monkeypatch.setenv("ML_MULTISCALE_SEGMENTATION", "true")
        engine = MLDetectorEngine()
        assert engine.multiscale_enabled is True
        assert engine.multiscale_fusion_strategy == "size_gated_closure"

        rng = np.random.default_rng(43)
        vv = rng.random((256, 256)).astype(np.float32)
        vh = rng.random((256, 256)).astype(np.float32)
        result = engine.detect(vv, vh)
        assert result.success
        assert any("Multiscale large-slick recovery active" in n for n in result.notes)

    def test_thread_safe_singleton_concurrency(self):
        """Multiple threads requesting get_ml_engine concurrently must receive identical instance."""
        def get_instance():
            return get_ml_engine()

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(get_instance) for _ in range(20)]
            instances = [f.result() for f in futures]

        first = instances[0]
        for inst in instances:
            assert inst is first

    def test_nan_and_inf_inputs_fail_closed_gracefully(self):
        """Invalid floating point inputs must return structured invalid result rather than raising."""
        engine = get_ml_engine()
        vv = np.full((256, 256), np.nan, dtype=np.float32)
        vh = np.ones((256, 256), dtype=np.float32)
        result = engine.detect(vv, vh)
        assert not result.success
        assert result.mask.shape == (256, 256)
        assert result.confidence == 0.0
        assert "NaN/Inf" in (result.fallback_reason or "")

        vv = np.full((256, 256), np.inf, dtype=np.float32)
        result = engine.detect(vv, vh)
        assert not result.success

    def test_shape_mismatch_fails_closed_gracefully(self):
        engine = get_ml_engine()
        vv = np.ones((256, 256), dtype=np.float32)
        vh = np.ones((256, 300), dtype=np.float32)
        result = engine.detect(vv, vh)
        assert not result.success
        assert "shape mismatch" in (result.fallback_reason or "").lower()

    def test_boundary_blooming_eliminated_by_boundary_preserved_strategy(self):
        """Scientific validation: direct_max causes boundary blooming on small slicks due to
        bilinear interpolation of half-resolution masks. boundary_preserved infill uses coarse
        erosion to retract coarse support inward, eliminating false positive blooming."""
        h, w = 256, 256
        stacked = np.zeros((2, h, w), dtype=np.float32)
        # Ground truth small slick: circle of radius 10 at (100, 100)
        yy, xx = np.ogrid[:h, :w]
        dist = np.sqrt((yy - 100)**2 + (xx - 100)**2)
        gt_mask = dist <= 10.0
        # Base scale: detects slick well, with 1px transition boundary
        prob_base_sim = np.zeros((h, w), dtype=np.float32)
        prob_base_sim[dist <= 10.0] = 0.88
        prob_base_sim[(dist > 10.0) & (dist <= 11.0)] = 0.55

        # Coarse scale (128x128): center at (50, 50), radius 5
        yy_c, xx_c = np.ogrid[:128, :128]
        dist_c = np.sqrt((yy_c - 50)**2 + (xx_c - 50)**2)
        prob_coarse_sim = np.zeros((128, 128), dtype=np.float32)
        prob_coarse_sim[dist_c <= 5.0] = 0.90
        prob_coarse_sim[(dist_c > 5.0) & (dist_c <= 6.0)] = 0.60

        def mock_evaluator(arr):
            if arr.shape[1] == 256:
                return prob_base_sim
            return prob_coarse_sim

        # 1. Base alone
        base_pred = prob_base_sim > 0.5
        base_fp = np.sum(base_pred & ~gt_mask)

        # 2. direct_max fusion
        out_direct, _ = stitch_multiscale_segmentation(
            mock_evaluator, stacked, tile_size=256, overlap=32, fits_direct=True, fusion_strategy="direct_max"
        )
        direct_pred = out_direct > 0.5
        direct_fp = np.sum(direct_pred & ~gt_mask)
        # direct_max causes boundary blooming (increased false positives)
        assert direct_fp > base_fp

        # 3. boundary_preserved fusion (promoted default)
        out_preserved, _ = stitch_multiscale_segmentation(
            mock_evaluator, stacked, tile_size=256, overlap=32, fits_direct=True, fusion_strategy="boundary_preserved"
        )
        preserved_pred = out_preserved > 0.5
        preserved_fp = np.sum(preserved_pred & ~gt_mask)
        # boundary_preserved strictly prevents blooming: FP matches baseline exactly!
        assert preserved_fp == base_fp
        assert preserved_fp < direct_fp

    def test_base_only_strategy_acts_as_zero_risk_rollback(self):
        """fusion_strategy='base_only' must return the base segmentation unchanged."""
        h, w = 128, 128
        stacked = np.zeros((2, h, w), dtype=np.float32)
        expected = np.random.uniform(0.1, 0.9, size=(h, w)).astype(np.float32)

        out, applied = stitch_multiscale_segmentation(
            lambda tile: expected if tile.shape[1] == 128 else np.ones(tile.shape[1:], dtype=np.float32),
            stacked,
            tile_size=128,
            overlap=16,
            fits_direct=True,
            fusion_strategy="base_only",
        )
        assert not applied
        assert np.array_equal(out, expected)

    def test_slicks_at_image_boundaries(self):
        """Slicks truncated by scene boundary must stitch correctly without index errors or boundary artifacts."""
        h, w = 256, 256
        stacked = np.zeros((2, h, w), dtype=np.float32)

        def mock_boundary_model(tile):
            th, tw = tile.shape[1], tile.shape[2]
            res = np.zeros((th, tw), dtype=np.float32)
            # Slick touching the top-left corner
            res[:30, :30] = 0.85
            return res

        out, applied = stitch_multiscale_segmentation(
            mock_boundary_model, stacked, tile_size=256, overlap=32, fits_direct=True
        )
        assert applied
        assert out.shape == (256, 256)
        assert out[0, 0] > 0.8
        assert out[200, 200] < 0.1

    def test_narrow_filament_slick_not_erased_by_multiscale(self):
        """Verifies that morphological erosion in coarse space does NOT erase narrow structures."""
        h, w = 256, 256
        stacked = np.zeros((2, h, w), dtype=np.float32)
        gt_fil = np.zeros((h, w), dtype=bool)
        gt_fil[40:180, 126:130] = True  # 4px wide vertical filament

        prob_base = np.zeros((h, w), dtype=np.float32)
        prob_base[gt_fil] = 0.88
        prob_coarse = np.zeros((128, 128), dtype=np.float32)
        prob_coarse[20:90, 63:65] = 0.85

        def eval_fn(arr):
            return prob_base if arr.shape[1] == 256 else prob_coarse

        out, applied = stitch_multiscale_segmentation(
            eval_fn, stacked, tile_size=256, overlap=32, fits_direct=True, fusion_strategy="boundary_preserved"
        )
        assert applied
        # Filament is fully preserved in output
        pred = out > 0.5
        assert np.sum(pred & gt_fil) == np.sum(gt_fil)

    def test_fragmented_slicks_component_count_preserved(self):
        """Verifies that separate slick components are not artificially bridged or merged."""
        import scipy.ndimage as ndi
        h, w = 256, 256
        stacked = np.zeros((2, h, w), dtype=np.float32)
        yy, xx = np.ogrid[:h, :w]
        prob_base = np.zeros((h, w), dtype=np.float32)
        for cy, cx in [(60, 60), (60, 180), (180, 60), (180, 180)]:
            d = np.sqrt((yy - cy)**2 + (xx - cx)**2)
            prob_base[d <= 12.0] = 0.85

        yy_c, xx_c = np.ogrid[:128, :128]
        prob_coarse = np.zeros((128, 128), dtype=np.float32)
        for cy, cx in [(30, 30), (30, 90), (90, 30), (90, 90)]:
            dc = np.sqrt((yy_c - cy)**2 + (xx_c - cx)**2)
            prob_coarse[dc <= 6.0] = 0.88

        out, _ = stitch_multiscale_segmentation(
            lambda arr: prob_base if arr.shape[1] == 256 else prob_coarse,
            stacked, 256, 32, fits_direct=True, fusion_strategy="boundary_preserved",
        )
        _, n_base = ndi.label(prob_base > 0.5)
        _, n_out = ndi.label(out > 0.5)
        assert n_base == 4
        assert n_out == 4

    def test_dark_lookalike_remains_below_detection_threshold(self):
        """A uniform sub-threshold dark region must not be amplified into a positive detection."""
        h, w = 256, 256
        stacked = np.zeros((2, h, w), dtype=np.float32)
        prob_la = np.full((h, w), 0.35, dtype=np.float32)
        prob_la_c = np.full((128, 128), 0.35, dtype=np.float32)

        out, _ = stitch_multiscale_segmentation(
            lambda arr: prob_la if arr.shape[1] == 256 else prob_la_c,
            stacked, 256, 32, fits_direct=True, fusion_strategy="boundary_preserved",
        )
        assert np.max(out) <= 0.35
        assert np.sum(out > 0.5) == 0

    def test_size_gated_closure_suppresses_isolated_lookalike_speckles(self):
        """Isolated speckles (< 100 px) in base do not trigger coarse expansion."""
        h, w = 256, 256
        stacked = np.zeros((2, h, w), dtype=np.float32)
        prob_base = np.zeros((h, w), dtype=np.float32)
        # Small 20-pixel lookalike noise speckle in base
        prob_base[50:54, 50:55] = 0.80

        # Coarse scale has a broad 40x40 coarse dark blob
        prob_coarse = np.zeros((128, 128), dtype=np.float32)
        prob_coarse[15:45, 15:45] = 0.85

        out, applied = stitch_multiscale_segmentation(
            lambda arr: prob_base if arr.shape[1] == 256 else prob_coarse,
            stacked, 256, 32, fits_direct=True, fusion_strategy="size_gated_closure",
        )
        assert applied
        # Because base component has area < 100 px, coarse expansion is suppressed
        assert np.array_equal(out, prob_base)

    def test_size_gated_closure_infills_large_hollow_slick(self):
        """Large slick ring (area >= 100 px) triggers morphological closure and coarse infill."""
        h, w = 256, 256
        stacked = np.zeros((2, h, w), dtype=np.float32)
        prob_base = np.zeros((h, w), dtype=np.float32)
        # Outer ring of large slick (box boundary of size 60x60, 4px thick -> area = ~900 px)
        prob_base[40:100, 40:44] = 0.85
        prob_base[40:100, 96:100] = 0.85
        prob_base[40:44, 40:100] = 0.85
        prob_base[96:100, 40:100] = 0.85
        # Interior center is hollow: prob_base[60:80, 60:80] == 0.0

        # Coarse scale has interior support
        prob_coarse = np.zeros((128, 128), dtype=np.float32)
        prob_coarse[20:50, 20:50] = 0.85

        out, applied = stitch_multiscale_segmentation(
            lambda arr: prob_base if arr.shape[1] == 256 else prob_coarse,
            stacked, 256, 32, fits_direct=True, fusion_strategy="size_gated_closure",
        )
        assert applied
        # Interior center was infilled by coarse pass
        assert np.all(out[65:75, 65:75] > 0.50)



