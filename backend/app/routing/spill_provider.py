"""
Spill zone provider: retrieves detected oil spill polygons from active investigations,
persisted database records, or default synthetic/satellite demo scenarios.
"""
from __future__ import annotations

from typing import List
from app.routing.models import SpillAvoidanceZone
from app.investigation.orchestrator import _STORE, get_investigation
from app.persistence import db as _db

# Default realistic slick geometry for the Arabian Sea SAR detection scenario (19.05°N, 71.20°E)
DEFAULT_DEMO_SPILL_POLYGON = [
    [19.082, 71.175],
    [19.095, 71.210],
    [19.070, 71.245],
    [19.040, 71.238],
    [19.020, 71.195],
    [19.045, 71.160],
    [19.082, 71.175],
]


def get_default_spill_zone() -> SpillAvoidanceZone:
    """Returns the primary demo Arabian Sea oil slick avoidance zone."""
    return SpillAvoidanceZone(
        id="demo_sar_slick_arabian_sea",
        name="Sentinel-1 SAR Detection - Arabian Sea (High Confidence)",
        polygon_latlon=DEFAULT_DEMO_SPILL_POLYGON,
        buffer_nm=2.0,
        centroid_lat=19.055,
        centroid_lon=71.205,
        area_km2=24.8,
        active=True,
    )


def resolve_active_spill_zones(investigation_id: str | None = None) -> List[SpillAvoidanceZone]:
    """
    Discovers detected spill polygons from:
    1. Specifically requested investigation ID if provided.
    2. Any active in-memory or persisted investigations with detected slick geometries.
    3. The default high-fidelity SAR demo slick zone.
    """
    zones: List[SpillAvoidanceZone] = []
    seen_ids = set()

    # If specific investigation requested
    if investigation_id:
        inv = get_investigation(investigation_id)
        if inv:
            inv_dict = inv.to_dict() if hasattr(inv, "to_dict") else inv
            geo = (inv_dict.get("satellite") or {}).get("geometry")
            if geo and geo.get("polygon_latlon"):
                zones.append(
                    SpillAvoidanceZone(
                        id=f"inv_{investigation_id}",
                        name=f"Spill: Investigation {investigation_id[:8]}",
                        polygon_latlon=geo["polygon_latlon"],
                        buffer_nm=2.0,
                        centroid_lat=geo.get("centroid_lat"),
                        centroid_lon=geo.get("centroid_lon"),
                        area_km2=geo.get("area_km2"),
                        active=True,
                    )
                )
                seen_ids.add(investigation_id)

    # Check in-memory store
    try:
        for inv_id, inv in list(_STORE.items()):
            if inv_id in seen_ids:
                continue
            inv_dict = inv.to_dict() if hasattr(inv, "to_dict") else inv
            geo = (inv_dict.get("satellite") or {}).get("geometry")
            if geo and geo.get("polygon_latlon") and len(geo["polygon_latlon"]) >= 3:
                zones.append(
                    SpillAvoidanceZone(
                        id=f"inv_{inv_id}",
                        name=f"Active Slick: Inv {inv_id[:8]}",
                        polygon_latlon=geo["polygon_latlon"],
                        buffer_nm=2.0,
                        centroid_lat=geo.get("centroid_lat"),
                        centroid_lon=geo.get("centroid_lon"),
                        area_km2=geo.get("area_km2"),
                        active=True,
                    )
                )
                seen_ids.add(inv_id)
                if len(zones) >= 5:
                    break
    except Exception:
        pass

    # Check persisted database investigations if more zones are needed (Section 6 & Phase 4 persistence requirement)
    if len(zones) < 5:
        try:
            persisted_list = _db.list_investigations(limit=10)
            for item in persisted_list:
                inv_id = item.get("id")
                if not inv_id or inv_id in seen_ids:
                    continue
                inv_data = _db.load_investigation(inv_id)
                if not inv_data:
                    continue
                geo = (inv_data.get("satellite") or {}).get("geometry")
                if geo and geo.get("polygon_latlon") and len(geo["polygon_latlon"]) >= 3:
                    zones.append(
                        SpillAvoidanceZone(
                            id=f"inv_{inv_id}",
                            name=f"Persisted Slick: Inv {inv_id[:8]}",
                            polygon_latlon=geo["polygon_latlon"],
                            buffer_nm=2.0,
                            centroid_lat=geo.get("centroid_lat"),
                            centroid_lon=geo.get("centroid_lon"),
                            area_km2=geo.get("area_km2"),
                            active=True,
                        )
                    )
                    seen_ids.add(inv_id)
                    if len(zones) >= 5:
                        break
        except Exception:
            pass

    # Fallback to default demo spill zone so routing always has immediate slick data
    if not zones:
        zones.append(get_default_spill_zone())

    return zones
