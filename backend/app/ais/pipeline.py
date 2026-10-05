"""
AIS processing pipeline: cleaning -> spatial filtering -> temporal filtering
-> trajectory reconstruction, as specified in Sections 13-16 of the SIH
brief.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from math import radians, sin, cos, sqrt, atan2

from app.ais.schema import AISRecord

EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlmb = radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(p1) * cos(p2) * sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_KM * atan2(sqrt(a), sqrt(1 - a))


def clean_records(records: list[AISRecord]) -> list[AISRecord]:
    """Drop invalid coordinates/timestamps and duplicate (mmsi, timestamp) pings."""
    seen = set()
    out = []
    for r in records:
        if r is None:
            continue
        if not (-90 <= r.lat <= 90 and -180 <= r.lon <= 180):
            continue
        key = (r.mmsi, r.timestamp.isoformat())
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def spatial_filter(records: list[AISRecord], center_lat: float, center_lon: float, radius_km: float) -> list[AISRecord]:
    return [r for r in records if haversine_km(center_lat, center_lon, r.lat, r.lon) <= radius_km]


def temporal_filter(records: list[AISRecord], start: datetime, end: datetime) -> list[AISRecord]:
    return [r for r in records if start <= r.timestamp <= end]


def summarize_exclusions(
    cleaned: list[AISRecord],
    candidate_mmsis: set,
    center_lat: float,
    center_lon: float,
    radius_km: float,
    window_start: datetime,
    window_end: datetime,
    limit: int = 5,
) -> list[dict]:
    """Explains, in plain language, why vessels that appeared in the raw AIS
    traffic did NOT become attribution candidates - i.e. the counterpart to
    detection.py's "Rejected region ..." notes, but for AIS filtering
    (Sections 14-15) instead of SAR detection (Section 4).

    Without this, the spatial/temporal filter stage silently drops vessels
    and the UI/report only ever shows aggregate funnel counts, never which
    specific nearby vessel was ruled out or why - even though the demo
    scenario (app/demo/scenario.py) deliberately includes a vessel built
    exactly to exercise this ("geographically close, wrong time window") and
    that narrative was previously invisible in every API response.

    Deliberately bounded to the `limit` closest excluded vessels (not the
    full excluded fleet) - this is explanatory context for a human reader,
    not an exhaustive audit trail of every discarded ping.
    """
    by_mmsi: dict[str, list[AISRecord]] = {}
    for r in cleaned:
        if r.mmsi in candidate_mmsis:
            continue
        by_mmsi.setdefault(r.mmsi, []).append(r)

    out = []
    for mmsi, pts in by_mmsi.items():
        distances = [haversine_km(center_lat, center_lon, p.lat, p.lon) for p in pts]
        min_distance_km = min(distances)
        any_in_radius = any(d <= radius_km for d in distances)
        any_in_window = any(window_start <= p.timestamp <= window_end for p in pts)

        if any_in_radius and any_in_window:
            # Would have produced a trajectory that passed both filters -
            # excluded for some other reason (e.g. a single stray ping in a
            # multi-vessel scenario). Not misleading to skip explaining this
            # case explicitly; it is rare and the funnel counts still cover it.
            continue
        if any_in_radius and not any_in_window:
            reason = (
                f"came within the {radius_km:.0f} km search radius, but none of its AIS "
                f"positions fall inside the \u00b1{(window_end - window_start).total_seconds() / 7200:.0f} h "
                "investigation time window around the estimated spill origin time."
            )
        elif any_in_window and not any_in_radius:
            reason = (
                f"was transmitting AIS within the investigation time window, but never came "
                f"closer than {min_distance_km:.1f} km to the estimated origin "
                f"(search radius {radius_km:.0f} km)."
            )
        else:
            reason = (
                f"closest recorded position was {min_distance_km:.1f} km from the estimated "
                "origin and outside the investigation time window."
            )

        out.append({
            "mmsi": mmsi,
            "vessel_name": next((p.vessel_name for p in pts if p.vessel_name), None),
            "min_distance_km": round(min_distance_km, 1),
            "reason": reason,
        })

    out.sort(key=lambda e: e["min_distance_km"])
    return out[:limit]


@dataclass
class VesselTrajectory:
    mmsi: str
    vessel_name: str | None
    vessel_type: str | None
    imo: str | None
    points: list  # list[AISRecord], time-sorted
    min_distance_km: float
    closest_approach_time: datetime
    closest_approach_point: AISRecord
    gaps_minutes: list  # list of gap durations (minutes) between consecutive pings
    max_gap_minutes: float
    avg_speed_knots: float
    speed_changes: list  # list of (time, delta_knots)
    course_changes: list  # list of (time, delta_deg)
    # Consecutive-ping (time, distance_km, implied_speed_knots) triples where
    # the implied speed exceeds MAX_REALISTIC_SPEED_KNOTS - i.e. physically
    # impossible for this to be the same vessel actually moving. This is a
    # DATA-QUALITY signal (corrupt/duplicate/spoofed record), not evidence
    # of vessel behaviour - see app/ais/behavior.py's POSITION_JUMP flag for
    # how it's surfaced with that distinction made explicit.
    position_jumps: list = field(default_factory=list)
    # Distance from the raw SATELLITE DETECTION location (the SAR-observed
    # slick centroid), as distinct from `min_distance_km` above which is
    # measured against the HINDCAST-ESTIMATED ORIGIN (a different point -
    # the origin is the drift model's backward-propagated estimate, the
    # detection location is where the slick was actually observed in the
    # image). SIH requirement D asks for both as separate, explainable
    # factors. `None` when the caller did not supply a detection location
    # (e.g. direct unit-test calls that only care about origin distance).
    min_distance_to_detection_km: float | None = None
    closest_approach_to_detection_time: datetime | None = None

    def to_dict(self):
        return {
            "mmsi": self.mmsi,
            "vessel_name": self.vessel_name,
            "vessel_type": self.vessel_type,
            "imo": self.imo,
            "points": [p.to_dict() for p in self.points],
            "min_distance_km": round(self.min_distance_km, 3),
            "closest_approach_time": self.closest_approach_time.isoformat(),
            "closest_approach_point": self.closest_approach_point.to_dict(),
            "gaps_minutes": [round(g, 1) for g in self.gaps_minutes],
            "max_gap_minutes": round(self.max_gap_minutes, 1),
            "avg_speed_knots": round(self.avg_speed_knots, 2),
            "speed_changes": [{"time": t.isoformat(), "delta_knots": round(d, 2)} for t, d in self.speed_changes],
            "course_changes": [{"time": t.isoformat(), "delta_deg": round(d, 1)} for t, d in self.course_changes],
            "position_jumps": [
                {"time": t.isoformat(), "distance_km": round(dist, 2), "implied_speed_knots": round(kn, 1)}
                for t, dist, kn in self.position_jumps
            ],
            "min_distance_to_detection_km": (
                round(self.min_distance_to_detection_km, 3) if self.min_distance_to_detection_km is not None else None
            ),
            "closest_approach_to_detection_time": (
                self.closest_approach_to_detection_time.isoformat()
                if self.closest_approach_to_detection_time is not None else None
            ),
        }


def _course_delta(a: float, b: float) -> float:
    d = abs(a - b) % 360
    return min(d, 360 - d)


# A generous physical upper bound on surface-vessel speed - fast naval
# craft/hydrofoils/racing boats can approach this, ordinary commercial
# shipping (this project's actual subject matter) is typically 10-25 kn.
# Anything ABOVE this implied by consecutive-ping distance/time for the
# SAME MMSI cannot be a real vessel movement - it indicates a data problem
# (duplicate/reused MMSI, corrupted lat/lon field, spoofed transponder),
# not evasive behaviour, so it is surfaced as a data-quality caveat (see
# `POSITION_JUMP` in app/ais/behavior.py) rather than treated as suspicious
# vessel conduct.
MAX_REALISTIC_SPEED_KNOTS = 60.0
_KM_PER_NM = 1.852


def _implied_speed_knots(dist_km: float, dt_minutes: float) -> float | None:
    """Speed implied by straight-line distance over elapsed time. `None`
    when the elapsed time is too small to give a meaningful (numerically
    stable) estimate rather than a division-by-near-zero artifact - e.g.
    two records sharing (or nearly sharing) a timestamp."""
    if dt_minutes <= 1e-6:
        return None
    dt_hours = dt_minutes / 60.0
    return (dist_km / _KM_PER_NM) / dt_hours


def reconstruct_trajectories(
    records: list[AISRecord],
    origin_lat: float,
    origin_lon: float,
    detection_lat: float | None = None,
    detection_lon: float | None = None,
) -> list[VesselTrajectory]:
    """Group by MMSI, sort by time, and derive trajectory-level features
    used by behavioural analysis and attribution scoring.

    `origin_lat/lon` is the hindcast-estimated spill origin (used for the
    primary `min_distance_km`, unchanged from before). `detection_lat/lon`
    is optional and, when supplied, is the raw satellite-observed slick
    location - a distinct point from the hindcast origin - so callers can
    expose "distance from detection location" as its own explainable factor
    (SIH requirement D) alongside "distance from estimated origin". Omitting
    it (the default) preserves the exact previous behaviour for existing
    callers/tests."""
    by_mmsi: dict[str, list[AISRecord]] = {}
    for r in records:
        by_mmsi.setdefault(r.mmsi, []).append(r)

    trajectories = []
    for mmsi, pts in by_mmsi.items():
        pts = sorted(pts, key=lambda r: r.timestamp)
        if len(pts) < 1:
            continue

        distances = [haversine_km(origin_lat, origin_lon, p.lat, p.lon) for p in pts]
        min_idx = min(range(len(pts)), key=lambda i: distances[i])

        det_min_distance = None
        det_closest_time = None
        if detection_lat is not None and detection_lon is not None:
            det_distances = [haversine_km(detection_lat, detection_lon, p.lat, p.lon) for p in pts]
            det_min_idx = min(range(len(pts)), key=lambda i: det_distances[i])
            det_min_distance = det_distances[det_min_idx]
            det_closest_time = pts[det_min_idx].timestamp

        gaps = []
        speed_changes = []
        course_changes = []
        position_jumps = []
        speeds = [p.sog for p in pts if p.sog is not None]
        for i in range(1, len(pts)):
            dt_min = (pts[i].timestamp - pts[i - 1].timestamp).total_seconds() / 60.0
            gaps.append(dt_min)
            if pts[i].sog is not None and pts[i - 1].sog is not None:
                d_speed = pts[i].sog - pts[i - 1].sog
                if abs(d_speed) >= 4.0:
                    speed_changes.append((pts[i].timestamp, d_speed))
            if pts[i].cog is not None and pts[i - 1].cog is not None:
                d_course = _course_delta(pts[i].cog, pts[i - 1].cog)
                if d_course >= 30.0:
                    course_changes.append((pts[i].timestamp, d_course))
            jump_km = haversine_km(pts[i - 1].lat, pts[i - 1].lon, pts[i].lat, pts[i].lon)
            implied_knots = _implied_speed_knots(jump_km, dt_min)
            if implied_knots is not None and implied_knots > MAX_REALISTIC_SPEED_KNOTS:
                position_jumps.append((pts[i].timestamp, jump_km, implied_knots))

        vessel_name = next((p.vessel_name for p in pts if p.vessel_name), None)
        vessel_type = next((p.vessel_type for p in pts if p.vessel_type), None)
        imo = next((p.imo for p in pts if p.imo), None)

        trajectories.append(VesselTrajectory(
            mmsi=mmsi,
            vessel_name=vessel_name,
            vessel_type=vessel_type,
            imo=imo,
            points=pts,
            min_distance_km=distances[min_idx],
            closest_approach_time=pts[min_idx].timestamp,
            closest_approach_point=pts[min_idx],
            gaps_minutes=gaps,
            max_gap_minutes=max(gaps) if gaps else 0.0,
            avg_speed_knots=(sum(speeds) / len(speeds)) if speeds else 0.0,
            speed_changes=speed_changes,
            course_changes=course_changes,
            position_jumps=position_jumps,
            min_distance_to_detection_km=det_min_distance,
            closest_approach_to_detection_time=det_closest_time,
        ))
    return trajectories
