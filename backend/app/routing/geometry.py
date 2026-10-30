"""
Geodesic geometry and spatial intersection utilities for maritime navigation.
All calculations are pure-Python without required C-extension dependencies.
"""
from __future__ import annotations

import math
from typing import Sequence, Tuple, List

EARTH_RADIUS_KM = 6371.0
KM_PER_NM = 1.852
EARTH_RADIUS_NM = EARTH_RADIUS_KM / KM_PER_NM  # ~3440.065 NM


def haversine_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Computes great-circle distance in kilometers using the Haversine formula."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = (math.sin(dphi / 2.0) ** 2 +
         math.cos(phi1) * math.cos(phi2) * (math.sin(dlambda / 2.0) ** 2))
    a = min(1.0, max(0.0, a))
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return EARTH_RADIUS_KM * c


def haversine_distance_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Computes great-circle distance in nautical miles (1 NM = 1.852 km)."""
    return haversine_distance_km(lat1, lon1, lat2, lon2) / KM_PER_NM


def initial_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Computes initial compass bearing (forward azimuth) from point 1 to point 2 in degrees [0, 360).
    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)

    y = math.sin(dlambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    bearing = math.degrees(math.atan2(y, x))
    return (bearing + 360.0) % 360.0


def destination_point(lat: float, lon: float, bearing_deg: float, distance_nm: float) -> Tuple[float, float]:
    """
    Computes destination latitude and longitude given start point, bearing, and distance in NM.
    """
    delta = distance_nm / EARTH_RADIUS_NM
    theta = math.radians(bearing_deg)
    phi1 = math.radians(lat)
    lambda1 = math.radians(lon)

    phi2 = math.asin(
        math.sin(phi1) * math.cos(delta) +
        math.cos(phi1) * math.sin(delta) * math.cos(theta)
    )
    lambda2 = lambda1 + math.atan2(
        math.sin(theta) * math.sin(delta) * math.cos(phi1),
        math.cos(delta) - math.sin(phi1) * math.sin(phi2)
    )
    # Normalize longitude to [-180, 180]
    lon_deg = (math.degrees(lambda2) + 540.0) % 360.0 - 180.0
    return math.degrees(phi2), lon_deg


def interpolate_great_circle(
    lat1: float, lon1: float, lat2: float, lon2: float, num_points: int = 15
) -> List[Tuple[float, float]]:
    """
    Interpolates intermediate coordinates along the great circle arc between two points.
    """
    if num_points <= 1:
        return [(lat1, lon1), (lat2, lon2)]

    total_dist_nm = haversine_distance_nm(lat1, lon1, lat2, lon2)
    if total_dist_nm < 1e-6:
        return [(lat1, lon1), (lat2, lon2)]

    phi1, lambda1 = math.radians(lat1), math.radians(lon1)
    phi2, lambda2 = math.radians(lat2), math.radians(lon2)

    # Angular distance d
    cos_d = math.sin(phi1) * math.sin(phi2) + math.cos(phi1) * math.cos(phi2) * math.cos(lambda2 - lambda1)
    cos_d = min(1.0, max(-1.0, cos_d))
    d = math.acos(cos_d)

    if d < 1e-9:
        return [(lat1, lon1), (lat2, lon2)]

    sin_d = math.sin(d)
    coords = []
    for i in range(num_points + 1):
        f = i / float(num_points)
        a = math.sin((1.0 - f) * d) / sin_d
        b = math.sin(f * d) / sin_d
        x = a * math.cos(phi1) * math.cos(lambda1) + b * math.cos(phi2) * math.cos(lambda2)
        y = a * math.cos(phi1) * math.sin(lambda1) + b * math.cos(phi2) * math.sin(lambda2)
        z = a * math.sin(phi1) + b * math.sin(phi2)
        inter_phi = math.atan2(z, math.sqrt(x * x + y * y))
        inter_lambda = math.atan2(y, x)
        coords.append((math.degrees(inter_phi), (math.degrees(inter_lambda) + 540.0) % 360.0 - 180.0))
    return coords


def point_in_polygon(lat: float, lon: float, polygon: Sequence[Sequence[float]]) -> bool:
    """
    Ray-casting algorithm to test whether (lat, lon) is inside a polygon.
    Polygon is expected to be a sequence of [lat, lon] or (lat, lon) pairs.
    """
    if len(polygon) < 3:
        return False

    inside = False
    n = len(polygon)
    j = n - 1

    for i in range(n):
        yi, xi = polygon[i][0], polygon[i][1]
        yj, xj = polygon[j][0], polygon[j][1]
        if ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / (yj - yi) + xi):
            inside = not inside
        j = i

    return inside


def _ccw(a: Sequence[float], b: Sequence[float], c: Sequence[float]) -> float:
    return (c[1] - a[1]) * (b[0] - a[0]) - (b[1] - a[1]) * (c[0] - a[0])


def segments_intersect(
    p1: Sequence[float], p2: Sequence[float], q1: Sequence[float], q2: Sequence[float]
) -> bool:
    """
    Determines if line segment (p1->p2) intersects line segment (q1->q2).
    Coordinates are [lat, lon].
    """
    # Bounding box test first for fast rejection
    if (max(p1[0], p2[0]) < min(q1[0], q2[0]) or
        min(p1[0], p2[0]) > max(q1[0], q2[0]) or
        max(p1[1], p2[1]) < min(q1[1], q2[1]) or
        min(p1[1], p2[1]) > max(q1[1], q2[1])):
        return False

    ccw1 = _ccw(p1, q1, q2)
    ccw2 = _ccw(p2, q1, q2)
    ccw3 = _ccw(p1, p2, q1)
    ccw4 = _ccw(p1, p2, q2)

    return ((ccw1 * ccw2 <= 1e-9) and (ccw3 * ccw4 <= 1e-9))


def segment_intersects_polygon(
    p1: Sequence[float], p2: Sequence[float], polygon: Sequence[Sequence[float]]
) -> bool:
    """
    Returns True if segment (p1->p2) penetrates polygon, or either endpoint is inside.
    """
    if len(polygon) < 3:
        return False

    if point_in_polygon(p1[0], p1[1], polygon) or point_in_polygon(p2[0], p2[1], polygon):
        return True

    n = len(polygon)
    for i in range(n):
        q1 = polygon[i]
        q2 = polygon[(i + 1) % n]
        if segments_intersect(p1, p2, q1, q2):
            return True

    return False


def expand_polygon_buffer(
    polygon: Sequence[Sequence[float]], buffer_nm: float
) -> List[List[float]]:
    """
    Expands a polygon outward by a safety buffer distance in Nautical Miles.
    Generates an expanded polygon perimeter accounting for latitude spherical scaling.
    """
    if not polygon or buffer_nm <= 0.0:
        return [[float(p[0]), float(p[1])] for p in polygon]

    coords = [[float(p[0]), float(p[1])] for p in polygon]
    if len(coords) < 3:
        return coords

    # Remove repeated closing vertex if present
    if (abs(coords[0][0] - coords[-1][0]) < 1e-6 and
        abs(coords[0][1] - coords[-1][1]) < 1e-6 and len(coords) > 3):
        coords = coords[:-1]

    # Calculate centroid
    avg_lat = sum(p[0] for p in coords) / len(coords)
    avg_lon = sum(p[1] for p in coords) / len(coords)

    # Conversion factor from NM to degrees at avg_lat
    # 1 NM = 1/60 degree latitude
    deg_per_nm_lat = 1.0 / 60.0
    cos_lat = max(0.01, math.cos(math.radians(avg_lat)))
    deg_per_nm_lon = (1.0 / 60.0) / cos_lat

    buf_lat = buffer_nm * deg_per_nm_lat
    buf_lon = buffer_nm * deg_per_nm_lon

    # For each vertex, compute the outward vector from centroid or edge bisector
    expanded: List[List[float]] = []
    n = len(coords)

    for i in range(n):
        curr = coords[i]
        prev_pt = coords[(i - 1) % n]
        next_pt = coords[(i + 1) % n]

        # Vector from centroid to vertex
        d_lat = curr[0] - avg_lat
        d_lon = curr[1] - avg_lon
        dist = math.sqrt((d_lat / deg_per_nm_lat) ** 2 + (d_lon / deg_per_nm_lon) ** 2)

        if dist < 1e-4:
            # Degenerate point, expand radially in standard direction
            angle = 2.0 * math.pi * i / n
            u_lat = math.sin(angle) * buf_lat
            u_lon = math.cos(angle) * buf_lon
        else:
            scale = (dist + buffer_nm) / dist
            u_lat = d_lat * (scale - 1.0)
            u_lon = d_lon * (scale - 1.0)

        expanded.append([round(curr[0] + u_lat, 6), round(curr[1] + u_lon, 6)])

    # Close polygon
    if expanded and (expanded[0][0] != expanded[-1][0] or expanded[0][1] != expanded[-1][1]):
        expanded.append([expanded[0][0], expanded[0][1]])

    return expanded


def distance_point_to_segment_nm(
    p_lat: float, p_lon: float,
    a_lat: float, a_lon: float,
    b_lat: float, b_lon: float
) -> float:
    """
    Computes shortest distance from point P to line segment A-B in Nautical Miles.
    """
    dist_ab = haversine_distance_nm(a_lat, a_lon, b_lat, b_lon)
    if dist_ab < 1e-5:
        return haversine_distance_nm(p_lat, p_lon, a_lat, a_lon)

    # Project P onto AB using equirectangular projection centered at A
    avg_lat = math.radians((a_lat + b_lat) / 2.0)
    cos_lat = math.cos(avg_lat)

    # Scale coordinates to NM
    ax, ay = a_lon * 60.0 * cos_lat, a_lat * 60.0
    bx, by = b_lon * 60.0 * cos_lat, b_lat * 60.0
    px, py = p_lon * 60.0 * cos_lat, p_lat * 60.0

    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy

    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
    proj_x = ax + t * dx
    proj_y = ay + t * dy

    return math.sqrt((px - proj_x) ** 2 + (py - proj_y) ** 2)


def min_route_distance_to_polygon_nm(
    route_coords: Sequence[Sequence[float]], polygon: Sequence[Sequence[float]]
) -> float:
    """
    Calculates closest distance in Nautical Miles from an entire route to a polygon boundary.
    """
    if not route_coords or not polygon:
        return float("inf")

    min_dist = float("inf")
    poly_len = len(polygon)

    for i in range(len(route_coords) - 1):
        r1 = route_coords[i]
        r2 = route_coords[i + 1]

        for j in range(poly_len):
            p1 = polygon[j]
            p2 = polygon[(j + 1) % poly_len]

            # Sample distances
            d1 = distance_point_to_segment_nm(p1[0], p1[1], r1[0], r1[1], r2[0], r2[1])
            d2 = distance_point_to_segment_nm(r1[0], r1[1], p1[0], p1[1], p2[0], p2[1])
            min_dist = min(min_dist, d1, d2)

    return min_dist
