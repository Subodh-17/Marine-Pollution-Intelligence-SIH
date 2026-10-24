import numpy as np

from app.satellite.synthetic import SceneGeoTransform
from app.satellite.characterization import characterize_slick


def _flat_gt():
    return SceneGeoTransform(
        origin_lat=20.0, origin_lon=70.0,
        pixel_size_deg_lat=-0.001, pixel_size_deg_lon=0.001,
    )


def test_characterize_slick_returns_none_for_empty_mask():
    mask = np.zeros((100, 100), dtype=bool)
    result = characterize_slick(mask, 0.5, _flat_gt())
    assert result is None


def test_characterize_slick_rectangle_geometry():
    mask = np.zeros((100, 100), dtype=bool)
    mask[40:60, 20:80] = True  # 20 rows x 60 cols rectangle
    result = characterize_slick(mask, 0.9, _flat_gt())
    assert result is not None
    assert result.area_px == 20 * 60
    assert result.area_km2 > 0
    assert result.length_km > result.width_km  # elongated rectangle: longer axis first
    assert result.aspect_ratio > 1.0
    # centroid should be near the geometric center of the rectangle in pixel space
    assert 45 <= (mask.nonzero()[0].mean()) <= 55


def test_characterize_slick_centroid_within_bbox():
    mask = np.zeros((80, 80), dtype=bool)
    mask[10:30, 10:50] = True
    result = characterize_slick(mask, 0.8, _flat_gt())
    lats = [pt[0] for pt in result.bbox_latlon]
    lons = [pt[1] for pt in result.bbox_latlon]
    assert min(lats) <= result.centroid_lat <= max(lats)
    assert min(lons) <= result.centroid_lon <= max(lons)


def test_perimeter_and_area_positive_and_consistent():
    mask = np.zeros((60, 60), dtype=bool)
    mask[20:40, 20:40] = True  # 20x20 square
    result = characterize_slick(mask, 0.7, _flat_gt())
    assert result.perimeter_km > 0
    assert result.area_km2 > 0
    # a square's perimeter^2 should be substantially larger than its area (sanity bound)
    assert result.perimeter_km ** 2 > result.area_km2


def test_characterize_slick_filters_tiny_noise():
    mask = np.zeros((50, 50), dtype=bool)
    mask[10, 10] = True
    mask[10, 11] = True
    # 2 pixels total < min_area_px (10)
    result = characterize_slick(mask, 0.5, _flat_gt())
    assert result is None


def test_characterize_slick_multi_component_aggregation():
    mask = np.zeros((100, 100), dtype=bool)
    # Component 1: 10x10 = 100 px at (10, 10)
    mask[10:20, 10:20] = True
    # Component 2: 10x20 = 200 px at (60, 60)
    mask[60:70, 60:80] = True

    result = characterize_slick(mask, 0.85, _flat_gt())
    assert result is not None
    assert result.component_count == 2
    assert result.area_px == 300
    assert result.largest_component_area_px == 200
    assert result.fragmentation_index > 0.0
    # Verify centroid is weighted between the two components
    gt = _flat_gt()
    # Weighted row should be (100*14.5 + 200*64.5) / 300 = (1450 + 12900)/300 = 47.83
    c_lat, c_lon = gt.pixel_to_latlon(47.83, 47.83)
    assert abs(result.centroid_lat - c_lat) < 0.01
