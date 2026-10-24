import os
import sys
import warnings

sys.path.insert(0, os.path.dirname(__file__))

# Historical note: this codebase no longer imports scikit-image anywhere
# (see app/satellite/geometry_utils.py, a pure NumPy/SciPy reimplementation
# of the handful of skimage functions the pipeline used). This warning
# filter is a leftover from when it did and is kept only in case a
# dependency of a dependency still emits it; harmless either way.
warnings.filterwarnings("ignore", message=".*min_size.*deprecated.*", category=FutureWarning)

import pytest


@pytest.fixture(autouse=True)
def _isolated_database(tmp_path, monkeypatch):
    """Section 6/12: every test gets its own throwaway SQLite file under
    pytest's tmp_path, never the real backend/data/app.db a locally-running
    instance would use. This is what makes persistence genuinely testable
    (a test can save, "restart" by clearing the in-memory store, and reload
    from this exact file) without ever touching a hardcoded path or leaving
    generated database files behind for the packaged ZIP to pick up."""
    from app.config import get_settings
    from app.persistence import db as _db

    db_path = str(tmp_path / "test_app.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    monkeypatch.setenv("DATASET_ROOT", str(tmp_path / "test_zenodo_sar"))
    get_settings.cache_clear()
    _db.reset_schema_cache()
    yield
    get_settings.cache_clear()


# The real, default backend/data/app.db path (mirrors app/config.py's own
# `database_path` default - not imported from there directly, since that
# resolution only runs with DATABASE_PATH unset, which every test's own
# `_isolated_database` fixture above deliberately prevents for the
# duration of the test itself; this is evaluated once, here, before any
# test has had a chance to touch the environment).
_REAL_DEFAULT_DB_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "data", "app.db")
)


@pytest.fixture(scope="session", autouse=True)
def _guard_real_database_untouched():
    """Session-wide regression guard, independent of and in addition to
    `_isolated_database` above: records whether the REAL local
    backend/data/app.db exists (and its mtime, if so) before the entire
    test session runs anything, and asserts at the very end that nothing
    changed - a test that manages to create or modify it despite the
    per-test isolation fixture (e.g. a classmethod-based setup hook like
    `TestNewExportEndpoints.setup_class` used to be, which runs before any
    function-scoped fixture including `_isolated_database`) fails the
    WHOLE session loudly, rather than silently leaving a real file behind
    for the next `git status` or packaged ZIP to find."""
    existed_before = os.path.exists(_REAL_DEFAULT_DB_PATH)
    mtime_before = os.path.getmtime(_REAL_DEFAULT_DB_PATH) if existed_before else None
    yield
    existed_after = os.path.exists(_REAL_DEFAULT_DB_PATH)
    mtime_after = os.path.getmtime(_REAL_DEFAULT_DB_PATH) if existed_after else None
    assert existed_after == existed_before and mtime_after == mtime_before, (
        f"A test created or modified the REAL local database at "
        f"{_REAL_DEFAULT_DB_PATH!r} instead of using the isolated tmp_path "
        f"database every test should get from the autouse _isolated_database "
        f"fixture above. This almost always means some test's database "
        f"access happened before that fixture's setup ran - e.g. inside a "
        f"classmethod-based setup_class/setup_module hook, which pytest "
        f"runs before any function-scoped (including autouse) fixture."
    )

