"""
Tests for the live AIS (app/ais/live_provider.py) and live environment
(app/environment/adapters.py::RealWeatherOceanAdapter) provider scaffolding
(Sections 3-4). No real network access is used or required - the single
`_http_get` method each provider exposes is monkeypatched directly, per
this project's "no internet access, no external APIs" testing constraint.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.config import get_settings


class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


# ---------------------------------------------------------------------------
# Environment live provider
# ---------------------------------------------------------------------------
def test_env_live_provider_not_configured_by_default():
    from app.environment.adapters import RealWeatherOceanAdapter, EnvironmentDataUnavailable
    with pytest.raises(EnvironmentDataUnavailable, match="not configured"):
        RealWeatherOceanAdapter().fetch(19.0, 71.0, datetime(2026, 1, 1))


def test_env_live_provider_success(monkeypatch):
    monkeypatch.setenv("ENV_LIVE_PROVIDER_URL", "https://example-weather.test/v1/conditions")
    get_settings.cache_clear()
    try:
        from app.environment.adapters import RealWeatherOceanAdapter

        def fake_get(self, url, params, headers, timeout):
            return _FakeResponse({
                "wind_speed_ms": 7.5, "wind_dir_deg": 190.0,
                "current_speed_ms": 0.4, "current_dir_deg": 220.0, "wave_height_m": 1.3,
            })

        monkeypatch.setattr(RealWeatherOceanAdapter, "_http_get", fake_get)
        result = RealWeatherOceanAdapter().fetch(19.0, 71.0, datetime(2026, 1, 1))
        assert result.source == "REAL"
        assert result.wind_speed_ms == 7.5
        assert result.retrieved_at
    finally:
        get_settings.cache_clear()


def test_env_live_provider_missing_fields_falls_through(monkeypatch):
    monkeypatch.setenv("ENV_LIVE_PROVIDER_URL", "https://example-weather.test/v1/conditions")
    get_settings.cache_clear()
    try:
        from app.environment.adapters import RealWeatherOceanAdapter, EnvironmentDataUnavailable

        def fake_get(self, url, params, headers, timeout):
            return _FakeResponse({"wind_speed_ms": 7.5})  # missing required fields

        monkeypatch.setattr(RealWeatherOceanAdapter, "_http_get", fake_get)
        with pytest.raises(EnvironmentDataUnavailable, match="missing required field"):
            RealWeatherOceanAdapter().fetch(19.0, 71.0, datetime(2026, 1, 1))
    finally:
        get_settings.cache_clear()


def test_env_live_provider_retries_then_fails(monkeypatch):
    monkeypatch.setenv("ENV_LIVE_PROVIDER_URL", "https://example-weather.test/v1/conditions")
    monkeypatch.setenv("ENV_LIVE_MAX_RETRIES", "2")
    get_settings.cache_clear()
    try:
        from app.environment.adapters import RealWeatherOceanAdapter, EnvironmentDataUnavailable
        calls = []

        def fake_get(self, url, params, headers, timeout):
            calls.append(1)
            raise TimeoutError("simulated timeout")

        monkeypatch.setattr(RealWeatherOceanAdapter, "_http_get", fake_get)
        with pytest.raises(EnvironmentDataUnavailable, match="failed after 3 attempt"):
            RealWeatherOceanAdapter().fetch(19.0, 71.0, datetime(2026, 1, 1))
        assert len(calls) == 3  # initial attempt + 2 retries
    finally:
        get_settings.cache_clear()


def test_resolve_environment_conditions_falls_back_with_reason():
    from app.environment.adapters import resolve_environment_conditions
    result = resolve_environment_conditions(19.0, 71.0, datetime(2026, 1, 1))
    assert result.source == "SYNTHETIC_DEMO"
    assert result.fallback_reason
    assert "not configured" in result.fallback_reason


def test_resolve_environment_conditions_uses_live_when_available(monkeypatch):
    monkeypatch.setenv("ENV_LIVE_PROVIDER_URL", "https://example-weather.test/v1/conditions")
    get_settings.cache_clear()
    try:
        from app.environment.adapters import RealWeatherOceanAdapter, resolve_environment_conditions

        def fake_get(self, url, params, headers, timeout):
            return _FakeResponse({
                "wind_speed_ms": 5.0, "wind_dir_deg": 100.0,
                "current_speed_ms": 0.2, "current_dir_deg": 150.0, "wave_height_m": 0.8,
            })

        monkeypatch.setattr(RealWeatherOceanAdapter, "_http_get", fake_get)
        result = resolve_environment_conditions(19.0, 71.0, datetime(2026, 1, 1))
        assert result.source == "REAL"
        assert result.fallback_reason is None
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Live AIS provider
# ---------------------------------------------------------------------------
def test_ais_live_provider_not_configured_by_default():
    from app.ais.live_provider import LiveAISProvider, LiveAISProviderUnavailable
    with pytest.raises(LiveAISProviderUnavailable, match="not configured"):
        LiveAISProvider().fetch(19.0, 71.0, 50.0, datetime(2026, 1, 1), datetime(2026, 1, 2))


def test_ais_live_provider_parses_and_cleans(monkeypatch):
    monkeypatch.setenv("AIS_LIVE_PROVIDER_URL", "https://example-ais.test/v1/records")
    get_settings.cache_clear()
    try:
        from app.ais.live_provider import LiveAISProvider

        def fake_get(self, url, params, headers, timeout):
            return _FakeResponse({"records": [
                {"mmsi": "123456789", "timestamp": "2026-01-01T00:00:00", "lat": 19.1, "lon": 71.1,
                 "sog": 12.0, "heading": 90.0, "vessel_name": "MV Test"},
                # invalid coordinate - should be dropped
                {"mmsi": "999999999", "timestamp": "2026-01-01T00:05:00", "lat": 999.0, "lon": 71.1},
                # not-available sentinel values - sog/heading should become None, not dropped
                {"mmsi": "222222222", "timestamp": "2026-01-01T00:10:00", "lat": 19.2, "lon": 71.2,
                 "sog": 102.3, "heading": 511},
                # exact duplicate of the first record - should be de-duplicated
                {"mmsi": "123456789", "timestamp": "2026-01-01T00:00:00", "lat": 19.1, "lon": 71.1},
            ]})

        monkeypatch.setattr(LiveAISProvider, "_http_get", fake_get)
        records = LiveAISProvider().fetch(19.0, 71.0, 50.0, datetime(2026, 1, 1), datetime(2026, 1, 2))
        assert len(records) == 2  # invalid-coordinate dropped, duplicate dropped
        by_mmsi = {r.mmsi: r for r in records}
        assert by_mmsi["123456789"].sog == 12.0
        assert by_mmsi["123456789"].source == "LIVE_AIS"
        assert by_mmsi["222222222"].sog is None      # sentinel filtered
        assert by_mmsi["222222222"].heading is None  # sentinel filtered
    finally:
        get_settings.cache_clear()


def test_ais_live_provider_malformed_response(monkeypatch):
    monkeypatch.setenv("AIS_LIVE_PROVIDER_URL", "https://example-ais.test/v1/records")
    get_settings.cache_clear()
    try:
        from app.ais.live_provider import LiveAISProvider, LiveAISProviderUnavailable

        def fake_get(self, url, params, headers, timeout):
            return _FakeResponse({"unexpected": "shape"})

        monkeypatch.setattr(LiveAISProvider, "_http_get", fake_get)
        with pytest.raises(LiveAISProviderUnavailable, match="not a list"):
            LiveAISProvider().fetch(19.0, 71.0, 50.0, datetime(2026, 1, 1), datetime(2026, 1, 2))
    finally:
        get_settings.cache_clear()


def test_resolve_ais_records_falls_back_with_reason():
    from app.ais.live_provider import resolve_ais_records
    from app.ais.schema import AISRecord

    fallback = [AISRecord(mmsi="1", timestamp=datetime(2026, 1, 1), lat=19.0, lon=71.0, source="SYNTHETIC_DEMO")]
    records, tag, reference = resolve_ais_records(
        19.0, 71.0, 50.0, datetime(2026, 1, 1), datetime(2026, 1, 2),
        fallback_records=fallback, fallback_source_tag="SYNTHETIC_DEMO", fallback_reference="synthetic demo fleet",
    )
    assert records is fallback
    assert tag == "LIVE_AIS_UNAVAILABLE_FALLBACK_SYNTHETIC"
    assert "unavailable" in reference


def test_resolve_ais_records_uses_live_when_available(monkeypatch):
    monkeypatch.setenv("AIS_LIVE_PROVIDER_URL", "https://example-ais.test/v1/records")
    get_settings.cache_clear()
    try:
        from app.ais.live_provider import LiveAISProvider, resolve_ais_records

        def fake_get(self, url, params, headers, timeout):
            return _FakeResponse({"records": [
                {"mmsi": "1", "timestamp": "2026-01-01T00:00:00", "lat": 19.0, "lon": 71.0},
            ]})

        monkeypatch.setattr(LiveAISProvider, "_http_get", fake_get)
        records, tag, reference = resolve_ais_records(
            19.0, 71.0, 50.0, datetime(2026, 1, 1), datetime(2026, 1, 2),
            fallback_records=[], fallback_source_tag="SYNTHETIC_DEMO", fallback_reference="synthetic demo fleet",
        )
        assert tag == "LIVE_AIS"
        assert len(records) == 1
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Orchestrator-level: mode="live_ais" end to end (falls back to synthetic,
# since no provider is configured in these tests)
# ---------------------------------------------------------------------------
def test_orchestrator_live_ais_mode_falls_back_cleanly():
    from app.investigation.orchestrator import run_investigation
    inv = run_investigation(mode="live_ais")
    assert inv.ais_summary["ais_source"] == "LIVE_AIS_UNAVAILABLE_FALLBACK_SYNTHETIC"
    assert "unavailable" in inv.ais_summary["reference_dataset"]


def test_orchestrator_live_ais_mode_via_api():
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    r = c.post("/api/spill/detect", params={"mode": "live_ais"})
    assert r.status_code == 200
    body = r.json()
    assert body["data_sources"]["ais"].endswith("LIVE_AIS_UNAVAILABLE_FALLBACK_SYNTHETIC")
