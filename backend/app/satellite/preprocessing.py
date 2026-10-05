"""
SAR preprocessing: speckle reduction, normalization, sea/land masking.

Implements a real, general-purpose Lee filter (a standard SAR despeckling
technique - Lee, 1980) rather than a generic image blur, because generic
blurring destroys the radiometric statistics that dark-spot detectors rely
on. This runs identically on synthetic or real Sentinel-1 amplitude arrays.
"""
from __future__ import annotations

import numpy as np
from scipy.ndimage import uniform_filter


def lee_filter(image: np.ndarray, window: int = 7, num_looks: int = 4) -> np.ndarray:
    """
    Standard (multiplicative-noise) Lee speckle filter for SAR amplitude
    imagery (Lee, 1980).

    Ci = local coefficient of variation = local_std / local_mean
    Cu = speckle coefficient of variation = 1 / sqrt(num_looks)
    W  = 1 - (Cu / Ci)^2, clamped to [0, 1]
    out = local_mean + W * (img - local_mean)

    This adapts smoothing strength to how noisy a neighbourhood is relative
    to the theoretical speckle noise level for the given number of looks:
    homogeneous areas (open ocean, or the interior of a slick) get smoothed
    hard (W -> 0, output -> local mean); heterogeneous edges/texture are
    preserved (W -> 1, output -> original pixel).
    """
    img = image.astype(np.float64)
    mean_local = uniform_filter(img, size=window)
    mean_sq_local = uniform_filter(img ** 2, size=window)
    var_local = np.maximum(mean_sq_local - mean_local ** 2, 0)

    ci = np.sqrt(var_local) / np.maximum(mean_local, 1e-6)
    cu = 1.0 / np.sqrt(num_looks)

    weight = 1.0 - (cu ** 2) / np.maximum(ci ** 2, 1e-6)
    weight = np.clip(weight, 0.0, 1.0)

    out = mean_local + weight * (img - mean_local)
    return np.clip(out, 0, None).astype(np.float32)


def normalize(image: np.ndarray) -> np.ndarray:
    """Min-max normalize to [0, 1] using robust percentiles to reduce the
    influence of outlier bright targets (ships, land, ambiguities)."""
    lo, hi = np.percentile(image, [1, 99])
    if hi <= lo:
        return np.zeros_like(image)
    out = (image - lo) / (hi - lo)
    return np.clip(out, 0, 1).astype(np.float32)


def simple_sea_mask(image: np.ndarray, land_brightness_threshold: float = 0.93) -> np.ndarray:
    """
    Heuristic land/sea mask for the demo pipeline.

    Real deployments should use an authoritative coastline layer (e.g. GSHHG
    / Natural Earth) reprojected onto the scene grid - that is a documented
    extension point (see ARCHITECTURE.md). For the offline synthetic scenes
    used here there is no land in-frame, so this returns all-sea (True
    everywhere) unless very bright, low-texture regions (a proxy for land /
    strong urban backscatter) are present.
    """
    bright = image > land_brightness_threshold
    return ~bright
