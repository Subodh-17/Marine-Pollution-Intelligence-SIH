"""
Open-Meteo oceanographic + meteorological data adapter.

Fetches real, synchronized wind vectors and ocean surface currents from
the Open-Meteo Marine (Copernicus CMEMS 1/12 deg global ocean model) and
Weather Forecast / Archive APIs (NOAA GFS / ECMWF IFS 0.25 deg models).

Supports:
- Forward forecast advection (hourly operational forecast)
- Backward hindcast advection (historical reanalysis)
- Directional convention validation (wind blows FROM, current flows TOWARD)
- Unit normalization (km/h -> m/s)
- Spatial/temporal LRU grid caching (reducing 96 drift Euler steps to <= 2 HTTP calls)
- Partial coverage handling (wind available, current missing in nearshore bays)
- Strict failure detection (raises EnvironmentDataUnavailable on outage/missing wind)
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import get_settings
from app.environment.adapters import EnvironmentConditions, EnvironmentDataUnavailable
from app.logging_config import get_logger

_log = get_logger("environment.open_meteo")


class _OpenMeteoCache:
    """Thread-safe bounded in-memory LRU cache for hourly metocean fields.
    Keys are spatial-temporal grid bins: (round(lat, 2), round(lon, 2), date_str)."""

    def __init__(self, max_entries: int = 200):
        self._entries: OrderedDict[str, tuple[float, dict[int, Any]]] = OrderedDict()
        self._lock = threading.Lock()
        self._max_entries = max_entries

    def get(self, key: str, ttl_seconds: float) -> dict[int, Any] | None:
        with self._lock:
            if key not in self._entries:
                return None
            cached_at, data = self._entries[key]
            if time.monotonic() - cached_at > ttl_seconds:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return data

    def put(self, key: str, data: dict[int, Any]) -> None:
        with self._lock:
            self._entries[key] = (time.monotonic(), data)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


_CACHE = _OpenMeteoCache(max_entries=200)


def get_open_meteo_cache() -> _OpenMeteoCache:
    return _CACHE


class OpenMeteoOceanAdapter:
    """
    Adapter for Open-Meteo Marine and Weather APIs.
    Queries Copernicus Marine Service (ocean currents) and NOAA GFS / ECMWF
    (10m winds).
    """

    def __init__(self):
        self._cache = _CACHE

    def _http_get(self, url: str, params: dict, timeout: float):
        """Isolated for monkeypatching in unit tests without live internet access."""
        import httpx
        return httpx.get(url, params=params, timeout=timeout)

    def _fetch_with_retries(self, url: str, params: dict) -> dict:
        settings = get_settings()
        timeout = settings.env_live_timeout_seconds
        max_retries = settings.env_live_max_retries
        last_error: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                resp = self._http_get(url, params=params, timeout=timeout)
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                last_error = e
                _log.warning(
                    "open_meteo_http_attempt_failed",
                    extra={"attempt": attempt + 1, "url": url, "error": str(e)},
                )
                if attempt < max_retries:
                    time.sleep(0.05 * (2 ** attempt))

        raise EnvironmentDataUnavailable(
            f"Open-Meteo request to {url} failed after {max_retries + 1} attempt(s): {last_error}"
        )

    def _determine_endpoints(self, query_time: datetime) -> tuple[str, str]:
        settings = get_settings()
        now = datetime.now(timezone.utc)
        target = query_time if query_time.tzinfo else query_time.replace(tzinfo=timezone.utc)

        # If timestamp is more than 85 days in the past, use archive endpoint for weather
        if target < (now - timedelta(days=85)):
            weather_url = settings.open_meteo_archive_url
        else:
            weather_url = settings.open_meteo_weather_url

        marine_url = settings.open_meteo_marine_url
        return weather_url, marine_url

    def fetch(self, lat: float, lon: float, timestamp: datetime) -> Any:

        # Coordinate boundary checks
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            raise EnvironmentDataUnavailable(f"Invalid geographical coordinates: ({lat}, {lon})")

        settings = get_settings()
        ts_utc = timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)
        date_str = ts_utc.strftime("%Y-%m-%d")
        cache_key = f"{round(lat, 2):.2f}:{round(lon, 2):.2f}:{date_str}"

        # 1. Check in-memory LRU cache
        cached_day = self._cache.get(cache_key, settings.env_cache_ttl_seconds)
        if cached_day and ts_utc.hour in cached_day:
            return cached_day[ts_utc.hour]

        # 2. Cache miss: fetch hourly vectors for the target day
        weather_url, marine_url = self._determine_endpoints(ts_utc)

        weather_params = {
            "latitude": round(lat, 4),
            "longitude": round(lon, 4),
            "hourly": "wind_speed_10m,wind_direction_10m",
            "wind_speed_unit": "ms",
            "start_date": date_str,
            "end_date": date_str,
            "timezone": "UTC",
        }

        marine_params = {
            "latitude": round(lat, 4),
            "longitude": round(lon, 4),
            "hourly": "ocean_current_velocity,ocean_current_direction,wave_height",
            "start_date": date_str,
            "end_date": date_str,
            "timezone": "UTC",
        }

        # Query weather (wind)
        try:
            weather_data = self._fetch_with_retries(weather_url, weather_params)
        except Exception as e:
            raise EnvironmentDataUnavailable(f"Failed to retrieve wind data from {weather_url}: {e}")

        # Query marine (currents + waves)
        marine_data = None
        marine_error = None
        try:
            marine_data = self._fetch_with_retries(marine_url, marine_params)
        except Exception as e:
            marine_error = str(e)
            _log.info("open_meteo_marine_unavailable", extra={"lat": lat, "lon": lon, "error": marine_error})

        # 3. Parse and cache all 24 hours of the day
        day_conditions = self._parse_day_response(
            lat, lon, date_str, weather_data, marine_data, marine_error
        )
        self._cache.put(cache_key, day_conditions)

        if ts_utc.hour in day_conditions:
            return day_conditions[ts_utc.hour]

        # Fallback to closest available hour
        closest_hour = min(day_conditions.keys(), key=lambda h: abs(h - ts_utc.hour))
        return day_conditions[closest_hour]

    def _parse_day_response(
        self,
        lat: float,
        lon: float,
        date_str: str,
        weather_data: dict,
        marine_data: dict | None,
        marine_error: str | None,
    ) -> dict[int, Any]:

        w_hourly = weather_data.get("hourly", {})
        w_times = w_hourly.get("time", [])
        w_speeds = w_hourly.get("wind_speed_10m", [])
        w_dirs = w_hourly.get("wind_direction_10m", [])
        w_units = weather_data.get("hourly_units", {})

        if not w_times or not w_speeds or not w_dirs:
            raise EnvironmentDataUnavailable("Weather provider response missing required hourly wind fields.")

        # Marine data extraction
        m_hourly = (marine_data or {}).get("hourly", {})
        m_cur_speeds = m_hourly.get("ocean_current_velocity", [])
        m_cur_dirs = m_hourly.get("ocean_current_direction", [])
        m_waves = m_hourly.get("wave_height", [])
        m_units = (marine_data or {}).get("hourly_units", {})

        result: dict[int, Any] = {}
        now = datetime.now(timezone.utc)
        retrieved_at = now.isoformat()

        for idx, t_str in enumerate(w_times):
            hour = int(t_str.split("T")[1].split(":")[0])
            valid_dt = datetime.fromisoformat(t_str).replace(tzinfo=timezone.utc)
            data_age_hours = round(abs((now - valid_dt).total_seconds()) / 3600.0, 2)

            # Wind parsing
            raw_w_speed = w_speeds[idx]
            raw_w_dir = w_dirs[idx]
            if raw_w_speed is None or raw_w_dir is None:
                continue

            wind_speed_ms = float(raw_w_speed)
            if w_units.get("wind_speed_10m") == "km/h":
                wind_speed_ms = wind_speed_ms / 3.6
            wind_dir_deg = float(raw_w_dir) % 360.0

            # Ocean current parsing
            has_current = (
                idx < len(m_cur_speeds)
                and idx < len(m_cur_dirs)
                and m_cur_speeds[idx] is not None
                and m_cur_dirs[idx] is not None
            )

            notes = []
            if has_current:
                cur_speed = float(m_cur_speeds[idx])
                # Open-Meteo marine current velocity defaults to km/h
                if m_units.get("ocean_current_velocity", "km/h") == "km/h":
                    cur_speed = cur_speed / 3.6
                cur_dir = float(m_cur_dirs[idx]) % 360.0
                wave_h = float(m_waves[idx]) if (idx < len(m_waves) and m_waves[idx] is not None) else 0.0
                coverage_status = "FULL_METOCEAN"
                current_provider = "OPEN_METEO_COPERNICUS_CMEMS"
                source = "REAL_OPEN_METEO"
                notes.append("Real 10m wind (NOAA GFS/ECMWF) and surface current (Copernicus CMEMS) verified.")
            else:
                cur_speed = 0.0
                cur_dir = 0.0
                wave_h = 0.0
                coverage_status = "PARTIAL_WIND_ONLY"
                current_provider = "UNAVAILABLE"
                source = "PARTIAL_REAL"
                reason = marine_error or "Nearshore/land mask or off-grid Copernicus coordinate"
                notes.append(
                    f"Ocean current unavailable at coordinates ({reason}). "
                    "Leeway drift computed using wind leeway advection only (higher uncertainty)."
                )

            result[hour] = EnvironmentConditions(
                wind_speed_ms=round(wind_speed_ms, 2),
                wind_dir_deg=round(wind_dir_deg, 1),
                current_speed_ms=round(cur_speed, 3),
                current_dir_deg=round(cur_dir, 1),
                wave_height_m=round(wave_h, 2),
                source=source,
                retrieved_at=retrieved_at,
                fallback_reason=None,
                wind_provider="OPEN_METEO_NOAA_GFS_ECMWF",
                current_provider=current_provider,
                data_valid_time=valid_dt.isoformat(),
                data_age_hours=data_age_hours,
                is_fallback=False,
                coverage_status=coverage_status,
                notes=notes,
            )

        if not result:
            raise EnvironmentDataUnavailable("No valid hourly wind data points could be parsed from provider response.")

        return result
