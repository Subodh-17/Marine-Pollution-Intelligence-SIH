# Security Regression Audit & Penetration Review Report

**Date**: 2026-10-24  
**Evaluated Commit**: `048f1319eb1196a099b71f3e155f14948f325484`  
**Target Environments**: Local Test Harness & Live Render API (`marine-pollution-intelligence-api.onrender.com`)  
**Security Status**: **VERIFIED — ZERO UNRESOLVED HIGH OR CRITICAL RISKS**

---

## 1. Remediation Verification Matrix

| Vulnerability ID | Description | Severity | Prior Risk | Remediation Implemented | Verification Evidence |
|---|---|:---:|---|---|---|
| **SEC-01** | Re-analyzed AIS/Attribution Candidates Not Persisted to DB | High | Re-analysis parameters lost upon server restart or LRU cache eviction | Added `_persistence_db.save_investigation(inv)` to `analyze_ais()` and `attribution_analyze()` | Verified via `test_ais_analyze_persists_to_database` and live probe. Persisted radius = 75.0km confirmed. |
| **SEC-02** | Cross-User AIS Upload State Bleed & File Unlink Race | High | Global `_LAST_UPLOADED_AIS` allowed any user to read or unlink concurrent users' temp AIS files | Replaced with per-user scoped `_USER_UPLOADED_AIS` and safe unlinking | Verified via `test_multi_user_ais_upload_isolation`. User B forbidden from accessing User A's uploaded AIS file. |
| **SEC-03** | Missing Ownership Filter in Investigation Listing | Medium | Any user calling `GET /api/investigations` could enumerate all users' investigations | Scoped storage query with `owner=current_user` when `auth_enabled=True` | Verified via `test_investigations_listing_ownership_filter`. User A cannot see User B's records. |
| **DB-01** | PostgreSQL UniqueViolation Crash in Registration | Medium | `psycopg2.errors.UniqueViolation` was not caught by `except sqlite3.IntegrityError`, causing unhandled 500 crashes | Handled via MRO class inspection: `any(c.__name__ in ("IntegrityError", "UniqueViolation") for c in type(e).__mro__)` | Verified via `test_duplicate_user_raises_auth_error_not_unhandled_crash`. OperationalErrors re-raise cleanly. |
| **PERF-01** | Unbounded Memory Growth in Cache `_STORE` | Medium | In-memory dictionary retained all investigations indefinitely, risking container OOM | Implemented `_BoundedInvestigationStore` LRU cache (100 capacity) with DB rehydration | Verified via `test_bounded_investigation_store_eviction` and multi-threaded concurrency test. |
| **DATA-01** | Incomplete JSON Sanitization in Rehydrated Objects | Low | `NaN` / `Inf` in rehydrated database payloads caused client JSON parse failures | Wrapped `to_dict()` with `_sanitize_for_json(raw)` | Verified via `test_rehydrated_investigation_sanitizes_non_finite_values`. Non-finite floats sanitized to 0.0. |
| **UX-01** | React Cascading Renders & Fast Refresh Warnings in `theme.jsx` | Low | Synchronous `setResolvedTheme` inside `useEffect` caused cascading re-renders and dev warnings | Migrated to React 19 `useSyncExternalStore` | Oxlint reports 0 errors and 0 warnings across 40 frontend files. |
| **UX-02** | Recharts Container Dimensions Warning in Headless JSDOM | Low | Missing explicit minimum container bounds triggered dimension calculation warnings in Vitest | Added `minWidth={0} minHeight={240}` to `ResponsiveContainer` | All 52 Vitest frontend unit tests passing cleanly. |

---

## 2. Threat Vector & Penetration Checklist

### A. Authentication & Insecure Direct Object References (IDOR)
- **Token Format**: Signed HMAC-SHA256 (`<username>.<expiry_unix_ts>.<sig>`). Expired tokens and forged signatures are rejected.
- **Investigation Endpoints**: All `/api/spill/{id}/*`, `/api/jobs/{id}`, and `/api/report/{id}` endpoints enforce `check_ownership(inv.owner, current_user)`. Unauthorized requests receive HTTP 404 (non-disclosing) to prevent IDOR enumeration.
- **Horizontal Tenant Isolation**: When `auth_enabled=True`, `_get_user_ais_path()` strictly isolates custom AIS uploads to the authenticated user.

### B. Input Validation & File Upload Safety
- **Path Traversal**: Filename parameters pass through `os.path.basename()` before filesystem resolution. Probing `POST /api/satellite/sample?sample_name=../../bad.tif` returned HTTP 404 without directory traversal.
- **File Upload Exhaustion**:
  - Upload sizes are capped by a streaming chunk counter (`max_upload_size_bytes = 50MB`).
  - Image dimensions are capped before full decompression (`max_image_dimension_px = 10240px`).
  - Unsupported file types (`.exe`, `.bin`) are rejected with HTTP 400.
  - Corrupted TIFF files are trapped and return HTTP 422 without Python tracebacks.

### C. Information Disclosure & Secret Leakage
- **Health Probes**: `/health` and `/ready` expose only boolean status flags and operational metadata. No internal IP addresses, database connection strings, or system paths are emitted.
- **Credentials in Logs**: Database URLs in logs and startup diagnostics pass through `_redact_credentials()`, masking passwords with `***`.
- **Frontend Assets**: Live inspection of Vercel production bundle `index-CzIukL6s.js` confirmed zero private backend secrets or tokens are present.

### D. Cross-Origin Resource Sharing (CORS)
- **Authorized Origin**: Probing `/health` with `Origin: https://marine-pollution-intelligence-sih.vercel.app` returned `Access-Control-Allow-Origin: https://marine-pollution-intelligence-sih.vercel.app`.
- **Unauthorized Origin**: Probing `/health` with `Origin: https://malicious-attacker-domain.evil` resulted in `Access-Control-Allow-Origin: None` (no wildcard or reflected origin).

---

## 3. Concurrency & Storage Resilience

- **Thread-Safe Cache**: `_BoundedInvestigationStore` employs an internal `threading.Lock()` guarding read promotion (`move_to_end`) and write eviction (`popitem(last=False)`), preventing race conditions under concurrent Uvicorn worker threads.
- **Ephemeral Hosting Persistence**: SQLite state is stored in `backend/data/app.db`. On Render Free tier, worker restarts retain state on the local ephemeral volume; container re-provisioning clears local storage unless an external PostgreSQL database is configured via `DATABASE_URL`.
