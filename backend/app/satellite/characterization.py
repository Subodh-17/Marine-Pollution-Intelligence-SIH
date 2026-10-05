"""
Slick geometric characterisation.

Converts a binary detection mask into real, measured geometric properties.
When a geotransform is supplied, pixel measurements are converted to
geographic units (km / km^2) using a local equirectangular approximation
around the scene centre - adequate at the scale of a single Sentinel-1
scene (~100-250 km swath) and clearly documented as an approximation.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
from app.satellite import geometry_utils as gu

from app.satellite.synthetic import SceneGeoTransform


@dataclass
class SlickGeometry:
    confidence: float
    area_km2: float
    perimeter_km: float
    length_km: float
    width_km: float
    aspect_ratio: float
    orientation_deg: float
    centroid_lat: float
    centroid_lon: float
    bbox_latlon: list  # [[lat,lon], ...] 4 corners
    polygon_latlon: list  # simplified polygon boundary [[lat,lon], ...]
    area_px: int

    def to_dict(self):
        return asdict(self)


def _pixel_area_km2(gt: SceneGeoTransform) -> float:
    # meters per pixel, approx via degree->m conversion at origin latitude
    lat_m = abs(gt.pixel_size_deg_lat) * 111_320.0
    lon_m = abs(gt.pixel_size_deg_lon) * 111_320.0 * np.cos(np.deg2rad(gt.origin_lat))
    return (lat_m * lon_m) / 1e6


def characterize_slick(mask: np.ndarray, confidence: float, gt: SceneGeoTransform) -> SlickGeometry | None:
    if mask.sum() == 0:
        return None

    labeled = gu.label(mask)
    props = gu.regionprops(labeled)
    if not props:
        return None
    r = max(props, key=lambda p: p.area)  # largest (should be only one)

    px_area_km2 = _pixel_area_km2(gt)
    area_km2 = r.area * px_area_km2

    # approximate meters-per-pixel along each axis for length/width/perimeter
    lat_m_per_px = abs(gt.pixel_size_deg_lat) * 111_320.0
    lon_m_per_px = abs(gt.pixel_size_deg_lon) * 111_320.0 * np.cos(np.deg2rad(gt.origin_lat))
    m_per_px = (lat_m_per_px + lon_m_per_px) / 2.0  # scene is near-square pixels by construction

    length_km = (r.axis_major_length * m_per_px) / 1000.0
    width_km = (r.axis_minor_length * m_per_px) / 1000.0
    aspect_ratio = float(length_km / width_km) if width_km > 0 else float("nan")
    perimeter_km = (r.perimeter * m_per_px) / 1000.0

    # orientation: skimage gives radians from vertical axis, convert to
    # compass-style degrees (0 = north, clockwise) for UI readability
    orientation_deg = float((90 - np.degrees(r.orientation)) % 180)

    centroid_row, centroid_col = r.centroid
    centroid_lat, centroid_lon = gt.pixel_to_latlon(centroid_row, centroid_col)

    min_row, min_col, max_row, max_col = r.bbox
    bbox_corners_px = [
        (min_row, min_col), (min_row, max_col),
        (max_row, max_col), (max_row, min_col),
    ]
    bbox_latlon = [list(gt.pixel_to_latlon(rr, cc)) for rr, cc in bbox_corners_px]

    # simplified polygon boundary (contour) -> lat/lon, subsampled for payload size
    contour = gu.find_boundary_contour(labeled == r.label)
    polygon_latlon = []
    if contour:
        step = max(1, len(contour) // 60)
        for pt in contour[::step]:
            lat, lon = gt.pixel_to_latlon(pt[0], pt[1])
            polygon_latlon.append([round(lat, 6), round(lon, 6)])

    return SlickGeometry(
        confidence=round(confidence, 4),
        area_km2=round(area_km2, 3),
        perimeter_km=round(perimeter_km, 3),
        length_km=round(length_km, 3),
        width_km=round(width_km, 3),
        aspect_ratio=round(aspect_ratio, 2) if aspect_ratio == aspect_ratio else 0.0,
        orientation_deg=round(orientation_deg, 1),
        centroid_lat=round(centroid_lat, 5),
        centroid_lon=round(centroid_lon, 5),
        bbox_latlon=[[round(a, 5), round(b, 5)] for a, b in bbox_latlon],
        polygon_latlon=polygon_latlon,
        area_px=int(r.area),
    )
