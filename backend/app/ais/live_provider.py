"""
Live AIS provider abstraction (Section 3).

`LiveAISProvider` attempts a real HTTP call when `AIS_LIVE_PROVIDER_URL` is
configured. As with the environment provider (app/environment/adapters.py -
read that module's docstring too), this sandboxed build's network egress
does not include any AIS data provider, so this is UNCONFIGURED by default
and has not been validated end-to-end against a real provider's response
schema. What IS real and tested (via a mocked HTTP layer, see
test_live_providers.py): the timeout/retry loop, response normalization
into `AISRecord`, timestamp normalization, duplicate removal, invalid
coordinate filtering, and speed/heading validation - all of the
"live-data hygiene" logic AIS_LIVE_PROVIDER_URL would need regardless of
which real provider ends up behind it.

`resolve_ais_records()` mirrors `resolve_environment_conditions()`'s
fallback-chain pattern: try live, and on any `LiveAISProviderUnavailable`
fall back to whatever the caller passes as the fallback (normally the
synthetic demo fleet - see orchestrator.py's mode="live_ais" branch),
carrying a `fallback_reason` string for the frontend/report.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.ais.schema import AISRecord
from app.config import get_settings
from app.logging_config import get_logger

_log = get_logger("ais.live_provider")


class LiveAISProviderUnavailable(Exception):
    pass


# AIS special/"not available" sentinel values per the ITU-R M.1371 message
# format - a heading of 511 or SOG of 102.3 means "not available", not a
# real measurement, and should be filtered rather than trusted.
_HEADING_NOT_AVAILABLE = 511
_SOG_NOT_AVAILABLE = 102.3


class LiveAISProvider:
    source_tag = "LIVE_AIS"

    def _http_get(self, url: str, params: dict, headers: dict, timeout: float):
        """Isolated so tests can monkeypatch just this call - see
        test_live_providers.py."""
        import httpx
        return httpx.get(url, params=params, headers=headers, timeout=timeout)

    def fetch(self, lat: float, lon: float, radius_km: float, start_time, end_time) -> list[AISRecord]:
        settings = get_settings()
        if not settings.ais_live_provider_url:
            raise LiveAISProviderUnavailable("AIS_LIVE_PROVIDER_URL is not configured.")

        headers = {"Authorization": f"Bearer {settings.ais_live_api_key}"} if settings.ais_live_api_key else {}
        params = {
            "lat": lat, "lon": lon, "radius_km": radius_km,
            "start_time": start_time.isoformat(), "end_time": end_time.isoformat(),
        }
        last_error: Exception | None = None

        for attempt in range(settings.ais_live_max_retries + 1):
            try:
                resp = self._http_get(settings.ais_live_provider_url, params, headers, settings.ais_live_timeout_seconds)
                resp.raise_for_status()
                data = resp.json()
                return self._parse_and_clean(data)
            except LiveAISProviderUnavailable:
                raise
            except Exception as e:
                last_error = e
                _log.warning("ais_live_provider_attempt_failed", extra={"attempt": attempt + 1, "error": str(e)})
                continue

        raise LiveAISProviderUnavailable(
            f"Live AIS provider failed after {settings.ais_live_max_retries + 1} attempt(s): {last_error}"
        )

    def _parse_and_clean(self, data) -> list[AISRecord]:
        """Normalizes a provider response's raw record list into
        `AISRecord`s, applying: timestamp normalization, invalid-coordinate
        filtering, speed/heading validation, and de-duplication (by
        mmsi+timestamp). Never raises on a malformed individual record -
        that record is just dropped (logged at debug-equivalent detail via
        the return count the caller can compare against the raw count)."""
        raw_records = data.get("records") if isinstance(data, dict) else data
        if not isinstance(raw_records, list):
            raise LiveAISProviderUnavailable("Live provider response was not a list of AIS records (or {'records': [...]})")

        seen: set[tuple[str, str]] = set()
        out: list[AISRecord] = []
        for r in raw_records:
            try:
                mmsi = str(r["mmsi"]).strip()
                ts = self._normalize_timestamp(r["timestamp"])
                lat = float(r["lat"])
                lon = float(r["lon"])
            except (KeyError, TypeError, ValueError):
                continue
            if not mmsi or ts is None:
                continue
            if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
                continue
            dedup_key = (mmsi, ts.isoformat())
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            sog = self._safe_float(r.get("sog"))
            if sog is not None and (sog < 0 or sog >= _SOG_NOT_AVAILABLE):
                sog = None
            heading = self._safe_float(r.get("heading"))
            if heading is not None and (heading < 0 or heading >= _HEADING_NOT_AVAILABLE):
                heading = None
            cog = self._safe_float(r.get("cog"))

            out.append(AISRecord(
                mmsi=mmsi, timestamp=ts, lat=lat, lon=lon, sog=sog, cog=cog, heading=heading,
                vessel_name=r.get("vessel_name"), vessel_type=r.get("vessel_type"),
                imo=r.get("imo"), call_sign=r.get("call_sign"), status=r.get("status"),
                source=self.source_tag,
            ))
        return out

    @staticmethod
    def _safe_float(v):
        try:
            return None if v is None else float(v)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _normalize_timestamp(raw) -> datetime | None:
        if isinstance(raw, (int, float)):
            try:
                return datetime.fromtimestamp(raw, tz=timezone.utc).replace(tzinfo=None)
            except (ValueError, OSError):
                return None
        if isinstance(raw, str):
            try:
                return datetime.fromisoformat(raw.replace("Z", ""))
            except ValueError:
                return None
        return None


def resolve_ais_records(lat: float, lon: float, radius_km: float, start_time, end_time,
                         fallback_records: list[AISRecord], fallback_source_tag: str,
                         fallback_reference: str) -> tuple[list[AISRecord], str, str]:
    """Attempts the live provider; on failure, returns `fallback_records`
    (normally the synthetic demo fleet) with a reference string that
    explicitly states live AIS was requested but unavailable and why -
    never silently substitutes synthetic data while claiming it's live.
    Returns (records, source_tag, reference_dataset_description)."""
    try:
        records = LiveAISProvider().fetch(lat, lon, radius_km, start_time, end_time)
        return records, LiveAISProvider.source_tag, (
            f"Live AIS provider ({get_settings().ais_live_provider_url}) - {len(records)} record(s) "
            "retrieved and validated (timestamp-normalized, de-duplicated, invalid-coordinate and "
            "not-available speed/heading values filtered)."
        )
    except LiveAISProviderUnavailable as e:
        return fallback_records, "LIVE_AIS_UNAVAILABLE_FALLBACK_SYNTHETIC", (
            f"Live AIS was requested but unavailable ({e}); fell back to: {fallback_reference}"
        )
