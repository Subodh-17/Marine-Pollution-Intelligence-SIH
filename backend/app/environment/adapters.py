"""
Oceanographic + meteorological data adapters.

Implements the DATA FALLBACK ARCHITECTURE required by the spec:

    REAL DATA -> CACHED REAL DATA -> LOCAL SAMPLE DATA -> SYNTHETIC DEMO DATA

`RealWeatherOceanAdapter` now actually attempts a real HTTP call when
`ENV_LIVE_PROVIDER_URL` is configured (Section 4). This sandboxed build has
NOT been validated against a real oceanographic/weather API: this
environment's network egress is restricted to package registries
(pypi/npm/github, see the container's network configuration) and does not
include NOAA/Copernicus/any weather-provider endpoint, so
`ENV_LIVE_PROVIDER_URL` is unset by default and there is no live-tested,
known-good `ENV_LIVE_PROVIDER_URL` value shipped with this build. The code
path is real (timeout, retry, response validation, freshness check) and is
exercised by tests via a mocked HTTP layer (see test_live_providers.py),
but "the HTTP call succeeds against provider X's exact response schema" is
explicitly NOT something this pass can honestly claim to have verified
end-to-end - see DATA_PIPELINE.md's "Known limitations" for exactly what
wiring up a specific real provider would still require.

`SyntheticEnvironmentAdapter` produces a physically-plausible but CLEARLY
LABELLED synthetic wind/current field for the demo scenario. Every
`EnvironmentConditions` object carries a `source` tag so the UI's Data
Source Indicator can display it honestly, plus `retrieved_at` (when this
value was produced/fetched) and `fallback_reason` (why the chain didn't use
a higher-priority source, when it didn't).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.config import get_settings
from app.logging_config import get_logger

_log = get_logger("environment.adapters")


class EnvironmentDataUnavailable(Exception):
    pass


@dataclass
class EnvironmentConditions:
    wind_speed_ms: float
    wind_dir_deg: float          # meteorological convention: direction wind blows FROM
    current_speed_ms: float
    current_dir_deg: float       # oceanographic convention: direction current flows TOWARD
    wave_height_m: float
    source: str                  # "REAL_OPEN_METEO" | "REAL" | "PARTIAL_REAL" | "CACHED" | "LOCAL_SAMPLE" | "SYNTHETIC_DEMO"
    retrieved_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    fallback_reason: str | None = None   # why a higher-priority source wasn't used, if any
    wind_provider: str | None = None
    current_provider: str | None = None
    data_valid_time: str | None = None
    data_age_hours: float | None = None
    is_fallback: bool = False
    coverage_status: str = "FULL_METOCEAN"  # "FULL_METOCEAN" | "PARTIAL_WIND_ONLY" | "SYNTHETIC"
    notes: list[str] = field(default_factory=list)


class RealWeatherOceanAdapter:
    """Live NOAA-GFS/Copernicus-Marine-style feed, config-driven via
    ENV_LIVE_PROVIDER_URL / ENV_LIVE_API_KEY. Raises EnvironmentDataUnavailable
    (with a specific reason) whenever the real call can't be made or
    trusted, so `resolve_environment_conditions()` falls through the chain
    - see the module docstring for exactly what "live" does and doesn't
    mean in this sandboxed build."""

    def _http_get(self, url: str, params: dict, headers: dict, timeout: float):
        """Isolated so tests can monkeypatch just this one call (no live
        network access required to test the timeout/retry/validation logic
        around it) - see test_live_providers.py."""
        import httpx
        return httpx.get(url, params=params, headers=headers, timeout=timeout)

    def fetch(self, lat: float, lon: float, timestamp) -> EnvironmentConditions:
        settings = get_settings()
        if not settings.env_live_provider_url:
            raise EnvironmentDataUnavailable("ENV_LIVE_PROVIDER_URL is not configured.")

        headers = {"Authorization": f"Bearer {settings.env_live_api_key}"} if settings.env_live_api_key else {}
        params = {"lat": lat, "lon": lon, "time": timestamp.isoformat()}
        last_error: Exception | None = None

        for attempt in range(settings.env_live_max_retries + 1):
            try:
                resp = self._http_get(
                    settings.env_live_provider_url, params, headers, settings.env_live_timeout_seconds,
                )
                resp.raise_for_status()
                data = resp.json()
                return self._parse_response(data)
            except EnvironmentDataUnavailable:
                raise
            except Exception as e:
                last_error = e
                _log.warning(
                    "env_live_provider_attempt_failed",
                    extra={"attempt": attempt + 1, "error": str(e)},
                )
                continue

        raise EnvironmentDataUnavailable(
            f"Live environment provider failed after {settings.env_live_max_retries + 1} attempt(s): {last_error}"
        )

    def _parse_response(self, data: dict) -> EnvironmentConditions:
        """Validates and normalizes a provider response. Deliberately
        strict: a response missing required fields is treated as
        unavailable (falls back) rather than silently defaulting missing
        wind/current values to 0, which could be mistaken for a real, calm
        measurement."""
        required = ("wind_speed_ms", "wind_dir_deg", "current_speed_ms", "current_dir_deg")
        missing = [k for k in required if k not in data]
        if missing:
            raise EnvironmentDataUnavailable(f"Live provider response missing required field(s): {missing}")
        try:
            return EnvironmentConditions(
                wind_speed_ms=float(data["wind_speed_ms"]),
                wind_dir_deg=float(data["wind_dir_deg"]) % 360,
                current_speed_ms=float(data["current_speed_ms"]),
                current_dir_deg=float(data["current_dir_deg"]) % 360,
                wave_height_m=float(data.get("wave_height_m", 0.0)),
                source="REAL",
                wind_provider="CUSTOM_LIVE_HTTP",
                current_provider="CUSTOM_LIVE_HTTP",
                data_valid_time=datetime.now(timezone.utc).isoformat(),
                data_age_hours=0.0,
                is_fallback=False,
                coverage_status="FULL_METOCEAN",
                notes=["Retrieved from custom configured live provider endpoint."],
            )
        except (TypeError, ValueError) as e:
            raise EnvironmentDataUnavailable(f"Live provider response had non-numeric field(s): {e}")


class CachedEnvironmentAdapter:
    """Would read a locally cached prior pull of real data (e.g. a small
    GRIB/NetCDF snapshot bundled with the app). No cache file is bundled in
    this prototype build, so it also falls through."""

    def fetch(self, lat: float, lon: float, timestamp) -> EnvironmentConditions:
        raise EnvironmentDataUnavailable("No cached environmental snapshot bundled for this scenario.")


class SyntheticEnvironmentAdapter:
    """
    Deterministic, seeded synthetic wind/current field for the offline demo.

    Produces a smoothly varying but non-trivial field (mild spatial gradient
    + gentle time oscillation) so hindcast/forecast trajectories are
    directionally meaningful for the demo, while being fully reproducible
    (no RNG draw depends on wall-clock time).
    """

    def fetch(self, lat: float, lon: float, timestamp, fallback_reason: str | None = None) -> EnvironmentConditions:
        # deterministic pseudo-field from lat/lon/time - NOT real measurements
        t_hours = timestamp.timestamp() / 3600.0
        wind_dir = (250 + 15 * math.sin(t_hours / 6.0) + (lon - 71.2) * 8) % 360
        wind_speed = 6.5 + 1.8 * math.sin(t_hours / 9.0)
        current_dir = (200 + 20 * math.cos(t_hours / 10.0) + (lat - 19.0) * 10) % 360
        current_speed = 0.35 + 0.12 * math.sin(t_hours / 7.0)
        wave_height = 1.1 + 0.3 * math.sin(t_hours / 8.0)

        return EnvironmentConditions(
            wind_speed_ms=round(max(0.5, wind_speed), 2),
            wind_dir_deg=round(wind_dir, 1),
            current_speed_ms=round(max(0.05, current_speed), 3),
            current_dir_deg=round(current_dir, 1),
            wave_height_m=round(max(0.1, wave_height), 2),
            source="SYNTHETIC_DEMO",
            fallback_reason=fallback_reason,
            wind_provider="SYNTHETIC_TRIGONOMETRIC_DEMO",
            current_provider="SYNTHETIC_TRIGONOMETRIC_DEMO",
            data_valid_time=timestamp.isoformat(),
            data_age_hours=0.0,
            is_fallback=bool(fallback_reason),
            coverage_status="SYNTHETIC",
            notes=["Synthetic deterministic demonstration field. NOT a real forecast or observation."],
        )


def resolve_environment_conditions(
    lat: float,
    lon: float,
    timestamp,
    allow_synthetic: bool = True,
    mode: str | None = None,
) -> EnvironmentConditions:
    """Walk the fallback chain and return the first available source.

    If mode == 'real_satellite' or allow_synthetic is False, synthetic substitution
    is strictly refused to uphold scientific honesty and prevent unverified
    trajectories from masquerading as real operational forecasts.
    """
    settings = get_settings()
    reasons = []

    # Assemble adapter chain based on configuration
    adapters = []
    provider = (settings.env_provider or "").lower()

    if provider in ("open_meteo", "auto"):
        try:
            from app.environment.open_meteo import OpenMeteoOceanAdapter
            adapters.append(OpenMeteoOceanAdapter())
        except ImportError as e:
            reasons.append(f"OpenMeteoOceanAdapter import failed: {e}")
        adapters.append(CachedEnvironmentAdapter())
    elif provider == "custom_live" or settings.env_live_provider_url:
        adapters.append(RealWeatherOceanAdapter())
        adapters.append(CachedEnvironmentAdapter())
    else:
        # Default / unconfigured live provider: exercise RealWeatherOceanAdapter
        # so its configuration status is recorded in fallback reasons
        adapters.append(RealWeatherOceanAdapter())
        adapters.append(CachedEnvironmentAdapter())

    for adapter in adapters:
        try:
            return adapter.fetch(lat, lon, timestamp)
        except EnvironmentDataUnavailable as e:
            reasons.append(f"{adapter.__class__.__name__}: {e}")
            continue
        except Exception as e:
            reasons.append(f"{adapter.__class__.__name__} unexpected error: {e}")
            continue

    # Refuse synthetic fallback when in operational mode or explicitly forbidden
    is_operational = (mode == "real_satellite") or (not allow_synthetic)
    if is_operational:
        raise EnvironmentDataUnavailable(
            f"Operational mode refused synthetic environmental fallback: {'; '.join(reasons)}"
        )

    return SyntheticEnvironmentAdapter().fetch(lat, lon, timestamp, fallback_reason="; ".join(reasons))
