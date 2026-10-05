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
    band = band.astype(np.float64)
    lo, hi = np.percentile(band, [1, 99])
    if hi <= lo:
        return np.zeros_like(band, dtype=np.float32)
    return np.clip((band - lo) / (hi - lo), 0, 1).astype(np.float32)


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

    center_lat/center_lon/pixel_size_m are used ONLY as the scene-center
    anchor for an assumed north-up affine geotransform - this is an
    approximation (see module docstring), not a value extracted from the
    file, unless a future GDAL-based extension point reads real embedded
    geolocation.
    """
    ext = os.path.splitext(path)[1].lower()
    geolocation_is_assumed = True
    geotiff_meta_dict: dict | None = None
    real_geo = None

    if ext in (".tif", ".tiff"):
        if not TIFFFILE_AVAILABLE:
            raise UnsupportedImageFormat("tifffile is required to read GeoTIFF input but is not installed.")
        arr = tifffile.imread(path)
        # Section 5: try to use REAL embedded geo-referencing before falling
        # back to the caller-supplied assumed center - never raises (see
        # geotiff_metadata.py docstring), so a corrupt/foreign/PROJECTED-CRS
        # GeoTIFF degrades safely to the pre-existing assumed-coordinate path.
        real_geo = read_geotiff_metadata(path)
        geotiff_meta_dict = real_geo.to_dict()
        if real_geo.available:
            geolocation_is_assumed = False
    elif ext in (".png", ".jpg", ".jpeg"):
        arr = np.array(Image.open(path).convert("F"))  # single-band float
    else:
        raise UnsupportedImageFormat(f"Unsupported file extension '{ext}'. Supported: .tif, .tiff, .png, .jpg")

    arr = np.nan_to_num(arr.astype(np.float64), nan=0.0, posinf=0.0, neginf=0.0)
    original_shape = arr.shape

    is_dual_pol = arr.ndim == 3 and arr.shape[-1] >= 2
    vv = vh = None
    if is_dual_pol:
        vv_raw, vh_raw = arr[..., 0], arr[..., 1]
        vv = _normalize_band(vv_raw)
        vh = _normalize_band(vh_raw)
        single_band = vv  # classical detector operates on the VV channel
        height, width = vv.shape
    elif arr.ndim == 3:
        # multi-band but not clearly dual-pol (e.g. RGB) - use luminance
        single_band = _normalize_band(arr.mean(axis=-1))
        height, width = single_band.shape
    else:
        single_band = _normalize_band(arr)
        height, width = single_band.shape

    if real_geo is not None and real_geo.available:
        # REAL embedded geo-referencing (Section 5) - the tie point in a
        # GeoTIFF anchors pixel (0, 0) (or whichever pixel the tie point
        # names; see geotiff_metadata.py's single-tiepoint assumption), which
        # already matches SceneGeoTransform's own pixel-(0,0)-origin
        # convention, so no additional height/width centering is needed here
        # (unlike the assumed-center branch below, which has to back out an
        # implied (0,0) origin from a caller-supplied scene *center*).
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
