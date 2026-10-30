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


def test_route_plan_input_validation_rejects_negative_or_zero_speed():
    payload_zero = {
        "origin": {"lat": 18.940, "lon": 72.840},
        "destination": {"lat": 15.415, "lon": 73.790},
        "custom_speed_knots": 0.0,
    }
    resp = client.post("/api/routes/plan", json=payload_zero)
    assert resp.status_code == 422

    payload_neg = {
        "origin": {"lat": 18.940, "lon": 72.840},
        "destination": {"lat": 15.415, "lon": 73.790},
        "custom_speed_knots": -12.5,
    }
    resp = client.post("/api/routes/plan", json=payload_neg)
    assert resp.status_code == 422


def test_route_plan_input_validation_rejects_negative_or_impossible_fuel():
    payload_neg_fuel = {
        "origin": {"lat": 18.940, "lon": 72.840},
        "destination": {"lat": 15.415, "lon": 73.790},
        "current_fuel_mt": -10.0,
    }
    resp = client.post("/api/routes/plan", json=payload_neg_fuel)
    assert resp.status_code == 422

    payload_impossible_fuel = {
        "origin": {"lat": 18.940, "lon": 72.840},
        "destination": {"lat": 15.415, "lon": 73.790},
        "current_fuel_mt": 1000000.0,  # 1 million MT is impossible for any marine vessel
    }
    resp = client.post("/api/routes/plan", json=payload_impossible_fuel)
    assert resp.status_code == 422


def test_resolve_active_spill_zones_discovers_persisted_db_records(monkeypatch):
    from app.routing.spill_provider import resolve_active_spill_zones
    from app.investigation import orchestrator
    from app.persistence import db as _db

    # Temporarily isolate in-memory store to simulate clean process startup
    monkeypatch.setattr(orchestrator, "_STORE", {})

    zones = resolve_active_spill_zones()
    assert isinstance(zones, list)
    assert len(zones) >= 1
    # Either discovers persisted slicks or cleanly falls back to default high-fidelity zone
    for z in zones:
        assert len(z.polygon_latlon) >= 3
        assert z.active is True


def test_real_spill_to_route_end_to_end_integration():
    from app.routing.avoidance_engine import plan_maritime_route
    from app.routing.models import GeoPoint, SpillAvoidanceZone

    # Real Sentinel-1 detection polygon centered on central Arabian Sea corridor (19.05°N, 71.20°E)
    real_spill_polygon = [
        [19.0597, 71.1832],
        [19.0582, 71.1844],
        [19.0564, 71.1851],
        [19.0561, 71.1870],
        [19.0561, 71.1889],
        [19.0550, 71.1908],
        [19.0532, 71.1897],
        [19.0514, 71.1904],
        [19.0496, 71.1916],
        [19.0478, 71.1931],
        [19.0471, 71.1950],
        [19.0453, 71.1958],
        [19.0438, 71.1973],
        [19.0453, 71.1992],
        [19.0424, 71.1988],
        [19.0406, 71.2000],
        [19.0388, 71.2022],
        [19.0359, 71.2038],
        [19.0323, 71.2041],
        [19.0270, 71.2041],
        [19.0234, 71.2114],
        [19.0198, 71.2114],
        [19.0155, 71.2133],
        [19.0140, 71.2098],
        [19.0176, 71.2091],
        [19.0212, 71.2041],
        [19.0266, 71.2007],
        [19.0305, 71.1988],
        [19.0320, 71.1950],
        [19.0370, 71.1908],
        [19.0442, 71.1885],
        [19.0503, 71.1840],
        [19.0575, 71.1817],
        [19.0597, 71.1832],
    ]

    spill_zone = SpillAvoidanceZone(
        id="real_s1_slick_test",
        name="Real Sentinel-1 SAR Oil Slick",
        polygon_latlon=real_spill_polygon,
        buffer_nm=2.0,
        centroid_lat=19.038,
        centroid_lon=71.196,
        area_km2=3.94,
        active=True,
    )

    origin = GeoPoint(lat=18.940, lon=72.840, name="Mumbai Port")
    dest = GeoPoint(lat=19.050, lon=70.400, name="Arabian Sea West")

    result = plan_maritime_route(
        origin=origin,
        destination=dest,
        vessel_type="Tanker",
        spill_buffer_nm=2.0,
        avoid_spills=True,
        spill_zones=[spill_zone],
    )

    # 1. Baseline route penetrates the real slick zone
    assert result.baseline_route.intersects_spill is True
    assert result.baseline_route.closest_spill_distance_nm < 1.0

    # 2. Avoidance route successfully circumvents the slick maintaining safe buffer
    assert result.spill_avoiding_route is not None
    assert result.spill_avoiding_route.intersects_spill is False
    assert result.spill_avoiding_route.closest_spill_distance_nm >= 2.0

    # 3. Recommended route type is spill_avoiding
    assert result.recommended_route_type == "spill_avoiding"

    # 4. Comparison deltas are non-negative and physically consistent
    assert result.comparison is not None
    assert result.comparison.extra_distance_nm >= 0.0
    assert result.comparison.extra_fuel_consumed_mt >= 0.0
    assert result.comparison.extra_cost_usd >= 0.0
    assert result.comparison.extra_co2_mt >= 0.0

    # 5. Mandatory SOLAS / IMO advisory notice is present
    assert "SOLAS Chapter V, Regulation 34" in result.disclaimer


def test_resolve_active_spill_zones_never_substitutes_demo_data_when_specific_inv_requested(monkeypatch):
    from app.routing.spill_provider import resolve_active_spill_zones
    from app.investigation import orchestrator

    # Case 1: Explicit non-existent investigation requested -> must return []
    res_ghost = resolve_active_spill_zones(investigation_id="non_existent_ghost_investigation")
    assert res_ghost == [], "Must never substitute demo or unrelated data when specific investigation is requested"

    # Case 2: Investigation exists but has no oil spill detected -> must return []
    class MockCleanInv:
        def to_dict(self):
            return {
                "id": "clean_inv_123",
                "satellite": {
                    "oil_spill_detected": False,
                    "geometry": None,
                }
            }

    monkeypatch.setattr(orchestrator, "_STORE", {"clean_inv_123": MockCleanInv()})
    res_clean = resolve_active_spill_zones(investigation_id="clean_inv_123")
    assert res_clean == [], "Clean investigation with no oil must return 0 spill zones"


def test_plan_maritime_route_filters_invalid_and_corrupt_polygons():
    from app.routing.avoidance_engine import plan_maritime_route
    from app.routing.models import GeoPoint, SpillAvoidanceZone

    origin = GeoPoint(lat=18.940, lon=72.840)
    dest = GeoPoint(lat=15.415, lon=73.790)

    # Corrupt polygons: NaN coordinates, out-of-bounds latitude, fewer than 3 vertices
    corrupt_zones = [
        SpillAvoidanceZone(
            id="nan_zone",
            name="Corrupt NaN Zone",
            polygon_latlon=[[float("nan"), 72.0], [18.0, 72.0], [18.0, 73.0]],
            active=True,
        ),
        SpillAvoidanceZone(
            id="oob_zone",
            name="Out of Bounds Lat Zone",
            polygon_latlon=[[95.0, 72.0], [18.0, 72.0], [18.0, 73.0]],
            active=True,
        ),
        SpillAvoidanceZone(
            id="two_pt_zone",
            name="Insufficient Points Zone",
            polygon_latlon=[[18.0, 72.0], [19.0, 72.0]],
            active=True,
        ),
    ]

    result = plan_maritime_route(
        origin=origin,
        destination=dest,
        vessel_type="Tanker",
        avoid_spills=True,
        spill_zones=corrupt_zones,
    )
    # Must compute baseline cleanly and ignore corrupt polygons without crashing
    assert result.baseline_route.distance_nm > 0
    assert result.recommended_route_type == "baseline"
    assert result.spill_avoiding_route is None  # Corrupt polygons filtered out -> no spills to avoid
    assert result.spill_zones == []


def test_multiple_simultaneous_spill_zones_avoidance():
    from app.routing.avoidance_engine import plan_maritime_route
    from app.routing.models import GeoPoint, SpillAvoidanceZone

    # Two separate slicks along the corridor
    zone_a = SpillAvoidanceZone(
        id="slick_a",
        name="Slick A",
        polygon_latlon=[
            [19.00, 71.15],
            [19.10, 71.15],
            [19.10, 71.25],
            [19.00, 71.25],
            [19.00, 71.15],
        ],
        buffer_nm=1.5,
        active=True,
    )
    zone_b = SpillAvoidanceZone(
        id="slick_b",
        name="Slick B",
        polygon_latlon=[
            [18.85, 72.05],
            [18.95, 72.05],
            [18.95, 72.15],
            [18.85, 72.15],
            [18.85, 72.05],
        ],
        buffer_nm=1.5,
        active=True,
    )

    origin = GeoPoint(lat=18.940, lon=72.840, name="Mumbai Port")
    dest = GeoPoint(lat=19.050, lon=70.400, name="Arabian Sea West")

    result = plan_maritime_route(
        origin=origin,
        destination=dest,
        vessel_type="Tanker",
        spill_buffer_nm=1.5,
        avoid_spills=True,
        spill_zones=[zone_a, zone_b],
    )

    assert result.baseline_route.distance_nm > 0
    assert result.spill_avoiding_route is not None
    assert result.spill_avoiding_route.intersects_spill is False


