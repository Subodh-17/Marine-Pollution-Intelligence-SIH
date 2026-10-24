"""
Phase 4 Regression Tests: Production Hardening, Durability & Attribution Safety

Covers:
1. Database transaction rollback and error surfacing upon persistence failure.
2. Complete rehydration fidelity from database storage across cache eviction.
3. Ownership enforcement and tenant isolation across rehydrated investigations.
4. Misleading provenance prevention: empty ML mask never masquerades as CNN detection.
5. Attribution safety gating on lookalike scenes (strict candidate abstention).
6. Missing AIS observations are treated neutrally and never imputed as evidence of guilt.
7. Ambiguous scene classifications fail-closed with unconfirmed attribution.
"""
from __future__ import annotations

import os
import pytest
from datetime import datetime, timezone, timedelta
import numpy as np

from app.config import get_settings
from app.investigation import orchestrator
from app.persistence import db as _db
from app.investigation.policy import (
    evaluate_attribution_eligibility,
    STATUS_LOOKALIKE,
    STATUS_EMPTY_MASK,
    STATUS_UNCERTAIN,
    STATUS_PERMITTED,
)
from app.ais.schema import AISRecord
from app.ais.pipeline import reconstruct_trajectories, VesselTrajectory
from app.scoring.attribution import (
    score_candidate,
)


# -----------------------------------------------------------------------------
# PRIORITY 1: Persistence, Rollback, Error Surfacing & Durability
# -----------------------------------------------------------------------------
def test_failed_db_persistence_returns_false_and_does_not_crash(monkeypatch):
    """When the database is unreachable or a transaction fails, save_investigation()
    must return False and log at ERROR level rather than silently pretending to persist."""
    inv = orchestrator.run_investigation(mode="demo")

    class FailingConn:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, *args, **kwargs):
            raise _db.sqlite3.OperationalError("Simulated disk I/O failure or database locked")

    monkeypatch.setattr(_db, "_connect", lambda: FailingConn())

    # save_investigation should handle the error, return False, and not crash
    success = _db.save_investigation(inv)
    assert success is False, "save_investigation must return False upon transaction failure"


def test_rehydration_preserves_all_attributes_across_cache_wipe(monkeypatch):
    """Investigation records must survive in-memory store eviction and rehydrate
    completely with all original fields, telemetry, and candidates preserved."""
    inv = orchestrator.run_investigation(mode="demo")
    inv_id = inv.id

    # Wipe in-memory store completely
    orchestrator._STORE.clear()
    assert inv_id not in orchestrator._STORE

    # Rehydrate via get_investigation()
    rehydrated = orchestrator.get_investigation(inv_id)
    assert rehydrated is not None
    assert rehydrated.id == inv_id
    assert rehydrated.mode == "demo"
    assert rehydrated.owner == inv.owner
    assert rehydrated.satellite["detector_provenance"] == inv.satellite["detector_provenance"]
    assert len(rehydrated.candidates) == len(inv.candidates)
    assert rehydrated.drift["probable_origin"] is not None

    # Verify to_dict serialization on rehydrated instance
    d = rehydrated.to_dict()
    assert d["id"] == inv_id
    assert len(d["candidates"]) == len(inv.candidates)


def test_rehydration_preserves_ownership_and_tenant_filtering(monkeypatch):
    """Rehydrated records must preserve the original owner and enforce horizontal isolation."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    get_settings.cache_clear()
    try:
        inv = orchestrator.run_investigation(mode="demo", owner="alice")
        inv_id = inv.id

        # Clear memory
        orchestrator._STORE.clear()

        # Alice should see her investigation
        alice_inv = orchestrator.get_investigation(inv_id)
        assert alice_inv is not None
        assert alice_inv.owner == "alice"

        # Listing scoped to alice
        alice_list = _db.list_investigations(owner="alice")
        assert any(item["id"] == inv_id for item in alice_list)

        # Listing scoped to bob must not see alice's record
        bob_list = _db.list_investigations(owner="bob")
        assert not any(item["id"] == inv_id for item in bob_list)
    finally:
        get_settings.cache_clear()


# -----------------------------------------------------------------------------
# PRIORITY 3: Detection & Attribution Safety, Provenance & Lookalike Safeguards
# -----------------------------------------------------------------------------
def test_empty_ml_mask_never_reports_misleading_ml_provenance(monkeypatch):
    """When ML detection yields an empty mask or falls back, the pipeline must
    NEVER claim ML_REAL_ZENODO_TRAINED; it must honestly report fallback provenance."""
    sample_path = os.path.join(os.path.dirname(__file__), "..", "data", "sample_sar", "demo_oil_scene_01.tif")
    if not os.path.exists(sample_path):
        pytest.skip("Sample scene not found")

    # Run investigation with detector='ml' on procedural white noise scene
    inv = orchestrator.run_investigation(
        mode="real_satellite",
        image_path=sample_path,
        detector="ml",
    )
    sat = inv.satellite
    prov = sat.get("detector_provenance")

    # It MUST be labelled as fallback, not a successful real ML model output
    assert prov == "CLASSICAL_DARK_SPOT_ML_FALLBACK", f"Expected ML fallback, got {prov}"
    assert "FALLBACK" in prov
    notes_text = " ".join(sat.get("detection_notes", [])).lower()
    assert "fallback" in notes_text or "lookalike" in notes_text or "empty" in notes_text


def test_lookalike_scene_attribution_strictly_gated():
    """Lookalike scene classifications must trigger attribution gating and produce ZERO accused candidates."""
    sc = {
        "oil_status": "LOOKALIKE",
        "probabilities": {"Oil": 0.05, "Lookalike": 0.88, "No-Oil": 0.07},
        "confidence": 0.88,
    }
    policy_res = evaluate_attribution_eligibility(
        scene_classification=sc,
        mask=np.ones((50, 50)),
        observed_time=datetime.now(timezone.utc),
    )
    assert policy_res.eligible is False
    assert policy_res.status == STATUS_LOOKALIKE
    assert "Lookalike" in policy_res.reason


def test_missing_ais_data_does_not_impute_guilt():
    """Missing SOG and COG observations must receive neutral scoring (0.4) and
    surface missing-data warnings, never penalizing or accusing a vessel without evidence."""
    t0 = datetime(2026, 8, 28, 6, 0)
    now = datetime(2026, 8, 28, 8, 0)

    # Records with missing COG and SOG (None)
    points = [
        AISRecord(mmsi="999888777", timestamp=t0, lat=19.04, lon=71.21, sog=None, cog=None),
        AISRecord(mmsi="999888777", timestamp=now, lat=19.05, lon=71.20, sog=None, cog=None),
    ]
    trajs = reconstruct_trajectories(points, 19.05, 71.20)
    assert len(trajs) == 1
    traj = trajs[0]

    candidate = score_candidate(
        traj=traj,
        origin_lat=19.05,
        origin_lon=71.20,
        origin_time=t0,
        search_radius_km=50.0,
        time_window_hours=12.0,
    )

    # Breakdown components for heading and speed
    breakdown_map = {b.component: b for b in candidate.breakdown}

    # Heading consistency must be neutral fraction (0.4)
    heading_b = breakdown_map.get("heading_consistency")
    assert heading_b is not None
    assert heading_b.raw_fraction == 0.4
    assert "neutral score" in heading_b.explanation.lower()

    # Speed consistency must be neutral fraction (0.4)
    speed_b = breakdown_map.get("speed_consistency")
    assert speed_b is not None
    assert speed_b.raw_fraction == 0.4
    assert "neutral" in speed_b.explanation.lower()

    # Data completeness must reflect missing fields and emit warnings
    assert candidate.data_completeness_pct < 100.0
    assert any("SOG" in w for w in candidate.data_warnings)
    assert any("COG" in w for w in candidate.data_warnings)


def test_uncertain_attribution_fails_closed():
    """Ambiguous model classifications (e.g. near 40% Oil vs 40% No-Oil) must fail closed."""
    ambiguous_sc = {
        "oil_status": "UNCERTAIN",
        "probabilities": {"Oil": 0.41, "No-Oil": 0.39, "Lookalike": 0.20},
        "confidence": 0.41,
    }
    policy_res = evaluate_attribution_eligibility(
        scene_classification=ambiguous_sc,
        mask=np.ones((60, 60)),
        observed_time=datetime.now(timezone.utc),
    )
    assert policy_res.eligible is False
    assert policy_res.status == STATUS_UNCERTAIN
    assert "ambiguous" in policy_res.reason.lower()


def test_unpersisted_investigation_evicted_from_memory_cache_on_db_failure(monkeypatch):
    """When DB persistence fails while database_enabled=True, the investigation
    must NOT linger in the in-memory cache _STORE, and inv.persisted must be False."""
    class FailingConn:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, *args, **kwargs):
            raise _db.sqlite3.OperationalError("Simulated disk write failure")

    monkeypatch.setattr(_db, "_connect", lambda: FailingConn())

    inv = orchestrator.run_investigation(mode="demo")
    assert inv.persisted is False
    assert inv.to_dict()["persisted"] is False
    # Memory cache _STORE must NOT retain the failed-persistence investigation
    assert inv.id not in orchestrator._STORE


def test_mandatory_persistence_failure_raises_and_returns_500(monkeypatch):
    """When DATABASE_MANDATORY=true, persistence failures must raise PersistenceError
    and API endpoints must return HTTP 500 rather than silently returning 200."""
    from fastapi.testclient import TestClient
    from app.main import app

    class FailingConn:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, *args, **kwargs):
            raise _db.sqlite3.OperationalError("Disk full or connection severed")

    monkeypatch.setattr(_db, "_connect", lambda: FailingConn())
    monkeypatch.setenv("DATABASE_MANDATORY", "true")
    get_settings.cache_clear()

    with pytest.raises(_db.PersistenceError):
        orchestrator.run_investigation(mode="demo")

    client = TestClient(app)
    resp = client.post("/api/spill/detect?mode=demo")
    assert resp.status_code == 500
    assert "Database persistence failure" in resp.json()["detail"]
    get_settings.cache_clear()


def test_reanalysis_fails_with_500_when_mandatory_persistence_fails(monkeypatch):
    """When DATABASE_MANDATORY=true, re-analysis endpoints must return HTTP 500 if save fails."""
    from fastapi.testclient import TestClient
    from app.main import app

    # First run normal demo investigation so it exists in memory/db
    inv = orchestrator.run_investigation(mode="demo")

    # Now make subsequent saves fail and enable database_mandatory
    class FailingConn:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, *args, **kwargs):
            raise _db.sqlite3.OperationalError("Disk full")

    monkeypatch.setattr(_db, "_connect", lambda: FailingConn())
    monkeypatch.setenv("DATABASE_MANDATORY", "true")
    get_settings.cache_clear()

    client = TestClient(app)
    resp = client.post(f"/api/ais/analyze?investigation_id={inv.id}&search_radius_km=40")
    assert resp.status_code == 500
    assert "Mandatory persistence failed" in resp.json()["detail"]
    get_settings.cache_clear()


@pytest.mark.anyio
async def test_startup_fails_when_database_mandatory_true_and_init_db_fails(monkeypatch):
    """FastAPI app startup must fail fast with RuntimeError when DATABASE_MANDATORY=true and init_db fails."""
    from app.main import _lifespan, app

    def _failing_init():
        raise _db.PersistenceError("Connection refused to database host")

    monkeypatch.setattr(_db, "init_db", _failing_init)
    monkeypatch.setenv("DATABASE_MANDATORY", "true")
    monkeypatch.setenv("DATABASE_ENABLED", "true")
    get_settings.cache_clear()

    with pytest.raises(RuntimeError) as exc_info:
        async with _lifespan(app):
            pass
    assert "DATABASE_MANDATORY is true but database initialization failed" in str(exc_info.value)
    get_settings.cache_clear()


def test_redact_credentials_masks_passwords_in_connection_urls():
    """Database URLs must mask the password while preserving username, host, and port."""
    raw_url = "postgresql://marine_user:super_secret_password_123@dpg-abc123-a.oregon-postgres.render.com:5432/marine_db"
    redacted = _db._redact_credentials(raw_url)
    assert "super_secret_password_123" not in redacted
    assert "marine_user" in redacted
    assert "://marine_user:***@" in redacted


def test_connect_postgres_masks_credentials_and_raises_persistence_error(monkeypatch):
    """When connecting to PostgreSQL fails, the error message must never leak the raw password."""
    fake_url = "postgresql://deployer:ultra_secret_pass@127.0.0.1:1/nonexistent_db"
    monkeypatch.setenv("DATABASE_URL", fake_url)
    monkeypatch.setenv("DATABASE_ENABLED", "true")
    get_settings.cache_clear()

    with pytest.raises(_db.PersistenceError) as exc_info:
        with _db._connect_postgres():
            pass

    err_str = str(exc_info.value)
    assert "ultra_secret_pass" not in err_str
    assert "deployer:***@" in err_str
    get_settings.cache_clear()
