"""
Preset maritime ports and strategic navigational waypoints.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, List, Any


@dataclass
class PortInfo:
    id: str
    name: str
    country: str
    lat: float
    lon: float
    description: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


PRESET_PORTS: Dict[str, PortInfo] = {
    "mumbai": PortInfo(
        id="mumbai",
        name="Mumbai Port / Anchorage",
        country="India",
        lat=18.940,
        lon=72.840,
        description="Premier natural deep-water harbour and gateway port of western India.",
    ),
    "jnpt": PortInfo(
        id="jnpt",
        name="JNPT (Jawaharlal Nehru Port)",
        country="India",
        lat=18.950,
        lon=72.950,
        description="Largest container port in India, handling over 50% of total container cargo.",
    ),
    "kandla": PortInfo(
        id="kandla",
        name="Deendayal Port (Kandla)",
        country="India",
        lat=23.010,
        lon=70.220,
        description="Major bulk cargo and crude oil importing hub on the Gulf of Kutch.",
    ),
    "mormugao": PortInfo(
        id="mormugao",
        name="Mormugao Port (Goa)",
        country="India",
        lat=15.415,
        lon=73.790,
        description="Leading iron ore exporting and deep-draft natural port in Goa.",
    ),
    "mangalore": PortInfo(
        id="mangalore",
        name="New Mangalore Port",
        country="India",
        lat=12.925,
        lon=74.810,
        description="Major all-weather port in Karnataka serving POL and pellet export trades.",
    ),
    "kochi": PortInfo(
        id="kochi",
        name="Cochin Port (Vallarpadam)",
        country="India",
        lat=9.965,
        lon=76.260,
        description="Strategic transshipment hub on international east-west sea routes.",
    ),
    "colombo": PortInfo(
        id="colombo",
        name="Port of Colombo",
        country="Sri Lanka",
        lat=6.950,
        lon=79.840,
        description="Major Indian Ocean transshipment container hub.",
    ),
    "dubai": PortInfo(
        id="dubai",
        name="Jebel Ali / Dubai Port",
        country="UAE",
        lat=25.010,
        lon=55.060,
        description="World's ninth busiest container port and gateway to the Persian Gulf.",
    ),
    "salalah": PortInfo(
        id="salalah",
        name="Port of Salalah",
        country="Oman",
        lat=16.940,
        lon=54.000,
        description="Major deepwater transshipment hub on the Arabian Sea / Gulf of Aden corridor.",
    ),
    "singapore": PortInfo(
        id="singapore",
        name="Port of Singapore",
        country="Singapore",
        lat=1.260,
        lon=103.820,
        description="World's largest transshipment container port and top global bunkering hub.",
    ),
    "spill_site": PortInfo(
        id="spill_site",
        name="Offshore SAR Slick Site",
        country="Arabian Sea (EEZ)",
        lat=19.050,
        lon=71.200,
        description="High-traffic corridor intersection corresponding to the detected SAR oil slick.",
    ),
}


def list_ports() -> List[PortInfo]:
    return list(PRESET_PORTS.values())


def get_port(port_id: str) -> PortInfo | None:
    return PRESET_PORTS.get(port_id.lower().strip())
