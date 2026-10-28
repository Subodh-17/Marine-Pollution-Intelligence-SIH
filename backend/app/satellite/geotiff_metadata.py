"""
Lightweight embedded-GeoTIFF metadata reader (Section 5 of the upgrade
request) - no GDAL/rasterio dependency, consistent with this project's
Windows-compatibility stance (see ARCHITECTURE.md, real_image.py).

Prior behaviour (still the fallback when this module can't help): every
uploaded GeoTIFF was treated as if it had no embedded geolocation at all,
and the caller-supplied center_lat/center_lon/pixel_size query params were
used to build an ASSUMED affine transform (see real_image.py). That is
still exactly correct for files that genuinely have no geo tags. This
module's job is narrower: when a GeoTIFF DOES carry real embedded
geo-referencing tags, read them and use the REAL transform instead of
guessing.

WHAT IS READ
------------
Standard baseline GeoTIFF tags (http://geotiff.maptools.org/spec/):
  - ModelPixelScaleTag     (33550) - (scale_x, scale_y, scale_z)
  - ModelTiepointTag       (33922) - one or more (i, j, k, X, Y, Z) tie points
  - ModelTransformationTag (34264) - full 4x4 affine, alternative to the two above
  - GeoKeyDirectoryTag     (34735) - parsed only as far as GTModelTypeGeoKey
    (1024) and the relevant EPSG code key (GeographicTypeGeoKey 2048 for a
    geographic CRS, ProjectedCSTypeGeoKey 3072 for a projected CRS), to
    answer "is this WGS84 lat/lon (or an unspecified geographic CRS we can
    treat as such), or something else".

WHAT THIS DELIBERATELY DOES NOT DO
-----------------------------------
This is not a general-purpose GeoTIFF/CRS library. It does not implement a
GeoKey/EPSG database and it does not reproject. Concretely:
  - Geographic CRS (GTModelTypeGeoKey == 2), e.g. EPSG:4326 - SUPPORTED.
    Tie point / pixel scale values are already in degrees, so they are used
    directly as the scene's real lat/lon affine transform.
  - Projected CRS (GTModelTypeGeoKey == 1), e.g. a UTM zone - the CRS and
    its EPSG code (when present) ARE reported, but the tie point coordinates
    are in projected easting/northing metres, not lat/lon, and converting
    them correctly requires a real projection library (pyproj/PROJ), which
    is not installed in this build. Rather than silently mis-project (e.g.
    treating easting/northing numbers as if they were lon/lat), this is
    reported as `available=False` with an explicit `reason`, and the caller
    falls back to the assumed-coordinate path, exactly like a file with no
    geo tags at all.
  - Any tag that doesn't parse cleanly (wrong length, non-finite values, a
    corrupt/truncated directory) is treated the same way: `available=False`
    with a specific reason, never a guess and never a crash.

This intentionally mirrors the existing fallback-chain pattern used
elsewhere in this codebase (app/environment/adapters.py,
app/ais/adapters.py): try the real thing, and only report failure - the
CALLER decides what safe default to use, this module never fabricates one
itself.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

try:
    import tifffile
    TIFFFILE_AVAILABLE = True
except ImportError:  # pragma: no cover
    TIFFFILE_AVAILABLE = False

# GeoTIFF tag codes (see http://geotiff.maptools.org/spec/geotiff2.4.html)
_TAG_MODEL_PIXEL_SCALE = 33550
_TAG_MODEL_TIEPOINT = 33922
_TAG_MODEL_TRANSFORMATION = 34264
_TAG_GEO_KEY_DIRECTORY = 34735
_TAG_GEO_ASCII_PARAMS = 34737

_GTMODELTYPE_GEOKEY = 1024
_GEOGRAPHIC_TYPE_GEOKEY = 2048
_PROJECTED_CS_TYPE_GEOKEY = 3072

_MODEL_TYPE_GEOGRAPHIC = 2
_MODEL_TYPE_PROJECTED = 1


@dataclass
class GeoTiffMetadata:
    """Result of attempting to read real embedded geo-referencing from a
    GeoTIFF. `available=True` is the ONLY case where `origin_lat`,
    `origin_lon`, `pixel_size_deg_lat`, and `pixel_size_deg_lon` should be
    trusted as real, non-assumed values."""

    available: bool
    reason: str | None = None
    crs_kind: str | None = None        # "geographic" | "projected" | None (unknown)
    crs_epsg: int | None = None
    origin_lat: float | None = None
    origin_lon: float | None = None
    pixel_size_deg_lat: float | None = None   # signed: negative = north-up (row increases southward)
    pixel_size_deg_lon: float | None = None
    raw_tags_found: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "available": self.available,
            "reason": self.reason,
            "crs_kind": self.crs_kind,
            "crs_epsg": self.crs_epsg,
            "origin_lat": self.origin_lat,
            "origin_lon": self.origin_lon,
            "pixel_size_deg_lat": self.pixel_size_deg_lat,
            "pixel_size_deg_lon": self.pixel_size_deg_lon,
            "raw_tags_found": self.raw_tags_found,
        }


def _unavailable(reason: str, raw_tags_found: list[str] | None = None,
                  crs_kind: str | None = None, crs_epsg: int | None = None) -> GeoTiffMetadata:
    return GeoTiffMetadata(
        available=False, reason=reason, crs_kind=crs_kind, crs_epsg=crs_epsg,
        raw_tags_found=raw_tags_found or [],
    )


def _parse_geo_keys(geo_key_values) -> tuple[str | None, int | None]:
    """Parses the GeoKeyDirectoryTag SHORT array into (crs_kind, epsg).
    Format: header (KeyDirectoryVersion, KeyRevision, MinorRevision,
    NumberOfKeys), then NumberOfKeys * (KeyID, TIFFTagLocation, Count,
    Value_or_Offset). Only inline (TIFFTagLocation==0) SHORT values for the
    two keys this module cares about are resolved; anything else (a key
    stored in GeoDoubleParamsTag/GeoAsciiParamsTag) is left as None rather
    than guessed."""
    vals = list(geo_key_values)
    if len(vals) < 4:
        raise ValueError(f"GeoKeyDirectoryTag too short to contain a header ({len(vals)} values).")
    num_keys = int(vals[3])
    expected_len = 4 + 4 * num_keys
    if len(vals) < expected_len:
        raise ValueError(
            f"GeoKeyDirectoryTag header declares {num_keys} keys (expects {expected_len} values) "
            f"but only {len(vals)} were present - truncated/corrupt directory."
        )

    model_type = None
    epsg = None
    for i in range(num_keys):
        base = 4 + 4 * i
        key_id, tag_location, count, value = vals[base:base + 4]
        if tag_location != 0:
            continue  # value lives in GeoDoubleParamsTag/GeoAsciiParamsTag - not resolved here
        if key_id == _GTMODELTYPE_GEOKEY:
            model_type = int(value)
        elif key_id == _GEOGRAPHIC_TYPE_GEOKEY and value not in (0, 32767):
            epsg = int(value)
        elif key_id == _PROJECTED_CS_TYPE_GEOKEY and value not in (0, 32767):
            epsg = int(value)

    if model_type == _MODEL_TYPE_GEOGRAPHIC:
        return "geographic", epsg
    if model_type == _MODEL_TYPE_PROJECTED:
        return "projected", epsg
    return None, epsg


def _parse_geotiff_tags(tf: Any) -> GeoTiffMetadata:
    if not tf.pages:
        return _unavailable("File has no image pages to read GeoTIFF tags from.")
    tags = tf.pages[0].tags
    found = [t.name for t in tags if t.code in (
        _TAG_MODEL_PIXEL_SCALE, _TAG_MODEL_TIEPOINT,
        _TAG_MODEL_TRANSFORMATION, _TAG_GEO_KEY_DIRECTORY, _TAG_GEO_ASCII_PARAMS,
    )]

    geokey_tag = tags.get(_TAG_GEO_KEY_DIRECTORY)
    crs_kind, crs_epsg = (None, None)
    if geokey_tag is not None:
        try:
            crs_kind, crs_epsg = _parse_geo_keys(geokey_tag.value)
        except (ValueError, TypeError, IndexError) as e:
            return _unavailable(f"GeoKeyDirectoryTag present but invalid/corrupt: {e}", found)

    pixel_scale_tag = tags.get(_TAG_MODEL_PIXEL_SCALE)
    tiepoint_tag = tags.get(_TAG_MODEL_TIEPOINT)
    transform_tag = tags.get(_TAG_MODEL_TRANSFORMATION)

    if transform_tag is not None:
        m = list(transform_tag.value)
        if len(m) != 16:
            return _unavailable(
                f"ModelTransformationTag present but has {len(m)} values (expected 16).", found,
                crs_kind, crs_epsg,
            )
        # Row-major 4x4: [a0 a1 a2 a3; a4 a5 a6 a7; ...]. For a
        # north-up raster, X = a0*i + a1*j + a3, Y = a4*i + a5*j + a7.
        origin_lon, px_lon, origin_lat, px_lat = m[3], m[0], m[7], m[5]
    elif pixel_scale_tag is not None and tiepoint_tag is not None:
        scale = list(pixel_scale_tag.value)
        tie = list(tiepoint_tag.value)
        if len(scale) < 2:
            return _unavailable(f"ModelPixelScaleTag has {len(scale)} values (expected >= 2).", found,
                                 crs_kind, crs_epsg)
        if len(tie) < 6:
            return _unavailable(f"ModelTiepointTag has {len(tie)} values (expected >= 6).", found,
                                 crs_kind, crs_epsg)
        # tie = (i, j, k, X, Y, Z) for the first tie point - the raster
        # pixel (i, j) maps to model-space (X, Y). Only the first tie
        # point is used (matches the common single-tiepoint case this
        # codebase's uploads are expected to use); a multi-tiepoint
        # (rubber-sheeted) GeoTIFF is intentionally out of scope.
        px_i, px_j, _px_k, model_x, model_y, _model_z = tie[:6]
        px_lon, px_lat_scale = scale[0], scale[1]
        # Pixel (0,0) origin, accounting for the tie point not
        # necessarily being at (0,0):
        origin_lon = model_x - px_i * px_lon
        origin_lat = model_y + px_j * px_lat_scale
        px_lat = -px_lat_scale  # north-up: latitude decreases as row increases
    else:
        return _unavailable(
            "No ModelTransformationTag and no ModelPixelScaleTag+ModelTiepointTag pair found - "
            "file has no usable affine geo-referencing tags.", found, crs_kind, crs_epsg,
        )

    if not all(math.isfinite(v) for v in (origin_lat, origin_lon, px_lat, px_lon)):
        return _unavailable("Affine transform tags parsed but contain non-finite values.", found,
                             crs_kind, crs_epsg)

    if crs_kind == "projected":
        return _unavailable(
            "File has a real embedded PROJECTED coordinate system"
            + (f" (EPSG:{crs_epsg})" if crs_epsg else "")
            + " - reprojecting easting/northing to lat/lon requires pyproj/PROJ, which is "
            "not installed in this build. Falling back to the assumed-coordinate path rather "
            "than mis-projecting.", found, crs_kind, crs_epsg,
        )
    if crs_kind is None:
        return _unavailable(
            "Affine transform tags were found but no GeoKeyDirectoryTag (or no "
            "GTModelTypeGeoKey within it) was present, so the coordinate system - and "
            "therefore whether these numbers are really degrees - could not be confirmed.",
            found, crs_kind, crs_epsg,
        )

    # crs_kind == "geographic": tie point / pixel scale values are
    # already in degrees (EPSG:4326 in the overwhelming common case;
    # this module does not distinguish other geographic datums, but
    # no other geographic CRS is expected from a Sentinel-1-style
    # SAR product uploaded to this app).
    if not (-90.0 <= origin_lat <= 90.0 and -180.0 <= origin_lon <= 180.0):
        return _unavailable(
            f"CRS is geographic but the parsed origin ({origin_lat}, {origin_lon}) is out of "
            "valid lat/lon range - treating as corrupt rather than trusting it.",
            found, crs_kind, crs_epsg,
        )

    return GeoTiffMetadata(
        available=True,
        crs_kind=crs_kind,
        crs_epsg=crs_epsg or 4326,
        origin_lat=float(origin_lat),
        origin_lon=float(origin_lon),
        pixel_size_deg_lat=float(px_lat),
        pixel_size_deg_lon=float(px_lon),
        raw_tags_found=found,
    )


def read_geotiff_metadata(path_or_tf: str | Any) -> GeoTiffMetadata:
    """
    Attempts to read real embedded geo-referencing from a GeoTIFF at `path_or_tf`.
    Accepts either a filesystem path or an already-opened `tifffile.TiffFile` instance.
    Never raises - any failure to read/parse is reported via
    `GeoTiffMetadata.available=False` and a human-readable `reason`, so
    callers (real_image.py) can safely fall back to an assumed transform.
    """
    if not TIFFFILE_AVAILABLE:
        return _unavailable("tifffile is not installed - cannot inspect GeoTIFF tags.")

    if not isinstance(path_or_tf, str):
        try:
            return _parse_geotiff_tags(path_or_tf)
        except Exception as e:
            return _unavailable(f"Could not parse TIFF object ({type(e).__name__}: {e}).")

    try:
        with tifffile.TiffFile(path_or_tf) as tf:
            return _parse_geotiff_tags(tf)
    except FileNotFoundError:
        return _unavailable(f"File not found: {path_or_tf}")
    except Exception as e:  # pragma: no cover - defensive: never let a corrupt file crash the upload
        return _unavailable(f"Could not parse file as TIFF ({type(e).__name__}: {e}).")
