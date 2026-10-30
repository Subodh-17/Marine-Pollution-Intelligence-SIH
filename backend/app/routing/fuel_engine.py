"""
Transparent Specific Fuel Oil Consumption (SFOC) and hydrodynamic propulsion model.
Implements the Admiralty coefficient cubic power-speed relationship and IMO carbon metrics.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Dict, Any

from app.routing.vessel_specs import VesselProfile

# IMO GHG Study standard carbon emission factor: 3.1144 tonnes CO2 per tonne marine fuel
IMO_CO2_EMISSION_FACTOR = 3.1144
DEFAULT_SEA_MARGIN = 1.08  # 8% weather/wave hydrodynamic resistance allowance
DEFAULT_BUNKER_PRICE_USD_PER_MT = 650.0  # Representative global bunker fuel price


@dataclass
class FuelAnalysis:
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

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def calculate_fuel_consumption(
    distance_nm: float,
    vessel: VesselProfile,
    speed_knots: float | None = None,
    current_fuel_mt: float | None = None,
    sea_margin: float = DEFAULT_SEA_MARGIN,
    bunker_price_usd_per_mt: float = DEFAULT_BUNKER_PRICE_USD_PER_MT,
) -> FuelAnalysis:
    """
    Calculates detailed voyage fuel consumption, remaining endurance, reserve margins,
    financial bunker cost, and environmental CO2 emissions.
    """
    v_op = float(speed_knots) if (speed_knots and speed_knots > 0.0) else vessel.service_speed_knots
    v_op = max(1.0, min(v_op, vessel.max_speed_knots * 1.15))

    v_design = max(1.0, vessel.service_speed_knots)

    # Admiralty Law: Propulsion power scales cubically with operational speed
    speed_ratio = v_op / v_design
    cubic_factor = speed_ratio ** 3

    # Propulsion power bounded between 10% idling/maneuvering and 108% MCR
    propulsion_power_kw = vessel.main_engine_power_kw * cubic_factor
    propulsion_power_kw = max(0.10 * vessel.main_engine_power_kw, min(propulsion_power_kw, 1.08 * vessel.main_engine_power_kw))

    # Total engine output including auxiliary generators and sea margin
    total_power_kw = (propulsion_power_kw * sea_margin) + vessel.auxiliary_power_kw

    # SFOC model: Total power * SFOC (g/kWh) / 1,000,000 = Metric Tonnes / hour
    hourly_consumption_mt = (total_power_kw * vessel.sfoc_g_per_kwh) / 1_000_000.0

    # Voyage duration
    duration_hours = distance_nm / v_op if v_op > 0.0 else 0.0

    # Total fuel consumed for voyage
    fuel_consumed_mt = duration_hours * hourly_consumption_mt
    density = max(0.70, vessel.fuel_density_kg_per_l)
    fuel_consumed_litres = (fuel_consumed_mt * 1000.0) / density

    # Tank parameters
    tank_cap = vessel.fuel_tank_capacity_mt
    init_fuel = float(current_fuel_mt) if current_fuel_mt is not None else (0.85 * tank_cap)
    init_fuel = max(0.0, min(init_fuel, tank_cap * 1.05))

    remaining_fuel_mt = init_fuel - fuel_consumed_mt
    remaining_fuel_litres = (max(0.0, remaining_fuel_mt) * 1000.0) / density

    # Reserve limit
    reserve_limit_mt = tank_cap * (vessel.fuel_reserve_threshold_pct / 100.0)
    reserve_margin_pct = (max(0.0, remaining_fuel_mt) / tank_cap) * 100.0

    # Status determination
    if remaining_fuel_mt <= 0.0:
        fuel_status = "INSUFFICIENT"
    elif remaining_fuel_mt < reserve_limit_mt:
        fuel_status = "LOW_RESERVE"
    else:
        fuel_status = "SUFFICIENT"

    # Cruising endurance / range at current consumption
    if hourly_consumption_mt > 1e-6:
        usable_fuel = max(0.0, remaining_fuel_mt)
        remaining_range_nm = (usable_fuel / hourly_consumption_mt) * v_op
    else:
        remaining_range_nm = 0.0

    # CO2 emissions and bunker cost
    co2_mt = fuel_consumed_mt * IMO_CO2_EMISSION_FACTOR
    bunker_cost = fuel_consumed_mt * bunker_price_usd_per_mt

    return FuelAnalysis(
        distance_nm=round(distance_nm, 2),
        speed_knots=round(v_op, 2),
        duration_hours=round(duration_hours, 2),
        power_kw=round(total_power_kw, 1),
        hourly_consumption_mt=round(hourly_consumption_mt, 4),
        total_fuel_consumed_mt=round(fuel_consumed_mt, 3),
        total_fuel_consumed_litres=round(fuel_consumed_litres, 1),
        initial_fuel_mt=round(init_fuel, 2),
        remaining_fuel_mt=round(remaining_fuel_mt, 2),
        remaining_fuel_litres=round(remaining_fuel_litres, 1),
        remaining_range_nm=round(remaining_range_nm, 1),
        fuel_status=fuel_status,
        reserve_limit_mt=round(reserve_limit_mt, 2),
        fuel_reserve_margin_pct=round(reserve_margin_pct, 1),
        fuel_tank_capacity_mt=round(tank_cap, 2),
        co2_emissions_mt=round(co2_mt, 3),
        fuel_cost_usd=round(bunker_cost, 2),
    )
