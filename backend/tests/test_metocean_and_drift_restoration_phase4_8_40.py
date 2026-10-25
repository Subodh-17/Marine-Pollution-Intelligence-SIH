"""
Phase 4.8.40 — Regression test suite for Metocean Resilience, MET Norway Provider,
and End-to-End Drift Reconstruction Restoration.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest
from app.config import get_settings
from app.drift.model import run_drift, DriftResult
from app.environment.adapters import (
    EnvironmentConditions,
    EnvironmentDataUnavailable,
    resolve_environment_conditions,
)
from app.environment.met_norway import MetNorwayWeatherAdapter, _MetNorwayCache
from app.environment.open_meteo import OpenMeteoOceanAdapter
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


def _sample_met_norway_timeseries(base_dt: datetime, wind_speed: float = 6.8, wind_dir: float = 145.0):
    series = []
    for h in range(-2, 10):
        t_dt = base_dt + timedelta(hours=h)
        series.append({
            "time": t_dt.strftime("%Y-%m-%dT%H:00:00Z"),
            "data": {
                "instant": {
                    "details": {
                        "air_pressure_at_sea_level": 1012.5,
                        "air_temperature": 27.5,
                        "wind_speed": wind_speed,
                        "wind_from_direction": wind_dir,
                    }
                }
            }
        })
    return {"properties": {"timeseries": series}}


class TestMetNorwayAdapter:
    def test_fetch_success_and_provenance(self, monkeypatch):
        now = datetime.now(timezone.utc)
        cache = _MetNorwayCache()
        adapter = MetNorwayWeatherAdapter(cache=cache)

        mock_payload = _sample_met_norway_timeseries(now, wind_speed=7.5, wind_dir=220.0)

        def mock_get(url, params, headers, timeout):
            return _MockResponse(mock_payload, status_code=200)

        monkeypatch.setattr(adapter, "_http_get", mock_get)

        cond = adapter.fetch(19.05, 71.20, now)
        assert isinstance(cond, EnvironmentConditions)
        assert cond.source == "REAL_MET_NORWAY"
        assert cond.wind_provider == "MET_NORWAY_ECMWF"
        assert cond.wind_speed_ms == 7.5
        assert cond.wind_dir_deg == 220.0
        assert cond.coverage_status in ("PARTIAL_WIND_ONLY", "FULL_METOCEAN")
        assert cond.is_fallback is False

    def test_historical_date_rejection_beyond_forecast_horizon(self):
        adapter = MetNorwayWeatherAdapter()
        old_ts = datetime.now(timezone.utc) - timedelta(days=10)
        with pytest.raises(EnvironmentDataUnavailable, match="does not cover historical timestamp"):
            adapter.fetch(19.05, 71.20, old_ts)

    def test_invalid_coordinates_rejected(self):
        adapter = MetNorwayWeatherAdapter()
        now = datetime.now(timezone.utc)
        with pytest.raises(EnvironmentDataUnavailable, match="Invalid geographical coordinates"):
            adapter.fetch(105.0, 71.20, now)

    def test_transient_retry_handling(self, monkeypatch):
        now = datetime.now(timezone.utc)
        cache = _MetNorwayCache()
        adapter = MetNorwayWeatherAdapter(cache=cache)
        calls = []

        mock_payload = _sample_met_norway_timeseries(now, wind_speed=5.0, wind_dir=90.0)

        def mock_get(url, params, headers, timeout):
            calls.append(len(calls) + 1)
            if len(calls) == 1:
                return _MockResponse({"error": "rate limit"}, status_code=429)
            return _MockResponse(mock_payload, status_code=200)

        monkeypatch.setattr(adapter, "_http_get", mock_get)
        monkeypatch.setattr(adapter, "_sleep", lambda s: None)

        cond = adapter.fetch(19.05, 71.20, now)
        assert len(calls) == 2
        assert cond.wind_speed_ms == 5.0


class TestMetoceanFallbackChain:
    def test_open_meteo_429_seamlessly_falls_to_met_norway(self, monkeypatch):
        now = datetime.now(timezone.utc)
        monkeypatch.setenv("ENV_PROVIDER", "open_meteo")
        get_settings.cache_clear()

        # Mock Open-Meteo failing with 429
        om_adapter = OpenMeteoOceanAdapter()

        def mock_om_get(url, params, timeout):
            return _MockResponse({"error": True}, status_code=429)

        monkeypatch.setattr(om_adapter, "_sleep", lambda s: None)
        monkeypatch.setattr(om_adapter, "_http_get", mock_om_get)

        import app.environment.open_meteo as om_mod
        monkeypatch.setattr(om_mod, "OpenMeteoOceanAdapter", lambda: om_adapter)

        # Mock MET Norway succeeding
        mn_adapter = MetNorwayWeatherAdapter(cache=_MetNorwayCache())
        mn_payload = _sample_met_norway_timeseries(now, wind_speed=8.2, wind_dir=180.0)
        monkeypatch.setattr(mn_adapter, "_http_get", lambda *a, **kw: _MockResponse(mn_payload, 200))

        import app.environment.met_norway as mn_mod
        monkeypatch.setattr(mn_mod, "MetNorwayWeatherAdapter", lambda: mn_adapter)

        cond = resolve_environment_conditions(19.05, 71.20, now, allow_synthetic=False, mode="real_satellite")
        assert cond.source == "REAL_MET_NORWAY"
        assert cond.wind_provider == "MET_NORWAY_ECMWF"
        assert cond.wind_speed_ms == 8.2

    def test_all_real_providers_exhausted_refuses_synthetic(self, monkeypatch):
        now = datetime.now(timezone.utc)
        monkeypatch.setenv("ENV_PROVIDER", "open_meteo")
        get_settings.cache_clear()

        # Both providers fail
        om_adapter = OpenMeteoOceanAdapter()
        monkeypatch.setattr(om_adapter, "_sleep", lambda s: None)
        monkeypatch.setattr(om_adapter, "_http_get", lambda u, p, t: _MockResponse({"error": True}, 429))

        import app.environment.open_meteo as om_mod
        monkeypatch.setattr(om_mod, "OpenMeteoOceanAdapter", lambda: om_adapter)

        mn_adapter = MetNorwayWeatherAdapter(cache=_MetNorwayCache())
        monkeypatch.setattr(mn_adapter, "_sleep", lambda s: None)
        monkeypatch.setattr(mn_adapter, "_http_get", lambda *a, **kw: _MockResponse({"error": True}, 500))

        import app.environment.met_norway as mn_mod
        monkeypatch.setattr(mn_mod, "MetNorwayWeatherAdapter", lambda: mn_adapter)

        with pytest.raises(EnvironmentDataUnavailable, match="Operational mode refused synthetic environmental fallback"):
            resolve_environment_conditions(19.05, 71.20, now, allow_synthetic=False, mode="real_satellite")


class TestDriftReconstructionRestoration:
    def test_drift_runs_successfully_with_partial_wind(self, monkeypatch):
        now = datetime.now(timezone.utc)
        mock_env = EnvironmentConditions(
            wind_speed_ms=7.0,
            wind_dir_deg=90.0,
            current_speed_ms=0.0,
            current_dir_deg=0.0,
            wave_height_m=0.0,
            source="REAL_MET_NORWAY",
            retrieved_at=now.isoformat(),
            wind_provider="MET_NORWAY_ECMWF",
            current_provider="UNAVAILABLE",
            coverage_status="PARTIAL_WIND_ONLY",
        )

        import app.drift.model as drift_mod
        monkeypatch.setattr(drift_mod, "resolve_environment_conditions", lambda lat, lon, t, **kw: mock_env)

        res = run_drift(19.05, 71.20, now, backward_hours=(6, 12), forward_hours=(6, 12), allow_synthetic=False, mode="real_satellite")
        assert isinstance(res, DriftResult)
        assert res.drift_status == "DEGRADED_PARTIAL_WIND"
        assert res.probable_origin is not None
        assert len(res.backward_track) == 2
        assert len(res.forward_track) == 2
        # Hindcast walks upwind (wind from 90° -> oil moved toward west -> origin is east)
        assert res.probable_origin.lon > 71.20

    def test_calm_wind_zero_is_valid_while_none_is_missing(self):
        calm = EnvironmentConditions(
            wind_speed_ms=0.0,
            wind_dir_deg=0.0,
            current_speed_ms=0.0,
            current_dir_deg=0.0,
            wave_height_m=0.0,
            source="REAL_OPEN_METEO",
        )
        assert calm.wind_speed_ms is not None
        assert calm.wind_speed_ms == 0.0

        missing = EnvironmentConditions(
            wind_speed_ms=None,
            wind_dir_deg=None,
            current_speed_ms=None,
            current_dir_deg=None,
            wave_height_m=None,
            source="UNAVAILABLE",
        )
        assert missing.wind_speed_ms is None

    def test_drift_intermediate_step_failure_uses_initial_env(self, monkeypatch):
        now = datetime.now(timezone.utc)
        initial_env = EnvironmentConditions(
            wind_speed_ms=5.5,
            wind_dir_deg=270.0,
            current_speed_ms=0.5,
            current_dir_deg=90.0,
            wave_height_m=1.0,
            source="REAL_MET_NORWAY",
            retrieved_at=now.isoformat(),
            wind_provider="MET_NORWAY_ECMWF",
            current_provider="COPERNICUS_CMEMS",
            coverage_status="FULL_METOCEAN",
        )

        call_count = 0
        def mock_resolve(lat, lon, t, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return initial_env
            raise EnvironmentDataUnavailable("Historical step 429")

        import app.drift.model as drift_mod
        monkeypatch.setattr(drift_mod, "resolve_environment_conditions", mock_resolve)

        res = run_drift(19.05, 71.20, now, backward_hours=(6, 12), forward_hours=(6, 12), allow_synthetic=False, mode="real_satellite")
        assert res.drift_status in ("COMPLETED", "DEGRADED_PARTIAL_WIND")
        assert res.probable_origin is not None
        assert len(res.backward_track) == 2
        assert len(res.forward_track) == 2


class TestEndToEndInvestigationRestoration:
    def test_real_satellite_investigation_with_met_norway(self, monkeypatch, tmp_path):
        import io
        from PIL import Image

        now = datetime.now(timezone.utc)
        mock_env = EnvironmentConditions(
            wind_speed_ms=6.0,
            wind_dir_deg=180.0,
            current_speed_ms=0.0,
            current_dir_deg=0.0,
            wave_height_m=0.0,
            source="REAL_MET_NORWAY",
            retrieved_at=now.isoformat(),
            wind_provider="MET_NORWAY_ECMWF",
            current_provider="UNAVAILABLE",
            coverage_status="PARTIAL_WIND_ONLY",
        )

        import app.investigation.orchestrator as orch_mod
        monkeypatch.setattr(orch_mod, "resolve_environment_conditions", lambda lat, lon, t, **kw: mock_env)

        import app.drift.model as drift_mod
        monkeypatch.setattr(drift_mod, "resolve_environment_conditions", lambda lat, lon, t, **kw: mock_env)

        img = Image.new("L", (64, 64), color=200)
        for y in range(25, 40):
            for x in range(25, 40):
                img.putpixel((x, y), 30)
        img_path = str(tmp_path / "test_slick.png")
        img.save(img_path)

        inv = run_investigation(
            mode="real_satellite",
            image_path=img_path,
            detector="classical",
            center_lat=19.05,
            center_lon=71.20,
            observed_time_override=now,
        )

        assert inv.id is not None
        assert inv.mode == "real_satellite"
        assert inv.environment["source"] == "REAL_MET_NORWAY"
        assert inv.environment["wind_provider"] == "MET_NORWAY_ECMWF"
        assert inv.satellite["age_estimate"] is not None
        assert inv.drift["drift_status"] == "DEGRADED_PARTIAL_WIND"
        assert inv.drift["probable_origin"] is not None
        assert inv.data_sources["drift_method"] == "Physics-based simplified advection (wind leeway only; ocean current unavailable)"
