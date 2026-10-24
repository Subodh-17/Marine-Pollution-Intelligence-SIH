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
    component_count: int = 1
    largest_component_area_px: int = 0
    fragmentation_index: float = 0.0
    slick_quartile: str = "Q1"

    def to_dict(self):
        return asdict(self)


def _pixel_area_km2(gt: SceneGeoTransform) -> float:
    # meters per pixel, approx via degree->m conversion at origin latitude
    lat_m = abs(gt.pixel_size_deg_lat) * 111_320.0
    lon_m = abs(gt.pixel_size_deg_lon) * 111_320.0 * np.cos(np.deg2rad(gt.origin_lat))
    return (lat_m * lon_m) / 1e6


def characterize_slick(
    mask: np.ndarray,
    confidence: float,
    gt: SceneGeoTransform,
    min_area_px: int = 10,
    min_component_px: int = 3,
) -> SlickGeometry | None:
    """
    Converts a binary detection mask into physical geometric properties.

    SIH-26143 AUDIT REVISION (§2 & §3):
    1. Rejection of tiny sub-threshold noise masks (min_area_px=10 by default)
       to prevent Lookalike false-alarm leakage from triggering AIS attribution.
    2. Multi-component aggregation: rather than discarding everything except
       the single largest connected component (which heavily under-reports
       spill area and mislocates centroids in large, fragmented slicks), all
       valid connected components (>= min_component_px) are aggregated.
    3. Global area-weighted centroid and overall bounding box across all parts.
    4. Slick-size quartile classification (Q1-Q4) and fragmentation index.
    """
    total_mask_px = int(mask.sum())
    if total_mask_px < min_area_px:
        return None

    labeled = gu.label(mask)
    props = gu.regionprops(labeled)
    if not props:
        return None

    # Filter out sub-pixel noise/speckle components
    valid_props = [p for p in props if p.area >= min_component_px]
    if not valid_props:
        return None

    valid_area_px = sum(p.area for p in valid_props)
    if valid_area_px < min_area_px:
        return None

    # Primary (largest) component
    r = max(valid_props, key=lambda p: p.area)

    px_area_km2 = _pixel_area_km2(gt)
    total_area_km2 = valid_area_px * px_area_km2

    # approximate meters-per-pixel along each axis for length/width/perimeter
    lat_m_per_px = abs(gt.pixel_size_deg_lat) * 111_320.0
    lon_m_per_px = abs(gt.pixel_size_deg_lon) * 111_320.0 * np.cos(np.deg2rad(gt.origin_lat))
    m_per_px = (lat_m_per_px + lon_m_per_px) / 2.0  # scene is near-square pixels by construction

    # Length and width from primary component
    length_km = (r.axis_major_length * m_per_px) / 1000.0
    width_km = (r.axis_minor_length * m_per_px) / 1000.0
    aspect_ratio = float(length_km / width_km) if width_km > 0 else float("nan")

    # Sum perimeters across all valid components
    total_perimeter_px = sum(p.perimeter for p in valid_props)
    perimeter_km = (total_perimeter_px * m_per_px) / 1000.0

    # orientation: skimage gives radians from vertical axis, convert to
    # compass-style degrees (0 = north, clockwise) for UI readability
    orientation_deg = float((90 - np.degrees(r.orientation)) % 180)

    # True area-weighted centroid across ALL valid components
    weighted_row = sum(p.area * p.centroid[0] for p in valid_props) / valid_area_px
    weighted_col = sum(p.area * p.centroid[1] for p in valid_props) / valid_area_px
    centroid_lat, centroid_lon = gt.pixel_to_latlon(weighted_row, weighted_col)

    # Global bounding box spanning all valid components
    min_row = min(p.bbox[0] for p in valid_props)
    min_col = min(p.bbox[1] for p in valid_props)
    max_row = max(p.bbox[2] for p in valid_props)
    max_col = max(p.bbox[3] for p in valid_props)

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

    # Fragmentation and slick quartile categorization
    frag_index = round(float(1.0 - (r.area / valid_area_px)), 3)
    frame_coverage = valid_area_px / float(mask.size) if mask.size > 0 else 0.0
    if frame_coverage < 0.019:
        quartile = "Q1 (<1.9%)"
    elif frame_coverage < 0.043:
        quartile = "Q2 (1.9-4.3%)"
    elif frame_coverage < 0.11:
        quartile = "Q3 (4.3-11%)"
    else:
        quartile = "Q4 (>11%)"

    return SlickGeometry(
        confidence=round(confidence, 4),
        area_km2=round(total_area_km2, 3),
        perimeter_km=round(perimeter_km, 3),
        length_km=round(length_km, 3),
        width_km=round(width_km, 3),
        aspect_ratio=round(aspect_ratio, 2) if aspect_ratio == aspect_ratio else 0.0,
        orientation_deg=round(orientation_deg, 1),
        centroid_lat=round(centroid_lat, 5),
        centroid_lon=round(centroid_lon, 5),
        bbox_latlon=[[round(a, 5), round(b, 5)] for a, b in bbox_latlon],
        polygon_latlon=polygon_latlon,
        area_px=int(valid_area_px),
        component_count=len(valid_props),
        largest_component_area_px=int(r.area),
        fragmentation_index=frag_index,
        slick_quartile=quartile,
    )
