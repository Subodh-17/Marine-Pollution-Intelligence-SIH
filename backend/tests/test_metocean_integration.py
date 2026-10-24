"""
Automated test suite for Real Metocean Data Integration (Phase 4.8.9).

Validates:
- Open-Meteo Marine & Weather API adapter parsing, units normalization, and provenance.
- Meteorological (FROM) vs Oceanographic (TOWARD) directional conventions.
- Physical leeway advection mathematics (Euler stepping, Ekman deflection).
- In-memory grid caching and request reduction.
- HTTP timeouts, backoff retries, and malformed response handling.
- Partial coverage handling (wind present, current unavailable).
- Scientific honesty guards:
  - mode="real_satellite" strictly refuses silent synthetic substitution.
  - mode="demo" clearly identifies synthetic demonstration provenance.
- Investigation orchestrator integrity and API schema compliance.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from app.config import get_settings
from app.drift.model import (
    _step_position,
)
from app.environment.adapters import (
    EnvironmentConditions,
    EnvironmentDataUnavailable,
    resolve_environment_conditions,
)
from app.environment.open_meteo import OpenMeteoOceanAdapter, get_open_meteo_cache
from app.investigation.orchestrator import run_investigation


class _MockResponse:
    def __init__(self, json_data: dict, status_code: int = 200):
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP Error {self.status_code}")

    def json(self):
        return self._json


def _sample_weather_json(wind_speed: float = 10.0, wind_dir: float = 270.0):
    times = [f"2026-08-28T{h:02d}:00" for h in range(24)]
    return {
        "latitude": 19.05,
        "longitude": 71.20,
        "hourly_units": {"wind_speed_10m": "m/s", "wind_direction_10m": "°"},
        "hourly": {
            "time": times,
            "wind_speed_10m": [wind_speed] * 24,
            "wind_direction_10m": [wind_dir] * 24,
        },
    }


def _sample_marine_json(current_velocity_kmh: float = 1.8, current_dir: float = 60.0, wave_height: float = 1.2):
    times = [f"2026-08-28T{h:02d}:00" for h in range(24)]
    return {
        "latitude": 19.05,
        "longitude": 71.20,
        "hourly_units": {
            "ocean_current_velocity": "km/h",
            "ocean_current_direction": "°",
            "wave_height": "m",
        },
        "hourly": {
            "time": times,
            "ocean_current_velocity": [current_velocity_kmh] * 24,
            "ocean_current_direction": [current_dir] * 24,
            "wave_height": [wave_height] * 24,
        },
    }


# ===========================================================================
# 1. OpenMeteo Provider Response & Units Validation
# ===========================================================================
class TestOpenMeteoProvider:
    def setup_method(self):
        get_open_meteo_cache().clear()
        get_settings.cache_clear()

    def teardown_method(self):
        get_open_meteo_cache().clear()
        get_settings.cache_clear()

    def test_full_metocean_parsing_and_provenance(self, monkeypatch):
        adapter = OpenMeteoOceanAdapter()

        def mock_get(url, params, timeout):
            if "marine" in url:
                return _MockResponse(_sample_marine_json(current_velocity_kmh=3.6, current_dir=90.0, wave_height=1.5))
            return _MockResponse(_sample_weather_json(wind_speed=8.0, wind_dir=240.0))

        monkeypatch.setattr(adapter, "_http_get", mock_get)
        t = datetime(2026, 8, 28, 6, 0, tzinfo=timezone.utc)
        cond = adapter.fetch(19.05, 71.20, t)

        assert cond.source == "REAL_OPEN_METEO"
        assert cond.coverage_status == "FULL_METOCEAN"
        assert cond.wind_speed_ms == 8.0
        assert cond.wind_dir_deg == 240.0
        # 3.6 km/h -> 1.0 m/s
        assert abs(cond.current_speed_ms - 1.0) < 1e-3
        assert cond.current_dir_deg == 90.0
        assert cond.wave_height_m == 1.5
        assert cond.wind_provider == "OPEN_METEO_NOAA_GFS_ECMWF"
        assert cond.current_provider == "OPEN_METEO_COPERNICUS_CMEMS"
        assert cond.is_fallback is False
        assert "Copernicus CMEMS" in cond.notes[0]

    def test_partial_coverage_when_current_is_missing(self, monkeypatch):
        adapter = OpenMeteoOceanAdapter()

        def mock_get(url, params, timeout):
            if "marine" in url:
                raise RuntimeError("404 Not Found: Land or off-grid point")
            return _MockResponse(_sample_weather_json(wind_speed=7.5, wind_dir=180.0))

        monkeypatch.setattr(adapter, "_http_get", mock_get)
        t = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
        cond = adapter.fetch(19.05, 71.20, t)

        assert cond.source == "PARTIAL_REAL"
        assert cond.coverage_status == "PARTIAL_WIND_ONLY"
        assert cond.wind_speed_ms == 7.5
        assert cond.wind_dir_deg == 180.0
        assert cond.current_speed_ms == 0.0
        assert cond.current_dir_deg == 0.0
        assert cond.current_provider == "UNAVAILABLE"
        assert any("wind leeway" in n.lower() for n in cond.notes)

    def test_missing_wind_raises_unavailable(self, monkeypatch):
        adapter = OpenMeteoOceanAdapter()

        def mock_get(url, params, timeout):
            # Weather endpoint fails
            raise RuntimeError("503 Service Unavailable")

        monkeypatch.setattr(adapter, "_http_get", mock_get)
        t = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
        with pytest.raises(EnvironmentDataUnavailable, match="Failed to retrieve wind data"):
            adapter.fetch(19.05, 71.20, t)

    def test_malformed_response_handling(self, monkeypatch):
        adapter = OpenMeteoOceanAdapter()

        def mock_get(url, params, timeout):
            return _MockResponse({"hourly": {}})  # empty hourly dict

        monkeypatch.setattr(adapter, "_http_get", mock_get)
        t = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
        with pytest.raises(EnvironmentDataUnavailable, match="missing required hourly wind fields"):
            adapter.fetch(19.05, 71.20, t)

    def test_retries_and_timeout_behavior(self, monkeypatch):
        adapter = OpenMeteoOceanAdapter()
        calls = []

        def mock_get(url, params, timeout):
            calls.append(url)
            raise TimeoutError("Socket timeout connecting to Open-Meteo")

        monkeypatch.setattr(adapter, "_http_get", mock_get)
        t = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
        with pytest.raises(EnvironmentDataUnavailable, match="Open-Meteo request"):
            adapter.fetch(19.05, 71.20, t)

        settings = get_settings()
        expected_calls = settings.env_live_max_retries + 1
        assert len(calls) == expected_calls


# ===========================================================================
# 2. Caching & Request Reduction Validation
# ===========================================================================
class TestGridCaching:
    def setup_method(self):
        get_open_meteo_cache().clear()
        get_settings.cache_clear()

    def teardown_method(self):
        get_open_meteo_cache().clear()
        get_settings.cache_clear()

    def test_cache_hits_reduce_external_http_calls(self, monkeypatch):
        adapter = OpenMeteoOceanAdapter()
        http_calls = 0

        def mock_get(url, params, timeout):
            nonlocal http_calls
            http_calls += 1
            if "marine" in url:
                return _MockResponse(_sample_marine_json())
            return _MockResponse(_sample_weather_json())

        monkeypatch.setattr(adapter, "_http_get", mock_get)

        # Initial call fetches full day (2 HTTP calls: 1 weather + 1 marine)
        t1 = datetime(2026, 8, 28, 6, 0, tzinfo=timezone.utc)
        adapter.fetch(19.05, 71.20, t1)
        assert http_calls == 2

        # Subsequent queries for different hours of the same day and nearby coordinates
        for h in range(7, 18):
            t_next = datetime(2026, 8, 28, h, 0, tzinfo=timezone.utc)
            cond = adapter.fetch(19.051, 71.202, t_next)  # rounds to 19.05, 71.20
            assert cond.source == "REAL_OPEN_METEO"

        # HTTP calls must remain 2 (cache served all remaining 11 requests)
        assert http_calls == 2


# ===========================================================================
# 3. Directional Conventions & Physical Leeway Model Correctness
# ===========================================================================
class TestPhysicalModelCorrectness:
    def test_wind_blows_from_vs_blows_toward_convention(self, monkeypatch):
        """
        Wind convention is 'direction wind blows FROM'.
        E.g. A West wind (270°) blows FROM the West TOWARD the East (90°).
        With Northern Hemisphere Ekman deflection (+15° clockwise),
        the effective wind leeway vector must point toward 105° (East-South-East).
        """
        get_settings.cache_clear()
        # Mock resolve_environment_conditions to return zero current and pure West wind (270°)
        def mock_env(lat, lon, t, allow_synthetic=True, mode=None):
            return EnvironmentConditions(
                wind_speed_ms=10.0,
                wind_dir_deg=270.0,  # FROM West
                current_speed_ms=0.0,
                current_dir_deg=0.0,
                wave_height_m=1.0,
                source="TEST_FIXTURE",
            )

        monkeypatch.setattr("app.drift.model.resolve_environment_conditions", mock_env)

        start_lat, start_lon = 19.0, 71.0
        t0 = datetime(2026, 8, 28, 6, 0, tzinfo=timezone.utc)
        # Advance 1 hour forward (+1)
        new_lat, new_lon, _src = _step_position(start_lat, start_lon, t0, 3600.0, direction=+1)

        # Vector towards 105° means lon must increase (moved East) and lat must decrease slightly (moved South)
        assert new_lon > start_lon
        assert new_lat < start_lat

    def test_current_flows_toward_convention(self, monkeypatch):
        """
        Ocean current convention is 'direction current flows TOWARD'.
        A current towards 090° (East) with 0 wind must transport the slick purely East.
        """
        get_settings.cache_clear()
        def mock_env(lat, lon, t, allow_synthetic=True, mode=None):
            return EnvironmentConditions(
                wind_speed_ms=0.0,
                wind_dir_deg=0.0,
                current_speed_ms=0.5,
                current_dir_deg=90.0,  # TOWARD East
                wave_height_m=0.5,
                source="TEST_FIXTURE",
            )

        monkeypatch.setattr("app.drift.model.resolve_environment_conditions", mock_env)

        start_lat, start_lon = 19.0, 71.0
        t0 = datetime(2026, 8, 28, 6, 0, tzinfo=timezone.utc)
        new_lat, new_lon, _src = _step_position(start_lat, start_lon, t0, 3600.0, direction=+1)

        assert new_lon > start_lon
        assert abs(new_lat - start_lat) < 1e-4  # Purely zonal eastward motion


# ===========================================================================
# 4. Scientific Honesty Guards (Operational Mode vs Demo Mode)
# ===========================================================================
class TestOperationalHonestyGuards:
    def setup_method(self):
        get_open_meteo_cache().clear()
        get_settings.cache_clear()

    def teardown_method(self):
        get_open_meteo_cache().clear()
        get_settings.cache_clear()

    def test_demo_mode_allows_transparent_synthetic_fallback(self, monkeypatch):
        """In mode='demo', when live data is unavailable, synthetic fallback is allowed with explicit provenance."""
        monkeypatch.setenv("ENV_PROVIDER", "synthetic")
        get_settings.cache_clear()

        t0 = datetime(2026, 8, 28, 6, 0, tzinfo=timezone.utc)
        cond = resolve_environment_conditions(19.05, 71.20, t0, allow_synthetic=True, mode="demo")

        assert cond.source == "SYNTHETIC_DEMO"
        assert cond.coverage_status == "SYNTHETIC"
        assert cond.wind_provider == "SYNTHETIC_TRIGONOMETRIC_DEMO"
        assert "NOT a real forecast" in cond.notes[0]

    def test_real_satellite_mode_strictly_refuses_synthetic_fallback(self, monkeypatch):
        """In mode='real_satellite', when live data is unavailable, silent synthetic fallback is REFUSED."""
        monkeypatch.setenv("ENV_PROVIDER", "synthetic")
        get_settings.cache_clear()

        t0 = datetime(2026, 8, 28, 6, 0, tzinfo=timezone.utc)
        with pytest.raises(EnvironmentDataUnavailable, match="Operational mode refused synthetic"):
            resolve_environment_conditions(19.05, 71.20, t0, allow_synthetic=False, mode="real_satellite")

    def test_orchestrator_handles_metocean_failure_honestly_in_real_satellite(self, monkeypatch, tmp_path):
        """
        When an operational satellite upload investigation cannot retrieve real metocean inputs:
        - The slick detection and geometry are preserved.
        - Drift is marked UNAVAILABLE (no phantom synthetic trajectory).
        - Probable origin is None.
        - environment.source is marked UNAVAILABLE.
        - data_sources.drift_method indicates NOT_RUN.
        """
        # Force provider failure
        monkeypatch.setenv("ENV_PROVIDER", "custom_live")
        monkeypatch.delenv("ENV_LIVE_PROVIDER_URL", raising=False)
        get_settings.cache_clear()

        # Create a mock synthetic SAR scene PNG to pass to real_satellite mode
        from PIL import Image
        img = Image.new("L", (100, 100), color=150)
        for y in range(40, 60):
            for x in range(40, 60):
                img.putpixel((x, y), 20)
        test_img_path = str(tmp_path / "test_slick.png")
        img.save(test_img_path)

        inv = run_investigation(
            mode="real_satellite",
            image_path=test_img_path,
            center_lat=19.05,
            center_lon=71.20,
        )

        assert inv.satellite["geometry"] is not None
        assert inv.environment["source"] == "UNAVAILABLE"
        assert inv.environment["coverage_status"] == "UNAVAILABLE"
        assert inv.drift["drift_status"] == "UNAVAILABLE"
        assert inv.drift["probable_origin"] is None
        assert "NOT_RUN" in inv.data_sources["drift_method"]
        assert any("refused synthetic" in n for n in inv.drift["notes"])
