"""
Tests for upload security hardening (Section 9) that isn't already covered
by tests/test_production_readiness.py (oversized files, unsupported
extensions, corrupt-image handling are already tested there).
"""
from __future__ import annotations

import os
import io

import numpy as np
import tifffile
from fastapi.testclient import TestClient

from app.config import get_settings


def client():
    from app.main import app
    return TestClient(app)


# ---------------------------------------------------------------------------
# Path traversal / malicious filenames
# ---------------------------------------------------------------------------
def test_path_traversal_filename_does_not_escape_temp_dir(tmp_path, monkeypatch):
    """A crafted filename must never be used to write outside the intended
    temp directory - the upload path is always a fresh tempfile.mkstemp-style
    random name (see _save_upload_with_size_limit), never derived from the
    caller-supplied filename."""
    c = client()
    malicious_names = [
        "../../../../etc/passwd.csv",
        "..\\..\\windows\\system32\\evil.csv",
        "/etc/passwd.csv",
        "normal\x00hidden.csv",
    ]
    for name in malicious_names:
        content = b"mmsi,timestamp,lat,lon\n123,2024-01-01T00:00:00,19.0,71.0\n"
        r = c.post("/api/ais/upload", files={"file": (name, content, "text/csv")})
        # Whatever status comes back, no file should ever land outside a
        # legitimate temp directory - nothing in the response should imply
        # otherwise, and the app must not crash (500).
        assert r.status_code in (200, 400), f"filename {name!r} caused unexpected status {r.status_code}"
        assert not os.path.exists("/etc/passwd.csv")


def test_satellite_upload_malicious_filename_handled_safely():
    c = client()
    r = c.post(
        "/api/satellite/upload",
        files={"file": ("../../../../tmp/evil.tif", b"not a real tiff", "image/tiff")},
    )
    assert r.status_code in (400, 422)
    assert not os.path.exists("/tmp/evil.tif")


# ---------------------------------------------------------------------------
# Max image dimensions (Section 9)
# ---------------------------------------------------------------------------
def test_oversized_image_dimensions_rejected(monkeypatch):
    monkeypatch.setenv("MAX_IMAGE_DIMENSION_PX", "32")
    get_settings.cache_clear()
    try:
        arr = np.random.rand(64, 64).astype(np.float32)
        buf = io.BytesIO()
        tifffile.imwrite(buf, arr)
        buf.seek(0)
        c = client()
        r = c.post("/api/satellite/upload", files={"file": ("big.tif", buf.read(), "image/tiff")})
        assert r.status_code == 422
        assert "exceed" in r.json()["detail"].lower()
    finally:
        get_settings.cache_clear()


def test_image_within_dimension_limit_accepted(monkeypatch):
    monkeypatch.setenv("MAX_IMAGE_DIMENSION_PX", "512")
    get_settings.cache_clear()
    try:
        arr = np.random.rand(64, 64).astype(np.float32)
        buf = io.BytesIO()
        tifffile.imwrite(buf, arr)
        buf.seek(0)
        c = client()
        r = c.post("/api/satellite/upload", files={"file": ("ok.tif", buf.read(), "image/tiff")})
        assert r.status_code == 200
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Temp file cleanup on AIS re-upload (bug fix - see main.py::upload_ais)
# ---------------------------------------------------------------------------
def test_ais_reupload_cleans_up_previous_temp_file():
    c = client()
    content_a = b"mmsi,timestamp,lat,lon\n111,2024-01-01T00:00:00,19.0,71.0\n"
    content_b = b"mmsi,timestamp,lat,lon\n222,2024-01-01T00:00:00,19.1,71.1\n"

    r1 = c.post("/api/ais/upload", files={"file": ("a.csv", content_a, "text/csv")})
    assert r1.status_code == 200
    from app.main import _LAST_UPLOADED_AIS
    first_path = _LAST_UPLOADED_AIS["path"]
    assert os.path.exists(first_path)

    r2 = c.post("/api/ais/upload", files={"file": ("b.csv", content_b, "text/csv")})
    assert r2.status_code == 200
    second_path = _LAST_UPLOADED_AIS["path"]
    assert second_path != first_path
    assert not os.path.exists(first_path), "previous AIS upload's temp file was not cleaned up"
    assert os.path.exists(second_path)


# ---------------------------------------------------------------------------
# Malformed TIFF (garbage bytes with a .tif extension) - defensive, not a 500
# ---------------------------------------------------------------------------
def test_malformed_tiff_returns_422_not_500():
    c = client()
    r = c.post("/api/satellite/upload", files={"file": ("broken.tif", b"garbage-not-a-tiff" * 20, "image/tiff")})
    assert r.status_code == 422
    assert "Traceback" not in r.json()["detail"]


def test_corrupt_image_error_does_not_leak_server_temp_path():
    """Regression test: PIL's own error message for an unreadable image is
    `cannot identify image file '<path it tried to open>'`, and that path
    used to be the server's own random tempfile.NamedTemporaryFile path
    (e.g. '/tmp/tmpsf5dh7b4.png') because we passed str(e) straight through
    to the client. That's an internal-path disclosure - low severity since
    the temp path is random and short-lived, but still unnecessary internal
    detail a client has no business seeing (task requirement: avoid
    exposing unnecessary internal errors). The handler now substitutes the
    user's own original filename before the message leaves the server."""
    c = client()
    r = c.post("/api/satellite/upload", files={"file": ("my_scene.png", b"not a real png" * 20, "image/png")})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "/tmp/" not in detail
    assert "my_scene.png" in detail
