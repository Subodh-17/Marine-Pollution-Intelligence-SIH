from datetime import datetime, timedelta

from app.ais.schema import AISRecord
from app.ais.pipeline import (
    clean_records, spatial_filter, temporal_filter, reconstruct_trajectories, haversine_km,
)


def _rec(mmsi, lat, lon, t, sog=10.0, cog=90.0):
    return AISRecord(mmsi=mmsi, timestamp=t, lat=lat, lon=lon, sog=sog, cog=cog, source="TEST")


def test_haversine_zero_distance():
    assert haversine_km(19.0, 71.0, 19.0, 71.0) == 0.0


def test_haversine_known_distance_roughly_correct():
    # 1 degree of longitude at the equator is ~111.32 km
    d = haversine_km(0.0, 0.0, 0.0, 1.0)
    assert 110 < d < 112


def test_clean_records_drops_invalid_coordinates_and_dupes():
    t = datetime(2026, 8, 28, 0, 0)
    recs = [
        _rec("1", 19.0, 71.0, t),
        _rec("1", 19.0, 71.0, t),          # exact duplicate (mmsi, timestamp)
        _rec("2", 999.0, 71.0, t),         # invalid latitude
        _rec("3", 19.0, 71.0, t + timedelta(minutes=1)),
    ]
    cleaned = clean_records(recs)
    mmsis = [r.mmsi for r in cleaned]
    assert mmsis.count("1") == 1
    assert "2" not in mmsis
    assert "3" in mmsis


def test_reconstruct_trajectories_omits_detection_distance_by_default():
    """Backward-compatibility: existing 2-positional-arg callers keep
    getting min_distance_to_detection_km=None, unchanged from before this
    field was added (SIH requirement D, 'distance from detection location')."""
    t = datetime(2026, 8, 28, 0, 0)
    recs = [_rec("v1", 19.05, 71.20, t)]
    traj = reconstruct_trajectories(recs, 19.05, 71.20)[0]
    assert traj.min_distance_to_detection_km is None
    assert traj.closest_approach_to_detection_time is None
    d = traj.to_dict()
    assert d["min_distance_to_detection_km"] is None
    assert d["closest_approach_to_detection_time"] is None


def test_reconstruct_trajectories_computes_distinct_detection_distance():
    """The estimated ORIGIN (hindcast) and the raw DETECTION location (SAR
    observation) are genuinely different points; a vessel can be close to
    one and far from the other, and both should be reported separately."""
    t = datetime(2026, 8, 28, 0, 0)
    origin_lat, origin_lon = 19.05, 71.20      # hindcast-estimated origin
    detection_lat, detection_lon = 19.30, 71.50  # raw SAR detection point (different)

    # This vessel sits right at the detection point but far from the origin.
    recs = [_rec("v1", detection_lat, detection_lon, t)]
    traj = reconstruct_trajectories(recs, origin_lat, origin_lon, detection_lat, detection_lon)[0]

    assert traj.min_distance_to_detection_km is not None
    assert traj.min_distance_to_detection_km < 1.0
    assert traj.min_distance_km > 30.0  # far from the origin
    assert traj.min_distance_to_detection_km != traj.min_distance_km
    assert traj.closest_approach_to_detection_time == t


def test_spatial_filter_keeps_only_nearby_vessels():
    t = datetime(2026, 8, 28, 0, 0)
    near = _rec("near", 19.05, 71.20, t)
    far = _rec("far", 25.0, 80.0, t)
    result = spatial_filter([near, far], 19.05, 71.20, radius_km=10)
    assert [r.mmsi for r in result] == ["near"]


def test_temporal_filter_keeps_only_records_in_window():
    origin_t = datetime(2026, 8, 28, 6, 0)
    inside = _rec("in", 19.0, 71.0, origin_t + timedelta(hours=2))
    outside = _rec("out", 19.0, 71.0, origin_t + timedelta(hours=30))
    result = temporal_filter([inside, outside], origin_t - timedelta(hours=12), origin_t + timedelta(hours=12))
    assert [r.mmsi for r in result] == ["in"]


def test_reconstruct_trajectories_computes_min_distance_and_closest_time():
    origin_lat, origin_lon = 19.05, 71.20
    t0 = datetime(2026, 8, 28, 0, 0)
    pts = [
        _rec("v1", 19.20, 71.35, t0),                              # farther
        _rec("v1", 19.051, 71.201, t0 + timedelta(hours=1)),        # very close - closest approach
        _rec("v1", 19.30, 71.40, t0 + timedelta(hours=2)),          # farther again
    ]
    trajs = reconstruct_trajectories(pts, origin_lat, origin_lon)
    assert len(trajs) == 1
    traj = trajs[0]
    assert traj.min_distance_km < 1.0
    assert traj.closest_approach_time == t0 + timedelta(hours=1)


def test_reconstruct_trajectories_detects_gaps():
    t0 = datetime(2026, 8, 28, 0, 0)
    pts = [
        _rec("v1", 19.0, 71.0, t0),
        _rec("v1", 19.0, 71.0, t0 + timedelta(hours=3)),  # 180 min gap
    ]
    trajs = reconstruct_trajectories(pts, 19.0, 71.0)
    assert trajs[0].max_gap_minutes == 180.0


def test_reconstruct_trajectories_flags_physically_impossible_position_jump():
    """A record placing the same MMSI ~500 km away just 2 minutes later
    implies ~8,000 kn - impossible for any surface vessel - and must be
    surfaced as a position_jumps entry rather than silently accepted into
    min_distance_km/closest_approach_time as if it were a real movement."""
    t0 = datetime(2026, 8, 28, 0, 0)
    pts = [
        _rec("v1", 19.0, 71.0, t0),
        _rec("v1", 23.5, 71.0, t0 + timedelta(minutes=2)),  # ~500 km north in 2 min
    ]
    trajs = reconstruct_trajectories(pts, 19.0, 71.0)
    traj = trajs[0]
    assert len(traj.position_jumps) == 1
    jump_time, jump_km, implied_kn = traj.position_jumps[0]
    assert jump_km > 400
    assert implied_kn > 1000
    # The (still real, still reported) points themselves are untouched -
    # this is a flag, not a silent data-dropping/rewriting operation.
    assert len(traj.points) == 2


def test_reconstruct_trajectories_does_not_flag_realistic_speed():
    """A normal ~10 kn vessel speed between pings must never trip the
    physically-impossible-jump detector - only genuinely impossible jumps
    should ever appear in position_jumps."""
    t0 = datetime(2026, 8, 28, 0, 0)
    pts = [
        _rec("v1", 19.0, 71.0, t0),
        _rec("v1", 19.05, 71.0, t0 + timedelta(minutes=20)),  # ~3 nm in 20 min = ~9 kn
    ]
    trajs = reconstruct_trajectories(pts, 19.0, 71.0)
    assert trajs[0].position_jumps == []


def test_position_jump_is_flagged_as_low_severity_data_quality_not_suspicious_behaviour():
    """A physically impossible jump is evidence of a DATA problem, not of
    what the vessel actually did - it must never be scored the same as a
    genuine behavioural anomaly like a real speed/course change."""
    from app.ais.behavior import analyze_behavior

    t0 = datetime(2026, 8, 28, 0, 0)
    pts = [
        _rec("v1", 19.0, 71.0, t0, sog=10.0, cog=90.0),
        _rec("v1", 23.5, 71.0, t0 + timedelta(minutes=2), sog=10.0, cog=90.0),
    ]
    traj = reconstruct_trajectories(pts, 19.0, 71.0)[0]
    flags = analyze_behavior(traj)
    jump_flags = [f for f in flags if f.code == "POSITION_JUMP"]
    assert len(jump_flags) == 1
    assert jump_flags[0].severity == "info"
