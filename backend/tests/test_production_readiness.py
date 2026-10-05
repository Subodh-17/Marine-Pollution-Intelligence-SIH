"""
Production-readiness regression tests (Part 1 / Part 2 of the hardening
pass). These are additive to the existing test_api.py / test_ml_and_real_
satellite.py coverage - they exercise the new config module, health/ready
endpoints, structured error handling, upload size limits, reproducibility
metadata, and the extended DetectionResult interface, without touching or
duplicating any existing test.
"""
from __future__ import annotations

import contextlib
import io
import logging
import os

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.config import get_settings

client = TestClient(app)


@contextlib.contextmanager
def _capture_app_logs(caplog, level=logging.ERROR, logger_name="marine_pollution_intel"):
    """pytest's `caplog` fixture only listens on the true root logger, but
    `app/logging_config.py` deliberately sets `propagate = False` on the
    app's own `marine_pollution_intel` logger - to give it exactly one
    handler (a single-line JSON `StreamHandler`) rather than also
    double-printing through Python's default root handler once something
    else (e.g. uvicorn, or pytest itself) attaches one. That's correct,
    deliberate production behavior, not a bug - see that module's docstring
    - so it is not changed here. But it does mean a record logged via
    `get_logger(...)` (a child of `marine_pollution_intel`) stops at that
    logger and never reaches caplog's handler through propagation alone.

    `caplog.at_level(level, logger=...)` does NOT fix this - it only
    adjusts logger/handler *levels*, it does not attach caplog's handler to
    a different logger (see `_pytest.logging.LogCaptureFixture.at_level`
    source). The actual fix is to attach caplog's own handler directly to
    the app's logger for the duration of the test instead of relying on
    propagation to root at all - `addHandler` works regardless of
    `propagate`, since propagation only governs whether a record continues
    on to ANCESTOR loggers' handlers, not whether it reaches handlers
    already attached to the logger that emitted it. This is the standard,
    non-invasive workaround for a non-propagating custom logger; it changes
    nothing about how the app itself logs.
    """
    app_logger = logging.getLogger(logger_name)
    original_level = app_logger.level
    app_logger.setLevel(level)
    app_logger.addHandler(caplog.handler)
    try:
        yield
    finally:
        app_logger.removeHandler(caplog.handler)
        app_logger.setLevel(original_level)


# =============================================================================
# Config
# =============================================================================

def test_settings_have_safe_defaults_with_no_env_vars():
    get_settings.cache_clear()
    s = get_settings()
    assert s.max_upload_size_mb > 0
    assert s.processing_timeout_seconds > 0
    assert 0.0 <= s.ml_confidence_threshold <= 1.0
    assert s.ais_search_radius_km_default > 0
    assert s.ais_time_window_hours_default > 0
    assert len(s.drift_backward_hours) > 0
    assert len(s.drift_forward_hours) > 0
    assert s.map_tile_provider
    get_settings.cache_clear()


def test_settings_respect_env_var_overrides(monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_SIZE_MB", "5")
    monkeypatch.setenv("AIS_SEARCH_RADIUS_KM_DEFAULT", "77")
    get_settings.cache_clear()
    s = get_settings()
    assert s.max_upload_size_mb == 5.0
    assert s.ais_search_radius_km_default == 77.0
    get_settings.cache_clear()
    monkeypatch.delenv("MAX_UPLOAD_SIZE_MB", raising=False)
    monkeypatch.delenv("AIS_SEARCH_RADIUS_KM_DEFAULT", raising=False)
    get_settings.cache_clear()


def test_settings_ignore_malformed_env_var_and_fall_back_to_default(monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_SIZE_MB", "not-a-number")
    get_settings.cache_clear()
    s = get_settings()
    assert s.max_upload_size_mb == 50.0  # falls back to the documented default
    get_settings.cache_clear()
    monkeypatch.delenv("MAX_UPLOAD_SIZE_MB", raising=False)
    get_settings.cache_clear()


def test_public_config_endpoint_exposes_no_secrets():
    r = client.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    for key in ("max_upload_size_mb", "ml_confidence_threshold", "ais_search_radius_km_default",
                "map_tile_provider", "app_version"):
        assert key in body
    # No credential-shaped keys should ever appear here.
    assert not any("key" in k.lower() or "secret" in k.lower() or "token" in k.lower() for k in body)


# =============================================================================
# Health / readiness
# =============================================================================

def test_root_health_endpoint():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_api_health_endpoint_unchanged_for_frontend_compatibility():
    """frontend/src/api.js calls GET /api/health - must keep working exactly
    as before."""
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_ready_endpoint_reports_ml_availability_separately_from_overall_readiness():
    r = client.get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert "ml_available" in body
    assert isinstance(body["ml_available"], bool)
    assert "checks" in body


def test_response_includes_request_id_header():
    r = client.get("/health")
    assert "x-request-id" in {k.lower() for k in r.headers.keys()}


def test_startup_warns_loudly_when_auth_enabled_with_insecure_default_secret(monkeypatch, caplog):
    """Regression test for a real gap found during audit: AUTH_ENABLED=true
    with the publicly-known placeholder AUTH_SECRET_KEY still in place lets
    anyone forge a valid token, and nothing previously surfaced this. Must
    warn loudly (visible in deploy logs) without crashing the app - same
    "never take down a working demo over an optional-safety concern"
    philosophy as the database startup check right above it in
    app/main.py's lifespan handler."""
    from app.config import get_settings, INSECURE_DEFAULT_AUTH_SECRET_KEY

    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET_KEY", INSECURE_DEFAULT_AUTH_SECRET_KEY)
    get_settings.cache_clear()
    try:
        with _capture_app_logs(caplog):
            with TestClient(app):
                pass
        assert any("insecure_auth_secret_key_in_use" in r.message for r in caplog.records)
    finally:
        get_settings.cache_clear()


def test_startup_does_not_warn_when_auth_enabled_with_a_real_secret(monkeypatch, caplog):
    from app.config import get_settings

    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET_KEY", "a-genuinely-random-production-secret-abc123")
    get_settings.cache_clear()
    try:
        with _capture_app_logs(caplog):
            with TestClient(app):
                pass
        assert not any("insecure_auth_secret_key_in_use" in r.message for r in caplog.records)
    finally:
        get_settings.cache_clear()


def test_startup_does_not_warn_when_auth_disabled_even_with_insecure_default(caplog):
    """The default local-demo configuration (AUTH_ENABLED=false, the
    placeholder secret untouched) must NEVER trigger this warning - it's
    only meaningful once auth is actually turned on."""
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        with _capture_app_logs(caplog):
            with TestClient(app):
                pass
        assert not any("insecure_auth_secret_key_in_use" in r.message for r in caplog.records)
    finally:
        get_settings.cache_clear()


def test_capture_app_logs_helper_attaches_and_detaches_directly(caplog):
    """Regression guard for the fix itself, exercising the REAL
    `_capture_app_logs` helper (not a reimplementation of it) against a
    throwaway logger name rather than `marine_pollution_intel` itself, so
    this is a clean, deterministic test of the helper's own mechanism -
    unaffected by whatever pytest's own `caplog` plugin may have already
    done to the *real* app logger by the time this test runs. (In practice,
    on the pytest version this repo currently resolves to, 9.1.1,
    `_pytest.logging.catching_logs.__enter__()` already auto-attaches its
    handler to any *already-existing* non-propagating logger, and
    `configure_logging()` runs at `app.main` import time - before any
    test's caplog context ever opens - so `marine_pollution_intel` already
    carries pytest's own handler before this test body even starts, which
    would make asserting "not yet attached" on that specific logger both
    false and version-dependent; verified directly while writing this.)

    That auto-attach is explicitly called out as a "not worth the trouble"
    best-effort in pytest's own source: it only attaches to loggers that
    already exist at `__enter__` time. A logger that becomes non-
    propagating *during* the `with` block - the very first app startup in
    a fresh interpreter, for instance - is exactly the gap
    `_capture_app_logs` closes unconditionally and deterministically,
    without depending on pytest's internal bookkeeping or import order at
    all, since it calls `addHandler` itself rather than hoping pytest
    already did.
    """
    probe_logger = logging.getLogger("marine_pollution_intel_test_probe_logger")
    probe_logger.propagate = False  # mirrors the real app logger's own setting
    original_level = probe_logger.level

    assert caplog.handler not in probe_logger.handlers
    with _capture_app_logs(caplog, level=logging.WARNING, logger_name=probe_logger.name):
        assert caplog.handler in probe_logger.handlers
        assert probe_logger.level == logging.WARNING
        # addHandler works regardless of propagate=False - propagate only
        # ever affects whether a record also reaches ANCESTOR loggers'
        # handlers, never whether it reaches handlers already attached
        # directly to the logger that emitted it.
        assert probe_logger.propagate is False
        probe_logger.error("regression_probe_message")
        assert any(r.message == "regression_probe_message" for r in caplog.records)

    # And it cleans up after itself: the next test using `caplog` on this
    # (or any other) logger is never left with a stray handler/level.
    assert caplog.handler not in probe_logger.handlers
    assert probe_logger.level == original_level


# =============================================================================
# Structured error handling (no raw tracebacks ever reach the client)
# =============================================================================

def test_malformed_query_param_returns_structured_422_not_a_traceback():
    # search_radius_km expects a float
    r = client.post("/api/spill/detect", params={"mode": "demo", "search_radius_km": "not-a-number"})
    assert r.status_code == 422
    body = r.json()
    assert "detail" in body
    assert "Traceback" not in body["detail"]
    assert "errors" in body


def test_nonexistent_investigation_returns_404_with_detail():
    r = client.get("/api/spill/does-not-exist")
    assert r.status_code == 404
    assert "detail" in r.json()


# =============================================================================
# Upload size limits
# =============================================================================

def test_oversized_ais_upload_rejected_with_413(monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_SIZE_MB", "0.001")  # ~1 KB cap
    get_settings.cache_clear()
    import app.main as main_module
    main_module.settings = get_settings()
    try:
        big_content = b"mmsi,timestamp,lat,lon\n" + (b"1,2024-01-01T00:00:00,1.0,1.0\n" * 500)
        r = client.post("/api/ais/upload", files={"file": ("big.csv", big_content, "text/csv")})
        assert r.status_code == 413
        assert "maximum allowed size" in r.json()["detail"].lower()
    finally:
        monkeypatch.delenv("MAX_UPLOAD_SIZE_MB", raising=False)
        get_settings.cache_clear()
        main_module.settings = get_settings()


def test_unsupported_ais_extension_rejected_with_400():
    r = client.post("/api/ais/upload", files={"file": ("data.exe", b"not a csv", "application/octet-stream")})
    assert r.status_code == 400
    assert "unsupported" in r.json()["detail"].lower()


def test_unsupported_satellite_extension_rejected_with_400():
    r = client.post(
        "/api/satellite/upload",
        files={"file": ("scene.bmp", b"not really a bmp", "image/bmp")},
    )
    assert r.status_code == 400


def test_corrupt_satellite_image_returns_422_not_500():
    """A file with an image extension but non-image bytes must fail
    gracefully (422, actionable message) instead of a raw 500 traceback."""
    r = client.post(
        "/api/satellite/upload",
        files={"file": ("scene.png", b"this is not a valid png file at all", "image/png")},
    )
    assert r.status_code == 422
    assert "Traceback" not in r.json()["detail"]


# =============================================================================
# Reproducibility metadata
# =============================================================================

def test_investigation_includes_config_used_and_processing_time():
    r = client.post("/api/spill/detect", params={"mode": "demo"})
    assert r.status_code == 200
    body = r.json()
    assert "config_used" in body
    assert body["config_used"]["mode"] == "demo"
    assert "detector_version" in body["config_used"]
    assert "processing_time_ms" in body
    assert body["processing_time_ms"] is None or body["processing_time_ms"] >= 0
    assert "detector_version" in body["satellite"]


def test_demo_investigation_is_deterministic_across_runs():
    """Same synthetic demo scenario -> same detection geometry every time
    (see README.md's Reproducibility section)."""
    r1 = client.post("/api/spill/detect", params={"mode": "demo"}).json()
    r2 = client.post("/api/spill/detect", params={"mode": "demo"}).json()
    assert r1["satellite"]["geometry"] == r2["satellite"]["geometry"]
    assert r1["candidates"][0]["mmsi"] == r2["candidates"][0]["mmsi"]


# =============================================================================
# Extended DetectionResult interface (Part 2)
# =============================================================================

def test_classical_detection_result_has_extended_fields():
    from app.satellite.detection import detect_oil_slick
    from app.satellite.synthetic import generate_synthetic_sar_scene
    from app.satellite.preprocessing import lee_filter, normalize, simple_sea_mask

    image, _ = generate_synthetic_sar_scene()
    despeckled = lee_filter(image, window=7)
    norm = normalize(despeckled)
    sea_mask = simple_sea_mask(norm)
    det = detect_oil_slick(norm, sea_mask)

    assert det.success is True
    assert det.detector_name == "classical_dark_spot"
    assert det.provenance == "CLASSICAL_DARK_SPOT"
    assert det.fallback_used is False
    assert isinstance(det.warnings, list)
    assert det.processing_time_ms is not None and det.processing_time_ms >= 0


def test_ml_unavailable_result_has_ml_unavailable_provenance():
    from app.ml.detector import MLDetectorEngine

    engine = MLDetectorEngine.__new__(MLDetectorEngine)
    engine.available = False
    engine.load_errors = ["PyTorch is not installed."]
    det = engine.detect(np.zeros((8, 8), dtype=np.float32), np.zeros((8, 8), dtype=np.float32))
    assert det.success is False
    assert det.provenance == "ML_UNAVAILABLE"
    assert det.fallback_reason


def test_confusion_matrix_matches_tp_fp_fn_tn():
    from app.evaluation.metrics import binary_mask_metrics, confusion_matrix

    pred = np.array([[True, False], [True, True]])
    gt = np.array([[True, False], [False, True]])
    m = binary_mask_metrics(pred, gt)
    cm = confusion_matrix(m)
    assert cm["matrix"] == [[m.tp, m.fn], [m.fp, m.tn]]
    assert cm["actual_positive"]["predicted_positive"] == m.tp
