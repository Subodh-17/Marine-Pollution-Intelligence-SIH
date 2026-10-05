"""
Physics-based slick drift model (backward hindcast + forward forecast).

MODEL
-----
Standard oil-spill trajectory "leeway" formulation used operationally
(e.g. NOAA GNOME, OpenDrift) at a level of fidelity appropriate for a
hackathon prototype:

    drift_velocity = current_velocity + WIND_DRIFT_FACTOR * R(theta) * wind_velocity

  - current_velocity: ocean surface current vector (from environment adapter)
  - wind_velocity: 10 m wind vector (from environment adapter)
  - WIND_DRIFT_FACTOR ~ 0.03 (oil moves at ~3% of wind speed - literature
    range is roughly 1-4% for oil/leeway objects; kept configurable)
  - R(theta): rotation applied to the wind vector to approximate the
    empirically observed deflection of wind-driven drift from the pure
    downwind direction (Ekman-type effect); default 15 deg in this build
    (configurable; sign should invert for southern-hemisphere deployments)

The model is integrated with simple forward Euler steps over a
configurable time step, re-sampling environment conditions at the current
estimated position/time at every step (so the field can vary in space and
time). This is explicitly a SIMPLIFIED model:
  - no turbulent diffusion / spreading is modelled (only advection of the
    centroid)
  - no Stokes drift / wave-induced transport term is separated out
  - environment fields are sourced from `resolve_environment_conditions`,
    which - absent live NOAA/Copernicus access in this environment - falls
    back to a clearly labelled synthetic field (see environment/adapters.py)

Both facts are surfaced in the API/report as "Drift: physics-based model
(simplified advection), environmental data: <source>", per spec Section 22.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import math

from app.environment.adapters import resolve_environment_conditions

WIND_DRIFT_FACTOR = 0.03
WIND_DEFLECTION_DEG = 15.0
EARTH_RADIUS_M = 6_371_000.0

# Uncertainty growth: radius (km) = BASE_UNCERTAINTY_KM + GROWTH_KM_PER_HOUR * |t_hours|
BASE_UNCERTAINTY_KM = 0.8
GROWTH_KM_PER_HOUR = 0.55


@dataclass
class TrackPoint:
    t_offset_hours: float       # negative = past (hindcast), positive = future (forecast), 0 = observed
    timestamp: str
    lat: float
    lon: float
    uncertainty_km: float
    label: str                  # e.g. "T-24h", "OBSERVED", "T+12h"


@dataclass
class DriftResult:
    observed: TrackPoint
    backward_track: list          # list[TrackPoint], ordered oldest -> observed (exclusive of observed)
    forward_track: list           # list[TrackPoint], ordered observed(exclusive) -> future
    probable_origin: TrackPoint
    origin_confidence: str        # "low" | "medium" | "high" (heuristic, see notes)
    environment_source: str
    wind_drift_factor: float = WIND_DRIFT_FACTOR
    wind_deflection_deg: float = WIND_DEFLECTION_DEG
    notes: list = field(default_factory=list)


def _dest_point(lat: float, lon: float, bearing_deg: float, distance_m: float) -> tuple[float, float]:
    """Great-circle destination point given start, bearing, distance."""
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)
    brng = math.radians(bearing_deg)
    d_r = distance_m / EARTH_RADIUS_M

    lat2 = math.asin(math.sin(lat1) * math.cos(d_r) + math.cos(lat1) * math.sin(d_r) * math.cos(brng))
    lon2 = lon1 + math.atan2(
        math.sin(brng) * math.sin(d_r) * math.cos(lat1),
        math.cos(d_r) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), (math.degrees(lon2) + 540) % 360 - 180


def destination_point(lat: float, lon: float, bearing_deg: float, distance_km: float) -> tuple[float, float]:
    """Public helper (used by the demo AIS scenario builder) - great-circle
    destination given a start point, bearing and distance in km."""
    return _dest_point(lat, lon, bearing_deg, distance_km * 1000.0)


def _vector_to_bearing_speed(u_east: float, v_north: float) -> tuple[float, float]:
    speed = math.hypot(u_east, v_north)
    bearing = (math.degrees(math.atan2(u_east, v_north))) % 360
    return bearing, speed


def _step_position(lat: float, lon: float, t: datetime, dt_seconds: float, direction: int) -> tuple[float, float]:
    """One Euler advection step. `direction` is +1 (forward in time) or -1 (backward)."""
    env = resolve_environment_conditions(lat, lon, t)

    # current: "direction current flows TOWARD" -> vector components
    cur_rad = math.radians(env.current_dir_deg)
    cur_u = env.current_speed_ms * math.sin(cur_rad)
    cur_v = env.current_speed_ms * math.cos(cur_rad)

    # wind: convention is "direction wind blows FROM" -> convert to blows-TOWARD, then deflect
    wind_toward_deg = (env.wind_dir_deg + 180) % 360
    deflected = (wind_toward_deg + WIND_DEFLECTION_DEG) % 360
    wind_rad = math.radians(deflected)
    wind_u = WIND_DRIFT_FACTOR * env.wind_speed_ms * math.sin(wind_rad)
    wind_v = WIND_DRIFT_FACTOR * env.wind_speed_ms * math.cos(wind_rad)

    u = cur_u + wind_u
    v = cur_v + wind_v
    bearing, speed = _vector_to_bearing_speed(u, v)

    distance_m = speed * dt_seconds
    if direction < 0:
        bearing = (bearing + 180) % 360  # walk backward along the drift vector

    new_lat, new_lon = _dest_point(lat, lon, bearing, distance_m)
    return new_lat, new_lon, env.source


def run_drift(
    observed_lat: float,
    observed_lon: float,
    observed_time: datetime,
    backward_hours: tuple = (6, 12, 18, 24),
    forward_hours: tuple = (6, 12, 24, 48),
    step_minutes: int = 30,
) -> DriftResult:
    """Integrate the drift model backward (hindcast) and forward (forecast)
    from the observed slick centroid, sampling checkpoints at the requested
    hour offsets."""
    dt = step_minutes * 60
    notes = [
        "Simplified advection model: current + 3% wind (15deg deflection). "
        "No spreading/diffusion term - centroid trajectory only.",
    ]

    def integrate(direction: int, target_hours: tuple) -> tuple[list, str]:
        points = []
        lat, lon, t = observed_lat, observed_lon, observed_time
        sources = set()
        max_h = max(target_hours)
        elapsed_s = 0
        target_set = sorted(target_hours)
        next_idx = 0
        total_steps = int((max_h * 3600) / dt)
        for i in range(1, total_steps + 1):
            lat, lon, src = _step_position(lat, lon, t, dt, direction)
            sources.add(src)
            t = t + timedelta(seconds=direction * dt)
            elapsed_s += dt
            elapsed_h = elapsed_s / 3600.0
            if next_idx < len(target_set) and elapsed_h + 1e-6 >= target_set[next_idx]:
                h = target_set[next_idx]
                signed_h = direction * h
                unc = BASE_UNCERTAINTY_KM + GROWTH_KM_PER_HOUR * h
                label = f"T{'+' if direction > 0 else '-'}{h}h"
                points.append(TrackPoint(
                    t_offset_hours=signed_h,
                    timestamp=t.isoformat(),
                    lat=round(lat, 5),
                    lon=round(lon, 5),
                    uncertainty_km=round(unc, 2),
                    label=label,
                ))
                next_idx += 1
        src_label = sources.pop() if len(sources) == 1 else "MIXED"
        return points, src_label

    backward_points, back_src = integrate(-1, backward_hours)
    backward_points.sort(key=lambda p: p.t_offset_hours)  # oldest first
    forward_points, fwd_src = integrate(+1, forward_hours)
    forward_points.sort(key=lambda p: p.t_offset_hours)

    observed_point = TrackPoint(
        t_offset_hours=0.0,
        timestamp=observed_time.isoformat(),
        lat=round(observed_lat, 5),
        lon=round(observed_lon, 5),
        uncertainty_km=BASE_UNCERTAINTY_KM,
        label="OBSERVED",
    )

    # probable origin = oldest backward checkpoint (furthest hindcast point)
    if backward_points:
        origin = backward_points[0]
        # heuristic confidence: shrinks with hindcast horizon length
        horizon = abs(origin.t_offset_hours)
        if horizon <= 12:
            origin_confidence = "medium"
        elif horizon <= 24:
            origin_confidence = "low"
        else:
            origin_confidence = "low"
        origin = TrackPoint(**{**origin.__dict__, "label": "PROBABLE_ORIGIN"})
    else:
        origin = observed_point
        origin_confidence = "low"

    return DriftResult(
        observed=observed_point,
        backward_track=backward_points,
        forward_track=forward_points,
        probable_origin=origin,
        origin_confidence=origin_confidence,
        environment_source=back_src,
        notes=notes,
    )
