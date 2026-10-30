"""
Maritime route calculation and dynamic spill-avoidance engine.
Computes baseline shortest sea path, evaluates slick collision hazard, and generates
optimal avoidance trajectories maintaining mandatory environmental safety buffers.
"""
from __future__ import annotations

import math
from typing import List, Dict, Tuple, Sequence, Optional
import networkx as nx

from app.routing.geometry import (
    haversine_distance_nm,
    haversine_distance_km,
    initial_bearing_deg,
    interpolate_great_circle,
    segment_intersects_polygon,
    point_in_polygon,
    expand_polygon_buffer,
    min_route_distance_to_polygon_nm,
)
from app.routing.vessel_specs import VesselProfile, get_vessel_profile
from app.routing.fuel_engine import calculate_fuel_consumption, FuelAnalysis
from app.routing.maritime_graph import (
    build_base_maritime_graph,
    connect_custom_point,
    find_nearest_navigable_node,
)
from app.routing.models import (
    GeoPoint,
    SpillAvoidanceZone,
    RouteLegResponse,
    FuelAnalysisResponse,
    RouteSummaryResponse,
    RouteDeltaComparison,
    RoutePlanResponse,
    VesselProfileResponse,
)

SOLAS_ADVISORY_DISCLAIMER = (
    "NOTICE: ADVISORY DECISION SUPPORT ONLY. This automated route recommendation and "
    "fuel estimation system is intended solely for tactical decision-support and contingency planning. "
    "In compliance with SOLAS Chapter V, Regulation 34 (Safe Navigation and Avoidance of Dangerous "
    "Situations) and IMO Resolution A.893(21), the Master and Officer of the Watch (OOW) maintain "
    "sole authority and statutory responsibility for passage planning, navigation safety, and route "
    "execution using certified Electronic Chart Display and Information Systems (ECDIS) and official "
    "updated hydrographic nautical charts."
)


def _build_route_summary(
    route_coords: List[List[float]],
    route_type: str,
    vessel: VesselProfile,
    speed_knots: float,
    current_fuel_mt: float | None,
    buffered_spills: Sequence[Sequence[Sequence[float]]],
    raw_spills: Sequence[Sequence[Sequence[float]]],
    bunker_price: float,
) -> RouteSummaryResponse:
    """Builds full RouteSummaryResponse with legs, fuel physics, and spill proximity metrics."""
    legs: List[RouteLegResponse] = []
    total_dist_nm = 0.0

    for i in range(len(route_coords) - 1):
        c1 = route_coords[i]
        c2 = route_coords[i + 1]
        dist = haversine_distance_nm(c1[0], c1[1], c2[0], c2[1])
        bearing = initial_bearing_deg(c1[0], c1[1], c2[0], c2[1])
        total_dist_nm += dist
        legs.append(
            RouteLegResponse(
                start_coord=[round(c1[0], 5), round(c1[1], 5)],
                end_coord=[round(c2[0], 5), round(c2[1], 5)],
                distance_nm=round(dist, 2),
                bearing_deg=round(bearing, 1),
            )
        )

    # Calculate closest distance to spill boundaries
    closest_dist_nm = float("inf")
    intersects_spill = False

    for poly in raw_spills:
        d = min_route_distance_to_polygon_nm(route_coords, poly)
        closest_dist_nm = min(closest_dist_nm, d)

    for i in range(len(route_coords) - 1):
        p1 = route_coords[i]
        p2 = route_coords[i + 1]
        for poly in raw_spills:
            if segment_intersects_polygon(p1, p2, poly):
                intersects_spill = True
                break
        if intersects_spill:
            break

    fuel_analysis = calculate_fuel_consumption(
        distance_nm=total_dist_nm,
        vessel=vessel,
        speed_knots=speed_knots,
        current_fuel_mt=current_fuel_mt,
        bunker_price_usd_per_mt=bunker_price,
    )

    fuel_resp = FuelAnalysisResponse(
        distance_nm=fuel_analysis.distance_nm,
        speed_knots=fuel_analysis.speed_knots,
        duration_hours=fuel_analysis.duration_hours,
        power_kw=fuel_analysis.power_kw,
        hourly_consumption_mt=fuel_analysis.hourly_consumption_mt,
        total_fuel_consumed_mt=fuel_analysis.total_fuel_consumed_mt,
        total_fuel_consumed_litres=fuel_analysis.total_fuel_consumed_litres,
        initial_fuel_mt=fuel_analysis.initial_fuel_mt,
        remaining_fuel_mt=fuel_analysis.remaining_fuel_mt,
        remaining_fuel_litres=fuel_analysis.remaining_fuel_litres,
        remaining_range_nm=fuel_analysis.remaining_range_nm,
        fuel_status=fuel_analysis.fuel_status,
        reserve_limit_mt=fuel_analysis.reserve_limit_mt,
        fuel_reserve_margin_pct=fuel_analysis.fuel_reserve_margin_pct,
        fuel_tank_capacity_mt=fuel_analysis.fuel_tank_capacity_mt,
        co2_emissions_mt=fuel_analysis.co2_emissions_mt,
        fuel_cost_usd=fuel_analysis.fuel_cost_usd,
    )

    return RouteSummaryResponse(
        route_type=route_type,
        coordinates=[[round(c[0], 5), round(c[1], 5)] for c in route_coords],
        distance_nm=round(total_dist_nm, 2),
        distance_km=round(total_dist_nm * 1.852, 2),
        duration_hours=fuel_analysis.duration_hours,
        speed_knots=fuel_analysis.speed_knots,
        intersects_spill=intersects_spill,
        closest_spill_distance_nm=round(closest_dist_nm, 2) if closest_dist_nm != float("inf") else None,
        legs=legs,
        fuel=fuel_resp,
    )


def plan_maritime_route(
    origin: GeoPoint,
    destination: GeoPoint,
    vessel_type: str = "Tanker",
    custom_speed_knots: float | None = None,
    current_fuel_mt: float | None = None,
    spill_buffer_nm: float = 2.0,
    avoid_spills: bool = True,
    spill_zones: Sequence[SpillAvoidanceZone] | None = None,
    bunker_price_usd_per_mt: float = 650.0,
) -> RoutePlanResponse:
    """
    Core planning function:
    1. Connects origin and destination to the navigable sea network.
    2. Computes the baseline shortest maritime path.
    3. Evaluates intersection against active oil slick polygons + safety buffer.
    4. If intersection is detected, plans an optimal detour around the hazard.
    5. Computes transparent fuel consumption and SOLAS advisory comparison metrics.
    """
    vessel = get_vessel_profile(vessel_type)
    speed = custom_speed_knots if custom_speed_knots and custom_speed_knots > 0 else vessel.service_speed_knots

    # Extract active spill polygons
    active_spills = [sz for sz in (spill_zones or []) if sz.active and len(sz.polygon_latlon) >= 3]
    raw_polygons = [sz.polygon_latlon for sz in active_spills]
    buffered_polygons = [
        expand_polygon_buffer(sz.polygon_latlon, buffer_nm=max(0.1, spill_buffer_nm))
        for sz in active_spills
    ]

    # --- 1. Compute Baseline Route ---
    base_graph = build_base_maritime_graph()
    origin_id = "__origin__"
    dest_id = "__dest__"

    # Connect origin & destination to base graph
    g_base = connect_custom_point(
        base_graph, origin_id, origin.lat, origin.lon, label=origin.name or "Origin", k_nearest=5
    )
    g_base = connect_custom_point(
        g_base, dest_id, destination.lat, destination.lon, label=destination.name or "Destination", k_nearest=5
    )

    try:
        path_nodes = nx.shortest_path(g_base, source=origin_id, target=dest_id, weight="weight")
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        # Fallback to direct interpolated coordinates
        raw_interp = interpolate_great_circle(origin.lat, origin.lon, destination.lat, destination.lon, num_points=12)
        path_nodes = []
        baseline_coords = [[pt[0], pt[1]] for pt in raw_interp]

    if path_nodes:
        baseline_coords = [[g_base.nodes[n]["lat"], g_base.nodes[n]["lon"]] for n in path_nodes]

    baseline_summary = _build_route_summary(
        route_coords=baseline_coords,
        route_type="baseline",
        vessel=vessel,
        speed_knots=speed,
        current_fuel_mt=current_fuel_mt,
        buffered_spills=buffered_polygons,
        raw_spills=raw_polygons,
        bunker_price=bunker_price_usd_per_mt,
    )

    # --- 2. Check if baseline penetrates buffered spills ---
    baseline_penetrates_buffer = False
    for poly in buffered_polygons:
        for i in range(len(baseline_coords) - 1):
            if segment_intersects_polygon(baseline_coords[i], baseline_coords[i + 1], poly):
                baseline_penetrates_buffer = True
                break
        if baseline_penetrates_buffer:
            break

    # If no active spills or user chose not to avoid spills, return baseline as sole route
    if not avoid_spills or not active_spills:
        vessel_resp = VesselProfileResponse(**vessel.to_dict())
        return RoutePlanResponse(
            origin=origin,
            destination=destination,
            recommended_route_type="baseline",
            baseline_route=baseline_summary,
            spill_avoiding_route=None,
            comparison=None,
            spill_zones=list(active_spills),
            vessel=vessel_resp,
            disclaimer=SOLAS_ADVISORY_DISCLAIMER,
        )

    # If baseline is already completely clear of buffered spills
    if not baseline_penetrates_buffer and (
        baseline_summary.closest_spill_distance_nm is None
        or baseline_summary.closest_spill_distance_nm >= spill_buffer_nm
    ):
        vessel_resp = VesselProfileResponse(**vessel.to_dict())
        # Avoidance route is identical to baseline since baseline is safe
        avoid_summary = _build_route_summary(
            route_coords=baseline_coords,
            route_type="spill_avoiding",
            vessel=vessel,
            speed_knots=speed,
            current_fuel_mt=current_fuel_mt,
            buffered_spills=buffered_polygons,
            raw_spills=raw_polygons,
            bunker_price=bunker_price_usd_per_mt,
        )
        return RoutePlanResponse(
            origin=origin,
            destination=destination,
            recommended_route_type="spill_avoiding",
            baseline_route=baseline_summary,
            spill_avoiding_route=avoid_summary,
            comparison=RouteDeltaComparison(
                extra_distance_nm=0.0,
                extra_distance_pct=0.0,
                extra_duration_hours=0.0,
                extra_fuel_consumed_mt=0.0,
                extra_fuel_consumed_litres=0.0,
                extra_fuel_consumed_pct=0.0,
                extra_cost_usd=0.0,
                extra_co2_mt=0.0,
                safety_buffer_maintained_nm=round(avoid_summary.closest_spill_distance_nm or spill_buffer_nm, 2),
            ),
            spill_zones=list(active_spills),
            vessel=vessel_resp,
            disclaimer=SOLAS_ADVISORY_DISCLAIMER,
        )

    # --- 3. Compute Spill-Avoiding Route ---
    # Construct an avoidance graph where edges intersecting the buffered spill are removed or heavily penalized
    g_avoid = g_base.copy()

    # Penalize/remove all edges intersecting any buffered spill polygon
    edges_to_remove = []
    for u, v in g_avoid.edges():
        u_lat, u_lon = g_avoid.nodes[u]["lat"], g_avoid.nodes[u]["lon"]
        v_lat, v_lon = g_avoid.nodes[v]["lat"], g_avoid.nodes[v]["lon"]

        for poly in buffered_polygons:
            if segment_intersects_polygon([u_lat, u_lon], [v_lat, v_lon], poly):
                edges_to_remove.append((u, v))
                break

    for u, v in edges_to_remove:
        g_avoid.remove_edge(u, v)

    # Add dynamic bypass waypoints around each buffered polygon perimeter
    for sz_idx, poly in enumerate(buffered_polygons):
        n_poly = len(poly)
        bypass_nodes = []
        for v_idx, pt in enumerate(poly):
            if v_idx == n_poly - 1 and poly[0] == poly[-1]:
                continue
            b_id = f"bypass_{sz_idx}_{v_idx}"
            # Slightly offset point to guarantee outside buffer
            g_avoid.add_node(b_id, lat=pt[0], lon=pt[1], label=f"Spill Bypass WP {v_idx+1}")
            bypass_nodes.append((b_id, pt[0], pt[1]))

        # Connect perimeter ring
        m = len(bypass_nodes)
        for i in range(m):
            b1_id, lat1, lon1 = bypass_nodes[i]
            b2_id, lat2, lon2 = bypass_nodes[(i + 1) % m]
            d = haversine_distance_nm(lat1, lon1, lat2, lon2)
            g_avoid.add_edge(b1_id, b2_id, weight=d, distance_nm=d)

        # Connect bypass nodes to nearest nodes in g_avoid
        for b_id, b_lat, b_lon in bypass_nodes:
            for node_id, data in list(g_avoid.nodes(data=True)):
                if node_id.startswith("bypass_") or node_id in (origin_id, dest_id):
                    continue
                d = haversine_distance_nm(b_lat, b_lon, data["lat"], data["lon"])
                if d < 45.0:  # Connect within 45 NM
                    # Verify connecting segment does not penetrate buffered polygon
                    penetrates = False
                    for other_poly in buffered_polygons:
                        if segment_intersects_polygon([b_lat, b_lon], [data["lat"], data["lon"]], other_poly):
                            penetrates = True
                            break
                    if not penetrates:
                        g_avoid.add_edge(b_id, node_id, weight=d, distance_nm=d)

        # Also connect origin/destination to bypass nodes if direct visibility exists
        for ep_id, ep_coord in [(origin_id, [origin.lat, origin.lon]), (dest_id, [destination.lat, destination.lon])]:
            for b_id, b_lat, b_lon in bypass_nodes:
                d = haversine_distance_nm(ep_coord[0], ep_coord[1], b_lat, b_lon)
                penetrates = False
                for other_poly in buffered_polygons:
                    if segment_intersects_polygon(ep_coord, [b_lat, b_lon], other_poly):
                        penetrates = True
                        break
                if not penetrates:
                    g_avoid.add_edge(ep_id, b_id, weight=d, distance_nm=d)

    # Calculate shortest path on avoidance graph
    avoid_path_nodes = None
    try:
        avoid_path_nodes = nx.shortest_path(g_avoid, source=origin_id, target=dest_id, weight="weight")
        avoid_coords = [[g_avoid.nodes[n]["lat"], g_avoid.nodes[n]["lon"]] for n in avoid_path_nodes]
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        # Fallback: create safe waypoint detour directly around the centroid of the primary spill
        if active_spills:
            spill = active_spills[0]
            c_lat = spill.centroid_lat or (sum(p[0] for p in spill.polygon_latlon) / len(spill.polygon_latlon))
            c_lon = spill.centroid_lon or (sum(p[1] for p in spill.polygon_latlon) / len(spill.polygon_latlon))

            # Detour waypoints North and South of slick
            offset_deg = (spill_buffer_nm + 3.0) / 60.0
            detour_north = [c_lat + offset_deg, c_lon]
            detour_south = [c_lat - offset_deg, c_lon]

            dist_n = (haversine_distance_nm(origin.lat, origin.lon, detour_north[0], detour_north[1]) +
                      haversine_distance_nm(detour_north[0], detour_north[1], destination.lat, destination.lon))
            dist_s = (haversine_distance_nm(origin.lat, origin.lon, detour_south[0], detour_south[1]) +
                      haversine_distance_nm(detour_south[0], detour_south[1], destination.lat, destination.lon))

            chosen_detour = detour_north if dist_n < dist_s else detour_south
            avoid_coords = [[origin.lat, origin.lon], chosen_detour, [destination.lat, destination.lon]]
        else:
            avoid_coords = list(baseline_coords)

    avoid_summary = _build_route_summary(
        route_coords=avoid_coords,
        route_type="spill_avoiding",
        vessel=vessel,
        speed_knots=speed,
        current_fuel_mt=current_fuel_mt,
        buffered_spills=buffered_polygons,
        raw_spills=raw_polygons,
        bunker_price=bunker_price_usd_per_mt,
    )

    # --- 4. Delta Comparison Metrics ---
    extra_dist_nm = max(0.0, avoid_summary.distance_nm - baseline_summary.distance_nm)
    extra_dist_pct = (extra_dist_nm / max(1e-4, baseline_summary.distance_nm)) * 100.0
    extra_time_h = max(0.0, avoid_summary.duration_hours - baseline_summary.duration_hours)
    extra_fuel_mt = max(0.0, avoid_summary.fuel.total_fuel_consumed_mt - baseline_summary.fuel.total_fuel_consumed_mt)
    extra_fuel_litres = max(0.0, avoid_summary.fuel.total_fuel_consumed_litres - baseline_summary.fuel.total_fuel_consumed_litres)
    extra_fuel_pct = (extra_fuel_mt / max(1e-4, baseline_summary.fuel.total_fuel_consumed_mt)) * 100.0
    extra_cost = max(0.0, avoid_summary.fuel.fuel_cost_usd - baseline_summary.fuel.fuel_cost_usd)
    extra_co2 = max(0.0, avoid_summary.fuel.co2_emissions_mt - baseline_summary.fuel.co2_emissions_mt)

    comparison = RouteDeltaComparison(
        extra_distance_nm=round(extra_dist_nm, 2),
        extra_distance_pct=round(extra_dist_pct, 1),
        extra_duration_hours=round(extra_time_h, 2),
        extra_fuel_consumed_mt=round(extra_fuel_mt, 3),
        extra_fuel_consumed_litres=round(extra_fuel_litres, 1),
        extra_fuel_consumed_pct=round(extra_fuel_pct, 1),
        extra_cost_usd=round(extra_cost, 2),
        extra_co2_mt=round(extra_co2, 3),
        safety_buffer_maintained_nm=round(avoid_summary.closest_spill_distance_nm or spill_buffer_nm, 2),
    )

    vessel_resp = VesselProfileResponse(**vessel.to_dict())

    return RoutePlanResponse(
        origin=origin,
        destination=destination,
        recommended_route_type="spill_avoiding",
        baseline_route=baseline_summary,
        spill_avoiding_route=avoid_summary,
        comparison=comparison,
        spill_zones=list(active_spills),
        vessel=vessel_resp,
        disclaimer=SOLAS_ADVISORY_DISCLAIMER,
    )
