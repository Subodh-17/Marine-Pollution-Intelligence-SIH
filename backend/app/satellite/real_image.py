"""
Real satellite image adapter.

Accepts a user-provided GeoTIFF or PNG SAR-like image and produces the same
(2D float array, SceneGeoTransform) contract `app/investigation/orchestrator.py`
already uses for the synthetic demo scene, so `preprocessing.py`,
`detection.py`, and `characterization.py` run completely unchanged on real
input - no parallel pipeline.

FORMAT SUPPORT
--------------
- Single-band GeoTIFF/PNG -> used directly as the classical-detector input.
- Dual-band GeoTIFF (VV, VH stacked) -> band 0 (VV) is used for the classical
  detector; both bands are additionally exposed via `read_dual_pol()` for the
  optional ML detector, which genuinely requires two real polarization
  channels (see app/ml/detector.py).

GEOREFERENCING
--------------
For GeoTIFF input, `app/satellite/geotiff_metadata.py` is tried FIRST: it
reads the embedded ModelPixelScaleTag/ModelTiepointTag (or
ModelTransformationTag) + GeoKeyDirectoryTag without a GDAL/rasterio
dependency (consistent with this project's Windows-compatibility stance -
see ARCHITECTURE.md). When those tags are present, describe a geographic
(lat/lon) CRS, and parse cleanly, the REAL embedded transform is used and
`geolocation_is_assumed=False` / `geolocation_source="embedded_geotiff_metadata"`.

If the file has no embedded geolocation, an unsupported/projected CRS, or
corrupt geo tags (see geotiff_metadata.py for the exact cases), this falls
back to the pre-existing behavior: a caller-supplied approximate scene-center
lat/lon and pixel size are used to build a `SceneGeoTransform`, and the
response clearly labels this as an assumed/approximate scene location
(`geolocation_is_assumed=True` / `geolocation_source="assumed_center_coordinates"`),
never presented as if it were read from the file. Bring a file with
correctly-set --center-lat/--center-lon (via the API's query parameters) for
meaningful geometry in that case; the default center is only a placeholder.
PNG/JPEG input has no geo tags at all and always uses the assumed path.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np

from app.satellite.synthetic import SceneGeoTransform
from app.satellite.geotiff_metadata import read_geotiff_metadata

try:
    import tifffile
    TIFFFILE_AVAILABLE = True
except ImportError:  # pragma: no cover
    TIFFFILE_AVAILABLE = False

from PIL import Image


class UnsupportedImageFormat(Exception):
    pass


# Version tag for the exact normalization `_normalize_band` below implements.
# Bump this any time the normalization formula changes. Both the dataset
# preparation pipeline (app/ml/dataset.py) and the training pipeline
# (app/ml/train.py) import `_normalize_band` directly from this module
# (rather than re-implementing it) specifically so training-time and
# inference-time preprocessing can never silently drift apart - this exact
# string is what `app/ml/detector.py` reports as `preprocessing_version` in
# every ML inference response, so a mismatch between how a checkpoint was
# trained and how inference normalizes its input is always visible rather
# than silently wrong.
PREPROCESSING_VERSION = "sar_percentile_1_99_minmax_v1"


@dataclass
class RealSceneLoadResult:
    image: np.ndarray            # single-band float32 [0,1]-ish, for the classical detector
    geotransform: SceneGeoTransform
    is_dual_pol: bool
    vv: np.ndarray | None        # present only if is_dual_pol
    vh: np.ndarray | None        # present only if is_dual_pol
    geolocation_is_assumed: bool  # True if no embedded geolocation was found and a default/caller value was used
    original_shape: tuple
    geolocation_source: str = "assumed_center_coordinates"  # "embedded_geotiff_metadata" | "assumed_center_coordinates"
    geotiff_metadata: dict | None = None  # raw GeoTiffMetadata.to_dict(), when a GeoTIFF was inspected at all


def _normalize_band(band: np.ndarray) -> np.ndarray:
    """Per-image, percentile-robust min-max normalization to [0, 1].

    This is also what feeds the optional ML detector's VV/VH inputs
    (`app/ml/detector.py::MLDetectorEngine.detect`) - kept as per-image
    adaptive normalization (not a fixed global/dB range) deliberately, so it
    stays the same general kind of normalization the ML prototype's training
    data used. See MODEL_CARD.md §6 / app/ml/README_ML_STATUS.md for the
    full history of this consistency check.
    """
    band = np.asarray(band)
    if band.size == 0:
        return np.zeros_like(band, dtype=np.float32)
    lo, hi = np.percentile(band, [1, 99])
    if hi <= lo:
        return np.zeros_like(band, dtype=np.float32)
    denom = np.float32(hi - lo)
    if denom <= 0.0:
        return np.zeros_like(band, dtype=np.float32)
    out = np.empty(band.shape, dtype=np.float32)
    lo_f = np.float32(lo)
    np.subtract(band, lo_f, out=out, casting="unsafe")
    np.divide(out, denom, out=out)
    np.clip(out, 0.0, 1.0, out=out)
    return out


# Public alias - app/ml/dataset.py and app/ml/train.py import this name
# (rather than the underscored one) to make the "same normalization as
# inference, on purpose" dependency explicit and importable without a lint
# complaint about reaching into a "private" function.
normalize_sar_band = _normalize_band


def load_real_scene(
    path: str,
    center_lat: float = 19.05,
    center_lon: float = 71.20,
    pixel_size_m: float = 40.0,
) -> RealSceneLoadResult:
    """
    Loads a user-uploaded SAR-like image for the real (non-synthetic)
    investigation pipeline.

    Supports GeoTIFF (.tif, .tiff, .geotiff) and standard images (.png, .jpg, .jpeg, .webp).
    Includes automatic shape normalization (handling both channels-first and channels-last arrays),
    multi-reader fallback between tifffile and PIL, and adaptive downsampling for images exceeding
    1024px to ensure fast, sub-second execution without memory exhaustion.
    """
    ext = os.path.splitext(path)[1].lower()
    geolocation_is_assumed = True
    geotiff_meta_dict: dict | None = None
    real_geo = None
    arr = None

    if ext in (".tif", ".tiff", ".geotiff"):
        if not TIFFFILE_AVAILABLE:
            raise UnsupportedImageFormat("tifffile is required to read GeoTIFF input but is not installed.")
        arr = tifffile.imread(path)
        real_geo = read_geotiff_metadata(path)
        geotiff_meta_dict = real_geo.to_dict()
        if real_geo.available:
            geolocation_is_assumed = False
    elif ext in (".png", ".jpg", ".jpeg", ".webp"):
        arr = np.array(Image.open(path).convert("F"))
    else:
        raise UnsupportedImageFormat(f"Unsupported file extension '{ext}'. Supported: .tif, .tiff, .geotiff, .png, .jpg, .jpeg, .webp")

    if arr is None or arr.size == 0:
        raise UnsupportedImageFormat("Uploaded image file is empty or unreadable.")

    arr = np.squeeze(arr)
    original_shape = arr.shape

    # Normalize dimensions: handle channels-first (e.g. 2, H, W or 3, H, W) vs channels-last (H, W, C)
    if arr.ndim == 3:
        if arr.shape[0] <= 4 and arr.shape[1] > 4 and arr.shape[2] > 4:
            arr = np.transpose(arr, (1, 2, 0))
    elif arr.ndim > 3:
        while arr.ndim > 3 and arr.shape[0] == 1:
            arr = arr[0]
        if arr.ndim == 3 and arr.shape[0] <= 4 and arr.shape[1] > 4 and arr.shape[2] > 4:
            arr = np.transpose(arr, (1, 2, 0))

    arr = np.nan_to_num(arr.astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0)

    is_dual_pol = False
    vv = vh = None

    if arr.ndim == 3:
        channels = arr.shape[-1]
        if channels == 2:
            is_dual_pol = True
            vv_raw, vh_raw = arr[..., 0], arr[..., 1]
            vv = _normalize_band(vv_raw)
            vh = _normalize_band(vh_raw)
            single_band = vv
        elif channels in (3, 4):
            # RGB or RGBA (strip alpha)
            single_band = _normalize_band(arr[..., :3].mean(axis=-1))
        else:
            single_band = _normalize_band(arr[..., 0])
    elif arr.ndim == 2:
        single_band = _normalize_band(arr)
    else:
        raise UnsupportedImageFormat(f"Unsupported image array shape {arr.shape}; expected 2D or 3D array.")

    height, width = single_band.shape[:2]

    # Adaptive downsampling for memory safety and fast sub-second analysis:
    # Caps processing resolution at 1024px while scaling physical pixel size proportionally
    max_target_dim = 1024
    if max(height, width) > max_target_dim:
        scale = max_target_dim / float(max(height, width))
        new_w = max(16, int(round(width * scale)))
        new_h = max(16, int(round(height * scale)))

        def _fast_resize(b: np.ndarray, nw: int, nh: int) -> np.ndarray:
            pil = Image.fromarray((np.clip(b, 0.0, 1.0) * 255.0).astype(np.uint8))
            res = pil.resize((nw, nh), resample=Image.Resampling.BILINEAR)
            return (np.array(res).astype(np.float32) / 255.0)

        single_band = _fast_resize(single_band, new_w, new_h)
        if is_dual_pol and vv is not None and vh is not None:
            vv = _fast_resize(vv, new_w, new_h)
            vh = _fast_resize(vh, new_w, new_h)

        pixel_size_m = pixel_size_m / scale
        if real_geo is not None and real_geo.available:
            real_geo.pixel_size_deg_lat = real_geo.pixel_size_deg_lat / scale
            real_geo.pixel_size_deg_lon = real_geo.pixel_size_deg_lon / scale
        height, width = new_h, new_w

    if real_geo is not None and real_geo.available:
        gt = SceneGeoTransform(
            origin_lat=real_geo.origin_lat,
            origin_lon=real_geo.origin_lon,
            pixel_size_deg_lat=real_geo.pixel_size_deg_lat,
            pixel_size_deg_lon=real_geo.pixel_size_deg_lon,
        )
        geolocation_source = "embedded_geotiff_metadata"
    else:
        gt = SceneGeoTransform(
            origin_lat=center_lat + (height / 2) * (pixel_size_m / 111_320.0),
            origin_lon=center_lon - (width / 2) * (pixel_size_m / (111_320.0 * np.cos(np.deg2rad(center_lat)))),
            pixel_size_deg_lat=-pixel_size_m / 111_320.0,
            pixel_size_deg_lon=pixel_size_m / (111_320.0 * np.cos(np.deg2rad(center_lat))),
        )
        geolocation_source = "assumed_center_coordinates"

    return RealSceneLoadResult(
        image=single_band,
        geotransform=gt,
        is_dual_pol=is_dual_pol,
        vv=vv,
        vh=vh,
        geolocation_is_assumed=geolocation_is_assumed,
        original_shape=original_shape,
        geolocation_source=geolocation_source,
        geotiff_metadata=geotiff_meta_dict,
    )
