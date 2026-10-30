"""
Maritime vessel performance and hydrodynamic specifications.
Provides baseline engineering constants for fuel and power calculations across vessel classes.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, Any


@dataclass
class VesselProfile:
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
    sfoc_g_per_kwh: float  # Specific Fuel Oil Consumption (g / kWh)
    fuel_tank_capacity_mt: float  # Metric Tonnes
    fuel_density_kg_per_l: float = 0.85  # MGO: ~0.85 kg/L, VLSFO: ~0.95 kg/L
    fuel_reserve_threshold_pct: float = 15.0  # Mandatory safety reserve buffer (15%)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


PRESET_VESSELS: Dict[str, VesselProfile] = {
    "Tanker": VesselProfile(
        vessel_type="Tanker",
        name="Aframax Crude / Products Tanker",
        length_m=245.0,
        beam_m=42.0,
        draft_m=14.5,
        displacement_dwt=115_000.0,
        service_speed_knots=14.5,
        max_speed_knots=16.0,
        main_engine_power_kw=13_500.0,
        auxiliary_power_kw=1_200.0,
        sfoc_g_per_kwh=172.0,
        fuel_tank_capacity_mt=2_800.0,
        fuel_density_kg_per_l=0.86,
        fuel_reserve_threshold_pct=15.0,
    ),
    "Container Ship": VesselProfile(
        vessel_type="Container Ship",
        name="Post-Panamax 8,500 TEU Container Ship",
        length_m=335.0,
        beam_m=42.8,
        draft_m=14.0,
        displacement_dwt=100_000.0,
        service_speed_knots=19.5,
        max_speed_knots=23.0,
        main_engine_power_kw=45_000.0,
        auxiliary_power_kw=3_500.0,
        sfoc_g_per_kwh=168.0,
        fuel_tank_capacity_mt=5_500.0,
        fuel_density_kg_per_l=0.86,
        fuel_reserve_threshold_pct=15.0,
    ),
    "Bulk Carrier": VesselProfile(
        vessel_type="Bulk Carrier",
        name="Capesize Bulk Carrier",
        length_m=292.0,
        beam_m=45.0,
        draft_m=18.0,
        displacement_dwt=180_000.0,
        service_speed_knots=13.0,
        max_speed_knots=15.0,
        main_engine_power_kw=16_000.0,
        auxiliary_power_kw=1_000.0,
        sfoc_g_per_kwh=170.0,
        fuel_tank_capacity_mt=3_200.0,
        fuel_density_kg_per_l=0.86,
        fuel_reserve_threshold_pct=15.0,
    ),
    "Offshore Supply Vessel": VesselProfile(
        vessel_type="Offshore Supply Vessel",
        name="AHTS / Platform Supply Vessel (PSV)",
        length_m=75.0,
        beam_m=18.0,
        draft_m=6.0,
        displacement_dwt=3_500.0,
        service_speed_knots=12.0,
        max_speed_knots=14.5,
        main_engine_power_kw=4_500.0,
        auxiliary_power_kw=600.0,
        sfoc_g_per_kwh=190.0,
        fuel_tank_capacity_mt=950.0,
        fuel_density_kg_per_l=0.84,
        fuel_reserve_threshold_pct=20.0,
    ),
    "General Cargo": VesselProfile(
        vessel_type="General Cargo",
        name="Coastal Feeder / General Cargo",
        length_m=120.0,
        beam_m=19.0,
        draft_m=7.5,
        displacement_dwt=8_500.0,
        service_speed_knots=12.5,
        max_speed_knots=14.0,
        main_engine_power_kw=3_800.0,
        auxiliary_power_kw=450.0,
        sfoc_g_per_kwh=180.0,
        fuel_tank_capacity_mt=600.0,
        fuel_density_kg_per_l=0.85,
        fuel_reserve_threshold_pct=15.0,
    ),
    "Fishing Vessel": VesselProfile(
        vessel_type="Fishing Vessel",
        name="Deep-Sea Commercial Trawler",
        length_m=45.0,
        beam_m=9.5,
        draft_m=4.2,
        displacement_dwt=850.0,
        service_speed_knots=10.5,
        max_speed_knots=12.5,
        main_engine_power_kw=1_400.0,
        auxiliary_power_kw=200.0,
        sfoc_g_per_kwh=205.0,
        fuel_tank_capacity_mt=180.0,
        fuel_density_kg_per_l=0.84,
        fuel_reserve_threshold_pct=20.0,
    ),
}


def get_vessel_profile(vessel_type: str | None) -> VesselProfile:
    """Returns a matching vessel profile, defaulting to Tanker if unspecified."""
    if not vessel_type:
        return PRESET_VESSELS["Tanker"]

    # Match normalized
    normalized = vessel_type.strip().lower()
    for key, prof in PRESET_VESSELS.items():
        if key.lower() == normalized or prof.vessel_type.lower() == normalized:
            return prof
        if normalized in key.lower() or key.lower() in normalized:
            return prof

    return PRESET_VESSELS["Tanker"]


def list_vessel_profiles() -> list[VesselProfile]:
    """Returns all available preset vessel profiles."""
    return list(PRESET_VESSELS.values())
