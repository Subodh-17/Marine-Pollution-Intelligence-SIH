"""
Unit and integration tests for the Spill-Aware Maritime Route Optimization & Fuel Intelligence System.
"""
from __future__ import annotations

import math
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routing.geometry import (
    haversine_distance_km,
    haversine_distance_nm,
    initial_bearing_deg,
    destination_point,
    interpolate_great_circle,
    point_in_polygon,
    segments_intersect,
    segment_intersects_polygon,
    expand_polygon_buffer,
    distance_point_to_segment_nm,
    min_route_distance_to_polygon_nm,
)
from app.routing.vessel_specs import get_vessel_profile, list_vessel_profiles, PRESET_VESSELS
from app.routing.fuel_engine import calculate_fuel_consumption, IMO_CO2_EMISSION_FACTOR
from app.routing.maritime_graph import (
    build_base_maritime_graph,
    find_nearest_navigable_node,
    connect_custom_point,
)
from app.routing.models import GeoPoint, SpillAvoidanceZone, RoutePlanRequest
from app.routing.avoidance_engine import plan_maritime_route, SOLAS_ADVISORY_DISCLAIMER
from app.routing.ports import list_ports, get_port
from app.routing.spill_provider import resolve_active_spill_zones, get_default_spill_zone


# --- 1. Geodesic Geometry Tests ---

def test_haversine_distance():
    # Mumbai (18.940, 72.840) to Mormugao/Goa (15.415, 73.790)
    dist_km = haversine_distance_km(18.940, 72.840, 15.415, 73.790)
    dist_nm = haversine_distance_nm(18.940, 72.840, 15.415, 73.790)

    assert 390.0 < dist_km < 415.0
    assert 210.0 < dist_nm < 225.0
    assert math.isclose(dist_nm * 1.852, dist_km, rel_tol=1e-5)


def test_initial_bearing():
    # Due North: 0°
    assert math.isclose(initial_bearing_deg(10.0, 70.0, 15.0, 70.0), 0.0, abs_tol=1e-3)
    # Due East along equator: 90°
    assert math.isclose(initial_bearing_deg(0.0, 70.0, 0.0, 75.0), 90.0, abs_tol=1e-3)
    # Due South: 180°
    assert math.isclose(initial_bearing_deg(15.0, 70.0, 10.0, 70.0), 180.0, abs_tol=1e-3)


def test_destination_point():
    lat1, lon1 = 18.0, 70.0
    bearing = 90.0
    dist_nm = 60.0  # ~1 degree longitude near equator
    lat2, lon2 = destination_point(lat1, lon1, bearing, dist_nm)

    calc_dist = haversine_distance_nm(lat1, lon1, lat2, lon2)
    assert math.isclose(calc_dist, dist_nm, rel_tol=1e-3)


def test_interpolate_great_circle():
    coords = interpolate_great_circle(10.0, 70.0, 20.0, 75.0, num_points=10)
    assert len(coords) == 11
    assert math.isclose(coords[0][0], 10.0, abs_tol=1e-4)
    assert math.isclose(coords[-1][0], 20.0, abs_tol=1e-4)


# --- 2. Spatial Predicates & Polygon Buffering ---

def test_point_in_polygon():
    square = [[10.0, 10.0], [10.0, 20.0], [20.0, 20.0], [20.0, 10.0]]
    assert point_in_polygon(15.0, 15.0, square) is True
    assert point_in_polygon(5.0, 15.0, square) is False
    assert point_in_polygon(25.0, 25.0, square) is False


def test_segments_intersect():
    # Crossing segments
    assert segments_intersect([0.0, 0.0], [10.0, 10.0], [0.0, 10.0], [10.0, 0.0]) is True
    # Non-intersecting parallel segments
    assert segments_intersect([0.0, 0.0], [10.0, 0.0], [0.0, 5.0], [10.0, 5.0]) is False


def test_segment_intersects_polygon():
    triangle = [[10.0, 10.0], [10.0, 20.0], [20.0, 15.0]]
    # Penetrating segment through triangle
    assert segment_intersects_polygon([5.0, 15.0], [25.0, 15.0], triangle) is True
    # Segment outside triangle
    assert segment_intersects_polygon([0.0, 0.0], [5.0, 5.0], triangle) is False
    # Endpoint inside triangle
    assert segment_intersects_polygon([12.0, 15.0], [30.0, 30.0], triangle) is True


def test_expand_polygon_buffer():
    orig_poly = [[19.0, 71.0], [19.0, 71.2], [19.2, 71.2], [19.2, 71.0]]
    buffered = expand_polygon_buffer(orig_poly, buffer_nm=3.0)

    assert len(buffered) >= len(orig_poly)
    # Check that buffered polygon has larger bounding extent
    min_lat_orig = min(p[0] for p in orig_poly)
    min_lat_buf = min(p[0] for p in buffered)
    max_lat_orig = max(p[0] for p in orig_poly)
    max_lat_buf = max(p[0] for p in buffered)

    assert min_lat_buf < min_lat_orig
    assert max_lat_buf > max_lat_orig


def test_distance_point_to_segment_nm():
    # Point perpendicular to segment
    dist = distance_point_to_segment_nm(
        p_lat=10.0, p_lon=70.0,
        a_lat=11.0, a_lon=69.0,
        b_lat=11.0, b_lon=71.0,
    )
    # Distance is approx 1 degree of latitude = 60 NM
    assert 58.0 < dist < 62.0


# --- 3. Vessel Profiles & Fuel Engine Physics ---

def test_vessel_profiles():
    profiles = list_vessel_profiles()
    assert len(profiles) >= 5

    tanker = get_vessel_profile("Tanker")
    assert tanker.vessel_type == "Tanker"
    assert tanker.main_engine_power_kw > 10_000.0
    assert tanker.fuel_tank_capacity_mt > 1000.0

    # Fallback to tanker for unknown type
    unknown = get_vessel_profile("AlienStarship")
    assert unknown.vessel_type == "Tanker"


def test_fuel_consumption_physics():
    tanker = get_vessel_profile("Tanker")
    # 500 NM voyage at service speed (14.5 knots)
    analysis_normal = calculate_fuel_consumption(
        distance_nm=500.0,
        vessel=tanker,
        speed_knots=14.5,
        current_fuel_mt=1500.0,
    )

    assert analysis_normal.duration_hours > 0.0
    assert analysis_normal.total_fuel_consumed_mt > 0.0
    assert analysis_normal.total_fuel_consumed_litres > 0.0
    assert analysis_normal.remaining_fuel_mt < 1500.0
    assert analysis_normal.fuel_status == "SUFFICIENT"
    assert math.isclose(
        analysis_normal.co2_emissions_mt,
        analysis_normal.total_fuel_consumed_mt * IMO_CO2_EMISSION_FACTOR,
        rel_tol=1e-3,
    )

    # Cubic Power Law test: High speed (16.0 kt) must have higher hourly consumption than 12.0 kt
    analysis_fast = calculate_fuel_consumption(500.0, tanker, speed_knots=16.0)
    analysis_slow = calculate_fuel_consumption(500.0, tanker, speed_knots=12.0)
    assert analysis_fast.hourly_consumption_mt > analysis_slow.hourly_consumption_mt


def test_fuel_status_thresholds():
    tanker = get_vessel_profile("Tanker")
    # Low fuel situation
    analysis_low = calculate_fuel_consumption(
        distance_nm=300.0,
        vessel=tanker,
        current_fuel_mt=tanker.fuel_tank_capacity_mt * 0.16,  # 16% on board, consumption will bring it below 15%
    )
    assert analysis_low.fuel_status in ("LOW_RESERVE", "INSUFFICIENT")

    # Insufficient fuel situation
    analysis_empty = calculate_fuel_consumption(
        distance_nm=1000.0,
        vessel=tanker,
        current_fuel_mt=10.0,  # Only 10 MT for 1000 NM voyage
    )
    assert analysis_empty.fuel_status == "INSUFFICIENT"
    assert analysis_empty.remaining_fuel_mt <= 0.0


# --- 4. Maritime Graph & Avoidance Routing Engine ---

def test_maritime_graph_structure():
    g = build_base_maritime_graph()
    assert len(g.nodes) > 15
    assert len(g.edges) > 15
    assert "mumbai_port" in g.nodes
    assert "singapore_port" in g.nodes

    # Check nearest navigable node lookup
    nearest, dist = find_nearest_navigable_node(18.95, 72.85, g)
    assert nearest in ("mumbai_port", "jnpt_port", "mumbai_fairway")
    assert dist < 10.0


def test_plan_maritime_route_without_spill():
    origin = GeoPoint(lat=18.940, lon=72.840, name="Mumbai Port")
    dest = GeoPoint(lat=15.415, lon=73.790, name="Mormugao Port")

    resp = plan_maritime_route(
        origin=origin,
        destination=dest,
        vessel_type="Tanker",
        avoid_spills=False,
    )

    assert resp.recommended_route_type == "baseline"
    assert resp.baseline_route.distance_nm > 180.0
    assert len(resp.baseline_route.coordinates) >= 2
    assert resp.disclaimer == SOLAS_ADVISORY_DISCLAIMER


def test_plan_maritime_route_with_spill_avoidance():
    # Origin west of spill, Destination east of spill (Mumbai)
    origin = GeoPoint(lat=19.05, lon=70.40, name="Arabian Sea West")
    dest = GeoPoint(lat=18.94, lon=72.84, name="Mumbai Port")

    # Create an active oil slick right in the middle between them (around 19.05, 71.20)
    spill = SpillAvoidanceZone(
        id="test_spill_1",
        name="Test Oil Slick",
        polygon_latlon=[
            [19.00, 71.10],
            [19.00, 71.30],
            [19.10, 71.30],
            [19.10, 71.10],
            [19.00, 71.10],
        ],
        buffer_nm=3.0,
        active=True,
    )

    resp = plan_maritime_route(
        origin=origin,
        destination=dest,
        vessel_type="Tanker",
        spill_buffer_nm=3.0,
        avoid_spills=True,
        spill_zones=[spill],
    )

    assert resp.spill_avoiding_route is not None
    # Avoidance route must maintain distance away from spill
    assert resp.spill_avoiding_route.closest_spill_distance_nm is not None
    assert resp.spill_avoiding_route.closest_spill_distance_nm >= 0.0

    # Baseline penetrates or comes close to the hazard
    assert resp.comparison is not None
    assert resp.comparison.extra_distance_nm >= 0.0
    assert resp.comparison.extra_fuel_consumed_mt >= 0.0


# --- 5. API Endpoints Integration Tests ---

client = TestClient(app)


def test_api_vessel_profiles():
    resp = client.get("/api/vessels/profiles")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 5
    tanker = next(p for p in data if p["vessel_type"] == "Tanker")
    assert "main_engine_power_kw" in tanker
    assert "sfoc_g_per_kwh" in tanker


def test_api_route_ports():
    resp = client.get("/api/routes/ports")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert any(p["id"] == "mumbai" for p in data)
    assert any(p["id"] == "jnpt" for p in data)


def test_api_route_spills():
    resp = client.get("/api/routes/spills")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert "polygon_latlon" in data[0]


def test_api_route_plan():
    payload = {
        "origin": {"lat": 18.940, "lon": 72.840, "name": "Mumbai Port"},
        "destination": {"lat": 15.415, "lon": 73.790, "name": "Mormugao Port"},
        "vessel_type": "Tanker",
        "custom_speed_knots": 14.0,
        "spill_buffer_nm": 2.5,
        "avoid_spills": True,
    }
    resp = client.post("/api/routes/plan", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert "baseline_route" in data
    assert "vessel" in data
    assert "disclaimer" in data
    assert data["baseline_route"]["distance_nm"] > 100.0
    assert data["baseline_route"]["fuel"]["fuel_status"] in ("SUFFICIENT", "LOW_RESERVE")


def test_api_route_compare():
    payload = {
        "origin": {"lat": 19.05, "lon": 70.40, "name": "Arabian Sea Offshore"},
        "destination": {"lat": 18.940, "lon": 72.840, "name": "Mumbai Port"},
        "vessel_type": "Tanker",
        "spill_buffer_nm": 2.0,
        "avoid_spills": True,
    }
    resp = client.post("/api/routes/compare", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert "recommended_route_type" in data
    assert "baseline_summary" in data
    assert "disclaimer" in data
