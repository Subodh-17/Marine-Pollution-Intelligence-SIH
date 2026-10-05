"""
Behavioural anomaly indicators (Section 17).

These are EVIDENCE INDICATORS, not proof of pollution - the scoring engine
(app/scoring/attribution.py) treats them as one weighted factor among
several, and the API/report language must never claim they constitute
proof (see Section 19).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.ais.pipeline import VesselTrajectory

SPEED_DROP_ANOMALY_KNOTS = 4.0
COURSE_CHANGE_ANOMALY_DEG = 30.0
LOITER_SPEED_KNOTS = 3.0
AIS_GAP_ANOMALY_MINUTES = 45.0


@dataclass
class BehaviorFlag:
    code: str
    description: str
    severity: str  # "info" | "notable" | "high"


def analyze_behavior(traj: VesselTrajectory) -> list[BehaviorFlag]:
    flags: list[BehaviorFlag] = []

    for t, delta in traj.speed_changes:
        if delta < 0:
            flags.append(BehaviorFlag(
                code="SPEED_DROP",
                description=f"Speed reduced by {abs(delta):.1f} kn near {t.isoformat()} - possible slow-down/discharge behaviour.",
                severity="notable",
            ))
        else:
            flags.append(BehaviorFlag(
                code="SPEED_INCREASE",
                description=f"Speed increased by {delta:.1f} kn near {t.isoformat()} - possible evasive departure.",
                severity="notable",
            ))

    for t, delta in traj.course_changes:
        flags.append(BehaviorFlag(
            code="COURSE_CHANGE",
            description=f"Course changed by {delta:.0f} deg near {t.isoformat()}.",
            severity="notable" if delta < 90 else "high",
        ))

    slow_points = [p for p in traj.points if p.sog is not None and p.sog <= LOITER_SPEED_KNOTS]
    if len(slow_points) >= max(2, len(traj.points) * 0.3) and len(traj.points) >= 3:
        flags.append(BehaviorFlag(
            code="LOITERING",
            description=f"Vessel held speed <= {LOITER_SPEED_KNOTS} kn for {len(slow_points)} of {len(traj.points)} recorded pings - possible loitering/discharge.",
            severity="high",
        ))

    if traj.max_gap_minutes >= AIS_GAP_ANOMALY_MINUTES:
        flags.append(BehaviorFlag(
            code="AIS_GAP",
            description=f"AIS transmission gap of {traj.max_gap_minutes:.0f} minutes detected - transponder may have been disabled.",
            severity="high",
        ))

    if traj.min_distance_km <= 3.0:
        flags.append(BehaviorFlag(
            code="TRAJECTORY_THROUGH_ORIGIN",
            description=f"Trajectory passed within {traj.min_distance_km:.1f} km of the estimated spill origin.",
            severity="high",
        ))

    # A physically impossible implied speed between two consecutive pings
    # for the SAME MMSI (see MAX_REALISTIC_SPEED_KNOTS in pipeline.py) is
    # evidence of a DATA problem (corrupt/duplicate/spoofed record) - no
    # real vessel actually moved that fast. This is intentionally kept at
    # "info" severity (a small, fixed contribution to the behavioural score
    # - see behavior_score() below) rather than "notable"/"high": it must
    # not be allowed to make a vessel look MORE suspicious, since it isn't
    # evidence of anything the vessel did. It is surfaced so an
    # investigator can see the trajectory's data quality is compromised
    # around that point, rather than silently dropping or trusting the
    # affected ping.
    for t, dist_km, implied_kn in traj.position_jumps:
        flags.append(BehaviorFlag(
            code="POSITION_JUMP",
            description=(
                f"Implausible position jump near {t.isoformat()}: {dist_km:.1f} km between consecutive "
                f"pings implies {implied_kn:.0f} kn, which is not physically realistic for a surface "
                "vessel. Likely a data/transponder anomaly (duplicate MMSI, corrupted coordinate, or "
                "spoofed signal) rather than genuine vessel movement - treat this trajectory's "
                "continuity with caution around this point."
            ),
            severity="info",
        ))

    return flags


def behavior_score(flags: list[BehaviorFlag]) -> float:
    """Map flags to a 0-1 behavioural anomaly score for the attribution engine."""
    if not flags:
        return 0.0
    weights = {"info": 0.05, "notable": 0.20, "high": 0.40}
    raw = sum(weights.get(f.severity, 0.1) for f in flags)
    return min(1.0, raw)
