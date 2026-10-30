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
    is_flat_scene: bool = False  # True if input is 8-bit image with negligible dynamic range (<20 DN), characteristic of clean open ocean


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
    max_target_dim = 1024
    scale = 1.0
    is_flat_scene = False

    if ext in (".tif", ".tiff", ".geotiff"):
        if not TIFFFILE_AVAILABLE:
            raise UnsupportedImageFormat("tifffile is required to read GeoTIFF input but is not installed.")
        with tifffile.TiffFile(path) as tf:
            if not tf.pages:
                raise UnsupportedImageFormat("Uploaded TIFF file is empty or contains no image pages.")
            real_geo = read_geotiff_metadata(tf)
            geotiff_meta_dict = real_geo.to_dict()
            if real_geo.available:
                geolocation_is_assumed = False

            nodata_val = None
            tag = tf.pages[0].tags.get(42113) or tf.pages[0].tags.get("GDAL_NODATA")
            if tag is not None:
                try:
                    nodata_val = float(tag.value)
                except (ValueError, TypeError):
                    pass

            try:
                arr = tf.asarray()
            except Exception as e:
                raise UnsupportedImageFormat(f"Failed to decode TIFF raster data: {e}")

        if arr is None or arr.size == 0:
            raise UnsupportedImageFormat("Uploaded image file is empty or unreadable.")

        arr = np.squeeze(arr)
        if arr.ndim == 3:
            if arr.shape[0] <= 4 and arr.shape[1] > 4 and arr.shape[2] > 4:
                arr = np.transpose(arr, (1, 2, 0))
        elif arr.ndim > 3:
            while arr.ndim > 3 and arr.shape[0] == 1:
                arr = arr[0]
            if arr.ndim == 3 and arr.shape[0] <= 4 and arr.shape[1] > 4 and arr.shape[2] > 4:
                arr = np.transpose(arr, (1, 2, 0))

        if arr.ndim not in (2, 3):
            raise UnsupportedImageFormat(f"Unsupported image array shape {arr.shape}; expected 2D or 3D array.")

        if arr.ndim == 3 and arr.shape[-1] > 4:
            raise UnsupportedImageFormat(
                f"Unsupported raster band count ({arr.shape[-1]}). Expected single-band (SAR amplitude), "
                f"2-band dual-polarization (VV+VH), or standard 3/4-band RGB(A)."
            )

        original_shape = arr.shape
        h, w = arr.shape[:2]

        if nodata_val is not None:
            import math
            if math.isnan(nodata_val):
                arr = np.nan_to_num(arr, nan=0.0)
            elif math.isfinite(nodata_val):
                arr = np.where(np.isclose(arr, nodata_val), 0, arr)

        if np.iscomplexobj(arr):
            arr = np.abs(arr).astype(np.float32)

        if max(h, w) > max_target_dim:
            scale = max_target_dim / float(max(h, w))
            new_w = max(16, int(round(w * scale)))
            new_h = max(16, int(round(h * scale)))

            if arr.ndim == 2:
                if arr.dtype == np.float64:
                    arr = arr.astype(np.float32)
                try:
                    im = Image.fromarray(arr)
                except Exception:
                    im = Image.fromarray(arr.astype(np.float32))
                arr = np.array(im.resize((new_w, new_h), resample=Image.Resampling.BILINEAR))
            elif arr.ndim == 3:
                channels = []
                for c in range(arr.shape[-1]):
                    band = arr[..., c]
                    if band.dtype == np.float64:
                        band = band.astype(np.float32)
                    try:
                        im = Image.fromarray(band)
                    except Exception:
                        im = Image.fromarray(band.astype(np.float32))
                    channels.append(np.array(im.resize((new_w, new_h), resample=Image.Resampling.BILINEAR)))
                arr = np.stack(channels, axis=-1)

        arr = np.nan_to_num(arr.astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0)

    elif ext in (".png", ".jpg", ".jpeg", ".webp"):
        try:
            with Image.open(path) as img:
                orig_w, orig_h = img.size
                if img.mode in ("L", "I", "I;16", "F"):
                    original_shape = (orig_h, orig_w)
                else:
                    original_shape = (orig_h, orig_w, len(img.getbands()))

                if max(orig_h, orig_w) > max_target_dim:
                    scale = max_target_dim / float(max(orig_h, orig_w))
                    new_w = max(16, int(round(orig_w * scale)))
                    new_h = max(16, int(round(orig_h * scale)))
                    img = img.resize((new_w, new_h), resample=Image.Resampling.BILINEAR)

                if img.mode in ("L", "I", "I;16", "F"):
                    arr = np.array(img, dtype=np.float32)
                elif img.mode in ("RGB", "RGBA"):
                    raw_arr = np.array(img, dtype=np.float32)
                    arr = raw_arr[..., :3]
                else:
                    arr = np.array(img.convert("F"), dtype=np.float32)
        except Exception as e:
            raise UnsupportedImageFormat(f"Failed to read image file: {e}")

        if arr is None or arr.size == 0:
            raise UnsupportedImageFormat("Uploaded image file is empty or unreadable.")
        arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)

        # Check raw 8-bit dynamic range before percentile stretching
        if arr.size > 0 and float(arr.min()) >= 0.0 and float(arr.max()) <= 255.0:
            p01, p99 = np.percentile(arr, [1, 99])
            if float(p99 - p01) < 20.0:
                is_flat_scene = True

    else:
        raise UnsupportedImageFormat(f"Unsupported file extension '{ext}'. Supported: .tif, .tiff, .geotiff, .png, .jpg, .jpeg, .webp")

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
            single_band = _normalize_band(arr[..., :3].mean(axis=-1))
        else:
            raise UnsupportedImageFormat(f"Unsupported raster band count ({channels}). Expected single-band, 2-band dual-pol, or 3/4-band RGB(A).")
    elif arr.ndim == 2:
        single_band = _normalize_band(arr)
    else:
        raise UnsupportedImageFormat(f"Unsupported image array shape {arr.shape}; expected 2D or 3D array.")

    height, width = single_band.shape[:2]
    orig_h, orig_w = original_shape[:2]
    scale_y = height / float(orig_h)
    scale_x = width / float(orig_w)

    if real_geo is not None and real_geo.available:
        real_geo.pixel_size_deg_lat = real_geo.pixel_size_deg_lat / scale_y
        real_geo.pixel_size_deg_lon = real_geo.pixel_size_deg_lon / scale_x
        gt = SceneGeoTransform(
            origin_lat=real_geo.origin_lat,
            origin_lon=real_geo.origin_lon,
            pixel_size_deg_lat=real_geo.pixel_size_deg_lat,
            pixel_size_deg_lon=real_geo.pixel_size_deg_lon,
        )
        geolocation_source = "embedded_geotiff_metadata"
    else:
        deg_lat_per_px = (-pixel_size_m / 111_320.0) / scale_y
        deg_lon_per_px = (pixel_size_m / (111_320.0 * np.cos(np.deg2rad(center_lat)))) / scale_x
        gt = SceneGeoTransform(
            origin_lat=center_lat - (height / 2.0) * deg_lat_per_px,
            origin_lon=center_lon - (width / 2.0) * deg_lon_per_px,
            pixel_size_deg_lat=deg_lat_per_px,
            pixel_size_deg_lon=deg_lon_per_px,
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
        is_flat_scene=is_flat_scene,
    )
