import io
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.satellite.characterization import SlickGeometry, characterize_slick
from app.satellite.synthetic import SceneGeoTransform
from app.satellite.real_image import load_real_scene


@pytest.fixture
def client():
    return TestClient(app)


def test_list_sample_satellite_scenes(client):
    r = client.get("/api/satellite/samples")
    assert r.status_code == 200
    samples = r.json()
    assert len(samples) >= 4
    sample_ids = [s["id"] for s in samples]
    assert "demo_oil_scene_01.tif" in sample_ids
    assert "demo_oil_scene_02.tif" in sample_ids
    assert "demo_lookalike_scene_01.tif" in sample_ids
    assert "demo_no_oil_scene_01.tif" in sample_ids


def test_analyze_sample_satellite_classical(client):
    r = client.post("/api/satellite/sample?sample_name=demo_oil_scene_01.tif&detector=classical")
    assert r.status_code == 200
    data = r.json()
    assert data["id"]
    assert data["mode"] == "real_satellite"
    assert data["satellite"]["confidence"] > 0.5
    assert len(data["candidates"]) > 0


def test_analyze_sample_satellite_ml(client):
    r = client.post("/api/satellite/sample?sample_name=demo_oil_scene_01.tif&detector=ml")
    assert r.status_code == 200
    data = r.json()
    assert data["id"]
    assert data["mode"] == "real_satellite"


def test_analyze_sample_satellite_not_found(client):
    r = client.post("/api/satellite/sample?sample_name=non_existent_scene.tif")
    assert r.status_code == 404


def test_channels_first_geotiff_normalization(tmp_path):
    import tifffile
    path = tmp_path / "channels_first.tif"
    # Shape (2, 64, 64) - channels first
    arr = np.random.uniform(0.1, 0.9, (2, 64, 64)).astype(np.float32)
    tifffile.imwrite(str(path), arr)

    scene = load_real_scene(str(path), center_lat=19.05, center_lon=71.20)
    assert scene.is_dual_pol is True
    assert scene.vv.shape == (64, 64)
    assert scene.vh.shape == (64, 64)
    assert scene.image.shape == (64, 64)


def test_large_image_adaptive_downsampling(tmp_path):
    path = tmp_path / "large_scene.png"
    # Create large image 2000 x 1500
    img = Image.new("L", (1500, 2000), color=128)
    img.save(str(path))

    scene = load_real_scene(str(path), center_lat=19.05, center_lon=71.20, pixel_size_m=40.0)
    # Target dim capped at 1024
    assert max(scene.image.shape) == 1024
    # Pixel size scaled proportionally so geographic coverage remains invariant
    expected_scale = 1024.0 / 2000.0
    expected_pixel_size = 40.0 / expected_scale
    assert scene.geotransform.pixel_size_deg_lat == pytest.approx(-expected_pixel_size / 111320.0, rel=1e-3)


def test_characterize_slick_nan_safety():
    gt = SceneGeoTransform(19.05, 71.20, -0.0003, 0.0003)
    # Mask with a small streak where width might be very small
    mask = np.zeros((100, 100), dtype=bool)
    mask[20:80, 50] = True  # 1-pixel wide line
    geom = characterize_slick(mask, 0.85, gt, min_area_px=10, min_component_px=3)
    assert geom is not None
    assert isinstance(geom.aspect_ratio, float)
    assert geom.aspect_ratio > 0.0
    import math
    assert math.isfinite(geom.aspect_ratio)
    assert math.isfinite(geom.area_km2)
    assert math.isfinite(geom.length_km)
    assert math.isfinite(geom.width_km)
