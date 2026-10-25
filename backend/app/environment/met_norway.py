"""
MET Norway (Norwegian Meteorological Institute) Locationforecast 2.0 adapter.

Fetches real, authoritative atmospheric weather fields (ECMWF global numerical
weather prediction model) from api.met.no:
- Global 10m wind speed (m/s) and wind direction (degrees FROM which wind blows)
- Open access under NLOD / Creative Commons Attribution 4.0 International
- Free global operational forecast coverage (today and upcoming days)
- Automatic coordinate validation, boundary checking, and ISO 8601 UTC time alignment
- Thread-safe bounded LRU caching to eliminate duplicate requests during drift integration
- Non-blocking fallback coupling with Copernicus CMEMS ocean currents when available
"""
from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app.config import get_settings
from app.environment.adapters import EnvironmentConditions, EnvironmentDataUnavailable
from app.logging_config import get_logger

_log = get_logger("environment.met_norway")


class _MetNorwayCache:
    """Thread-safe bounded in-memory LRU cache for MET Norway forecasts."""

    def __init__(self, max_entries: int = 200):
        self._entries: OrderedDict[str, tuple[float, list[dict[str, Any]]]] = OrderedDict()
        self._lock = threading.Lock()
        self._max_entries = max_entries

    def get(self, key: str, ttl_seconds: float) -> list[dict[str, Any]] | None:
        with self._lock:
            if key not in self._entries:
                return None
            cached_at, data = self._entries[key]
            if time.monotonic() - cached_at > ttl_seconds:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return data

    def put(self, key: str, data: list[dict[str, Any]]) -> None:
        with self._lock:
            self._entries[key] = (time.monotonic(), data)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


_CACHE = _MetNorwayCache(max_entries=200)


def get_met_norway_cache() -> _MetNorwayCache:
    return _CACHE


def _parse_iso_utc(ts_str: str) -> datetime:
    """Parse ISO 8601 timestamp string from MET Norway into UTC datetime."""
    # Handles strings like '2026-10-25T16:00:00Z'
    if ts_str.endswith("Z"):
        ts_str = ts_str[:-1] + "+00:00"
    dt = datetime.fromisoformat(ts_str)
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class MetNorwayWeatherAdapter:
    """
    Operational real-data weather adapter querying the MET Norway
    Locationforecast 2.0 API.
    """

    def __init__(
        self,
        sleep_fn: Callable[[float], None] | None = None,
        cache: _MetNorwayCache | None = None,
    ):
        self._cache = cache or _CACHE
        self._custom_sleep = sleep_fn

    def _sleep(self, seconds: float) -> None:
        if self._custom_sleep is not None:
            self._custom_sleep(seconds)
        else:
            time.sleep(seconds)

    def _http_get(self, url: str, params: dict, headers: dict, timeout: float):
        """Isolated for monkeypatching in unit tests without internet access."""
        import httpx
        return httpx.get(url, params=params, headers=headers, timeout=timeout)

    def _fetch_timeseries(self, lat: float, lon: float) -> list[dict[str, Any]]:
        settings = get_settings()
        url = settings.met_norway_url
        headers = {"User-Agent": settings.met_norway_user_agent}
        # MET Norway requests coordinates with max 4 decimal digits
        params = {"lat": f"{lat:.4f}", "lon": f"{lon:.4f}"}
        timeout = settings.env_live_timeout_seconds
        max_retries = min(settings.env_live_max_retries, 2)
        last_error: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                resp = self._http_get(url, params=params, headers=headers, timeout=timeout)
                if resp.status_code == 422:
                    raise EnvironmentDataUnavailable(
                        f"MET Norway rejected coordinates ({lat}, {lon}) with 422 Unprocessable Entity."
                    )
                if resp.status_code in (400, 401, 403, 404):
                    raise EnvironmentDataUnavailable(
                        f"MET Norway API returned non-retryable error {resp.status_code}."
                    )
                resp.raise_for_status()
                data = resp.json()
                timeseries = data.get("properties", {}).get("timeseries", [])
                if not timeseries:
                    raise EnvironmentDataUnavailable("MET Norway returned an empty timeseries forecast.")
                return timeseries
            except EnvironmentDataUnavailable:
                raise
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    self._sleep(0.5 * (2 ** attempt))
                    continue

        raise EnvironmentDataUnavailable(
            f"MET Norway API request to {url} failed after {max_retries + 1} attempt(s): {last_error}"
        )

    def fetch(self, lat: float, lon: float, timestamp: datetime) -> EnvironmentConditions:
        # 1. Coordinate boundary validation
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            raise EnvironmentDataUnavailable(f"Invalid geographical coordinates: ({lat}, {lon})")

        settings = get_settings()
        ts_utc = timestamp.astimezone(timezone.utc) if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)
        now_utc = datetime.now(timezone.utc)

        # 2. Historical cutoff validation
        # MET Norway locationforecast only serves the current forecast horizon (~9-10 days forward)
        # plus the most recent observations. Timestamps older than 2 days in the past cannot
        # be truthfully resolved from this forecast feed.
        historical_cutoff = (now_utc - timedelta(days=2)).date()
        if ts_utc.date() < historical_cutoff:
            raise EnvironmentDataUnavailable(
                f"MET Norway locationforecast does not cover historical timestamp ({ts_utc.strftime('%Y-%m-%d')}) "
                f"older than the operational forecast window (cutoff {historical_cutoff})."
            )

        # 3. Spatial grid cache lookup (snapped to 0.05° ~ 5km)
        cache_lat = round(lat, 2)
        cache_lon = round(lon, 2)
        cache_key = f"{cache_lat:.2f}:{cache_lon:.2f}:{ts_utc.strftime('%Y-%m-%d')}"

        timeseries = self._cache.get(cache_key, settings.env_cache_ttl_seconds)
        if timeseries is None:
            timeseries = self._fetch_timeseries(cache_lat, cache_lon)
            self._cache.put(cache_key, timeseries)

        # 4. Find the closest time step in the forecast
        closest_point: dict[str, Any] | None = None
        min_dt_seconds = float("inf")

        for point in timeseries:
            point_time_str = point.get("time")
            if not point_time_str:
                continue
            try:
                point_dt = _parse_iso_utc(point_time_str)
            except Exception:
                continue
            diff = abs((point_dt - ts_utc).total_seconds())
            if diff < min_dt_seconds:
                min_dt_seconds = diff
                closest_point = point

        if closest_point is None:
            raise EnvironmentDataUnavailable("No valid time steps found in MET Norway forecast.")

        # Ensure matched time step is reasonably aligned (within 6 hours)
        max_allowed_delta = 6.0 * 3600.0
        if min_dt_seconds > max_allowed_delta:
            raise EnvironmentDataUnavailable(
                f"Closest MET Norway forecast time step is {min_dt_seconds / 3600.0:.1f}h from requested "
                f"time {ts_utc.isoformat()} (exceeds {max_allowed_delta / 3600.0:.1f}h alignment threshold)."
            )

        # 5. Extract and validate required atmospheric wind variables
        instant = closest_point.get("data", {}).get("instant", {}).get("details", {})
        raw_speed = instant.get("wind_speed")
        raw_dir = instant.get("wind_from_direction")

        if raw_speed is None or raw_dir is None:
            raise EnvironmentDataUnavailable(
                f"MET Norway time step at {closest_point.get('time')} missing required wind fields."
            )

        try:
            wind_speed = float(raw_speed)
            wind_dir = float(raw_dir) % 360.0
        except (ValueError, TypeError) as e:
            raise EnvironmentDataUnavailable(f"MET Norway returned non-numeric wind fields: {e}")

        if not (math.isfinite(wind_speed) and math.isfinite(wind_dir)):
            raise EnvironmentDataUnavailable("MET Norway returned non-finite wind values.")

        if wind_speed < 0 or wind_speed > 100.0:
            raise EnvironmentDataUnavailable(f"MET Norway wind speed ({wind_speed} m/s) out of physical bounds.")

        data_valid_time = closest_point.get("time")
        data_age_hours = round(min_dt_seconds / 3600.0, 2)

        # 6. Optional coupling with Copernicus CMEMS currents from Open-Meteo Marine
        # If Open-Meteo Marine is reachable, include real CMEMS currents.
        # If Open-Meteo Marine is rate-limited or fails, gracefully proceed with wind leeway only.
        current_speed = 0.0
        current_dir = 0.0
        current_provider = "UNAVAILABLE"
        coverage_status = "PARTIAL_WIND_ONLY"
        notes = [
            "Real atmospheric wind (ECMWF global model) retrieved from MET Norway Locationforecast 2.0 API. "
            "Ocean surface currents unavailable; advection driven by wind leeway."
        ]

        try:
            from app.environment.open_meteo import OpenMeteoOceanAdapter, cmems_grid_node
            om = OpenMeteoOceanAdapter()
            grid_lat, grid_lon = cmems_grid_node(lat, lon)
            date_str = ts_utc.strftime("%Y-%m-%d")
            marine_params = {
                "latitude": grid_lat,
                "longitude": grid_lon,
                "hourly": "ocean_current_velocity,ocean_current_direction,wave_height",
                "start_date": date_str,
                "end_date": date_str,
                "timezone": "UTC",
            }
            if settings.open_meteo_api_key:
                marine_params["apikey"] = settings.open_meteo_api_key
            marine_resp = om._fetch_with_retries(settings.open_meteo_marine_url, marine_params)
            hourly_marine = marine_resp.get("hourly", {})
            times = hourly_marine.get("time", [])
            velocities = hourly_marine.get("ocean_current_velocity", [])
            directions = hourly_marine.get("ocean_current_direction", [])
            hour_idx = ts_utc.hour
            if hour_idx < len(velocities) and velocities[hour_idx] is not None:
                current_speed = float(velocities[hour_idx])
                current_dir = float(directions[hour_idx]) % 360.0
                current_provider = "COPERNICUS_CMEMS"
                coverage_status = "FULL_METOCEAN"
                notes = [
                    "Real atmospheric wind (ECMWF) retrieved from MET Norway; "
                    "ocean surface currents retrieved from Copernicus Marine Service (CMEMS 1/12°)."
                ]
        except Exception as e:
            _log.info(
                "met_norway_marine_coupling_skipped",
                extra={"lat": lat, "lon": lon, "reason": str(e)},
            )

        return EnvironmentConditions(
            wind_speed_ms=round(wind_speed, 2),
            wind_dir_deg=round(wind_dir, 1),
            current_speed_ms=round(current_speed, 3),
            current_dir_deg=round(current_dir, 1),
            wave_height_m=0.0,
            source="REAL_MET_NORWAY",
            retrieved_at=now_utc.isoformat(),
            fallback_reason=None,
            wind_provider="MET_NORWAY_ECMWF",
            current_provider=current_provider,
            data_valid_time=data_valid_time,
            data_age_hours=data_age_hours,
            is_fallback=False,
            coverage_status=coverage_status,
            notes=notes,
        )
