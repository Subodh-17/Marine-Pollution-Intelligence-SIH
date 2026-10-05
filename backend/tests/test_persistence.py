"""
Tests for app/persistence/db.py and the orchestrator's restart-survival
behavior (Section 6). Every test uses the per-test isolated DATABASE_PATH
set up by the autouse `_isolated_database` fixture in conftest.py - never
the real backend/data/app.db a locally-running instance would use.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from app.persistence import db as pdb


def test_save_and_load_round_trip():
    class FakeInv:
        id = "abc123"
        created_at = "2026-01-01T00:00:00"
        mode = "demo"
        owner = "anonymous"

        def to_dict(self):
            return {
                "id": self.id, "created_at": self.created_at, "mode": self.mode,
                "data_sources": {"satellite": "x"}, "satellite": {"detector_provenance": "CLASSICAL_DARK_SPOT"},
                "environment": None, "drift": {}, "ais_summary": {}, "candidates": [],
                "progress_log": [], "config_used": {}, "processing_time_ms": 12.3,
            }

    ok = pdb.save_investigation(FakeInv())
    assert ok is True

    loaded = pdb.load_investigation("abc123")
    assert loaded is not None
    assert loaded["id"] == "abc123"
    assert loaded["satellite"]["detector_provenance"] == "CLASSICAL_DARK_SPOT"
    assert loaded["_owner"] == "anonymous"


def test_load_missing_investigation_returns_none():
    assert pdb.load_investigation("does-not-exist") is None


def test_database_disabled_short_circuits(monkeypatch):
    from app.config import get_settings
    monkeypatch.setenv("DATABASE_ENABLED", "false")
    get_settings.cache_clear()
    try:
        class FakeInv:
            id = "xyz"
            created_at = "now"
            mode = "demo"
            owner = "anonymous"

            def to_dict(self):
                return {"id": "xyz", "created_at": "now", "mode": "demo", "candidates": []}

        assert pdb.save_investigation(FakeInv()) is False
        assert pdb.load_investigation("xyz") is None
        assert pdb.list_investigations() == []
    finally:
        get_settings.cache_clear()


def test_list_and_count_investigations():
    class FakeInv:
        def __init__(self, iid, owner):
            self.id = iid
            self.created_at = "2026-01-01T00:00:00"
            self.mode = "demo"
            self.owner = owner

        def to_dict(self):
            return {
                "id": self.id, "created_at": self.created_at, "mode": self.mode,
                "data_sources": {}, "satellite": {"detector_provenance": "CLASSICAL_DARK_SPOT"},
                "environment": None, "drift": {}, "ais_summary": {}, "candidates": [],
                "progress_log": [], "config_used": {}, "processing_time_ms": 1.0,
            }

    pdb.save_investigation(FakeInv("inv-1", "alice"))
    pdb.save_investigation(FakeInv("inv-2", "bob"))
    pdb.save_investigation(FakeInv("inv-3", "alice"))

    assert pdb.count_investigations() == 3
    alice_list = pdb.list_investigations(owner="alice")
    assert {r["id"] for r in alice_list} == {"inv-1", "inv-3"}
    everyone = pdb.list_investigations()
    assert len(everyone) == 3


# ---------------------------------------------------------------------------
# Orchestrator-level: an investigation survives losing the in-memory cache
# (the closest a single-process pytest run can get to simulating an actual
# backend restart, which drops the in-memory `_STORE` dict but not the
# SQLite file on disk).
# ---------------------------------------------------------------------------
def test_investigation_survives_in_memory_store_being_cleared():
    from app.investigation import orchestrator

    inv = orchestrator.run_investigation(mode="demo")
    inv_id = inv.id
    assert orchestrator.get_investigation(inv_id) is not None

    # Simulate "the process restarted": the in-memory dict is gone, but the
    # SQLite file (this test's isolated tmp_path DB) is not.
    orchestrator._STORE.pop(inv_id, None)
    assert inv_id not in orchestrator._STORE

    rehydrated = orchestrator.get_investigation(inv_id)
    assert rehydrated is not None
    assert rehydrated.id == inv_id
    assert rehydrated.mode == "demo"
    assert rehydrated.drift["probable_origin"] is not None
    assert len(rehydrated.candidates) == len(inv.candidates)
    if rehydrated.candidates:
        # attribute access (used by main.py::get_vessel) ...
        assert rehydrated.candidates[0].mmsi == inv.candidates[0].mmsi
        # ... and .to_dict() (used everywhere else) both work.
        assert rehydrated.candidates[0].to_dict()["mmsi"] == inv.candidates[0].mmsi
    assert rehydrated.to_dict()["id"] == inv_id


def test_api_restart_persistence():
    """End-to-end: run an investigation through the real API, drop it from
    the in-memory store to simulate a restart, and confirm every read route
    the frontend actually calls still works from the database alone."""
    from app.main import app
    from app.investigation import orchestrator

    client = TestClient(app)
    r = client.post("/api/spill/detect", params={"mode": "demo"})
    assert r.status_code == 200
    inv_id = r.json()["id"]

    orchestrator._STORE.pop(inv_id, None)

    r2 = client.get(f"/api/spill/{inv_id}")
    assert r2.status_code == 200
    assert r2.json()["id"] == inv_id

    r3 = client.get(f"/api/spill/{inv_id}/geometry")
    assert r3.status_code == 200

    r4 = client.get(f"/api/vessels/ranking", params={"investigation_id": inv_id})
    assert r4.status_code == 200

    r5 = client.get(f"/api/report/{inv_id}", params={"fmt": "markdown"})
    assert r5.status_code == 200
    assert "markdown" in r5.json()
