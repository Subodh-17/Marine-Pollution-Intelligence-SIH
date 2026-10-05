"""
Internal normalized AIS record schema.

Every AIS source adapter (MarineCadastre CSV, generic CSV/Parquet, synthetic
demo generator) converts its native format into this one representation so
all downstream processing (filtering, trajectory reconstruction, behaviour
analysis, scoring) is source-agnostic.

Field reference (aligned to the MarineCadastre AIS field set named in the
SIH problem statement: MMSI, Timestamp, Latitude, Longitude, SOG, COG,
Heading, Vessel Name, Vessel Type, IMO).
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional


@dataclass
class AISRecord:
    mmsi: str
    timestamp: datetime
    lat: float
    lon: float
    sog: Optional[float] = None       # speed over ground, knots
    cog: Optional[float] = None       # course over ground, degrees
    heading: Optional[float] = None   # degrees
    vessel_name: Optional[str] = None
    vessel_type: Optional[str] = None
    imo: Optional[str] = None
    call_sign: Optional[str] = None
    status: Optional[str] = None
    source: str = "UNKNOWN"

    def to_dict(self):
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d


REQUIRED_FIELDS = ("mmsi", "timestamp", "lat", "lon")
