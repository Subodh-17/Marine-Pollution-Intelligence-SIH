"""
PostgreSQL-backed persistence tests (Section 6/17 of the deployment-
readiness upgrade). These exercise the exact same app/persistence/db.py
functions as tests/test_persistence.py, just with DATABASE_URL pointed at
a real PostgreSQL server instead of the SQLite path - proving the
abstraction genuinely works against Postgres, not just in theory.

Skips (via the module-level `pytestmark` below), rather than fails, when
either `psycopg2` isn't installed or no reachable Postgres server is
configured - exactly the same graceful-degradation convention already
used for the optional `torch` dependency elsewhere in this suite. Set
TEST_POSTGRES_URL to point this at a specific server; the default matches
the local server this was actually developed and run against:
    postgresql://postgres:postgres@localhost:5432/mpi_test
Nothing in the application's request-serving path requires Postgres to be
reachable - this file existing and being skippable is exactly the point.
"""
from __future__ import annotations

import os

import pytest

TEST_POSTGRES_URL = os.environ.get(
    "TEST_POSTGRES_URL", "postgresql://postgres:postgres@localhost:5432/mpi_test"
)


def _postgres_reachable() -> bool:
    try:
        import psycopg2
    except ImportError:
        return False
    try:
        conn = psycopg2.connect(TEST_POSTGRES_URL, connect_timeout=2)
        conn.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _postgres_reachable(),
    reason="psycopg2 not installed or no reachable PostgreSQL server at TEST_POSTGRES_URL",
)


@pytest.fixture(autouse=True)
def _use_postgres(monkeypatch):
    """Points the persistence layer at the real Postgres server for every
    test in this file, and truncates this project's three tables first for
    isolation - unlike test_persistence.py's SQLite fixture, this is a
    shared, persistent database across test runs (there is no equivalent
    of a fresh SQLite tmp_path file), so isolation has to be an explicit
    TRUNCATE rather than a fresh file."""
    from app.config import get_settings
    from app.persistence import db as _db

    monkeypatch.setenv("DATABASE_URL", TEST_POSTGRES_URL)
    monkeypatch.setenv("DATABASE_ENABLED", "true")
    get_settings.cache_clear()
    _db.reset_schema_cache()
    with _db._connect() as conn:
        conn.execute("TRUNCATE TABLE investigations, jobs, users")
    yield
    get_settings.cache_clear()
    _db.reset_schema_cache()


def test_using_postgres_helper_reports_true_when_configured():
    from app.persistence import db as pdb
    assert pdb._using_postgres() is True


def test_save_and_load_round_trip_against_real_postgres():
    from app.persistence import db as pdb

    class FakeInv:
        id = "pg-abc123"
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

    loaded = pdb.load_investigation("pg-abc123")
    assert loaded is not None
    assert loaded["id"] == "pg-abc123"
    assert loaded["satellite"]["detector_provenance"] == "CLASSICAL_DARK_SPOT"
    assert loaded["_owner"] == "anonymous"


def test_upsert_on_conflict_updates_existing_row_in_postgres():
    """The ON CONFLICT ... DO UPDATE upsert is the one SQL construct most
    likely to silently behave differently across engines if the dialect
    shim ever regresses - worth its own explicit test rather than trusting
    the round-trip test above to catch a partial failure."""
    from app.persistence import db as pdb

    class FakeInv:
        def __init__(self, candidate_count_marker):
            self.id = "pg-upsert-1"
            self.created_at = "2026-01-01T00:00:00"
            self.mode = "demo"
            self.owner = "anonymous"
            self._n = candidate_count_marker

        def to_dict(self):
            return {
                "id": self.id, "created_at": self.created_at, "mode": self.mode,
                "data_sources": {}, "satellite": {"detector_provenance": "CLASSICAL_DARK_SPOT"},
                "environment": None, "drift": {}, "ais_summary": {},
                "candidates": [{} for _ in range(self._n)],
                "progress_log": [], "config_used": {}, "processing_time_ms": 1.0,
            }

    pdb.save_investigation(FakeInv(1))
    pdb.save_investigation(FakeInv(3))  # same id -> must UPDATE, not fail/duplicate

    assert pdb.count_investigations() == 1
    loaded = pdb.load_investigation("pg-upsert-1")
    assert len(loaded["candidates"]) == 3


def test_load_missing_investigation_returns_none_from_postgres():
    from app.persistence import db as pdb
    assert pdb.load_investigation("does-not-exist-in-postgres") is None


def test_list_and_count_investigations_in_postgres():
    from app.persistence import db as pdb

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

    pdb.save_investigation(FakeInv("pg-inv-1", "alice"))
    pdb.save_investigation(FakeInv("pg-inv-2", "bob"))
    pdb.save_investigation(FakeInv("pg-inv-3", "alice"))

    assert pdb.count_investigations() == 3
    alice_list = pdb.list_investigations(owner="alice")
    assert {r["id"] for r in alice_list} == {"pg-inv-1", "pg-inv-3"}
    everyone = pdb.list_investigations()
    assert len(everyone) == 3


def test_investigation_survives_in_memory_store_being_cleared_with_postgres():
    """The same restart-survival scenario test_persistence.py checks for
    SQLite, run against a real Postgres server instead - this is the
    actual point of the whole abstraction: a deployment where the
    filesystem doesn't survive a restart but the database does."""
    from app.investigation import orchestrator

    inv = orchestrator.run_investigation(mode="demo")
    inv_id = inv.id
    assert orchestrator.get_investigation(inv_id) is not None

    orchestrator._STORE.pop(inv_id, None)
    assert inv_id not in orchestrator._STORE

    rehydrated = orchestrator.get_investigation(inv_id)
    assert rehydrated is not None
    assert rehydrated.id == inv_id
    assert rehydrated.mode == "demo"
    assert rehydrated.drift["probable_origin"] is not None
    assert len(rehydrated.candidates) == len(inv.candidates)


def test_init_db_fails_loudly_on_unreachable_postgres_host(monkeypatch):
    """A misconfigured DATABASE_URL (wrong host/port/credentials) must
    raise a clear PersistenceError at startup, not hang or crash with a
    raw driver traceback three requests later."""
    from app.config import get_settings
    from app.persistence import db as pdb

    monkeypatch.setenv("DATABASE_URL", "postgresql://baduser:badpass@localhost:59999/nope")
    get_settings.cache_clear()
    pdb.reset_schema_cache()
    try:
        with pytest.raises(pdb.PersistenceError):
            pdb.init_db()
    finally:
        get_settings.cache_clear()
        pdb.reset_schema_cache()


def test_init_db_error_never_leaks_the_database_password(monkeypatch):
    """Regression test: a broken DATABASE_URL's connection failure
    surfaces through BOTH a startup log line AND the publicly-reachable,
    unauthenticated GET /ready endpoint (see app/main.py) - discovered
    live during development, where the raw password briefly appeared in
    both places. The exception message must contain the username (useful
    for diagnosing "wrong user" vs "wrong password") but never the
    plaintext password, which app/persistence/db.py's _redact_credentials
    is specifically responsible for."""
    from app.config import get_settings
    from app.persistence import db as pdb

    monkeypatch.setenv(
        "DATABASE_URL", "postgresql://baduser:supersecretpassword@localhost:59999/nope"
    )
    get_settings.cache_clear()
    pdb.reset_schema_cache()
    try:
        with pytest.raises(pdb.PersistenceError) as exc_info:
            pdb.init_db()
        message = str(exc_info.value)
        assert "supersecretpassword" not in message
        assert "baduser" in message  # username retained: still diagnostically useful
        assert "***" in message
    finally:
        get_settings.cache_clear()
        pdb.reset_schema_cache()
