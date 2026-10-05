"""
Deterministic offline demo scenario (Section 20 - Synthetic Demonstration
Mode, mandatory).

Builds a synthetic AIS fleet around a given estimated spill origin
(lat/lon/time), reproducing the five-vessel narrative described in the SIH
brief:

    Vessel A -> passes directly through estimated origin, correct time
                window, trajectory strongly matches (+ a discharge-like
                slow-down at closest approach)
    Vessel B -> passes moderately close, weaker temporal match
    Vessel C -> geographically close to the origin location, but only at a
                time well outside the investigation window
    Vessel D -> far away throughout
    Vessel E -> route passes near the origin but has an AIS transmission
                gap right around closest approach

A number of additional "background" vessels are added far from the origin
and/or outside the time window purely to demonstrate that the spatial /
temporal filtering stage actually removes irrelevant traffic (Sections
14-15) rather than the candidate list being hand-picked.

ALL data produced here is synthetic and is tagged `source="SYNTHETIC_DEMO"`
on every AISRecord. It must never be described as a real AIS extract.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import math
import random

from app.ais.schema import AISRecord
from app.drift.model import destination_point

VESSEL_TYPES = ["Tanker", "Bulk Carrier", "Container Ship", "Cargo", "Tanker"]


def _track(
    mmsi: str,
    name: str,
    vessel_type: str,
    imo: str,
    start_lat: float,
    start_lon: float,
    bearing_deg: float,
    speed_knots: float,
    start_time: datetime,
    n_points: int,
    interval_minutes: int,
    slowdown_index: int | None = None,
    slowdown_knots: float = 2.0,
    gap_index: int | None = None,
    gap_span: int = 0,
    heading_change_index: int | None = None,
    heading_change_deg: float = 0.0,
    jitter_seed: int = 0,
) -> list[AISRecord]:
    rng = random.Random(jitter_seed)
    records = []
    lat, lon = start_lat, start_lon
    cur_bearing = bearing_deg
    cur_speed = speed_knots
    t = start_time
    for i in range(n_points):
        if gap_index is not None and gap_index <= i < gap_index + gap_span:
            # simulate AIS silence: advance position/time but do not emit a record
            distance_km = (cur_speed * 1.852) * (interval_minutes / 60.0)
            lat, lon = destination_point(lat, lon, cur_bearing, distance_km)
            t = t + timedelta(minutes=interval_minutes)
            continue

        if slowdown_index is not None and i == slowdown_index:
            cur_speed = slowdown_knots
        elif slowdown_index is not None and i == slowdown_index + 2:
            cur_speed = speed_knots  # resume

        if heading_change_index is not None and i == heading_change_index:
            cur_bearing = (cur_bearing + heading_change_deg) % 360

        jitter_lat = rng.uniform(-0.002, 0.002)
        jitter_lon = rng.uniform(-0.002, 0.002)

        records.append(AISRecord(
            mmsi=mmsi,
            timestamp=t,
            lat=round(lat + jitter_lat, 5),
            lon=round(lon + jitter_lon, 5),
            sog=round(cur_speed + rng.uniform(-0.3, 0.3), 1),
            cog=round(cur_bearing % 360, 1),
            heading=round(cur_bearing % 360, 1),
            vessel_name=name,
            vessel_type=vessel_type,
            imo=imo,
            call_sign=None,
            status="Under way using engine",
            source="SYNTHETIC_DEMO",
        ))

        distance_km = (cur_speed * 1.852) * (interval_minutes / 60.0)
        lat, lon = destination_point(lat, lon, cur_bearing, distance_km)
        t = t + timedelta(minutes=interval_minutes)
    return records


def build_demo_ais_fleet(origin_lat: float, origin_lon: float, origin_time: datetime) -> list[AISRecord]:
    records: list[AISRecord] = []
    window_start = origin_time - timedelta(hours=15)

    # --- Vessel A: strong candidate ---------------------------------
    # Track laid out to pass through the origin near the midpoint of its
    # journey, at (close to) origin_time, with a discharge-like slowdown.
    a_start = destination_point(origin_lat, origin_lon, 250, 55)  # 55km SW of origin
    a_start_time = origin_time - timedelta(hours=9, minutes=10)
    records += _track(
        mmsi="357123000", name="MV KONKAN STAR", vessel_type="Tanker", imo="9123456",
        start_lat=a_start[0], start_lon=a_start[1], bearing_deg=70, speed_knots=12.5,
        start_time=a_start_time, n_points=40, interval_minutes=18,
        slowdown_index=18, slowdown_knots=1.8, jitter_seed=1,
    )

    # --- Vessel B: moderate candidate, weaker temporal match --------
    b_start = destination_point(origin_lat, origin_lon, 20, 60)
    b_start_time = origin_time - timedelta(hours=13)
    records += _track(
        mmsi="357456000", name="MV SAGAR PRIDE", vessel_type="Bulk Carrier", imo="9234567",
        start_lat=b_start[0], start_lon=b_start[1], bearing_deg=190, speed_knots=11.0,
        start_time=b_start_time, n_points=44, interval_minutes=18,
        heading_change_index=20, heading_change_deg=25, jitter_seed=2,
    )

    # --- Vessel C: geographically close, WRONG time (well outside window) ---
    c_pass_time = origin_time - timedelta(hours=30)  # passed near origin over a day earlier
    c_start = destination_point(origin_lat, origin_lon, 300, 65)
    c_start_time = c_pass_time - timedelta(hours=6)
    records += _track(
        mmsi="357789000", name="MV DEEP HORIZON TRADER", vessel_type="Container Ship", imo="9345678",
        start_lat=c_start[0], start_lon=c_start[1], bearing_deg=95, speed_knots=13.0,
        start_time=c_start_time, n_points=50, interval_minutes=18, jitter_seed=3,
    )

    # --- Vessel D: far away throughout --------------------------------
    d_start = destination_point(origin_lat, origin_lon, 45, 220)
    records += _track(
        mmsi="357999000", name="MV PACIFIC VOYAGER", vessel_type="Cargo", imo="9456789",
        start_lat=d_start[0], start_lon=d_start[1], bearing_deg=160, speed_knots=14.0,
        start_time=window_start, n_points=48, interval_minutes=18, jitter_seed=4,
    )

    # --- Vessel E: near origin, AIS gap right at closest approach -----
    e_start = destination_point(origin_lat, origin_lon, 340, 48)
    e_start_time = origin_time - timedelta(hours=8)
    records += _track(
        mmsi="357222000", name="MV NEPTUNE CARRIER", vessel_type="Tanker", imo="9567890",
        start_lat=e_start[0], start_lon=e_start[1], bearing_deg=140, speed_knots=12.0,
        start_time=e_start_time, n_points=40, interval_minutes=18,
        gap_index=15, gap_span=6, jitter_seed=5,
    )

    # --- Background / irrelevant traffic (should be filtered out) ------
    bg_rng = random.Random(99)
    for i in range(12):
        brg = bg_rng.uniform(0, 360)
        dist = bg_rng.uniform(120, 400)  # well outside typical search radii
        s_lat, s_lon = destination_point(origin_lat, origin_lon, brg, dist)
        start_t = window_start + timedelta(hours=bg_rng.uniform(-20, 40))
        records += _track(
            mmsi=f"357{600 + i:03d}000",
            name=f"MV BACKGROUND {i+1:02d}",
            vessel_type=bg_rng.choice(VESSEL_TYPES),
            imo=f"9{900000 + i}",
            start_lat=s_lat, start_lon=s_lon,
            bearing_deg=bg_rng.uniform(0, 360),
            speed_knots=bg_rng.uniform(9, 16),
            start_time=start_t, n_points=20, interval_minutes=30,
            jitter_seed=100 + i,
        )

    return records
