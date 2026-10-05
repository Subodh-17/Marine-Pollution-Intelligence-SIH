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
