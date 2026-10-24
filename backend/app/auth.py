"""
Lightweight authentication (Section 7).

DISABLED BY DEFAULT (`AUTH_ENABLED=false`) so every existing route, the
existing frontend, and every pre-existing test keep working exactly as
before with zero configuration - this mirrors the "if authentication would
break the existing demo, provide a clearly documented demo mode" language
in the upgrade request. See DEMO_GUIDE.md for how to turn it on.

WHAT THIS IS
------------
  - Username/password login (POST /api/auth/login) against a `users` table
    in the same SQLite database investigations are stored in
    (app/persistence/db.py), with a self-registration endpoint
    (POST /api/auth/register) for a local prototype's convenience.
  - Passwords are hashed with PBKDF2-HMAC-SHA256 (stdlib `hashlib`, no new
    dependency) with a random per-user salt and a modern iteration count -
    never stored or logged in plaintext, never compared with `==` (uses
    `hmac.compare_digest`).
  - Access tokens are a minimal, dependency-free scheme: HMAC-SHA256 over
    "<username>.<expiry_unix_ts>" using AUTH_SECRET_KEY, base64url-encoded
    alongside the payload - genuinely tamper-evident (a forged/edited token
    fails signature verification) and genuinely time-limited, but this is
    NOT a JWT library and does not implement JWT's algorithm-negotiation
    surface (deliberately - that surface is a common source of real
    vulnerabilities, and this app has no need for interop with an external
    JWT consumer).
  - A demo account (DEMO_USERNAME/DEMO_PASSWORD, both env-overridable) is
    seeded lazily on first login attempt IF AND ONLY IF that username
    doesn't already exist as a real registered user - it is never silently
    reset to the default password once a real user has claimed that
    username.

WHAT THIS IS NOT
-----------------
  - Not OAuth/OIDC, not session cookies, not CSRF-protected form login, not
    role-based access control - "lightweight authentication suitable for a
    local prototype" per the request, not a production IdP. AUTH_SECRET_KEY
    defaults to an obviously-fake value (`auth_secret_key` in
    app/config.py) - set a real random secret via env var for any
    deployment where this is more than a local demo.
  - Not multi-tenant-hardened: `AUTH_ALLOW_REGISTRATION=true` (the default)
    lets anyone create an account, appropriate for a local SIH prototype,
    not for an internet-facing deployment.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from dataclasses import dataclass

from fastapi import Header, HTTPException

from app.config import get_settings
from app.persistence import db as _db
from app.logging_config import get_logger

_log = get_logger("auth")

_PBKDF2_ITERATIONS = 200_000
_ANONYMOUS_USER = "anonymous"


class AuthError(Exception):
    pass


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------
def _hash_password(password: str, salt: bytes) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return base64.b64encode(dk).decode("ascii")


def hash_new_password(password: str) -> tuple[str, str]:
    """Returns (password_hash, salt), both base64-encoded, ready to store."""
    salt = os.urandom(16)
    return _hash_password(password, salt), base64.b64encode(salt).decode("ascii")


def verify_password(password: str, password_hash: str, salt_b64: str) -> bool:
    salt = base64.b64decode(salt_b64)
    candidate = _hash_password(password, salt)
    return hmac.compare_digest(candidate, password_hash)


# ---------------------------------------------------------------------------
# User store (SQLite - same DB as investigations, see app/persistence/db.py)
# ---------------------------------------------------------------------------
def create_user(username: str, password: str) -> None:
    if not username or not username.strip():
        raise AuthError("Username cannot be empty.")
    if len(password) < 4:
        raise AuthError("Password must be at least 4 characters.")
    username = username.strip()
    password_hash, salt = hash_new_password(password)
    import sqlite3
    from datetime import datetime, timezone
    with _db._connect() as conn:
        try:
            conn.execute(
                "INSERT INTO users (username, password_hash, salt, created_at) VALUES (?, ?, ?, ?)",
                (username, password_hash, salt, datetime.now(timezone.utc).isoformat()),
            )
        except Exception as e:
            if any(c.__name__ in ("IntegrityError", "UniqueViolation") for c in type(e).__mro__):
                raise AuthError(f"Username '{username}' is already taken.")
            raise


def _get_user_row(username: str):
    with _db._connect() as conn:
        return conn.execute(
            "SELECT username, password_hash, salt FROM users WHERE username = ?", (username,)
        ).fetchone()


def _seed_demo_user_if_absent() -> None:
    """Lazily creates the configured demo account the first time it's
    needed, but only if that username hasn't already been claimed by a real
    registered user - never overwrites an existing password."""
    settings = get_settings()
    if _get_user_row(settings.auth_demo_username) is not None:
        return
    try:
        create_user(settings.auth_demo_username, settings.auth_demo_password)
        _log.info("demo_user_seeded", extra={"username": settings.auth_demo_username})
    except AuthError:
        pass  # lost a race with a concurrent request seeding it - fine


def authenticate(username: str, password: str) -> bool:
    """Verifies a username/password pair. Seeds the configured demo account
    on first use so a fresh install can log in immediately with the
    documented demo credentials without a separate setup step."""
    settings = get_settings()
    if username == settings.auth_demo_username:
        _seed_demo_user_if_absent()
    row = _get_user_row(username)
    if row is None:
        return False
    return verify_password(password, row["password_hash"], row["salt"])


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------
def _sign(payload: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def create_access_token(username: str) -> dict:
    settings = get_settings()
    expires_at = int(time.time() + settings.auth_token_expire_minutes * 60)
    payload = f"{username}.{expires_at}"
    signature = _sign(payload, settings.auth_secret_key)
    token = base64.urlsafe_b64encode(f"{payload}.{signature}".encode("utf-8")).decode("ascii")
    return {"access_token": token, "token_type": "bearer", "expires_in_seconds": settings.auth_token_expire_minutes * 60}


def verify_token(token: str) -> str | None:
    """Returns the username if `token` is well-formed, correctly signed,
    and not expired; otherwise None. Never raises on malformed input - a
    tampered/garbage token is just an invalid token, not a server error."""
    settings = get_settings()
    try:
        decoded = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
        username, expires_at_str, signature = decoded.rsplit(".", 2)
        expires_at = int(expires_at_str)
    except Exception:
        return None
    payload = f"{username}.{expires_at}"
    expected_signature = _sign(payload, settings.auth_secret_key)
    if not hmac.compare_digest(signature, expected_signature):
        return None
    if time.time() > expires_at:
        return None
    return username


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------
def get_current_user(authorization: str | None = Header(default=None)) -> str:
    """FastAPI dependency for protected routes. When AUTH_ENABLED=false (the
    default), returns a fixed "anonymous" identity and never requires a
    header - existing behavior, unchanged. When AUTH_ENABLED=true, requires
    a valid `Authorization: Bearer <token>` header (obtained from
    POST /api/auth/login)."""
    settings = get_settings()
    if not settings.auth_enabled:
        return _ANONYMOUS_USER
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header. Login via POST /api/auth/login.")
    token = authorization.split(" ", 1)[1].strip()
    username = verify_token(token)
    if not username:
        raise HTTPException(401, "Invalid or expired access token.")
    return username


def check_ownership(resource_owner: str | None, current_user: str) -> None:
    """Enforces per-owner access when auth is enabled. When auth is
    disabled, every investigation's owner is "anonymous" and every caller's
    current_user is also "anonymous", so this is always a no-op - existing
    behavior, unchanged."""
    settings = get_settings()
    if not settings.auth_enabled:
        return
    if resource_owner and resource_owner != current_user:
        # 404, not 403: per Section 7 ("unauthenticated users cannot access
        # private investigation data"), this deliberately does not confirm
        # to a caller whether an investigation ID they don't own exists at
        # all - the same response as a genuinely-nonexistent id.
        raise HTTPException(404, "Investigation not found")
