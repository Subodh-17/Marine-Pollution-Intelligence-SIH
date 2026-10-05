from datetime import datetime

from app.drift.model import run_drift, destination_point


def test_destination_point_moves_in_expected_direction():
    lat, lon = destination_point(0.0, 0.0, bearing_deg=0, distance_km=111.32)
    # moving due north ~1 degree of latitude
    assert 0.9 < lat < 1.1
    assert abs(lon) < 0.05


def test_run_drift_produces_ordered_backward_track():
    result = run_drift(19.05, 71.20, datetime(2026, 8, 28, 6, 0, 0))
    hours = [p.t_offset_hours for p in result.backward_track]
    assert hours == sorted(hours)  # oldest (most negative) first
    assert all(h < 0 for h in hours)


def test_run_drift_produces_ordered_forward_track():
    result = run_drift(19.05, 71.20, datetime(2026, 8, 28, 6, 0, 0))
    hours = [p.t_offset_hours for p in result.forward_track]
    assert hours == sorted(hours)
    assert all(h > 0 for h in hours)


def test_uncertainty_grows_with_time_horizon():
    result = run_drift(19.05, 71.20, datetime(2026, 8, 28, 6, 0, 0))
    uncertainties = [p.uncertainty_km for p in result.backward_track]
    assert uncertainties == sorted(uncertainties, reverse=True)  # furthest-back = most uncertain


def test_probable_origin_is_oldest_backward_point():
    result = run_drift(19.05, 71.20, datetime(2026, 8, 28, 6, 0, 0))
    assert result.probable_origin.lat == result.backward_track[0].lat
    assert result.probable_origin.lon == result.backward_track[0].lon


def test_drift_is_deterministic_given_same_inputs():
    r1 = run_drift(19.05, 71.20, datetime(2026, 8, 28, 6, 0, 0))
    r2 = run_drift(19.05, 71.20, datetime(2026, 8, 28, 6, 0, 0))
    assert r1.probable_origin.lat == r2.probable_origin.lat
    assert r1.probable_origin.lon == r2.probable_origin.lon
