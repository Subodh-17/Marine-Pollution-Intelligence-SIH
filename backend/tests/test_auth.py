"""
Tests for app/auth.py and its wiring into app/main.py (Section 7).

Every test that flips AUTH_ENABLED restores it afterward via the
`_auth_enabled` fixture below, and uses the per-test isolated database from
conftest.py's autouse `_isolated_database` fixture so user accounts created
here never leak between tests or touch the real backend/data/app.db.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import auth as auth_module
from app.config import get_settings


@pytest.fixture
def auth_enabled(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-secret-key-for-pytest-only")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def client():
    from app.main import app
    return TestClient(app)


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------
def test_password_hashing_round_trip():
    h, salt = auth_module.hash_new_password("correct horse battery staple")
    assert auth_module.verify_password("correct horse battery staple", h, salt)
    assert not auth_module.verify_password("wrong password", h, salt)


def test_password_hash_is_not_plaintext():
    h, salt = auth_module.hash_new_password("mysecret")
    assert "mysecret" not in h
    assert "mysecret" not in salt


def test_same_password_different_salts_different_hashes():
    h1, s1 = auth_module.hash_new_password("samepassword")
    h2, s2 = auth_module.hash_new_password("samepassword")
    assert s1 != s2
    assert h1 != h2


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------
def test_token_round_trip(monkeypatch):
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-secret")
    get_settings.cache_clear()
    token = auth_module.create_access_token("alice")["access_token"]
    assert auth_module.verify_token(token) == "alice"
    get_settings.cache_clear()


def test_tampered_token_rejected(monkeypatch):
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-secret")
    get_settings.cache_clear()
    token = auth_module.create_access_token("alice")["access_token"]
    tampered = token[:-2] + ("aa" if token[-2:] != "aa" else "bb")
    assert auth_module.verify_token(tampered) is None
    get_settings.cache_clear()


def test_garbage_token_rejected():
    assert auth_module.verify_token("not-a-real-token") is None
    assert auth_module.verify_token("") is None


def test_expired_token_rejected(monkeypatch):
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-secret")
    monkeypatch.setenv("AUTH_TOKEN_EXPIRE_MINUTES", "0")
    get_settings.cache_clear()
    token = auth_module.create_access_token("alice")["access_token"]
    import time
    time.sleep(1.1)
    assert auth_module.verify_token(token) is None
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# User creation / authentication
# ---------------------------------------------------------------------------
def test_create_and_authenticate_user():
    auth_module.create_user("bob", "bobpassword")
    assert auth_module.authenticate("bob", "bobpassword") is True
    assert auth_module.authenticate("bob", "wrongpassword") is False
    assert auth_module.authenticate("nonexistent", "anything") is False


def test_duplicate_username_rejected():
    auth_module.create_user("carol", "password123")
    with pytest.raises(auth_module.AuthError):
        auth_module.create_user("carol", "differentpassword")


def test_short_password_rejected():
    with pytest.raises(auth_module.AuthError):
        auth_module.create_user("dave", "abc")


# ---------------------------------------------------------------------------
# get_current_user / disabled-by-default behavior
# ---------------------------------------------------------------------------
def test_auth_disabled_by_default_requires_no_header():
    c = client()
    r = c.post("/api/spill/detect", params={"mode": "demo"})
    assert r.status_code == 200
    inv_id = r.json()["id"]
    r2 = c.get(f"/api/spill/{inv_id}")
    assert r2.status_code == 200


def test_public_config_reports_auth_status():
    c = client()
    r = c.get("/api/config")
    assert r.status_code == 200
    assert "auth_enabled" in r.json()


# ---------------------------------------------------------------------------
# Auth enabled: login flow + protected routes + ownership
# ---------------------------------------------------------------------------
def test_login_and_register_endpoints(auth_enabled):
    c = client()
    r = c.post("/api/auth/register", json={"username": "erin", "password": "erinpassword"})
    assert r.status_code == 200
    assert "access_token" in r.json()

    r2 = c.post("/api/auth/login", json={"username": "erin", "password": "erinpassword"})
    assert r2.status_code == 200
    assert "access_token" in r2.json()

    r3 = c.post("/api/auth/login", json={"username": "erin", "password": "wrongpassword"})
    assert r3.status_code == 401


def test_protected_route_rejects_missing_token(auth_enabled):
    c = client()
    r = c.post("/api/spill/detect", params={"mode": "demo"})
    assert r.status_code == 401


def test_protected_route_accepts_valid_token(auth_enabled):
    c = client()
    reg = c.post("/api/auth/register", json={"username": "frank", "password": "frankpassword"})
    token = reg.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    r = c.post("/api/spill/detect", params={"mode": "demo"}, headers=headers)
    assert r.status_code == 200
    inv_id = r.json()["id"]

    r2 = c.get(f"/api/spill/{inv_id}", headers=headers)
    assert r2.status_code == 200


def test_ownership_blocks_other_users(auth_enabled):
    c = client()
    reg1 = c.post("/api/auth/register", json={"username": "grace", "password": "gracepassword"})
    token1 = reg1.json()["access_token"]
    reg2 = c.post("/api/auth/register", json={"username": "heidi", "password": "heidipassword"})
    token2 = reg2.json()["access_token"]

    r = c.post("/api/spill/detect", params={"mode": "demo"}, headers={"Authorization": f"Bearer {token1}"})
    assert r.status_code == 200
    inv_id = r.json()["id"]

    # heidi cannot see grace's investigation.
    r2 = c.get(f"/api/spill/{inv_id}", headers={"Authorization": f"Bearer {token2}"})
    assert r2.status_code == 404

    # grace can see her own.
    r3 = c.get(f"/api/spill/{inv_id}", headers={"Authorization": f"Bearer {token1}"})
    assert r3.status_code == 200


def test_me_endpoint(auth_enabled):
    c = client()
    reg = c.post("/api/auth/register", json={"username": "ivan", "password": "ivanpassword"})
    token = reg.json()["access_token"]
    r = c.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["username"] == "ivan"


def test_demo_account_seeded_lazily(auth_enabled):
    c = client()
    settings = get_settings()
    r = c.post("/api/auth/login", json={"username": settings.auth_demo_username, "password": settings.auth_demo_password})
    assert r.status_code == 200
