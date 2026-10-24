"""
Focused unit tests for SAR band normalization (_normalize_band / normalize_sar_band).

Verifies:
1. Numerical equivalence to the legacy float64 normalization within appropriate tolerance (atol=1e-6).
2. Output dtype is strictly float32 and shape/contiguity are preserved.
3. Input array is never modified in-place.
4. Proper clipping to [0.0, 1.0] for values outside the 1st/99th percentiles.
5. Constant band and zero-range inputs return zeros_like float32.
6. Non-finite inputs (NaN, +Inf, -Inf) are handled consistently with legacy behavior.
7. Multi-dtype compatibility (float32, float64, integer types, strided slices).
8. Memory efficiency: avoiding full-size float64 intermediates.
9. Public alias (normalize_sar_band) and version string integrity.
"""
import os
import tracemalloc
import numpy as np
import pytest

from app.satellite.real_image import (
    _normalize_band,
    normalize_sar_band,
    PREPROCESSING_VERSION,
)


def _legacy_normalize_band(band: np.ndarray) -> np.ndarray:
    """Exact legacy normalization implementation prior to memory optimization."""
    band = band.astype(np.float64)
    lo, hi = np.percentile(band, [1, 99])
    if hi <= lo:
        return np.zeros_like(band, dtype=np.float32)
    return np.clip((band - lo) / (hi - lo), 0, 1).astype(np.float32)


class TestBandNormalizationEquivalence:
    """Tests verifying numerical equivalence between new and legacy normalization."""

    @pytest.mark.parametrize("seed", [1, 42, 2026])
    @pytest.mark.parametrize("shape", [(64, 64), (128, 256), (512, 512)])
    def test_numerical_equivalence_uniform_distribution(self, seed, shape):
        rng = np.random.default_rng(seed)
        band = rng.uniform(-35.0, 5.0, size=shape).astype(np.float32)

        legacy = _legacy_normalize_band(band)
        optimized = _normalize_band(band)

        assert optimized.dtype == np.float32
        assert optimized.shape == band.shape
        np.testing.assert_allclose(optimized, legacy, atol=1e-6, rtol=1e-5)

    @pytest.mark.parametrize("seed", [10, 99])
    def test_numerical_equivalence_sar_like_distribution(self, seed):
        """SAR radar backscatter is typically heavy-tailed with extreme bright specular reflections."""
        rng = np.random.default_rng(seed)
        # Log-normal intensity converted to dB-like scale
        raw = rng.lognormal(mean=1.5, sigma=0.8, size=(256, 256)).astype(np.float32)
        band = 10.0 * np.log10(np.maximum(raw, 1e-4))

        legacy = _legacy_normalize_band(band)
        optimized = _normalize_band(band)

        np.testing.assert_allclose(optimized, legacy, atol=1e-6, rtol=1e-5)

    def test_numerical_equivalence_on_large_scene(self):
        """Test on large (1024, 1024) realistic array."""
        rng = np.random.default_rng(777)
        band = rng.normal(loc=-15.0, scale=4.0, size=(1024, 1024)).astype(np.float32)

        legacy = _legacy_normalize_band(band)
        optimized = _normalize_band(band)

        assert optimized.dtype == np.float32
        np.testing.assert_allclose(optimized, legacy, atol=1e-6, rtol=1e-5)


class TestClippingAndBounds:
    """Tests verifying clipping semantics and value bounds."""

    def test_clipping_outliers_below_and_above_percentiles(self):
        arr = np.linspace(0.0, 100.0, 1000, dtype=np.float32)
        arr[0] = -10000.0   # Extreme negative outlier
        arr[-1] = 50000.0   # Extreme positive outlier

        out = _normalize_band(arr)

        assert out.dtype == np.float32
        assert out[0] == 0.0
        assert out[-1] == 1.0
        assert out.min() >= 0.0
        assert out.max() <= 1.0

    def test_all_finite_outputs_within_zero_one(self):
        rng = np.random.default_rng(1234)
        band = rng.standard_cauchy(size=(200, 200)).astype(np.float32)

        out = _normalize_band(band)

        assert out.min() >= 0.0
        assert out.max() <= 1.0


class TestConstantAndDegenerateBands:
    """Tests verifying constant bands, invalid bounds, and empty arrays."""

    @pytest.mark.parametrize("const_val", [0.0, 1.0, -25.5, 100.0])
    def test_constant_band_returns_zeros(self, const_val):
        band = np.full((128, 128), const_val, dtype=np.float32)
        legacy = _legacy_normalize_band(band)
        optimized = _normalize_band(band)

        assert optimized.dtype == np.float32
        assert optimized.shape == band.shape
        np.testing.assert_array_equal(optimized, 0.0)
        np.testing.assert_array_equal(optimized, legacy)

    def test_single_value_array(self):
        band = np.array([42.0], dtype=np.float32)
        out = _normalize_band(band)
        assert out.dtype == np.float32
        assert out[0] == 0.0

    def test_empty_array_handling(self):
        band = np.empty((0, 0), dtype=np.float32)
        out = _normalize_band(band)
        assert out.dtype == np.float32
        assert out.shape == (0, 0)

    def test_tiny_spread_below_float32_resolution(self):
        """When spread is so microscopic that denom <= 0 in float32."""
        base = 1.0
        tiny_diff = 1e-45
        band = np.array([base, base + tiny_diff], dtype=np.float32)
        out = _normalize_band(band)
        assert out.dtype == np.float32
        np.testing.assert_array_equal(out, 0.0)


class TestNonFiniteHandling:
    """Tests verifying non-finite (NaN, Inf, -Inf) input handling matches existing semantics."""

    def test_array_with_nan_preserves_legacy_semantics(self):
        band = np.array([[1.0, np.nan], [2.0, 3.0]], dtype=np.float32)
        legacy = _legacy_normalize_band(band)
        optimized = _normalize_band(band)

        assert optimized.dtype == np.float32
        np.testing.assert_allclose(optimized, legacy, equal_nan=True)

    def test_all_nan_array(self):
        band = np.full((10, 10), np.nan, dtype=np.float32)
        legacy = _legacy_normalize_band(band)
        optimized = _normalize_band(band)

        assert optimized.dtype == np.float32
        assert np.isnan(optimized).all()
        np.testing.assert_allclose(optimized, legacy, equal_nan=True)

    def test_array_with_positive_inf(self):
        band = np.array([[1.0, np.inf], [2.0, 3.0]], dtype=np.float32)
        legacy = _legacy_normalize_band(band)
        optimized = _normalize_band(band)

        assert optimized.dtype == np.float32
        np.testing.assert_allclose(optimized, legacy, equal_nan=True)

    def test_array_with_negative_inf(self):
        band = np.array([[1.0, -np.inf], [2.0, 3.0]], dtype=np.float32)
        legacy = _legacy_normalize_band(band)
        optimized = _normalize_band(band)

        assert optimized.dtype == np.float32
        np.testing.assert_allclose(optimized, legacy, equal_nan=True)


class TestDtypesAndMemory:
    """Tests verifying dtype handling, non-mutation, strided slices, and memory safety."""

    @pytest.mark.parametrize("dtype", [np.float32, np.float64, np.int16, np.int32, np.uint8])
    def test_supports_multiple_input_dtypes(self, dtype):
        band = np.arange(100, dtype=dtype).reshape(10, 10)
        legacy = _legacy_normalize_band(band)
        optimized = _normalize_band(band)

        assert optimized.dtype == np.float32
        np.testing.assert_allclose(optimized, legacy, atol=1e-6)

    def test_input_array_is_not_mutated(self):
        band = np.array([10.0, 20.0, 30.0, 40.0], dtype=np.float32)
        band_copy = band.copy()

        _ = _normalize_band(band)

        np.testing.assert_array_equal(band, band_copy)

    def test_strided_channel_slice_input(self):
        """Simulate loading a dual-pol stacked GeoTIFF where vv_raw is arr[..., 0]."""
        cube = np.random.default_rng(42).normal(size=(64, 64, 2)).astype(np.float32)
        vv_raw = cube[..., 0]
        assert not vv_raw.flags.c_contiguous

        out = _normalize_band(vv_raw)

        assert out.dtype == np.float32
        assert out.flags.c_contiguous
        assert out.shape == (64, 64)

    def test_memory_allocation_does_not_create_float64_intermediate(self):
        """For a (2048, 2048) float32 array (16 MiB), peak memory must remain near 16 MiB,
        not allocating 32 MiB float64 intermediates as the legacy implementation did."""
        band = np.zeros((2048, 2048), dtype=np.float32)
        band[0, 0] = -10.0
        band[-1, -1] = 10.0

        tracemalloc.start()
        out = _normalize_band(band)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        peak_mib = peak / (1024 * 1024)
        # Output array itself is 16.0 MiB.
        # We ensure peak allocation is comfortably below 32 MiB (e.g. < 24 MiB),
        # confirming zero 32.0 MiB float64 intermediates were allocated.
        assert peak_mib < 25.0, f"Peak memory was {peak_mib:.2f} MiB (expected < 25 MiB)"
        assert out.dtype == np.float32
        assert out.shape == (2048, 2048)


class TestRealSampleSceneParity:
    """Tests using the shipped sample SAR TIFF file."""

    def test_sample_sar_tiff_parity(self):
        tif_path = os.path.join(
            os.path.dirname(__file__), "..", "data", "sample_sar", "demo_oil_scene_01.tif"
        )
        if not os.path.exists(tif_path):
            pytest.skip("Sample SAR TIFF not found")

        import tifffile
        arr = tifffile.imread(tif_path)
        for ch in range(arr.shape[-1]):
            band = arr[..., ch]
            legacy = _legacy_normalize_band(band)
            optimized = _normalize_band(band)

            assert optimized.dtype == np.float32
            assert optimized.min() >= 0.0
            assert optimized.max() <= 1.0
            np.testing.assert_allclose(optimized, legacy, atol=1e-6, rtol=1e-5)


class TestPublicContractAndVersion:
    """Verifies public export and version string integrity."""

    def test_public_alias_identity(self):
        assert normalize_sar_band is _normalize_band

    def test_preprocessing_version_string_preserved(self):
        assert PREPROCESSING_VERSION == "sar_percentile_1_99_minmax_v1"
