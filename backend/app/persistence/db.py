"""
Persistence (Section 6): SQLite by default, PostgreSQL when configured.

Design choices, and why:

  - SQLite remains the default and the only thing required to run this app
    locally - a single file, stdlib `sqlite3`, no server process.
    `DATABASE_ENABLED=false` runs fully in-memory (nothing survives a
    restart) for anyone who wants the pre-persistence behavior back.
  - Setting `DATABASE_URL` to a `postgres://`/`postgresql://` URL switches
    to a real PostgreSQL server instead, for deployments where the
    filesystem is ephemeral between restarts/deploys (e.g. most PaaS
    "free tier" containers) and SQLite's single local file would not
    survive one. This was actually installed and exercised against a real
    local PostgreSQL 16 server during development (see
    tests/test_persistence_postgres.py) - it is not untested,
    theoretical code, though it has not been run against a managed cloud
    Postgres instance specifically (see docs/DEPLOYMENT.md).
  - The application/business logic in orchestrator.py and app/main.py
    never sees this distinction at all: both backends are reached through
    the exact same six functions below (`init_db`, `save_investigation`,
    `load_investigation`, `list_investigations`, `count_investigations`,
    plus the schema/connection helpers jobs.py uses), taking and returning
    the same plain dicts/booleans either way. This is a thin dialect
    shim - translating `?` placeholders to `%s` and giving both engines'
    rows the same `row["col"]` dict-style access - not a full ORM, since
    this project's entire persisted surface is five simple queries against
    two tables' worth of columns; an ORM would be more moving parts for a
    smaller job than this shim already does.
  - Investigations are stored as a single JSON blob per row (the same dict
    `Investigation.to_dict()` already produces for the API) plus a handful
    of indexed columns for filtering/listing, rather than maintaining a
    second, parallel relational schema for a payload whose shape already
    lives in - and is entirely owned by - the orchestrator.
  - Every function opens a short-lived connection per call rather than
    holding one open for the life of the process. For this prototype's
    request volume this is simpler and safer (no shared-connection
    threading bugs across FastAPI's threadpool) than pooling; it would not
    be the right choice for a high-throughput deployment (see
    docs/DEPLOYMENT.md for the pooling note if that ever changes).
  - `save_investigation` never raises into the caller's investigation flow -
    a persistence failure is logged and swallowed, since a save-to-disk
    failure should not fail an otherwise-successful investigation the user
    is looking at right now (the in-memory store in orchestrator.py is
    still the fast path and is unaffected).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

from app.config import get_settings
from app.logging_config import get_logger

_log = get_logger("persistence.db")

_initialized_paths: set[str] = set()

# Deliberately plain, portable SQL: every type used here (TEXT, INTEGER,
# REAL) and the ON CONFLICT ... DO UPDATE SET ... EXCLUDED.col upsert
# syntax are valid, identical, in both SQLite (3.24+) and PostgreSQL
# (9.5+) - PostgreSQL is in fact where that upsert syntax originated and
# SQLite later adopted a compatible dialect of it. This lets both backends
# share one schema/query definition instead of two parallel copies.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS investigations (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    mode TEXT NOT NULL,
    owner TEXT,
    detector_provenance TEXT,
    processing_time_ms REAL,
    candidate_count INTEGER,
    data_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_investigations_owner ON investigations(owner);
CREATE INDEX IF NOT EXISTS idx_investigations_created_at ON investigations(created_at);

CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL,
    owner TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    progress_stage TEXT,
    result_investigation_id TEXT,
    error TEXT
);
"""


class PersistenceError(Exception):
    pass


def _using_postgres() -> bool:
    url = get_settings().database_url
    return bool(url) and (url.startswith("postgres://") or url.startswith("postgresql://"))


def _db_path() -> str:
    return get_settings().database_path


_QMARK_RE = re.compile(r"\?")


def _to_pyformat(sql: str) -> str:
    """Translates this module's `?`-style placeholders (SQLite's dialect)
    to `%s`-style (psycopg2's dialect). Every query in this file uses
    plain `?` markers with no literal question marks in string content,
    so a straight regex substitution is exact here - there is no free-text
    user data embedded in any SQL string, only bound parameters."""
    return _QMARK_RE.sub("%s", sql)


class _DictRowCursor:
    """Wraps a psycopg2 cursor so `row["col"]` works the same way
    sqlite3.Row already does, and translates `?` placeholders to `%s` on
    the way in - the only two differences the functions below would
    otherwise need two code paths for."""

    def __init__(self, cur):
        self._cur = cur

    def execute(self, sql: str, params: tuple = ()):
        self._cur.execute(_to_pyformat(sql), params)
        return self

    def executescript(self, sql: str):
        self._cur.execute(sql)

    def fetchone(self):
        row = self._cur.fetchone()
        return dict(row) if row is not None else None

    def fetchall(self):
        return [dict(r) for r in self._cur.fetchall()]


def _ensure_schema_sqlite(conn: sqlite3.Connection, path: str) -> None:
    if path in _initialized_paths:
        return
    conn.executescript(_SCHEMA)
    _initialized_paths.add(path)


def _ensure_schema_postgres(cur: _DictRowCursor, url: str) -> None:
    if url in _initialized_paths:
        return
    cur.executescript(_SCHEMA)
    _initialized_paths.add(url)


@contextmanager
def _connect_sqlite() -> Iterator[sqlite3.Connection]:
    path = _db_path()
    if path != ":memory:":
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_schema_sqlite(conn, path)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def _connect_postgres() -> Iterator[_DictRowCursor]:
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError as e:  # pragma: no cover - exercised via skip, see tests
        raise PersistenceError(
            "DATABASE_URL is set to a PostgreSQL URL but the psycopg2-binary "
            "package is not installed. Run: pip install psycopg2-binary "
            "(see requirements-postgres.txt / docs/DEPLOYMENT.md)."
        ) from e
    url = get_settings().database_url
    conn = psycopg2.connect(url)
    try:
        # RealDictCursor is what makes `row["col"]` (and the plain
        # `dict(row)` calls below) work identically to sqlite3.Row's
        # dict-style access - the default psycopg2 cursor returns plain
        # tuples, which `dict()` would silently misinterpret rather than
        # raise a clear error for (a single (long_string, short_string)
        # row looks enough like a malformed 2-item mapping input to trip
        # dict()'s "sequence element has wrong length" path instead of an
        # obviously-wrong-type error), so this is not optional.
        cur = _DictRowCursor(conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor))
        _ensure_schema_postgres(cur, url)
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def _connect():
    """Yields a connection-like object supporting `.execute(sql, params)`
    returning something with `.fetchone()`/`.fetchall()` giving
    dict-style row access, backed by SQLite or PostgreSQL depending on
    configuration. This is the ONLY function below that knows which
    backend is active - everything else is written once against this
    common interface."""
    if _using_postgres():
        with _connect_postgres() as cur:
            yield cur
    else:
        with _connect_sqlite() as conn:
            yield conn


def reset_schema_cache() -> None:
    """Test helper: forces the next `_connect()` to re-run
    `CREATE TABLE IF NOT EXISTS` (needed after pointing DATABASE_PATH/
    DATABASE_URL at a fresh target in a test - a *new* target is handled
    automatically; this is only needed if a test reuses an already-seen
    target and expects tables to not yet exist)."""
    _initialized_paths.clear()


_CREDENTIALS_IN_URL_RE = re.compile(r"://([^:/@\s]+):([^@/\s]*)@")


def _redact_credentials(text: str) -> str:
    """Masks the password portion of any `scheme://user:password@host`
    substring in `text` (keeping the username visible, since it's useful
    for diagnosing "wrong user" vs "wrong password" without ever
    reproducing the password itself). Applied to EVERY message this
    module raises or logs that might contain `database_url` or a
    driver-generated string derived from it - `init_db()`'s own
    diagnostic-target string, and defensively, the driver exception's
    `str(e)` too, in case a future psycopg2 version or connection error
    variant ever echoes the DSN back (this project's error DID leak the
    password here during development - a misconfigured DATABASE_URL's
    connection failure surfaced through both the startup log and the
    publicly-reachable, unauthenticated GET /ready endpoint - until this
    redaction was added; see docs/DEPLOYMENT.md and the security-audit
    section of the upgrade request this fixes)."""
    return _CREDENTIALS_IN_URL_RE.sub(r"://\1:***@", text)


def init_db() -> None:
    """Explicitly creates the schema if it doesn't already exist. Not
    required before calling the functions below (they self-initialize on
    first use), but useful to call once at app startup so a broken
    DATABASE_PATH/DATABASE_URL (e.g. an unwritable directory, or an
    unreachable Postgres host) fails fast and loudly instead of on the
    first investigation."""
    try:
        with _connect():
            pass
    except PersistenceError:
        raise
    except Exception as e:
        target = _redact_credentials(get_settings().database_url or _db_path())
        detail = _redact_credentials(str(e))
        raise PersistenceError(f"Could not initialize database at {target}: {detail}") from e


def save_investigation(inv) -> bool:
    """Persists an Investigation (the orchestrator's dataclass, or anything
    with a compatible `.to_dict()`) so it survives a backend restart.
    Returns True on success, False on failure (logged, never raised)."""
    if not get_settings().database_enabled:
        return False
    try:
        data = inv.to_dict()
        payload = json.dumps(data, default=str)
        candidate_count = len(data.get("candidates") or [])
        detector_provenance = (data.get("satellite") or {}).get("detector_provenance")
        owner = getattr(inv, "owner", None)
        with _connect() as conn:
            conn.execute(
                """INSERT INTO investigations
                   (id, created_at, mode, owner, detector_provenance, processing_time_ms,
                    candidate_count, data_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     data_json=excluded.data_json, owner=excluded.owner,
                     detector_provenance=excluded.detector_provenance,
                     processing_time_ms=excluded.processing_time_ms,
                     candidate_count=excluded.candidate_count""",
                (
                    data["id"], data["created_at"], data["mode"], owner,
                    detector_provenance, data.get("processing_time_ms"),
                    candidate_count, payload,
                ),
            )
        return True
    except Exception as e:  # pragma: no cover - defensive, see docstring
        _log.warning(
            "investigation_persist_failed",
            extra={"investigation_id": getattr(inv, "id", None), "error": str(e)},
        )
        return False


def load_investigation(investigation_id: str) -> dict | None:
    """Returns the raw stored dict (the same shape `Investigation.to_dict()`
    produces, plus an `_owner` key) for `investigation_id`, or None if not
    found / DB disabled / DB unavailable."""
    if not get_settings().database_enabled:
        return None
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT data_json, owner FROM investigations WHERE id = ?", (investigation_id,)
            ).fetchone()
            if row is None:
                return None
            data = json.loads(row["data_json"])
            data["_owner"] = row["owner"]
            return data
    except Exception as e:  # pragma: no cover - defensive
        _log.warning("investigation_load_failed", extra={"investigation_id": investigation_id, "error": str(e)})
        return None


def list_investigations(owner: str | None = None, limit: int = 100) -> list[dict]:
    """Lightweight listing (no full JSON payload) - id/created_at/mode/owner/
    detector/candidate count only."""
    if not get_settings().database_enabled:
        return []
    try:
        with _connect() as conn:
            if owner is not None:
                rows = conn.execute(
                    """SELECT id, created_at, mode, owner, detector_provenance, candidate_count
                       FROM investigations WHERE owner = ? ORDER BY created_at DESC LIMIT ?""",
                    (owner, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT id, created_at, mode, owner, detector_provenance, candidate_count
                       FROM investigations ORDER BY created_at DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:  # pragma: no cover - defensive
        _log.warning("investigation_list_failed", extra={"error": str(e)})
        return []


def count_investigations() -> int:
    if not get_settings().database_enabled:
        return 0
    try:
        with _connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM investigations").fetchone()
            return int(row["n"])
    except Exception:  # pragma: no cover - defensive
        return 0

