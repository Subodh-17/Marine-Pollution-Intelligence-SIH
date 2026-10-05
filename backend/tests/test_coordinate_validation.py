"""
Dedicated coordinate-transformation / geospatial-correctness tests
(upgrade-request Section 5). These lock in behaviour that was already
correct when audited (no lat/lon swap, correct antimeridian wrapping, no
NaN/Inf from the great-circle math even at the poles or over
Earth-circumference-scale distances) so a future change can't silently
regress it, plus the boundary-validation behaviour AIS ingestion already
relies on.
"""
from __future__ import annotations

import math
from datetime import datetime

import pytest

from app.satellite.synthetic import SceneGeoTransform
from app.drift.model import destination_point
from app.ais.pipeline import clean_records
from app.ais.schema import AISRecord


# ---------------------------------------------------------------------------
# SceneGeoTransform: pixel <-> lat/lon
# ---------------------------------------------------------------------------

def test_pixel_to_latlon_does_not_swap_row_col_or_lat_lon():
    """row must map to latitude (north-south) and col to longitude
    (east-west) - a swap here would silently place every detected slick,
    origin estimate, and exported polygon at the wrong location without
    ever raising an error."""
    gt = SceneGeoTransform(origin_lat=20.0, origin_lon=70.0, pixel_size_deg_lat=-0.001, pixel_size_deg_lon=0.001)
    lat, lon = gt.pixel_to_latlon(row=10, col=0)
    # Moving down in row (south, since pixel_size_deg_lat is negative)
    # must change latitude, not longitude.
    assert lat == pytest.approx(20.0 - 0.01)
    assert lon == pytest.approx(70.0)

    lat, lon = gt.pixel_to_latlon(row=0, col=10)
    # Moving right in col (east) must change longitude, not latitude.
    assert lat == pytest.approx(20.0)
    assert lon == pytest.approx(70.0 + 0.01)


def test_pixel_to_latlon_round_trips_through_latlon_to_pixel():
    gt = SceneGeoTransform(origin_lat=19.05, origin_lon=71.20, pixel_size_deg_lat=-0.00036, pixel_size_deg_lon=0.00036)
    for row, col in [(0, 0), (256, 256), (511, 0), (0, 511), (123.5, 87.25)]:
        lat, lon = gt.pixel_to_latlon(row, col)
        row2, col2 = gt.latlon_to_pixel(lat, lon)
        assert row2 == pytest.approx(row, abs=1e-6)
        assert col2 == pytest.approx(col, abs=1e-6)


def test_pixel_to_latlon_stays_within_valid_ranges_for_realistic_scene():
    """A realistic Sentinel-1-scale scene (a few hundred km) must never
    produce an out-of-range lat/lon from an in-bounds pixel."""
    gt = SceneGeoTransform(origin_lat=19.05, origin_lon=71.20, pixel_size_deg_lat=-0.00036, pixel_size_deg_lon=0.00036)
    for row in (0, 511):
        for col in (0, 511):
            lat, lon = gt.pixel_to_latlon(row, col)
            assert -90.0 <= lat <= 90.0
            assert -180.0 <= lon <= 180.0


# ---------------------------------------------------------------------------
# Drift great-circle math: antimeridian wrapping, pole behaviour, NaN/Inf
# ---------------------------------------------------------------------------

def test_destination_point_wraps_correctly_across_the_antimeridian():
    """Starting just west of the dateline and moving east must wrap to a
    negative longitude just past -180, not an out-of-range value like
    180.1 (which would break any consumer that assumes [-180, 180])."""
    lat, lon = destination_point(0.0, 179.9, bearing_deg=90.0, distance_km=50)
    assert -180.0 <= lon <= 180.0
    # 50km east of 179.9E should land just past the dateline, i.e. a
    # longitude close to -180 (not still positive/near +180).
    assert lon < 0


@pytest.mark.parametrize("start_lat", [89.999999, 90.0, -90.0, -89.999999, 0.0])
@pytest.mark.parametrize("distance_km", [0.001, 100, 10_000, 20_015, 40_030])
def test_destination_point_never_produces_nan_or_inf(start_lat, distance_km):
    """Sweeps latitudes at/near both poles and distances up to a full
    Earth circumference - large/extreme inputs a corrupted or
    misconfigured environment-data feed could plausibly produce - and
    confirms the great-circle formula's asin()/atan2() calls never hit a
    domain error or return a non-finite coordinate (task requirement:
    prevent NaN/Inf coordinates and physically impossible trajectory
    jumps)."""
    for bearing in (0, 45, 90, 135, 180, 270):
        lat, lon = destination_point(start_lat, 0.0, bearing_deg=bearing, distance_km=distance_km)
        assert math.isfinite(lat)
        assert math.isfinite(lon)
        assert -90.0 <= lat <= 90.0
        assert -180.0 <= lon <= 180.0


# ---------------------------------------------------------------------------
# AIS ingestion: out-of-range coordinates are rejected, not silently wrapped
# ---------------------------------------------------------------------------

def test_ais_pipeline_rejects_out_of_range_coordinates():
    """An out-of-range value in a real-world AIS feed is far more likely to
    indicate a corrupt/garbled field than a legitimate antimeridian
    crossing, so the pipeline's correct behaviour is to drop the record,
    not guess a wrapped value."""
    t = datetime(2024, 1, 1, 0, 0, 0)
    records = [
        AISRecord(mmsi="111", timestamp=t, lat=19.0, lon=71.0),
        AISRecord(mmsi="222", timestamp=t, lat=95.0, lon=71.0),   # invalid lat
        AISRecord(mmsi="333", timestamp=t, lat=19.0, lon=185.0),  # invalid lon
    ]
    cleaned = clean_records(records)
    assert len(cleaned) == 1
    assert cleaned[0].mmsi == "111"
