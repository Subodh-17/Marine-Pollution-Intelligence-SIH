from datetime import datetime, timedelta

import pytest

from app.ais.schema import AISRecord
from app.ais.pipeline import reconstruct_trajectories
from app.scoring.attribution import (
    score_candidate, rank_candidates, DEFAULT_WEIGHTS,
    _speed_consistency_fraction, _data_completeness, _confidence_level,
)


def _rec(mmsi, lat, lon, t, sog=10.0, cog=90.0):
    return AISRecord(mmsi=mmsi, timestamp=t, lat=lat, lon=lon, sog=sog, cog=cog, source="TEST")


def test_weights_sum_to_100():
    assert sum(DEFAULT_WEIGHTS.values()) == 100


def test_close_vessel_scores_higher_than_far_vessel():
    origin_lat, origin_lon = 19.05, 71.20
    origin_time = datetime(2026, 8, 28, 6, 0)

    close_pts = [_rec("close", 19.051, 71.201, origin_time)]
    far_pts = [_rec("far", 20.5, 73.0, origin_time)]

    close_traj = reconstruct_trajectories(close_pts, origin_lat, origin_lon)[0]
    far_traj = reconstruct_trajectories(far_pts, origin_lat, origin_lon)[0]

    close_score = score_candidate(close_traj, origin_lat, origin_lon, origin_time)
    far_score = score_candidate(far_traj, origin_lat, origin_lon, origin_time)

    assert close_score.final_score > far_score.final_score


def test_temporal_mismatch_reduces_score():
    origin_lat, origin_lon = 19.05, 71.20
    origin_time = datetime(2026, 8, 28, 6, 0)

    on_time_pts = [_rec("v1", 19.06, 71.21, origin_time)]
    off_time_pts = [_rec("v2", 19.06, 71.21, origin_time + timedelta(hours=48))]

    on_traj = reconstruct_trajectories(on_time_pts, origin_lat, origin_lon)[0]
    off_traj = reconstruct_trajectories(off_time_pts, origin_lat, origin_lon)[0]

    on_score = score_candidate(on_traj, origin_lat, origin_lon, origin_time)
    off_score = score_candidate(off_traj, origin_lat, origin_lon, origin_time)

    assert on_score.final_score > off_score.final_score


def test_final_score_never_exceeds_100():
    origin_lat, origin_lon = 19.05, 71.20
    origin_time = datetime(2026, 8, 28, 6, 0)
    pts = [_rec("v1", 19.05, 71.20, origin_time, sog=1.0)]
    traj = reconstruct_trajectories(pts, origin_lat, origin_lon)[0]
    score = score_candidate(traj, origin_lat, origin_lon, origin_time)
    assert 0 <= score.final_score <= 100


def test_rank_candidates_sorted_descending():
    origin_lat, origin_lon = 19.05, 71.20
    origin_time = datetime(2026, 8, 28, 6, 0)
    pts = [
        _rec("close", 19.051, 71.201, origin_time),
        _rec("mid", 19.3, 71.5, origin_time),
        _rec("far", 22.0, 75.0, origin_time),
    ]
    trajs = reconstruct_trajectories(pts, origin_lat, origin_lon)
    ranked = rank_candidates(trajs, origin_lat, origin_lon, origin_time)
    scores = [c.final_score for c in ranked]
    assert scores == sorted(scores, reverse=True)


def test_breakdown_components_sum_to_final_score():
    origin_lat, origin_lon = 19.05, 71.20
    origin_time = datetime(2026, 8, 28, 6, 0)
    pts = [_rec("v1", 19.06, 71.21, origin_time)]
    traj = reconstruct_trajectories(pts, origin_lat, origin_lon)[0]
    score = score_candidate(traj, origin_lat, origin_lon, origin_time)
    total_points = sum(b.points for b in score.breakdown)
    assert abs(total_points - score.final_score) < 1e-6


# ---------------------------------------------------------------------------
# SIH requirement D: "speed consistency where available", "data
# completeness"; requirement E: "missing-data warnings", "confidence level".
# ---------------------------------------------------------------------------

def test_all_required_explainable_factors_present_in_breakdown():
    """SIH requirement D lists trajectory intersection, heading consistency
    and speed consistency as explainable factors alongside spatial/temporal;
    confirms every one of them is actually a named, weighted component - not
    just present in prose evidence text."""
    origin_lat, origin_lon = 19.05, 71.20
    origin_time = datetime(2026, 8, 28, 6, 0)
    pts = [_rec("v1", 19.06, 71.21, origin_time)]
    traj = reconstruct_trajectories(pts, origin_lat, origin_lon)[0]
    score = score_candidate(traj, origin_lat, origin_lon, origin_time)
    names = {b.component for b in score.breakdown}
    assert names == {
        "spatial_proximity", "temporal_correlation", "trajectory_consistency",
        "heading_consistency", "speed_consistency", "behavioural_anomalies",
        "ais_continuity", "data_completeness",
    }


def test_speed_consistency_neutral_when_no_sog_data():
    origin_lat, origin_lon = 19.05, 71.20
    origin_time = datetime(2026, 8, 28, 6, 0)
    pts = [AISRecord(mmsi="v1", timestamp=origin_time, lat=19.06, lon=71.21, sog=None, cog=None, source="TEST")]
    traj = reconstruct_trajectories(pts, origin_lat, origin_lon)[0]
    frac, expl = _speed_consistency_fraction(traj)
    assert frac == pytest.approx(0.4)
    assert "no speed" in expl.lower() or "no speed-over-ground" in expl.lower() or "sog" in expl.lower()


def test_speed_consistency_favors_slow_vessel_over_fast_vessel():
    origin_lat, origin_lon = 19.05, 71.20
    origin_time = datetime(2026, 8, 28, 6, 0)
    slow_traj = reconstruct_trajectories(
        [_rec("slow", 19.06, 71.21, origin_time, sog=0.5)], origin_lat, origin_lon
    )[0]
    fast_traj = reconstruct_trajectories(
        [_rec("fast", 19.06, 71.21, origin_time, sog=18.0)], origin_lat, origin_lon
    )[0]
    slow_frac, _ = _speed_consistency_fraction(slow_traj)
    fast_frac, _ = _speed_consistency_fraction(fast_traj)
    assert slow_frac > fast_frac
    assert fast_frac == 0.0  # above the slow-speed threshold -> zero credit


def test_data_completeness_full_vs_missing_fields():
    origin_lat, origin_lon = 19.05, 71.20
    origin_time = datetime(2026, 8, 28, 6, 0)

    complete_pts = [AISRecord(mmsi="v1", timestamp=origin_time, lat=19.06, lon=71.21,
                               sog=10.0, cog=90.0, vessel_name="MV TEST", vessel_type="Tanker",
                               imo="IMO1234567", source="TEST")]
    sparse_pts = [AISRecord(mmsi="v2", timestamp=origin_time, lat=19.06, lon=71.21,
                             sog=None, cog=None, source="TEST")]

    complete_traj = reconstruct_trajectories(complete_pts, origin_lat, origin_lon)[0]
    sparse_traj = reconstruct_trajectories(sparse_pts, origin_lat, origin_lon)[0]

    c_frac, c_pct, c_warn = _data_completeness(complete_traj)
    s_frac, s_pct, s_warn = _data_completeness(sparse_traj)

    assert c_frac == pytest.approx(1.0)
    assert c_pct == pytest.approx(100.0)
    # A single-point track still earns a "short track" warning on its own
    # merits (this is a genuine, separate limitation - see _data_completeness)
    # even when every AIS field on that one point is fully populated, so
    # assert no *field*-completeness warning fired rather than an empty list.
    assert not any("SOG" in w or "COG" in w or "name" in w or "type" in w or "IMO" in w for w in c_warn)

    assert s_frac < c_frac
    assert s_pct < 100.0
    assert len(s_warn) > 0
    assert any("SOG" in w or "speed" in w.lower() for w in s_warn)
    assert any("COG" in w or "course" in w.lower() for w in s_warn)


def test_confidence_level_heuristic_thresholds():
    assert _confidence_level(0.9, 5) == "high"
    assert _confidence_level(0.5, 2) == "medium"
    assert _confidence_level(0.1, 1) == "low"
    assert _confidence_level(0.9, 0) == "low"  # no points at all -> can't be "high"


def test_candidate_exposes_confidence_level_and_data_warnings():
    origin_lat, origin_lon = 19.05, 71.20
    origin_time = datetime(2026, 8, 28, 6, 0)
    sparse_pts = [AISRecord(mmsi="v1", timestamp=origin_time, lat=19.06, lon=71.21,
                             sog=None, cog=None, source="TEST")]
    traj = reconstruct_trajectories(sparse_pts, origin_lat, origin_lon)[0]
    cand = score_candidate(traj, origin_lat, origin_lon, origin_time)
    d = cand.to_dict()
    assert d["confidence_level"] in ("low", "medium", "high")
    assert isinstance(d["data_warnings"], list) and len(d["data_warnings"]) > 0
    assert 0.0 <= d["data_completeness_pct"] <= 100.0


def test_distance_from_detection_flows_through_score_candidate():
    """Distinguishes 'distance from estimated origin' (min_distance_km) from
    'distance from spill detection location' (distance_from_detection_km) -
    None when no detection point is supplied (backward compatible), a real
    (and potentially different) number when it is."""
    origin_lat, origin_lon = 19.05, 71.20
    origin_time = datetime(2026, 8, 28, 6, 0)
    detection_lat, detection_lon = 19.30, 71.50

    pts = [_rec("v1", detection_lat, detection_lon, origin_time)]

    traj_no_detection = reconstruct_trajectories(pts, origin_lat, origin_lon)[0]
    cand_no_detection = score_candidate(traj_no_detection, origin_lat, origin_lon, origin_time)
    assert cand_no_detection.distance_from_detection_km is None

    traj_with_detection = reconstruct_trajectories(pts, origin_lat, origin_lon, detection_lat, detection_lon)[0]
    cand_with_detection = score_candidate(
        traj_with_detection, origin_lat, origin_lon, origin_time, detection_time=origin_time
    )
    assert cand_with_detection.distance_from_detection_km is not None
    assert cand_with_detection.distance_from_detection_km < 1.0
    assert cand_with_detection.distance_from_detection_km != cand_with_detection.min_distance_km
    assert cand_with_detection.time_delta_from_detection_hours == pytest.approx(0.0)
