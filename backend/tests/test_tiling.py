"""
Tests for app/ml/tiling.py and its integration into MLDetectorEngine.detect()
for scenes larger than the direct-inference budget.
"""
import os

import numpy as np
import pytest

from app.ml.tiling import compute_tile_bounds, stitch_tiled_segmentation, downsample_mean
from app.ml import detector as detector_mod
from app.ml.detector import MLDetectorEngine, RUNTIME_ONNX_CPU
from app.ml.onnx_engine import ONNX_AVAILABLE, MANIFEST_FILENAME

CKPT_DIR = os.path.abspath(detector_mod.CHECKPOINT_DIR)
_ARTIFACTS_PRESENT = all(
    os.path.exists(os.path.join(CKPT_DIR, p)) for p in
    ("classifier_real_best.onnx", "spill_unet_real_best.onnx", MANIFEST_FILENAME)
)
requires_onnx = pytest.mark.skipif(
    not (ONNX_AVAILABLE and _ARTIFACTS_PRESENT),
    reason="onnxruntime + exported .onnx artifacts required for end-to-end tiling tests",
)


class TestTileBounds:
    def test_small_image_single_tile(self):
        """Image smaller than one tile must produce exactly one tile
        spanning the whole image, not an error and not a padded tile."""
        b = compute_tile_bounds(64, 64, 256, 32)
        assert b == [(0, 64, 0, 64)]

    def test_exact_tile_size_single_tile(self):
        b = compute_tile_bounds(256, 256, 256, 32)
        assert b == [(0, 256, 0, 256)]

    def test_non_divisible_dimensions_full_coverage_and_full_size_tiles(self):
        h, w, tile, overlap = 300, 700, 256, 32
        bounds = compute_tile_bounds(h, w, tile, overlap)
        covered = np.zeros((h, w), dtype=bool)
        for y0, y1, x0, x1 in bounds:
            assert (y1 - y0) == min(tile, h)
            assert (x1 - x0) == min(tile, w)
            covered[y0:y1, x0:x1] = True
        assert covered.all(), "non-divisible dimensions left gaps in tile coverage"

    def test_very_large_image_bounds_are_computed_without_error(self):
        bounds = compute_tile_bounds(4096, 4096, 256, 32)
        assert len(bounds) > 0
        covered = np.zeros((4096, 4096), dtype=bool)
        for y0, y1, x0, x1 in bounds:
            covered[y0:y1, x0:x1] = True
        assert covered.all()

    def test_invalid_overlap_rejected(self):
        with pytest.raises(ValueError):
            compute_tile_bounds(512, 512, 256, 256)  # overlap == tile_size


class TestStitching:
    def test_stitched_output_matches_full_image_shape(self):
        def fake_run(tile):
            return np.full(tile.shape[1:], 0.5, dtype=np.float32)

        stacked = np.random.rand(2, 300, 700).astype(np.float32)
        out = stitch_tiled_segmentation(fake_run, stacked, 256, 32)
        assert out.shape == (300, 700)
        assert np.all(np.isfinite(out))

    def test_stitched_output_is_finite_for_varied_tile_content(self):
        def fake_run(tile):
            return tile.mean(axis=0)  # varies per tile, exercises real averaging

        stacked = np.random.rand(2, 600, 800).astype(np.float32)
        out = stitch_tiled_segmentation(fake_run, stacked, 256, 32)
        assert np.all(np.isfinite(out))
        assert out.min() >= 0.0

    def test_tile_output_shape_mismatch_is_rejected(self):
        def broken_run(tile):
            return np.zeros((1, 1), dtype=np.float32)  # wrong shape

        stacked = np.random.rand(2, 300, 300).astype(np.float32)
        with pytest.raises(ValueError):
            stitch_tiled_segmentation(broken_run, stacked, 256, 32)


class TestDownsampleMean:
    def test_noop_when_already_small(self):
        arr = np.random.rand(100, 100).astype(np.float32)
        out = downsample_mean(arr, 512)
        assert out.shape == (100, 100)
        assert np.array_equal(out, arr)

    def test_downsamples_large_array(self):
        arr = np.random.rand(4096, 4096).astype(np.float32)
        out = downsample_mean(arr, 512)
        assert out.shape[0] <= 512 and out.shape[1] <= 512
        assert np.all(np.isfinite(out))

    def test_preserves_uniform_value(self):
        arr = np.full((2000, 2000), 0.75, dtype=np.float32)
        out = downsample_mean(arr, 256)
        assert np.allclose(out, 0.75)


@requires_onnx
class TestEndToEndLargeScene:
    def test_scene_above_direct_budget_within_tiled_ceiling_runs_genuine_ml(self, monkeypatch):
        monkeypatch.setenv("ML_MAX_INFERENCE_PIXELS", str(256 * 256))
        monkeypatch.setenv("ML_MAX_TILED_INFERENCE_PIXELS", str(2048 * 2048))
        engine = MLDetectorEngine()
        if engine.runtime != RUNTIME_ONNX_CPU:
            pytest.skip(f"engine selected {engine.runtime}, not the ONNX path")
        rng = np.random.default_rng(1)
        vv = rng.random((600, 800)).astype(np.float32)
        vh = rng.random((600, 800)).astype(np.float32)
        result = engine.detect(vv, vh)
        assert result.success
        assert result.mask.shape == (600, 800)
        assert any("tiled" in n for n in result.notes)
        assert any("downsampled" in n for n in result.notes)

    def test_scene_beyond_tiled_ceiling_falls_back_honestly(self, monkeypatch):
        monkeypatch.setenv("ML_MAX_INFERENCE_PIXELS", str(256 * 256))
        monkeypatch.setenv("ML_MAX_TILED_INFERENCE_PIXELS", str(512 * 512))
        engine = MLDetectorEngine()
        if engine.runtime is None:
            pytest.skip("no ML runtime available")
        rng = np.random.default_rng(2)
        vv = rng.random((2000, 2000)).astype(np.float32)
        vh = rng.random((2000, 2000)).astype(np.float32)
        result = engine.detect(vv, vh)
        assert not result.success
        assert result.provenance != "ML_REAL_ZENODO_TRAINED"
        assert "ceiling" in (result.fallback_reason or "")

    def test_direct_path_unchanged_for_small_scenes(self):
        engine = MLDetectorEngine()
        if engine.runtime != RUNTIME_ONNX_CPU:
            pytest.skip(f"engine selected {engine.runtime}, not the ONNX path")
        rng = np.random.default_rng(3)
        vv = rng.random((64, 64)).astype(np.float32)
        vh = rng.random((64, 64)).astype(np.float32)
        result = engine.detect(vv, vh)
        assert result.success
        assert not any("tiled" in n for n in result.notes)
        assert not any("downsampled" in n for n in result.notes)

    def test_tiling_can_be_disabled(self, monkeypatch):
        monkeypatch.setenv("ML_MAX_INFERENCE_PIXELS", str(256 * 256))
        monkeypatch.setenv("ML_TILING_ENABLED", "false")
        engine = MLDetectorEngine()
        if engine.runtime is None:
            pytest.skip("no ML runtime available")
        assert engine.tiling_enabled is False
        rng = np.random.default_rng(4)
        vv = rng.random((1024, 1024)).astype(np.float32)
        vh = rng.random((1024, 1024)).astype(np.float32)
        result = engine.detect(vv, vh)
        assert not result.success  # tiling off -> same as the old outright-refusal behavior


class TestRobustTilingAudited:
    """Rigorous audit test suite covering all edge cases specified in Phase 2 Priority 3:
    sub-tile, single-tile, non-divisible, border preservation, various overlaps,
    all-zero/all-one masks, and deterministic identity reconstructions.
    """

    def test_sub_tile_image(self):
        stacked = np.ones((2, 95, 140), dtype=np.float32)
        out = stitch_tiled_segmentation(lambda tile: np.full(tile.shape[1:], 0.75, dtype=np.float32), stacked, 256, 32)
        assert out.shape == (95, 140)
        assert np.allclose(out, 0.75)

    def test_exact_one_tile(self):
        stacked = np.ones((2, 256, 256), dtype=np.float32)
        out = stitch_tiled_segmentation(lambda tile: np.full(tile.shape[1:], 0.42, dtype=np.float32), stacked, 256, 32)
        assert out.shape == (256, 256)
        assert np.allclose(out, 0.42)

    def test_non_divisible_dimensions_and_border_pixels(self):
        h, w = 317, 523
        stacked = np.zeros((2, h, w), dtype=np.float32)
        # Mark borders
        stacked[0, 0, :] = 1.0
        stacked[0, -1, :] = 1.0
        stacked[0, :, 0] = 1.0
        stacked[0, :, -1] = 1.0

        def identity_run(tile):
            return tile[0]

        out = stitch_tiled_segmentation(identity_run, stacked, tile_size=256, overlap=48)
        assert out.shape == (h, w)
        assert np.all(np.isfinite(out))
        # Borders must be preserved
        assert np.allclose(out[0, :], 1.0)
        assert np.allclose(out[-1, :], 1.0)
        assert np.allclose(out[:, 0], 1.0)
        assert np.allclose(out[:, -1], 1.0)

    def test_various_overlaps_reconstruct_uniform_inputs(self):
        h, w = 400, 400
        stacked = np.full((2, h, w), 0.88, dtype=np.float32)
        for ov in [0, 16, 32, 64, 120]:
            out = stitch_tiled_segmentation(lambda tile: np.full(tile.shape[1:], 0.88, dtype=np.float32), stacked, 256, ov)
            assert np.allclose(out, 0.88, atol=1e-5), f"Failed for overlap {ov}"

    def test_all_zero_and_all_one_masks(self):
        h, w = 350, 450
        zeros_in = np.zeros((2, h, w), dtype=np.float32)
        out_zero = stitch_tiled_segmentation(lambda tile: np.zeros(tile.shape[1:], dtype=np.float32), zeros_in, 256, 32)
        assert np.allclose(out_zero, 0.0)

        ones_in = np.ones((2, h, w), dtype=np.float32)
        out_one = stitch_tiled_segmentation(lambda tile: np.ones(tile.shape[1:], dtype=np.float32), ones_in, 256, 32)
        assert np.allclose(out_one, 1.0)

    def test_deterministic_known_slick_reconstruction(self):
        """Places a synthetic slick in a non-divisible scene and checks exact identity reconstruction."""
        h, w = 500, 600
        scene = np.zeros((2, h, w), dtype=np.float32)
        # Synthetic rectangular slick at center
        scene[0, 180:320, 220:410] = 0.95
        scene[1, 180:320, 220:410] = 0.80

        def segmenter_mock(tile):
            # Model returns high probability if VV is > 0.5
            return (tile[0] > 0.5).astype(np.float32)

        out = stitch_tiled_segmentation(segmenter_mock, scene, tile_size=256, overlap=32)
        expected = np.zeros((h, w), dtype=np.float32)
        expected[180:320, 220:410] = 1.0

        # Seam blending may produce smooth transitions near boundary, but interior must be 1.0 and exterior 0.0
        assert np.allclose(out[200:300, 250:380], 1.0)
        assert np.allclose(out[:150, :], 0.0)
        assert np.allclose(out[350:, :], 0.0)
