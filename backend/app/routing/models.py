"""
Pydantic schemas for the Maritime Route Optimization & Fuel Intelligence API.
"""
from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field


class GeoPoint(BaseModel):
    lat: float = Field(..., ge=-90.0, le=90.0, description="Latitude in decimal degrees")
    lon: float = Field(..., ge=-180.0, le=180.0, description="Longitude in decimal degrees")
    name: Optional[str] = Field(None, description="Optional port or waypoint label")


class VesselProfileResponse(BaseModel):
    vessel_type: str
    name: str
    length_m: float
    beam_m: float
    draft_m: float
    displacement_dwt: float
    service_speed_knots: float
    max_speed_knots: float
    main_engine_power_kw: float
    auxiliary_power_kw: float
    sfoc_g_per_kwh: float
    fuel_tank_capacity_mt: float
    fuel_density_kg_per_l: float
    fuel_reserve_threshold_pct: float


class SpillAvoidanceZone(BaseModel):
    id: str
    name: str
    polygon_latlon: List[List[float]]
    buffer_nm: float = 2.0
    centroid_lat: Optional[float] = None
    centroid_lon: Optional[float] = None
    area_km2: Optional[float] = None
    active: bool = True


class RouteLegResponse(BaseModel):
    start_coord: List[float]
    end_coord: List[float]
    distance_nm: float
    bearing_deg: float
    waypoint_name: Optional[str] = None


class FuelAnalysisResponse(BaseModel):
    distance_nm: float
    speed_knots: float
    duration_hours: float
    power_kw: float
    hourly_consumption_mt: float
    total_fuel_consumed_mt: float
    total_fuel_consumed_litres: float
    initial_fuel_mt: float
    remaining_fuel_mt: float
    remaining_fuel_litres: float
    remaining_range_nm: float
    fuel_status: str  # "SUFFICIENT" | "LOW_RESERVE" | "INSUFFICIENT"
    reserve_limit_mt: float
    fuel_reserve_margin_pct: float
    fuel_tank_capacity_mt: float
    co2_emissions_mt: float
    fuel_cost_usd: float


class RouteSummaryResponse(BaseModel):
    route_type: str  # "baseline" | "spill_avoiding"
    coordinates: List[List[float]]
    distance_nm: float
    distance_km: float
    duration_hours: float
    speed_knots: float
    intersects_spill: bool
    closest_spill_distance_nm: Optional[float] = None
    legs: List[RouteLegResponse]
    fuel: FuelAnalysisResponse


class RouteDeltaComparison(BaseModel):
    extra_distance_nm: float
    extra_distance_pct: float
    extra_duration_hours: float
    extra_fuel_consumed_mt: float
    extra_fuel_consumed_litres: float
    extra_fuel_consumed_pct: float
    extra_cost_usd: float
    extra_co2_mt: float
    safety_buffer_maintained_nm: float


class RoutePlanRequest(BaseModel):
    origin: GeoPoint
    destination: GeoPoint
    vessel_type: str = "Tanker"
    custom_speed_knots: Optional[float] = Field(None, gt=0.0, le=45.0, description="Optional custom operational speed in knots (must be positive)")
    current_fuel_mt: Optional[float] = Field(None, ge=0.0, le=50000.0, description="Initial fuel onboard in Metric Tonnes")
    spill_buffer_nm: float = Field(2.0, ge=0.1, le=20.0, description="Safety buffer in Nautical Miles")
    avoid_spills: bool = True
    active_spill_ids: Optional[List[str]] = None
    custom_spill_polygons: Optional[List[List[List[float]]]] = None
    bunker_price_usd_per_mt: float = Field(650.0, ge=10.0, le=5000.0, description="Bunker fuel cost in USD per MT")


class RoutePlanResponse(BaseModel):
    origin: GeoPoint
    destination: GeoPoint
    recommended_route_type: str
    baseline_route: RouteSummaryResponse
    spill_avoiding_route: Optional[RouteSummaryResponse] = None
    comparison: Optional[RouteDeltaComparison] = None
    spill_zones: List[SpillAvoidanceZone]
    vessel: VesselProfileResponse
    disclaimer: str
