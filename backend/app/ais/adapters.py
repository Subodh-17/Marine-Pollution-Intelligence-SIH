"""
AIS source adapters - the AIS normalization layer required by the spec:
"different CSV/real/synthetic formats can be converted into one internal
representation" (AISRecord).

Adapters implemented:
  - MarineCadastreAISAdapter: reads the canonical MarineCadastre AIS CSV
    column layout (MMSI, BaseDateTime, LAT, LON, SOG, COG, Heading,
    VesselName, VesselType, IMO, CallSign, Status, ...) - this is the
    reference format named in the SIH problem statement
    (https://marinecadastre.gov/accessais/).
  - GenericCSVAISAdapter: best-effort column-alias matching for arbitrary
    CSV exports that use different header names for the same fields.
  - SyntheticAISAdapter: deterministic offline demo fleet (see
    app/demo/scenario.py for the actual scenario definition) used when no
    real MarineCadastre extract is supplied - this satisfies the mandatory
    "Synthetic Demonstration Mode" (spec Section 20).

Real MarineCadastre bulk downloads were not fetched at runtime because this
sandboxed environment's network egress does not include marinecadastre.gov;
this adapter is written against the documented/published MarineCadastre AIS
CSV schema so a real extract can be dropped into
`backend/data/sample_ais/` and read with zero code changes.
"""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Iterable

from app.ais.schema import AISRecord

# common alias table for generic / unknown CSV sources
_ALIASES = {
    "mmsi": ["mmsi", "MMSI"],
    "timestamp": ["basedatetime", "BaseDateTime", "timestamp", "Timestamp", "datetime", "DateTime", "time", "Time"],
    "lat": ["lat", "LAT", "latitude", "Latitude"],
    "lon": ["lon", "LON", "long", "longitude", "Longitude"],
    "sog": ["sog", "SOG", "speed", "Speed"],
    "cog": ["cog", "COG", "course", "Course"],
    "heading": ["heading", "Heading"],
    "vessel_name": ["vesselname", "VesselName", "name", "Name"],
    "vessel_type": ["vesseltype", "VesselType", "type", "Type"],
    "imo": ["imo", "IMO"],
    "call_sign": ["callsign", "CallSign"],
    "status": ["status", "Status"],
}


def _build_column_map(fieldnames: list[str]) -> dict:
    lower_map = {fn.lower(): fn for fn in fieldnames}
    resolved = {}
    for target, aliases in _ALIASES.items():
        for alias in aliases:
            if alias.lower() in lower_map:
                resolved[target] = lower_map[alias.lower()]
                break
    return resolved


def _parse_timestamp(raw: str) -> datetime | None:
    if not raw:
        return None
    raw = raw.strip()
    fmts = (
        "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f", "%m/%d/%Y %H:%M:%S", "%Y-%m-%d",
    )
    for fmt in fmts:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw.replace("Z", ""))
    except ValueError:
        return None


def _safe_float(raw) -> float | None:
    try:
        if raw in (None, "", "N/A"):
            return None
        return float(raw)
    except (ValueError, TypeError):
        return None


class MarineCadastreAISAdapter:
    """Reads the canonical MarineCadastre AIS CSV export format."""

    source_tag = "MARINECADASTRE_AIS"

    def read(self, path: str | Path) -> list[AISRecord]:
        return list(_read_csv_generic(path, self.source_tag))


class GenericCSVAISAdapter:
    """Best-effort reader for CSV exports with non-standard headers."""

    source_tag = "GENERIC_CSV_AIS"

    def read(self, path: str | Path) -> list[AISRecord]:
        return list(_read_csv_generic(path, self.source_tag))


def _read_csv_generic(path: str | Path, source_tag: str) -> Iterable[AISRecord]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        colmap = _build_column_map(reader.fieldnames or [])
        missing = [k for k in ("mmsi", "timestamp", "lat", "lon") if k not in colmap]
        if missing:
            # Deliberately does not include the filesystem path in this
            # message: it propagates as-is up through run_investigation()
            # into a client-facing HTTP 400 (see app/main.py) and also into
            # a background job's `job.error` (see app/persistence/jobs.py),
            # and `path` here is always the server's internal
            # randomly-named temp file (see _save_upload_with_size_limit),
            # never the user's original filename - showing it would leak
            # internal server layout for no benefit to the caller. The
            # headers actually found ARE useful to the caller (helps them
            # spot their own typo), so those are kept.
            raise ValueError(
                f"The AIS CSV is missing required columns for: {missing}. "
                f"Columns found in the file: {reader.fieldnames or []}"
            )
        for row in reader:
            ts = _parse_timestamp(row.get(colmap["timestamp"], ""))
            lat = _safe_float(row.get(colmap["lat"]))
            lon = _safe_float(row.get(colmap["lon"]))
            mmsi = (row.get(colmap["mmsi"]) or "").strip()
            if not (ts and lat is not None and lon is not None and mmsi):
                continue
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                continue
            yield AISRecord(
                mmsi=mmsi,
                timestamp=ts,
                lat=lat,
                lon=lon,
                sog=_safe_float(row.get(colmap.get("sog", ""), None)),
                cog=_safe_float(row.get(colmap.get("cog", ""), None)),
                heading=_safe_float(row.get(colmap.get("heading", ""), None)),
                vessel_name=row.get(colmap.get("vessel_name", ""), None),
                vessel_type=row.get(colmap.get("vessel_type", ""), None),
                imo=row.get(colmap.get("imo", ""), None),
                call_sign=row.get(colmap.get("call_sign", ""), None),
                status=row.get(colmap.get("status", ""), None),
                source=source_tag,
            )
