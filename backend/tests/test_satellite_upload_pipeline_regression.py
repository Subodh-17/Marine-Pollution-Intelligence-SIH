import io
import os
import math
import numpy as np
import pytest
from PIL import Image
import tifffile
from fastapi.testclient import TestClient

from app.main import app
from app.config import get_settings
from app.satellite.real_image import load_real_scene, UnsupportedImageFormat, _normalize_band
from app.investigation.orchestrator import run_investigation

@pytest.fixture
def client():
    return TestClient(app)


def test_regression_valid_supported_png(client):
    img = Image.new("L", (128, 128), color=180)
    for y in range(40, 70):
        for x in range(40, 70):
            img.putpixel((x, y), 30)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    res = client.post(
        "/api/satellite/upload?detector=classical&center_lat=19.05&center_lon=71.20",
        files={"file": ("valid_scene.png", buf.getvalue(), "image/png")}
    )
    assert res.status_code == 200
    data = res.json()
    assert data["id"]
    assert data["mode"] == "real_satellite"
    assert data["satellite"]["geolocation_is_assumed"] is True
    assert data["satellite"]["detector_provenance"] == "CLASSICAL_DARK_SPOT"


def test_regression_valid_single_band_geotiff(tmp_path, client):
    path = tmp_path / "single_band.tif"
    arr = np.random.uniform(0.1, 0.9, (128, 128)).astype(np.float32)
    tifffile.imwrite(str(path), arr)

    scene = load_real_scene(str(path), center_lat=19.05, center_lon=71.20)
    assert scene.is_dual_pol is False
    assert scene.image.shape == (128, 128)

    with open(path, "rb") as f:
        res = client.post(
            "/api/satellite/upload?detector=classical",
            files={"file": ("single_band.tif", f, "image/tiff")}
        )
    assert res.status_code == 200
    assert res.json()["mode"] == "real_satellite"


def test_regression_valid_dual_pol_geotiff(tmp_path, client):
    path = tmp_path / "dual_pol.tif"
    arr = np.random.uniform(0.1, 0.9, (128, 128, 2)).astype(np.float32)
    tifffile.imwrite(str(path), arr)

    scene = load_real_scene(str(path), center_lat=19.05, center_lon=71.20)
    assert scene.is_dual_pol is True
    assert scene.vv is not None
    assert scene.vh is not None
    assert scene.image.shape == (128, 128)


def test_regression_corrupted_png(client):
    corrupt_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRcorrupt_data_here"
    res = client.post(
        "/api/satellite/upload",
        files={"file": ("broken.png", corrupt_bytes, "image/png")}
    )
    assert res.status_code == 422
    assert "could not process uploaded image" in res.json()["detail"].lower()


def test_regression_corrupted_tiff(client):
    corrupt_bytes = b"II*\x00\x08\x00\x00\x00corrupt_tiff_bytes"
    res = client.post(
        "/api/satellite/upload",
        files={"file": ("broken.tif", corrupt_bytes, "image/tiff")}
    )
    assert res.status_code == 422
    assert "could not process uploaded image" in res.json()["detail"].lower()


def test_regression_unsupported_extension(client):
    res = client.post(
        "/api/satellite/upload",
        files={"file": ("malicious.exe", b"binary_content", "application/octet-stream")}
    )
    assert res.status_code == 400
    assert "unsupported file type" in res.json()["detail"].lower()


def test_regression_unsupported_band_count(tmp_path):
    path = tmp_path / "multispectral_8bands.tif"
    arr = np.random.uniform(0.1, 0.9, (64, 64, 8)).astype(np.float32)
    tifffile.imwrite(str(path), arr)

    with pytest.raises(UnsupportedImageFormat) as exc_info:
        load_real_scene(str(path))
    assert "unsupported raster band count (8)" in str(exc_info.value).lower()


def test_regression_file_exceeding_size_limit(monkeypatch, client):
    monkeypatch.setenv("MAX_UPLOAD_SIZE_MB", "0.01")
    get_settings.cache_clear()
    try:
        big_content = b"x" * 25000
        res = client.post(
            "/api/satellite/upload",
            files={"file": ("large.png", big_content, "image/png")}
        )
        assert res.status_code == 413
        assert "exceeds" in res.json()["detail"].lower()
    finally:
        get_settings.cache_clear()


def test_regression_missing_geospatial_metadata_fallback(tmp_path):
    path = tmp_path / "plain_image.png"
    img = Image.new("L", (100, 100), color=120)
    img.save(str(path))

    scene = load_real_scene(str(path), center_lat=20.5, center_lon=72.5, pixel_size_m=50.0)
    assert scene.geolocation_is_assumed is True
    assert scene.geolocation_source == "assumed_center_coordinates"
    assert math.isclose(scene.geotransform.origin_lat, 20.5 + 50 * (50.0 / 111320.0), rel_tol=1e-3)


def test_regression_raster_invalid_or_constant_values():
    zeros = np.zeros((64, 64), dtype=np.float32)
    norm_zeros = _normalize_band(zeros)
    assert np.all(norm_zeros == 0.0)

    constant = np.full((64, 64), 42.0, dtype=np.float32)
    norm_const = _normalize_band(constant)
    assert np.all(norm_const == 0.0)

    nans = np.array([[np.nan, 1.0], [np.inf, -np.inf]], dtype=np.float32)
    cleaned = np.nan_to_num(nans, nan=0.0, posinf=0.0, neginf=0.0)
    assert np.all(np.isfinite(cleaned))


def test_regression_ml_detector_single_band_honest_fallback(tmp_path, client):
    path = tmp_path / "single_band_for_ml.png"
    img = Image.new("L", (64, 64), color=150)
    img.save(str(path))

    with open(path, "rb") as f:
        res = client.post(
            "/api/satellite/upload?detector=ml",
            files={"file": ("single_band_for_ml.png", f, "image/png")}
        )
    assert res.status_code == 200
    data = res.json()
    assert data["satellite"]["detector_provenance"] == "CLASSICAL_DARK_SPOT_ML_FALLBACK"


def test_regression_real_test_png_file_e2e():
    png_path = r"C:\Users\Subodh\Downloads\Sentinel1_SAR_test_image.png"
    if not os.path.exists(png_path):
        pytest.skip("Test PNG file not present on this machine")

    scene = load_real_scene(png_path)
    assert scene.image is not None
    assert max(scene.image.shape) <= 1024
    assert scene.original_shape == (4191, 6409)

    inv = run_investigation(
        mode="real_satellite",
        image_path=png_path,
        detector="classical",
        center_lat=19.05,
        center_lon=71.20,
    )
    assert inv.id
    assert inv.mode == "real_satellite"
    assert inv.satellite is not None
    assert inv.satellite["scene_classification"] is not None


def test_regression_real_test_tiff_file_e2e():
    tiff_path = r"C:\Users\Subodh\Downloads\S1A_IW_GRDH_1SDV_20200724T111906_20200724T111931_033596_03E4C4_3F32.tiff"
    if not os.path.exists(tiff_path):
        pytest.skip("Test TIFF file not present on this machine")

    scene = load_real_scene(tiff_path)
    assert scene.image is not None
    assert max(scene.image.shape) <= 1024
    assert scene.original_shape == (4191, 6409)

    inv = run_investigation(
        mode="real_satellite",
        image_path=tiff_path,
        detector="classical",
        center_lat=19.05,
        center_lon=71.20,
    )
    assert inv.id
    assert inv.mode == "real_satellite"
    assert inv.satellite is not None
    assert inv.satellite["scene_classification"] is not None


def test_regression_exact_geospatial_preservation_embedded_geotiff(tmp_path):
    orig_h, orig_w = 2000, 3000
    orig_px_lat, orig_px_lon = -0.0001, 0.0001
    orig_origin_lat, orig_origin_lon = 19.05, 71.20
    arr = np.random.uniform(0.1, 0.9, (orig_h, orig_w)).astype(np.float32)
    extratags = [
        (33550, "d", 3, [abs(orig_px_lon), abs(orig_px_lat), 0.0], False),
        (33922, "d", 6, [0.0, 0.0, 0.0, orig_origin_lon, orig_origin_lat, 0.0], False),
        (34735, "H", 12, [1, 1, 0, 2, 1024, 0, 1, 2, 2048, 0, 1, 4326], False),
    ]
    path = tmp_path / "preservation_test.tif"
    tifffile.imwrite(str(path), arr, extratags=extratags)

    scene = load_real_scene(str(path))
    assert scene.geolocation_is_assumed is False
    assert scene.geolocation_source == "embedded_geotiff_metadata"

    new_h, new_w = scene.image.shape
    assert max(new_h, new_w) <= 1024

    orig_br_lat = orig_origin_lat + orig_h * orig_px_lat
    orig_br_lon = orig_origin_lon + orig_w * orig_px_lon

    down_br_lat, down_br_lon = scene.geotransform.pixel_to_latlon(new_h, new_w)

    assert math.isclose(down_br_lat, orig_br_lat, abs_tol=1e-12)
    assert math.isclose(down_br_lon, orig_br_lon, abs_tol=1e-12)

    orig_mid_lat = orig_origin_lat + (orig_h / 2.0) * orig_px_lat
    orig_mid_lon = orig_origin_lon + (orig_w / 2.0) * orig_px_lon
    down_mid_lat, down_mid_lon = scene.geotransform.pixel_to_latlon(new_h / 2.0, new_w / 2.0)
    assert math.isclose(down_mid_lat, orig_mid_lat, abs_tol=1e-12)
    assert math.isclose(down_mid_lon, orig_mid_lon, abs_tol=1e-12)


def test_regression_exact_geospatial_preservation_assumed_coords(tmp_path):
    orig_h, orig_w = 1500, 2500
    path = tmp_path / "assumed_preservation.png"
    img = Image.new("L", (orig_w, orig_h), color=128)
    img.save(str(path))

    center_lat, center_lon, px_m = 18.50, 72.80, 50.0
    scene = load_real_scene(str(path), center_lat=center_lat, center_lon=center_lon, pixel_size_m=px_m)

    new_h, new_w = scene.image.shape
    assert max(new_h, new_w) <= 1024

    full_deg_lat = -px_m / 111_320.0
    full_deg_lon = px_m / (111_320.0 * np.cos(np.deg2rad(center_lat)))
    full_origin_lat = center_lat - (orig_h / 2.0) * full_deg_lat
    full_origin_lon = center_lon - (orig_w / 2.0) * full_deg_lon

    assert math.isclose(scene.geotransform.origin_lat, full_origin_lat, abs_tol=1e-12)
    assert math.isclose(scene.geotransform.origin_lon, full_origin_lon, abs_tol=1e-12)

    down_center_lat, down_center_lon = scene.geotransform.pixel_to_latlon(new_h / 2.0, new_w / 2.0)
    assert math.isclose(down_center_lat, center_lat, abs_tol=1e-12)
    assert math.isclose(down_center_lon, center_lon, abs_tol=1e-12)


def test_regression_decoded_memory_limit_exceeded(client, tmp_path):
    path = tmp_path / "huge_decoded.tif"
    arr = np.ones((7000, 7000), dtype=np.float32)
    tifffile.imwrite(str(path), arr, compression="deflate")

    with open(path, "rb") as f:
        res = client.post(
            "/api/satellite/upload",
            files={"file": ("huge_decoded.tif", f, "image/tiff")}
        )
    assert res.status_code == 422
    assert "exceeds maximum safe limit" in res.json()["detail"].lower()


def test_regression_complex_sar_data_handling(tmp_path):
    path = tmp_path / "slc_sar.tif"
    real_part = np.random.uniform(0.1, 0.9, (128, 128)).astype(np.float32)
    imag_part = np.random.uniform(0.1, 0.9, (128, 128)).astype(np.float32)
    complex_arr = real_part + 1j * imag_part
    tifffile.imwrite(str(path), complex_arr)

    scene = load_real_scene(str(path))
    assert scene.image is not None
    assert scene.image.shape == (128, 128)
    assert np.all(np.isfinite(scene.image))
    assert np.min(scene.image) >= 0.0
    assert np.max(scene.image) <= 1.0
