"""
Explainable vessel attribution scoring (Section 18).

Weighted, transparent scoring - every candidate gets a full breakdown, not
just a final number. Weights are configurable constants (not hidden inside
opaque model weights) so a judge/investigator can audit exactly how a score
was produced.

IMPORTANT LANGUAGE NOTE (Section 19): outputs use "candidate", "attribution
score", "evidence", "likelihood" - never a claim of proven causation. This
module returns structured data only; the human-readable phrasing enforcing
that language lives in app/reports/generator.py and the API response
builders, but the field names here already avoid asserting guilt.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.ais.pipeline import VesselTrajectory, haversine_km
from app.ais.behavior import analyze_behavior, behavior_score, BehaviorFlag
from app.config import get_settings

DEFAULT_WEIGHTS = {
    "spatial_proximity": 24,
    "temporal_correlation": 20,
    "trajectory_consistency": 12,
    "heading_consistency": 8,
    "speed_consistency": 8,
    "behavioural_anomalies": 10,
    "ais_continuity": 10,
    "data_completeness": 8,
}
assert sum(DEFAULT_WEIGHTS.values()) == 100

# Sourced from app/config.py (env-var overridable: AIS_SEARCH_RADIUS_KM_DEFAULT /
# AIS_TIME_WINDOW_HOURS_DEFAULT) so the operational default can be tuned
# without a code change; the fallback values (50 km / 12 h) are unchanged
# from this project's original hardcoded defaults.
SEARCH_RADIUS_KM_DEFAULT = get_settings().ais_search_radius_km_default
TIME_WINDOW_HOURS_DEFAULT = get_settings().ais_time_window_hours_default


@dataclass
class ScoreBreakdown:
    component: str
    weight: float
    raw_fraction: float   # 0-1 before weighting
    points: float          # weight * raw_fraction
    max_points: float
    explanation: str


@dataclass
class VesselCandidate:
    mmsi: str
    vessel_name: str | None
    vessel_type: str | None
    imo: str | None
    min_distance_km: float
    closest_approach_time: str
    time_delta_hours: float
    final_score: float
    breakdown: list  # list[ScoreBreakdown]
    behavior_flags: list  # list[BehaviorFlag]
    evidence_summary: list  # list[str] - the "WHY THIS VESSEL RANKED #N" bullets
    trajectory_points: list  # list of {lat,lon,timestamp} for map display
    # Distinct from min_distance_km/time_delta_hours above (which are
    # measured against the hindcast-estimated ORIGIN): this is the vessel's
    # closest approach to the raw SATELLITE DETECTION location (SIH
    # requirement D, "distance from spill detection location"). `None` when
    # no detection location was supplied to score_candidate (e.g. direct
    # unit-test calls).
    distance_from_detection_km: float | None = None
    time_delta_from_detection_hours: float | None = None
    # Fraction (0-1) of expected AIS fields (SOG, COG, vessel identity)
    # actually present for this vessel - SIH requirement D "data
    # completeness" - plus plain-language warnings about what is missing,
    # SIH requirement E "missing-data warnings".
    data_completeness_pct: float = 100.0
    data_warnings: list = field(default_factory=list)
    # Heuristic "how much can this SPECIFIC score be trusted", derived from
    # how much real (non-neutral-fallback) data backed it - distinct from
    # `final_score` itself, which measures correlation strength, not data
    # quality. SIH requirement E "confidence level".
    confidence_level: str = "medium"

    def to_dict(self):
        return {
            "mmsi": self.mmsi,
            "vessel_name": self.vessel_name,
            "vessel_type": self.vessel_type,
            "imo": self.imo,
            "min_distance_km": round(self.min_distance_km, 2),
            "closest_approach_time": self.closest_approach_time,
            "time_delta_hours": round(self.time_delta_hours, 2),
            "distance_from_detection_km": (
                round(self.distance_from_detection_km, 2) if self.distance_from_detection_km is not None else None
            ),
            "time_delta_from_detection_hours": (
                round(self.time_delta_from_detection_hours, 2) if self.time_delta_from_detection_hours is not None else None
            ),
            "final_score": round(self.final_score, 1),
            "confidence_level": self.confidence_level,
            "data_completeness_pct": round(self.data_completeness_pct, 1),
            "data_warnings": self.data_warnings,
            "breakdown": [
                {
                    "component": b.component, "weight": b.weight,
                    "raw_fraction": round(b.raw_fraction, 3),
                    "points": round(b.points, 2), "max_points": b.max_points,
                    "explanation": b.explanation,
                } for b in self.breakdown
            ],
            "behavior_flags": [
                {"code": f.code, "description": f.description, "severity": f.severity}
                for f in self.behavior_flags
            ],
            "evidence_summary": self.evidence_summary,
            "trajectory_points": self.trajectory_points,
        }


def _proximity_fraction(distance_km: float, radius_km: float) -> float:
    if distance_km >= radius_km:
        return 0.0
    return max(0.0, 1.0 - (distance_km / radius_km))


def _temporal_fraction(time_delta_hours: float, window_hours: float) -> float:
    if time_delta_hours >= window_hours:
        return 0.0
    return max(0.0, 1.0 - (time_delta_hours / window_hours))


def _trajectory_consistency_fraction(traj: VesselTrajectory, origin_lat: float, origin_lon: float) -> tuple[float, str]:
    """Higher when the vessel's track actually passes close to the origin
    (not just one ping near it) - approximated via how many of the vessel's
    points fall within 2x the closest-approach distance band around origin."""
    if not traj.points:
        return 0.0, "No trajectory points."
    close_pts = sum(
        1 for p in traj.points
        if haversine_km(origin_lat, origin_lon, p.lat, p.lon) <= max(5.0, traj.min_distance_km * 2)
    )
    frac = min(1.0, close_pts / max(1, len(traj.points)))
    return frac, f"{close_pts}/{len(traj.points)} recorded positions fall within the origin proximity band."


def _heading_consistency_fraction(traj: VesselTrajectory, origin_lat: float, origin_lon: float) -> tuple[float, str]:
    """Compares the vessel's course-over-ground near closest approach to the
    bearing from that point toward the estimated origin - a rough proxy for
    'was it heading roughly toward/through the origin'."""
    cap = traj.closest_approach_point
    if cap.cog is None:
        return 0.4, "No course-over-ground data available near closest approach; neutral score applied."
    import math
    lat1, lon1 = math.radians(cap.lat), math.radians(cap.lon)
    lat2, lon2 = math.radians(origin_lat), math.radians(origin_lon)
    dlon = lon2 - lon1
    bearing = (math.degrees(math.atan2(
        math.sin(dlon) * math.cos(lat2),
        math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon),
    )) + 360) % 360
    diff = abs(cap.cog - bearing) % 360
    diff = min(diff, 360 - diff)
    frac = max(0.0, 1.0 - diff / 90.0)  # within 90deg -> partial credit, 0deg -> full
    return frac, f"Course {cap.cog:.0f} deg vs. bearing-to-origin {bearing:.0f} deg (delta {diff:.0f} deg)."


def _continuity_fraction(traj: VesselTrajectory) -> tuple[float, str]:
    if traj.max_gap_minutes <= 15:
        return 1.0, f"Continuous AIS reporting (max gap {traj.max_gap_minutes:.0f} min)."
    if traj.max_gap_minutes >= 120:
        return 0.15, f"Large AIS gap ({traj.max_gap_minutes:.0f} min) - reduced continuity confidence, but gap itself is logged as a behavioural flag."
    frac = max(0.15, 1.0 - (traj.max_gap_minutes / 120.0))
    return frac, f"AIS gap of {traj.max_gap_minutes:.0f} min detected."


# Speed (knots) below which a vessel is considered "slow/stopped" for the
# speed-consistency heuristic below. Deliberately a plain engineering
# threshold, not a validated operational-discharge cutoff.
SLOW_SPEED_KNOTS_THRESHOLD = 15.0


def _speed_consistency_fraction(traj: VesselTrajectory) -> tuple[float, str]:
    """SIH requirement D: "speed consistency where available".

    A low speed (or a stop) recorded at/near closest approach is treated as
    a WEAK, EXPLAINABLE correlate - vessels moving slowly or stationary near
    the estimated origin at the estimated spill time are, in general marine
    pollution investigation practice, considered somewhat more consistent
    with an operational discharge event (e.g. tank cleaning, ballast
    pumping) than a vessel transiting at normal cruising speed. This is
    explicitly NOT evidence of guilt (a vessel could be slow for any number
    of innocent reasons, e.g. waiting for a pilot, anchored, engine issue) -
    it is one transparent, inspectable input among several, exactly like
    heading_consistency above. Returns a neutral 0.4 fraction (matching the
    heading_consistency neutral fallback) when no SOG data is available at
    all, per the "where available" qualifier in the requirement."""
    cap = traj.closest_approach_point
    speed_knots = cap.sog if cap.sog is not None else (traj.avg_speed_knots if traj.avg_speed_knots > 0 else None)
    if speed_knots is None:
        return 0.4, "No speed-over-ground (SOG) data available near closest approach; neutral score applied."
    frac = max(0.0, min(1.0, 1.0 - (speed_knots / SLOW_SPEED_KNOTS_THRESHOLD)))
    return frac, (
        f"Speed at closest approach {speed_knots:.1f} kn (threshold {SLOW_SPEED_KNOTS_THRESHOLD:.0f} kn). "
        "Lower/near-zero speed near the estimated spill time and location is treated as a weak, "
        "non-conclusive correlate with possible operational discharge, not evidence of intent."
    )


def _data_completeness(traj: VesselTrajectory) -> tuple[float, float, list[str]]:
    """SIH requirement D "data completeness" + requirement E "missing-data
    warnings". Checks the fraction of expected AIS fields that are actually
    present for this vessel: per-position SOG/COG, plus vessel-identity
    fields (name, type, IMO) that are static per vessel. Returns
    (fraction_0_to_1, percent_0_to_100, warnings)."""
    warnings: list[str] = []
    n = len(traj.points)
    sog_present = sum(1 for p in traj.points if p.sog is not None)
    cog_present = sum(1 for p in traj.points if p.cog is not None)
    position_fields_present = sog_present + cog_present
    position_fields_total = 2 * n

    identity_fields = [traj.vessel_name, traj.vessel_type, traj.imo]
    identity_present = sum(1 for f in identity_fields if f)

    total_fields = position_fields_total + len(identity_fields)
    present_fields = position_fields_present + identity_present
    fraction = (present_fields / total_fields) if total_fields > 0 else 0.0

    if sog_present < n:
        warnings.append(f"Speed-over-ground (SOG) missing for {n - sog_present}/{n} AIS position(s).")
    if cog_present < n:
        warnings.append(f"Course-over-ground (COG) missing for {n - cog_present}/{n} AIS position(s).")
    if not traj.vessel_name:
        warnings.append("Vessel name not reported in AIS data.")
    if not traj.vessel_type:
        warnings.append("Vessel type not reported in AIS data.")
    if not traj.imo:
        warnings.append("IMO number not reported in AIS data.")
    if n < 3:
        warnings.append(f"Only {n} AIS position(s) available in the investigation window - a short track limits trajectory/heading confidence.")

    return fraction, round(fraction * 100.0, 1), warnings


def _confidence_level(data_completeness_fraction: float, num_points: int) -> str:
    """Heuristic label for how much the SCORE ITSELF can be trusted, based
    on how much real data backed it - not to be confused with `final_score`
    (which measures spatio-temporal correlation strength, not data
    quality). Two vessels can have the same final_score with very different
    confidence_level if one has rich AIS data and the other has almost
    none."""
    if data_completeness_fraction >= 0.75 and num_points >= 3:
        return "high"
    if data_completeness_fraction >= 0.4 and num_points >= 1:
        return "medium"
    return "low"


def score_candidate(
    traj: VesselTrajectory,
    origin_lat: float,
    origin_lon: float,
    origin_time: datetime,
    search_radius_km: float = SEARCH_RADIUS_KM_DEFAULT,
    time_window_hours: float = TIME_WINDOW_HOURS_DEFAULT,
    weights: dict = None,
    detection_time: datetime | None = None,
) -> VesselCandidate:
    weights = weights or DEFAULT_WEIGHTS

    time_delta_hours = abs((traj.closest_approach_time - origin_time).total_seconds()) / 3600.0

    prox_frac = _proximity_fraction(traj.min_distance_km, search_radius_km)
    temp_frac = _temporal_fraction(time_delta_hours, time_window_hours)
    traj_frac, traj_expl = _trajectory_consistency_fraction(traj, origin_lat, origin_lon)
    head_frac, head_expl = _heading_consistency_fraction(traj, origin_lat, origin_lon)
    speed_frac, speed_expl = _speed_consistency_fraction(traj)
    flags = analyze_behavior(traj)
    beh_frac = behavior_score(flags)
    cont_frac, cont_expl = _continuity_fraction(traj)
    completeness_frac, completeness_pct, data_warnings = _data_completeness(traj)

    components = [
        ("spatial_proximity", prox_frac, f"Closest approach {traj.min_distance_km:.1f} km from estimated origin (search radius {search_radius_km:.0f} km)."),
        ("temporal_correlation", temp_frac, f"Closest approach {time_delta_hours:.1f} h from estimated origin time (window {time_window_hours:.0f} h)."),
        ("trajectory_consistency", traj_frac, traj_expl),
        ("heading_consistency", head_frac, head_expl),
        ("speed_consistency", speed_frac, speed_expl),
        ("behavioural_anomalies", beh_frac, f"{len(flags)} behavioural flag(s) detected." if flags else "No notable behavioural anomalies."),
        ("ais_continuity", cont_frac, cont_expl),
        ("data_completeness", completeness_frac, f"{completeness_pct:.0f}% of expected AIS fields (SOG/COG per position, vessel identity) are present for this vessel."),
    ]

    breakdown = []
    total = 0.0
    for name, frac, expl in components:
        w = weights[name]
        pts = w * frac
        total += pts
        breakdown.append(ScoreBreakdown(component=name, weight=w, raw_fraction=frac, points=pts, max_points=w, explanation=expl))

    evidence = []
    if prox_frac > 0:
        evidence.append(f"Passed {traj.min_distance_km:.1f} km from the estimated spill origin.")
    if temp_frac > 0:
        evidence.append(f"Closest approach within {time_delta_hours:.1f} h of the estimated origin time.")
    if traj_frac > 0.5:
        evidence.append("Trajectory intersects the probable origin region.")
    if head_frac > 0.6:
        evidence.append("Heading is consistent with the estimated movement toward the origin.")
    if speed_frac > 0.6:
        evidence.append("Recorded speed near closest approach is consistent with a slow/stationary vessel.")
    for f in flags:
        prefix = "\u26a0" if f.severity != "high" else "\u2757"
        evidence.append(f"{prefix} {f.description}")
    if not evidence:
        evidence.append("Limited spatio-temporal correlation with the estimated origin.")

    traj_points_out = [{"lat": p.lat, "lon": p.lon, "timestamp": p.timestamp.isoformat(), "sog": p.sog, "cog": p.cog} for p in traj.points]

    distance_from_detection_km = traj.min_distance_to_detection_km
    time_delta_from_detection_hours = None
    if traj.closest_approach_to_detection_time is not None and detection_time is not None:
        time_delta_from_detection_hours = abs(
            (traj.closest_approach_to_detection_time - detection_time).total_seconds()
        ) / 3600.0

    confidence_level = _confidence_level(completeness_frac, len(traj.points))

    return VesselCandidate(
        mmsi=traj.mmsi,
        vessel_name=traj.vessel_name,
        vessel_type=traj.vessel_type,
        imo=traj.imo,
        min_distance_km=traj.min_distance_km,
        closest_approach_time=traj.closest_approach_time.isoformat(),
        time_delta_hours=time_delta_hours,
        distance_from_detection_km=distance_from_detection_km,
        time_delta_from_detection_hours=time_delta_from_detection_hours,
        final_score=total,
        breakdown=breakdown,
        behavior_flags=flags,
        evidence_summary=evidence,
        trajectory_points=traj_points_out,
        data_completeness_pct=completeness_pct,
        data_warnings=data_warnings,
        confidence_level=confidence_level,
    )


def rank_candidates(
    trajectories: list[VesselTrajectory],
    origin_lat: float, origin_lon: float, origin_time: datetime,
    search_radius_km: float = SEARCH_RADIUS_KM_DEFAULT,
    time_window_hours: float = TIME_WINDOW_HOURS_DEFAULT,
    weights: dict = None,
    detection_time: datetime | None = None,
) -> list[VesselCandidate]:
    candidates = [
        score_candidate(t, origin_lat, origin_lon, origin_time, search_radius_km, time_window_hours, weights, detection_time)
        for t in trajectories
    ]
    candidates.sort(key=lambda c: c.final_score, reverse=True)
    return candidates
