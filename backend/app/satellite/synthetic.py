"""
Synthetic Sentinel-1 SAR scene generator.

WHY THIS EXISTS
----------------
The SIH problem statement references the Zenodo "Sentinel-1 SAR Oil Spill
Dataset". This environment has no network access to zenodo.org or Copernicus,
so real SAR scenes cannot be downloaded at runtime. To keep the detection
pipeline scientifically meaningful and fully offline-capable, this module
synthesizes a single-band SAR-like backscatter image that reproduces the two
key statistical properties real SAR oil-spill scenes have:

  1. Multiplicative speckle noise (Gamma-distributed), the dominant noise
     source in real SAR amplitude imagery.
  2. A region of suppressed backscatter (a "dark spot") that mimics the
     wave-dampening signature oil films produce in C-band SAR (this is the
     actual physical mechanism used by real detectors, not an arbitrary
     image trick).

This is explicitly a SYNTHETIC / DEMONSTRATION data source. It is used only
when real or cached Sentinel-1 imagery is not available (see
app/environment/adapters.py and the DATA FALLBACK ARCHITECTURE in the
project README). It must never be presented to a user as a real
observation; the API always tags it with `source: "SYNTHETIC_DEMO"`.

The detection algorithms in `detection.py` operate identically whether the
input array comes from this generator or from a real GeoTIFF - they only
consume a 2D float array + a geotransform, so swapping in real Sentinel-1
data later (via a `Sentinel1Adapter` reading an actual .tif/.SAFE product)
requires no changes to the detection/characterisation code.
"""
from __future__ import annotations

import numpy as np
from dataclasses import dataclass


@dataclass
class SceneGeoTransform:
    """Maps pixel (row, col) -> (lat, lon). Simple affine, north-up."""
    origin_lat: float
    origin_lon: float
    pixel_size_deg_lat: float  # negative (north -> south as row increases)
    pixel_size_deg_lon: float  # positive (west -> east as col increases)

    def pixel_to_latlon(self, row: float, col: float) -> tuple[float, float]:
        lat = self.origin_lat + row * self.pixel_size_deg_lat
        lon = self.origin_lon + col * self.pixel_size_deg_lon
        return lat, lon

    def latlon_to_pixel(self, lat: float, lon: float) -> tuple[float, float]:
        row = (lat - self.origin_lat) / self.pixel_size_deg_lat
        col = (lon - self.origin_lon) / self.pixel_size_deg_lon
        return row, col


def generate_synthetic_sar_scene(
    height: int = 512,
    width: int = 512,
    center_lat: float = 19.05,
    center_lon: float = 71.20,
    pixel_size_m: float = 40.0,
    slick_center_rc: tuple[int, int] | None = None,
    slick_length_px: int = 170,
    slick_width_px: int = 42,
    slick_orientation_deg: float = 34.0,
    speckle_looks: int = 4,
    seed: int = 42,
) -> tuple[np.ndarray, SceneGeoTransform]:
    """
    Build a synthetic single-band SAR amplitude image (values ~ [0, 1]) with
    a slick-like dark elongated region embedded in speckled open-ocean
    background, roughly resembling a Sentinel-1 IW GRD scene off the
    Maharashtra-Konkan coast (used only for the demo scenario).

    Returns (image, geotransform).
    """
    rng = np.random.default_rng(seed)

    # --- clean backscatter field (before speckle) -----------------------
    # Open ocean backscatter has a mild spatial gradient (incidence angle
    # effect across the swath) plus some low-level texture from wind-driven
    # capillary waves.
    row_grad = np.linspace(0.62, 0.48, height)[:, None]
    col_tex = 0.02 * np.sin(np.linspace(0, 12 * np.pi, width))[None, :]
    base = np.tile(row_grad, (1, width)) + col_tex
    base += rng.normal(0, 0.015, size=(height, width))
    base = np.clip(base, 0.05, 1.0)

    # --- embed an elongated low-backscatter "slick" region ---------------
    if slick_center_rc is None:
        slick_center_rc = (height // 2 + 25, width // 2 - 15)
    rr, cc = np.meshgrid(np.arange(height), np.arange(width), indexing="ij")
    theta = np.deg2rad(slick_orientation_deg)
    dr = rr - slick_center_rc[0]
    dc = cc - slick_center_rc[1]
    # rotate into slick-aligned frame
    along = dr * np.cos(theta) + dc * np.sin(theta)
    across = -dr * np.sin(theta) + dc * np.cos(theta)
    a = slick_length_px / 2.0
    b = slick_width_px / 2.0
    # smooth elliptical damping envelope with irregular edges (oil slicks
    # are not perfect ellipses in reality; add low-frequency perturbation)
    edge_noise = 0.15 * np.sin(along / 9.0) + 0.10 * np.cos(across / 6.0)
    dist = ((along / a) ** 2 + (across / b) ** 2) ** 0.5 - edge_noise
    damping = np.clip(1.0 - dist, 0, 1) ** 1.5  # 0 outside, 1 at center
    slick_suppression = 0.55  # fraction of backscatter suppressed at core
    field = base * (1 - slick_suppression * damping)

    # --- multiplicative speckle (Gamma, mean 1, L looks) ------------------
    speckle = rng.gamma(shape=speckle_looks, scale=1.0 / speckle_looks, size=(height, width))
    image = np.clip(field * speckle, 0, 1.3)
    image = image / image.max()

    gt = SceneGeoTransform(
        origin_lat=center_lat + (height / 2) * (pixel_size_m / 111_320.0),
        origin_lon=center_lon - (width / 2) * (pixel_size_m / (111_320.0 * np.cos(np.deg2rad(center_lat)))),
        pixel_size_deg_lat=-pixel_size_m / 111_320.0,
        pixel_size_deg_lon=pixel_size_m / (111_320.0 * np.cos(np.deg2rad(center_lat))),
    )
    return image.astype(np.float32), gt
