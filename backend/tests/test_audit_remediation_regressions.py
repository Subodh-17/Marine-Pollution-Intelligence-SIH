"""
Regression tests for Master Audit remediations:
- SEC-01: Re-analyzed AIS and attribution parameters persisted to database
- SEC-02: Per-user AIS upload isolation and safe cleanup
- SEC-03: Investigation listing ownership filtering when auth enabled
- DB-01: Duplicate username raises AuthError cleanly across database engines
- PERF-01: Bounded LRU store for in-memory investigations
- DATA-01: Rehydrated investigation JSON sanitization for NaNs/Infs
"""
from __future__ import annotations

import os
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app import auth as auth_module


@pytest.fixture
def auth_enabled(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-secret-key-for-audit-regression-only")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def client():
    from app.main import app
    return TestClient(app)


# ---------------------------------------------------------------------------
# SEC-01: Persistence across restart for re-analyzed AIS and attribution
# ---------------------------------------------------------------------------
def test_ais_analyze_persists_to_database():
    c = client()
    r = c.post("/api/spill/detect", params={"mode": "demo"})
    assert r.status_code == 200
    inv_id = r.json()["id"]

    # Re-analyze with a distinct search radius (75 km)
    r_reanalyze = c.post(
        "/api/ais/analyze",
        params={"investigation_id": inv_id, "search_radius_km": 75.0, "time_window_hours": 16.0},
    )
    assert r_reanalyze.status_code == 200
    assert r_reanalyze.json()["search_radius_km"] == 75.0

    # Simulate backend restart by clearing the in-memory cache
    from app.investigation.orchestrator import _STORE
    _STORE.clear()

    # Re-fetch from database via /api/spill/{id}
    r_fetched = c.get(f"/api/spill/{inv_id}")
    assert r_fetched.status_code == 200
    fetched_data = r_fetched.json()
    assert fetched_data["ais_summary"]["search_radius_km"] == 75.0
    assert fetched_data["ais_summary"]["time_window_hours"] == 16.0


def test_attribution_analyze_persists_to_database():
    c = client()
    r = c.post("/api/spill/detect", params={"mode": "demo"})
    assert r.status_code == 200
    inv_id = r.json()["id"]

    # Re-score with customized weights
    custom_weights = {
        "spatial_proximity": 50,
        "temporal_correlation": 50,
        "trajectory_consistency": 0,
        "heading_consistency": 0,
        "speed_consistency": 0,
        "behavioural_anomalies": 0,
        "ais_continuity": 0,
        "data_completeness": 0,
    }
    r_rescore = c.post(f"/api/attribution/analyze?investigation_id={inv_id}", json=custom_weights)
    assert r_rescore.status_code == 200

    # Simulate restart
    from app.investigation.orchestrator import _STORE
    _STORE.clear()

    r_fetched = c.get(f"/api/spill/{inv_id}")
    assert r_fetched.status_code == 200
    # Verified that re-scored candidates exist in the rehydrated record
    assert len(r_fetched.json()["candidates"]) == len(r_rescore.json()["candidates"])


# ---------------------------------------------------------------------------
# SEC-02: Per-user AIS upload isolation and safe temp file lifecycle
# ---------------------------------------------------------------------------
def test_multi_user_ais_upload_isolation(auth_enabled):
    c = client()
    # Register and login user A
    r_a = c.post("/api/auth/register", json={"username": "user_a", "password": "password_a"})
    token_a = r_a.json()["access_token"]
    headers_a = {"Authorization": f"Bearer {token_a}"}

    # Register and login user B
    r_b = c.post("/api/auth/register", json={"username": "user_b", "password": "password_b"})
    token_b = r_b.json()["access_token"]
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # User A uploads a valid AIS CSV
    content_a = b"mmsi,timestamp,lat,lon\n111111111,2026-08-28T06:00:00,19.05,71.20\n"
    r_up_a = c.post("/api/ais/upload", files={"file": ("fleet_a.csv", content_a, "text/csv")}, headers=headers_a)
    assert r_up_a.status_code == 200

    from app.main import _USER_UPLOADED_AIS
    path_a = _USER_UPLOADED_AIS["user_a"]["path"]
    assert os.path.exists(path_a)

    # User B has NOT uploaded an AIS file yet. Trying to run mode=custom_ais must return 400, not use user A's file
    r_detect_b = c.post("/api/spill/detect", params={"mode": "custom_ais"}, headers=headers_b)
    assert r_detect_b.status_code == 400
    assert "upload" in r_detect_b.json()["detail"].lower()

    # User B now uploads their own AIS CSV
    content_b = b"mmsi,timestamp,lat,lon\n222222222,2026-08-28T06:00:00,19.10,71.25\n"
    r_up_b = c.post("/api/ais/upload", files={"file": ("fleet_b.csv", content_b, "text/csv")}, headers=headers_b)
    assert r_up_b.status_code == 200

    path_b = _USER_UPLOADED_AIS["user_b"]["path"]
    assert os.path.exists(path_b)
    # User A's file MUST NOT be deleted when User B uploads
    assert os.path.exists(path_a), "User A's file was erroneously deleted on User B upload!"


# ---------------------------------------------------------------------------
# SEC-03: Investigation listing ownership filtering
# ---------------------------------------------------------------------------
def test_investigations_listing_ownership_filter(auth_enabled):
    c = client()
    reg1 = c.post("/api/auth/register", json={"username": "owner_one", "password": "password123"})
    token1 = reg1.json()["access_token"]
    reg2 = c.post("/api/auth/register", json={"username": "owner_two", "password": "password123"})
    token2 = reg2.json()["access_token"]

    # Owner One runs an investigation
    r1 = c.post("/api/spill/detect", params={"mode": "demo"}, headers={"Authorization": f"Bearer {token1}"})
    assert r1.status_code == 200
    inv1_id = r1.json()["id"]

    # Owner Two runs an investigation
    r2 = c.post("/api/spill/detect", params={"mode": "demo"}, headers={"Authorization": f"Bearer {token2}"})
    assert r2.status_code == 200
    inv2_id = r2.json()["id"]

    # Owner One lists investigations: must see inv1, must NOT see inv2
    list_one = c.get("/api/investigations", headers={"Authorization": f"Bearer {token1}"}).json()
    ids_one = [item["id"] for item in list_one]
    assert inv1_id in ids_one
    assert inv2_id not in ids_one

    # Owner Two lists investigations: must see inv2, must NOT see inv1
    list_two = c.get("/api/investigations", headers={"Authorization": f"Bearer {token2}"}).json()
    ids_two = [item["id"] for item in list_two]
    assert inv2_id in ids_two
    assert inv1_id not in ids_two


# ---------------------------------------------------------------------------
# DB-01: Duplicate user creation raises AuthError cleanly
# ---------------------------------------------------------------------------
def test_duplicate_user_raises_auth_error_not_unhandled_crash():
    auth_module.create_user("audit_user_unique", "securepass1")
    with pytest.raises(auth_module.AuthError) as exc_info:
        auth_module.create_user("audit_user_unique", "securepass2")
    assert "already taken" in str(exc_info.value).lower()


def test_unrelated_database_error_is_not_swallowed(monkeypatch):
    import sqlite3
    from app.persistence import db as _db

    class MockConn:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, *args, **kwargs):
            raise sqlite3.OperationalError("disk I/O error or lock failure")

    monkeypatch.setattr(_db, "_connect", lambda: MockConn())
    with pytest.raises(sqlite3.OperationalError):
        auth_module.create_user("some_new_user", "password123")


# ---------------------------------------------------------------------------
# PERF-01: Bounded investigation store eviction & on-demand rehydration
# ---------------------------------------------------------------------------
def test_bounded_investigation_store_eviction():
    from app.investigation.orchestrator import _BoundedInvestigationStore, run_investigation, get_investigation

    store = _BoundedInvestigationStore(max_entries=3)
    dummy_inv1 = run_investigation(mode="demo")
    dummy_inv2 = run_investigation(mode="demo")
    dummy_inv3 = run_investigation(mode="demo")
    dummy_inv4 = run_investigation(mode="demo")

    store[dummy_inv1.id] = dummy_inv1
    store[dummy_inv2.id] = dummy_inv2
    store[dummy_inv3.id] = dummy_inv3
    assert len(store) == 3
    assert dummy_inv1.id in store

    # Adding a 4th entry should evict the oldest (dummy_inv1) from memory
    store[dummy_inv4.id] = dummy_inv4
    assert len(store) == 3
    assert dummy_inv1.id not in store
    assert dummy_inv4.id in store

    # But dummy_inv1 is still in database and rehydrates via get_investigation()
    rehydrated = get_investigation(dummy_inv1.id)
    assert rehydrated is not None
    assert rehydrated.id == dummy_inv1.id


def test_bounded_investigation_store_thread_safe_concurrency():
    import concurrent.futures
    from app.investigation.orchestrator import _BoundedInvestigationStore

    store = _BoundedInvestigationStore(max_entries=10)

    def worker(worker_id):
        for i in range(25):
            key = f"inv_{worker_id}_{i}"
            store[key] = f"val_{worker_id}_{i}"
            _ = store.get(key)
            _ = store.values()
            _ = store.items()

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(worker, w) for w in range(4)]
        for f in futures:
            f.result()

    assert len(store) <= 10


# ---------------------------------------------------------------------------
# DATA-01: Rehydrated investigation sanitization
# ---------------------------------------------------------------------------
def test_rehydrated_investigation_sanitizes_non_finite_values():
    from app.investigation.orchestrator import RehydratedInvestigation

    rehydrated = RehydratedInvestigation(
        id="test_rehydrated_01",
        created_at="2026-10-24T12:00:00",
        mode="demo",
        data_sources={},
        satellite={"nan_value": float("nan"), "inf_value": float("inf")},
        environment={},
        drift={},
        ais_summary={},
        candidates=[],
        progress_log=[],
        config_used={},
        processing_time_ms=None,
    )
    d = rehydrated.to_dict()
    assert d["satellite"]["nan_value"] == 0.0
    assert d["satellite"]["inf_value"] == 0.0
