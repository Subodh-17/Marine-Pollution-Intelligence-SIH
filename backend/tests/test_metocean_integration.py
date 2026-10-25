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

from datetime import datetime, timedelta, timezone

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
    def __init__(self, json_data: dict, status_code: int = 200, headers: dict | None = None):
        self._json = json_data
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            req = httpx.Request("GET", "https://mock.url")
            resp = httpx.Response(self.status_code, request=req, headers=self.headers)
            raise httpx.HTTPStatusError(f"HTTP Error {self.status_code}", request=req, response=resp)

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


# ===========================================================================
# 5. Rate Limiting, Backoff & Historical Routing Validation (Phase 4.8.23)
# ===========================================================================
class TestRateLimitAndRouting:
    def setup_method(self):
        get_open_meteo_cache().clear()
        get_settings.cache_clear()

    def teardown_method(self):
        get_open_meteo_cache().clear()
        get_settings.cache_clear()

    def test_historical_timestamp_routes_to_archive_endpoint(self, monkeypatch):
        """Past dates (before today) must route to the Historical Archive API."""
        adapter = OpenMeteoOceanAdapter()
        captured_urls = []

        def mock_get(url, params, timeout):
            captured_urls.append(url)
            if "marine" in url:
                return _MockResponse(_sample_marine_json())
            return _MockResponse(_sample_weather_json())

        monkeypatch.setattr(adapter, "_http_get", mock_get)

        past_ts = datetime(2026, 8, 28, 6, 0, tzinfo=timezone.utc)
        cond = adapter.fetch(19.05, 71.20, past_ts)

        settings = get_settings()
        assert cond.source == "REAL_OPEN_METEO"
        assert any(settings.open_meteo_archive_url in u for u in captured_urls)
        assert not any(settings.open_meteo_weather_url in u for u in captured_urls)
        assert any(settings.open_meteo_marine_url in u for u in captured_urls)

    def test_future_and_today_timestamp_routes_to_forecast_endpoint(self, monkeypatch):
        """Future and current dates must route to the Weather Forecast API."""
        from datetime import timedelta
        adapter = OpenMeteoOceanAdapter()
        captured_urls = []

        def mock_get(url, params, timeout):
            captured_urls.append(url)
            if "marine" in url:
                return _MockResponse(_sample_marine_json())
            return _MockResponse(_sample_weather_json())

        monkeypatch.setattr(adapter, "_http_get", mock_get)

        future_ts = datetime.now(timezone.utc) + timedelta(days=2)
        cond = adapter.fetch(19.05, 71.20, future_ts)

        settings = get_settings()
        assert cond.source == "REAL_OPEN_METEO"
        assert any(settings.open_meteo_weather_url in u for u in captured_urls)
        assert not any(settings.open_meteo_archive_url in u for u in captured_urls)

    def test_http_429_respects_retry_after_header(self, monkeypatch):
        """HTTP 429 with Retry-After header sleeps the requested duration and retries."""
        adapter = OpenMeteoOceanAdapter()
        weather_attempts = 0
        marine_attempts = 0
        sleep_durations = []

        def mock_sleep(seconds):
            sleep_durations.append(seconds)

        def mock_get(url, params, timeout):
            nonlocal weather_attempts, marine_attempts
            if "marine" in url:
                marine_attempts += 1
                return _MockResponse(_sample_marine_json())
            weather_attempts += 1
            if weather_attempts == 1:
                # First attempt fails with 429 and Retry-After: 2
                return _MockResponse({"error": True, "reason": "rate limit"}, status_code=429, headers={"Retry-After": "2"})
            # Second attempt succeeds
            return _MockResponse(_sample_weather_json())

        monkeypatch.setattr(adapter, "_sleep", mock_sleep)
        monkeypatch.setattr(adapter, "_http_get", mock_get)

        past_ts = datetime(2026, 8, 28, 6, 0, tzinfo=timezone.utc)
        cond = adapter.fetch(19.05, 71.20, past_ts)

        assert cond.source == "REAL_OPEN_METEO"
        assert weather_attempts == 2
        assert marine_attempts == 1
        assert sleep_durations == [2.0]

    def test_http_429_excessive_retry_after_aborts_immediately(self, monkeypatch):
        """HTTP 429 with excessive Retry-After (>10s) aborts immediately without hanging."""
        adapter = OpenMeteoOceanAdapter()
        sleep_durations = []

        def mock_sleep(seconds):
            sleep_durations.append(seconds)

        def mock_get(url, params, timeout):
            return _MockResponse({"error": True, "reason": "rate limit"}, status_code=429, headers={"Retry-After": "120"})

        monkeypatch.setattr(adapter, "_sleep", mock_sleep)
        monkeypatch.setattr(adapter, "_http_get", mock_get)

        past_ts = datetime(2026, 8, 28, 6, 0, tzinfo=timezone.utc)
        with pytest.raises(EnvironmentDataUnavailable, match="exceeds maximum budget"):
            adapter.fetch(19.05, 71.20, past_ts)

        # No sleeps should have been performed
        assert sleep_durations == []

    def test_http_429_without_retry_after_uses_bounded_backoff(self, monkeypatch):
        """HTTP 429 without Retry-After uses bounded exponential backoff with jitter."""
        # Use deterministic jitter_fn returning the upper bound (1.0)
        adapter = OpenMeteoOceanAdapter(jitter_fn=lambda low, high: high)
        weather_attempts = 0
        marine_attempts = 0
        sleep_durations = []

        def mock_sleep(seconds):
            sleep_durations.append(seconds)

        def mock_get(url, params, timeout):
            nonlocal weather_attempts, marine_attempts
            if "marine" in url:
                marine_attempts += 1
                return _MockResponse(_sample_marine_json())
            weather_attempts += 1
            if weather_attempts == 1:
                return _MockResponse({"error": True, "reason": "rate limit"}, status_code=429)
            return _MockResponse(_sample_weather_json())

        monkeypatch.setattr(adapter, "_sleep", mock_sleep)
        monkeypatch.setattr(adapter, "_http_get", mock_get)

        past_ts = datetime(2026, 8, 28, 6, 0, tzinfo=timezone.utc)
        cond = adapter.fetch(19.05, 71.20, past_ts)

        assert cond.source == "REAL_OPEN_METEO"
        assert weather_attempts == 2
        assert marine_attempts == 1
        assert len(sleep_durations) == 1
        assert sleep_durations == [1.0]

    def test_http_backoff_jitter_bounds(self, monkeypatch):
        """Default jitter function produces delays strictly bounded in [0.5 * base, base]."""
        adapter = OpenMeteoOceanAdapter()
        weather_attempts = 0
        sleep_durations = []

        def mock_sleep(seconds):
            sleep_durations.append(seconds)

        def mock_get(url, params, timeout):
            nonlocal weather_attempts
            if "marine" in url:
                return _MockResponse(_sample_marine_json())
            weather_attempts += 1
            if weather_attempts == 1:
                return _MockResponse({"error": True, "reason": "rate limit"}, status_code=429)
            return _MockResponse(_sample_weather_json())

        monkeypatch.setattr(adapter, "_sleep", mock_sleep)
        monkeypatch.setattr(adapter, "_http_get", mock_get)

        past_ts = datetime(2026, 8, 28, 6, 0, tzinfo=timezone.utc)
        cond = adapter.fetch(19.05, 71.20, past_ts)

        assert cond.source == "REAL_OPEN_METEO"
        assert len(sleep_durations) == 1
        # attempt 0 base is 1.0, jitter range is [0.5, 1.0]
        assert 0.5 <= sleep_durations[0] <= 1.0

    def test_http_429_exhaustion_falls_back_in_demo_mode(self, monkeypatch):
        """When retries are exhausted under 429, demo mode falls back to synthetic with documented provenance."""
        monkeypatch.setenv("ENV_PROVIDER", "open_meteo")
        get_settings.cache_clear()

        adapter = OpenMeteoOceanAdapter()
        sleep_durations = []

        def mock_sleep(seconds):
            sleep_durations.append(seconds)

        def mock_get(url, params, timeout):
            return _MockResponse({"error": True, "reason": "rate limit"}, status_code=429)

        monkeypatch.setattr(adapter, "_sleep", mock_sleep)
        monkeypatch.setattr(adapter, "_http_get", mock_get)

        # Monkeypatch adapter instantiation in adapters.py
        import app.environment.open_meteo as om_mod
        monkeypatch.setattr(om_mod, "OpenMeteoOceanAdapter", lambda: adapter)

        past_ts = datetime(2026, 8, 28, 6, 0, tzinfo=timezone.utc)
        cond = resolve_environment_conditions(19.05, 71.20, past_ts, allow_synthetic=True, mode="demo")

        assert cond.source == "SYNTHETIC_DEMO"
        assert cond.is_fallback is True
        assert cond.coverage_status == "SYNTHETIC"
        assert "OpenMeteoOceanAdapter" in cond.fallback_reason
        assert "429" in cond.fallback_reason

    def test_timezone_aware_timestamp_normalizes_to_utc_date(self, monkeypatch):
        """A timezone-aware timestamp across midnight (e.g. +05:30) must normalize to UTC date."""
        from datetime import timezone as tz, timedelta as td
        adapter = OpenMeteoOceanAdapter()
        captured_params = []

        def mock_get(url, params, timeout):
            captured_params.append(dict(params))
            if "marine" in url:
                return _MockResponse(_sample_marine_json())
            return _MockResponse(_sample_weather_json())

        monkeypatch.setattr(adapter, "_http_get", mock_get)

        # 01:00 AM on Oct 25 at +05:30 is 19:30 PM on Oct 24 UTC
        ist = tz(td(hours=5, minutes=30))
        t_ist = datetime(2026, 10, 25, 1, 0, tzinfo=ist)

        cond = adapter.fetch(19.05, 71.20, t_ist)

        assert cond.source == "REAL_OPEN_METEO"
        # The query to Open-Meteo must request 2026-10-24, NOT 2026-10-25
        assert all(p.get("start_date") == "2026-10-24" for p in captured_params)

    def test_non_retryable_client_error_aborts_immediately(self, monkeypatch):
        """HTTP 400 Client Error aborts immediately on attempt 1 without sleeping or retrying."""
        adapter = OpenMeteoOceanAdapter()
        attempts = 0
        sleep_durations = []

        def mock_sleep(seconds):
            sleep_durations.append(seconds)

        def mock_get(url, params, timeout):
            nonlocal attempts
            attempts += 1
            return _MockResponse({"error": True, "reason": "Bad parameter"}, status_code=400)

        monkeypatch.setattr(adapter, "_sleep", mock_sleep)
        monkeypatch.setattr(adapter, "_http_get", mock_get)

        past_ts = datetime(2026, 8, 28, 6, 0, tzinfo=timezone.utc)
        with pytest.raises(EnvironmentDataUnavailable, match="non-retryable status 400"):
            adapter.fetch(19.05, 71.20, past_ts)

        assert attempts == 1
        assert sleep_durations == []

    @pytest.mark.parametrize("status_code", [401, 403, 404, 405, 409, 422])
    def test_other_non_retryable_client_errors_fail_fast(self, monkeypatch, status_code):
        """HTTP client errors (401, 403, 404, 405, 409, 422) must fail fast without retrying."""
        adapter = OpenMeteoOceanAdapter()
        attempts = 0
        sleep_durations = []

        def mock_sleep(seconds):
            sleep_durations.append(seconds)

        def mock_get(url, params, timeout):
            nonlocal attempts
            attempts += 1
            return _MockResponse({"error": True, "reason": "Client error"}, status_code=status_code)

        monkeypatch.setattr(adapter, "_sleep", mock_sleep)
        monkeypatch.setattr(adapter, "_http_get", mock_get)

        past_ts = datetime(2026, 8, 28, 6, 0, tzinfo=timezone.utc)
        with pytest.raises(EnvironmentDataUnavailable, match=f"non-retryable status {status_code}"):
            adapter.fetch(19.05, 71.20, past_ts)

        assert attempts == 1
        assert sleep_durations == []

    @pytest.mark.parametrize("status_code", [500, 502, 503, 504])
    def test_transient_5xx_server_errors_retry_with_jitter(self, monkeypatch, status_code):
        """Transient 5xx server errors must retry up to max_retries with jittered backoff."""
        adapter = OpenMeteoOceanAdapter(jitter_fn=lambda low, high: high)
        attempts = 0
        sleep_durations = []

        def mock_sleep(seconds):
            sleep_durations.append(seconds)

        def mock_get(url, params, timeout):
            nonlocal attempts
            if "marine" in url:
                return _MockResponse(_sample_marine_json())
            attempts += 1
            if attempts == 1:
                return _MockResponse({"error": True, "reason": "Server error"}, status_code=status_code)
            return _MockResponse(_sample_weather_json())

        monkeypatch.setattr(adapter, "_sleep", mock_sleep)
        monkeypatch.setattr(adapter, "_http_get", mock_get)

        past_ts = datetime(2026, 8, 28, 6, 0, tzinfo=timezone.utc)
        cond = adapter.fetch(19.05, 71.20, past_ts)

        assert cond.source == "REAL_OPEN_METEO"
        assert attempts == 2
        assert len(sleep_durations) == 1
        # attempt 0 for 5xx base is 0.25s
        assert sleep_durations == [0.25]

    def test_two_day_cutoff_routing_boundary(self, monkeypatch):
        """Verify the 2-day historical cutoff: <= 2 days ago uses forecast, > 2 days ago uses archive."""
        from datetime import timedelta
        adapter = OpenMeteoOceanAdapter()
        settings = get_settings()
        now = datetime.now(timezone.utc)

        # 1 day ago (near-term past) -> Forecast API
        one_day_ago = now - timedelta(days=1)
        w_url, m_url = adapter._determine_endpoints(one_day_ago)
        assert w_url == settings.open_meteo_weather_url
        assert m_url == settings.open_meteo_marine_url

        # 2 days ago (cutoff boundary) -> Forecast API
        two_days_ago = now - timedelta(days=2)
        w_url, m_url = adapter._determine_endpoints(two_days_ago)
        assert w_url == settings.open_meteo_weather_url
        assert m_url == settings.open_meteo_marine_url

        # 3 days ago (> 2 days past) -> Archive API
        three_days_ago = now - timedelta(days=3)
        w_url, m_url = adapter._determine_endpoints(three_days_ago)
        assert w_url == settings.open_meteo_archive_url
        assert m_url == settings.open_meteo_marine_url

        # Deep historical (months ago) -> Archive API
        deep_past = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
        w_url, m_url = adapter._determine_endpoints(deep_past)
        assert w_url == settings.open_meteo_archive_url
        assert m_url == settings.open_meteo_marine_url

    def test_retry_after_http_date_format_parsing(self):
        """_parse_retry_after parses RFC 7231 HTTP-date and delta-seconds."""
        from app.environment.open_meteo import _parse_retry_after
        from datetime import timezone as tz, timedelta as td

        assert _parse_retry_after(None) is None
        assert _parse_retry_after("") is None
        assert _parse_retry_after("invalid") is None
        assert _parse_retry_after("5") == 5.0
        assert _parse_retry_after("  2.5  ") == 2.5
        assert _parse_retry_after("-10") == 0.0

        # Future HTTP-date
        future_dt = datetime.now(tz.utc) + td(seconds=4)
        from email.utils import format_datetime
        header_date = format_datetime(future_dt)
        parsed = _parse_retry_after(header_date)
        assert parsed is not None
        assert 1.0 <= parsed <= 6.0


# ===========================================================================
# 6. CMEMS Provider Grid Resolution, Boundaries, and Trajectory Caching
# ===========================================================================
class TestCmemsGridResolutionAndCaching:
    def setup_method(self):
        get_open_meteo_cache().clear()
        get_settings.cache_clear()

    def teardown_method(self):
        get_open_meteo_cache().clear()
        get_settings.cache_clear()

    def test_cmems_grid_node_snapping_and_boundaries(self):
        """
        Copernicus CMEMS 1/12° grid is equi-angular with nodes centered at
        (floor(coord * 12) + 0.5) / 12.
        Points within the same interval [k/12, (k+1)/12) map to the exact same node.
        """
        from app.environment.open_meteo import cmems_grid_node

        # Base coordinate in Arabian Sea
        node_lat, node_lon = cmems_grid_node(19.0388, 71.1960)
        assert node_lat == 19.0417
        assert node_lon == 71.2083

        # Coordinates within the same 1/12° cell [19.0, 19.08333) and [71.16667, 71.25)
        # must map to identical grid node
        n2_lat, n2_lon = cmems_grid_node(19.0100, 71.1700)
        n3_lat, n3_lon = cmems_grid_node(19.0700, 71.2400)
        assert (n2_lat, n2_lon) == (node_lat, node_lon)
        assert (n3_lat, n3_lon) == (node_lat, node_lon)

        # Coordinate crossing the boundary at 19.08333° moves to next cell node (19.1250)
        n4_lat, n4_lon = cmems_grid_node(19.0850, 71.1960)
        assert n4_lat == 19.1250
        assert n4_lon == node_lon

        # Southern hemisphere and western hemisphere coordinate mapping
        s_lat, w_lon = cmems_grid_node(-19.0388, -71.1960)
        assert s_lat == -19.0417
        assert w_lon == -71.2083

    def test_grid_cell_cache_reuse_within_cmems_cell(self, monkeypatch):
        """
        Multiple calls within the same CMEMS 1/12° cell on the same date
        reuse the cached daily vectors, making zero additional HTTP calls.
        """
        adapter = OpenMeteoOceanAdapter()
        http_calls = 0

        def mock_get(url, params, timeout):
            nonlocal http_calls
            http_calls += 1
            if "marine" in url:
                return _MockResponse(_sample_marine_json())
            return _MockResponse(_sample_weather_json())

        monkeypatch.setattr(adapter, "_http_get", mock_get)

        # First call at (19.04, 71.20) at 10:00 UTC -> 2 HTTP calls (weather + marine)
        t1 = datetime(2026, 8, 28, 10, 0, tzinfo=timezone.utc)
        cond1 = adapter.fetch(19.04, 71.20, t1)
        assert http_calls == 2
        assert cond1.source == "REAL_OPEN_METEO"

        # Second call at nearby coordinate (19.06, 71.22) at same hour -> Cache HIT (0 HTTP calls)
        cond2 = adapter.fetch(19.06, 71.22, t1)
        assert http_calls == 2
        assert cond2.source == "REAL_OPEN_METEO"

        # Third call at (19.05, 71.21) at different hour (18:00 UTC) -> Cache HIT (0 HTTP calls)
        t2 = datetime(2026, 8, 28, 18, 0, tzinfo=timezone.utc)
        cond3 = adapter.fetch(19.05, 71.21, t2)
        assert http_calls == 2
        assert cond3.source == "REAL_OPEN_METEO"

    def test_grid_cell_cache_miss_across_cell_boundary(self, monkeypatch):
        """
        Coordinates crossing outside the 1/12° cell boundary into an adjacent cell
        trigger a separate fetch and cache entry under the new grid node key.
        """
        adapter = OpenMeteoOceanAdapter()
        http_calls = 0

        def mock_get(url, params, timeout):
            nonlocal http_calls
            http_calls += 1
            if "marine" in url:
                return _MockResponse(_sample_marine_json())
            return _MockResponse(_sample_weather_json())

        monkeypatch.setattr(adapter, "_http_get", mock_get)

        t1 = datetime(2026, 8, 28, 10, 0, tzinfo=timezone.utc)
        # Cell 1: (19.0417, 71.2083)
        adapter.fetch(19.04, 71.20, t1)
        assert http_calls == 2

        # Cell 2: (19.1250, 71.2083) across boundary at 19.0833°
        adapter.fetch(19.12, 71.20, t1)
        assert http_calls == 4

        cache = get_open_meteo_cache()
        assert len(cache._entries) == 2
        assert "archive:19.0417:71.2083:2026-08-28" in cache._entries
        assert "archive:19.1250:71.2083:2026-08-28" in cache._entries

    def test_cache_keys_segregated_by_utc_date(self, monkeypatch):
        """
        Identical spatial coordinates on different calendar dates must NOT share
        cache entries.
        """
        adapter = OpenMeteoOceanAdapter()
        http_calls = 0

        def mock_get(url, params, timeout):
            nonlocal http_calls
            http_calls += 1
            if "marine" in url:
                return _MockResponse(_sample_marine_json())
            return _MockResponse(_sample_weather_json())

        monkeypatch.setattr(adapter, "_http_get", mock_get)

        # Day 1: 2026-08-28
        t1 = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
        adapter.fetch(19.04, 71.20, t1)
        assert http_calls == 2

        # Day 2: 2026-08-29 (same spatial location, next day) -> Must fetch Day 2
        t2 = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
        adapter.fetch(19.04, 71.20, t2)
        assert http_calls == 4

        cache = get_open_meteo_cache()
        assert len(cache._entries) == 2
        assert "archive:19.0417:71.2083:2026-08-28" in cache._entries
        assert "archive:19.0417:71.2083:2026-08-29" in cache._entries

    def test_cache_keys_segregated_by_endpoint_type(self, monkeypatch):
        """
        Historical Archive and Forecast endpoints must never cross-contaminate
        or share cache entries.
        """
        adapter = OpenMeteoOceanAdapter()
        http_calls = 0

        def mock_get(url, params, timeout):
            nonlocal http_calls
            http_calls += 1
            if "marine" in url:
                return _MockResponse(_sample_marine_json())
            return _MockResponse(_sample_weather_json())

        monkeypatch.setattr(adapter, "_http_get", mock_get)

        # Deep past -> Archive endpoint
        t_hist = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
        adapter.fetch(19.04, 71.20, t_hist)
        assert http_calls == 2

        # Future date -> Forecast endpoint
        now = datetime.now(timezone.utc)
        t_future = now + timedelta(days=2)
        adapter.fetch(19.04, 71.20, t_future)
        assert http_calls == 4

        cache = get_open_meteo_cache()
        assert len(cache._entries) == 2
        hist_key = f"archive:19.0417:71.2083:{t_hist.strftime('%Y-%m-%d')}"
        future_key = f"forecast:19.0417:71.2083:{t_future.strftime('%Y-%m-%d')}"
        assert hist_key in cache._entries
        assert future_key in cache._entries

    def test_drift_reconstruction_bounds_http_calls_and_preserves_provenance(self, monkeypatch):
        """
        Under 144 Euler integration steps across a 72-hour hindcast/forecast window,
        Copernicus CMEMS 1/12° grid caching bounds total external HTTP requests to <= 52
        (an 81.8% reduction from 286 unoptimized calls), while preserving REAL_OPEN_METEO provenance.
        """
        from app.drift.model import run_drift

        adapter = OpenMeteoOceanAdapter()
        http_calls = 0

        def mock_get(url, params, timeout):
            nonlocal http_calls
            http_calls += 1
            if "marine" in url:
                return _MockResponse(_sample_marine_json(current_velocity_kmh=1.8, current_dir=90.0))
            return _MockResponse(_sample_weather_json(wind_speed=8.0, wind_dir=240.0))

        monkeypatch.setattr(adapter, "_http_get", mock_get)
        monkeypatch.setattr("app.drift.model.resolve_environment_conditions", lambda lat, lon, t, allow_synthetic=True, mode=None: adapter.fetch(lat, lon, t))

        t0 = datetime(2026, 8, 28, 10, 0, tzinfo=timezone.utc)
        drift = run_drift(
            observed_lat=19.03876,
            observed_lon=71.19603,
            observed_time=t0,
            backward_hours=(6, 12, 18, 24),
            forward_hours=(6, 12, 24, 48),
            allow_synthetic=False,
            mode="real_satellite",
        )

        assert drift.drift_status == "COMPLETED"
        assert drift.environment_source == "REAL_OPEN_METEO"
        assert drift.probable_origin is not None
        assert len(drift.backward_track) == 4
        assert len(drift.forward_track) == 4
        # Requests bounded to <= 52 (26 unique spatial-temporal CMEMS nodes x 2 endpoints)
        assert http_calls <= 52
