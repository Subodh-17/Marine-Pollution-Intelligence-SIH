"""
Tests for app/satellite/geotiff_metadata.py (Section 5: real GeoTIFF
georeferencing) and its integration into app/satellite/real_image.py.

Every test GeoTIFF is synthesized in-memory/tmp_path with `tifffile`'s
`extratags` parameter - no external test-data files or network access
required, per the "platform-independent, no internet access, use tmp_path"
testing constraints.
"""
from __future__ import annotations

import numpy as np
import pytest
import tifffile

from app.satellite.geotiff_metadata import read_geotiff_metadata
from app.satellite.real_image import load_real_scene

ARR = np.random.RandomState(0).rand(16, 16).astype(np.float32)


def _write_tif(path, extratags):
    tifffile.imwrite(str(path), ARR, extratags=extratags)


def _geokeys(entries):
    """entries: list of (key_id, tag_location, count, value) tuples."""
    header = [1, 1, 0, len(entries)]
    flat = []
    for e in entries:
        flat.extend(e)
    return header + flat


# ---------------------------------------------------------------------------
# 1. Valid georeferenced GeoTIFF (geographic / EPSG:4326)
# ---------------------------------------------------------------------------
def test_valid_georeferenced_geotiff(tmp_path):
    path = tmp_path / "valid.tif"
    geokeys = _geokeys([(1024, 0, 1, 2), (1025, 0, 1, 1), (2048, 0, 1, 4326)])
    _write_tif(path, [
        (33550, "d", 3, (0.01, 0.02, 0.0), True),
        (33922, "d", 6, (0, 0, 0, 70.0, 20.0, 0.0), True),
        (34735, "H", len(geokeys), geokeys, True),
    ])
    meta = read_geotiff_metadata(str(path))
    assert meta.available is True
    assert meta.crs_kind == "geographic"
    assert meta.crs_epsg == 4326
    assert meta.origin_lat == pytest.approx(20.0)
    assert meta.origin_lon == pytest.approx(70.0)
    assert meta.pixel_size_deg_lat == pytest.approx(-0.02)
    assert meta.pixel_size_deg_lon == pytest.approx(0.01)


# ---------------------------------------------------------------------------
# 2. Missing CRS (affine tags present, no GeoKeyDirectoryTag at all)
# ---------------------------------------------------------------------------
def test_missing_crs(tmp_path):
    path = tmp_path / "missing_crs.tif"
    _write_tif(path, [
        (33550, "d", 3, (0.01, 0.01, 0.0), True),
        (33922, "d", 6, (0, 0, 0, 70.0, 20.0, 0.0), True),
    ])
    meta = read_geotiff_metadata(str(path))
    assert meta.available is False
    assert "GeoKeyDirectoryTag" in meta.reason or "coordinate system" in meta.reason
    assert meta.crs_kind is None


# ---------------------------------------------------------------------------
# 3. Missing transform (CRS present, no pixel-scale/tiepoint/transformation)
# ---------------------------------------------------------------------------
def test_missing_transform(tmp_path):
    path = tmp_path / "missing_transform.tif"
    geokeys = _geokeys([(1024, 0, 1, 2), (2048, 0, 1, 4326)])
    _write_tif(path, [
        (34735, "H", len(geokeys), geokeys, True),
    ])
    meta = read_geotiff_metadata(str(path))
    assert meta.available is False
    assert "affine" in meta.reason.lower() or "transform" in meta.reason.lower()
    assert meta.crs_kind == "geographic"  # CRS was still resolvable, just no transform


# ---------------------------------------------------------------------------
# 4. Invalid / corrupt metadata (GeoKeyDirectoryTag header lies about length)
# ---------------------------------------------------------------------------
def test_invalid_geokey_directory(tmp_path):
    path = tmp_path / "invalid.tif"
    # Header claims 5 keys but only 1 is actually present -> truncated/corrupt.
    corrupt = [1, 1, 0, 5, 1024, 0, 1, 2]
    _write_tif(path, [
        (33550, "d", 3, (0.01, 0.01, 0.0), True),
        (33922, "d", 6, (0, 0, 0, 70.0, 20.0, 0.0), True),
        (34735, "H", len(corrupt), corrupt, True),
    ])
    meta = read_geotiff_metadata(str(path))
    assert meta.available is False
    assert "corrupt" in meta.reason.lower() or "truncated" in meta.reason.lower()


def test_nonexistent_file():
    meta = read_geotiff_metadata("/tmp/does_not_exist_marine_pollution.tif")
    assert meta.available is False
    assert meta.reason


def test_not_a_tiff_file(tmp_path):
    path = tmp_path / "not_a_tiff.tif"
    path.write_bytes(b"this is not a tiff file at all")
    meta = read_geotiff_metadata(str(path))
    assert meta.available is False
    assert meta.reason


# ---------------------------------------------------------------------------
# 5. Unsupported CRS (projected, e.g. a UTM zone)
# ---------------------------------------------------------------------------
def test_unsupported_projected_crs(tmp_path):
    path = tmp_path / "projected.tif"
    geokeys = _geokeys([(1024, 0, 1, 1), (3072, 0, 1, 32643)])  # UTM zone 43N
    _write_tif(path, [
        (33550, "d", 3, (10.0, 10.0, 0.0), True),
        (33922, "d", 6, (0, 0, 0, 500000.0, 2200000.0, 0.0), True),
        (34735, "H", len(geokeys), geokeys, True),
    ])
    meta = read_geotiff_metadata(str(path))
    assert meta.available is False
    assert meta.crs_kind == "projected"
    assert meta.crs_epsg == 32643
    assert "pyproj" in meta.reason or "projected" in meta.reason.lower()


# ---------------------------------------------------------------------------
# 6/7. Fallback coordinate mode via real_image.load_real_scene
# ---------------------------------------------------------------------------
def test_real_image_uses_real_georeferencing_when_available(tmp_path):
    path = tmp_path / "geo.tif"
    geokeys = _geokeys([(1024, 0, 1, 2), (2048, 0, 1, 4326)])
    _write_tif(path, [
        (33550, "d", 3, (0.001, 0.001, 0.0), True),
        (33922, "d", 6, (0, 0, 0, 71.5, 18.5, 0.0), True),
        (34735, "H", len(geokeys), geokeys, True),
    ])
    scene = load_real_scene(str(path), center_lat=0.0, center_lon=0.0)
    assert scene.geolocation_is_assumed is False
    assert scene.geolocation_source == "embedded_geotiff_metadata"
    assert scene.geotransform.origin_lat == pytest.approx(18.5)
    assert scene.geotransform.origin_lon == pytest.approx(71.5)
    assert scene.geotiff_metadata["available"] is True


def test_real_image_falls_back_to_assumed_when_no_geo_tags(tmp_path):
    path = tmp_path / "no_geo.tif"
    tifffile.imwrite(str(path), ARR)  # no geo tags at all
    scene = load_real_scene(str(path), center_lat=19.05, center_lon=71.20)
    assert scene.geolocation_is_assumed is True
    assert scene.geolocation_source == "assumed_center_coordinates"
    assert scene.geotiff_metadata["available"] is False


def test_real_image_falls_back_for_projected_crs(tmp_path):
    path = tmp_path / "utm.tif"
    geokeys = _geokeys([(1024, 0, 1, 1), (3072, 0, 1, 32643)])
    tifffile.imwrite(str(path), ARR, extratags=[
        (33550, "d", 3, (10.0, 10.0, 0.0), True),
        (33922, "d", 6, (0, 0, 0, 500000.0, 2200000.0, 0.0), True),
        (34735, "H", len(geokeys), geokeys, True),
    ])
    scene = load_real_scene(str(path), center_lat=19.05, center_lon=71.20)
    assert scene.geolocation_is_assumed is True
    assert scene.geolocation_source == "assumed_center_coordinates"
    assert scene.geotiff_metadata["crs_kind"] == "projected"
