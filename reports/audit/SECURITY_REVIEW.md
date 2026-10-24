# Security & API Review

**Date**: 2026-10-24
**Audit Scope**: Backend authentication, authorization, IDOR, session & CSRF, CORS, file upload safety, rate limits, sensitive logging, database queries, and secret management.

---

## 1. Endpoint Classification & Authorization Matrix

| Endpoint | Method | Classification | Required Auth | Server-side Enforced? | CSRF / CORS Protected | Audit Notes |
|---|---|---|---|---|---|---|
| `/health` | GET | Public | None | N/A | CORS Restricted | Basic liveness probe. Emits no internal diagnostics or secrets. |
| `/api/health` | GET | Public | None | N/A | CORS Restricted | Backward-compatible frontend health ping. |
| `/ready` | GET | Public | None | N/A | CORS Restricted | Readiness probe with ML runtime & DB status booleans. |
| `/api/config` | GET | Public | None | N/A | CORS Restricted | Non-secret public configuration only. |
| `/api/command-center` | GET | Public Read / Authenticated | Optional/Bearer | Yes | CORS Restricted | Bento grid summary metrics. |
| `/api/investigations` | GET | Authenticated Read | Bearer (if Auth Enabled) | Yes | CORS Restricted | Filtered to `current_user` when auth enabled (SEC-03 remediation). |
| `/api/auth/login` | POST | Public Mutation | None | Yes | CORS Restricted | Authenticates user credentials via PBKDF2-HMAC-SHA256. |
| `/api/auth/register` | POST | Public Mutation | None | Yes | CORS Restricted | Creates user account. Gated by `AUTH_ALLOW_REGISTRATION`. |
| `/api/auth/me` | GET | Authenticated Read | Bearer (if Auth Enabled) | Yes | CORS Restricted | Returns current user identity. |
| `/api/spill/detect` | POST | State-Changing | Bearer (if Auth Enabled) | Yes | CORS Restricted | End-to-end pipeline execution. Input file bound per-user. |
| `/api/ais/upload` | POST | State-Changing Upload | Bearer (if Auth Enabled) | Yes | CORS Restricted | Streaming size cap (50MB) + path traversal guard + per-user isolation. |
| `/api/satellite/upload` | POST | State-Changing Upload | Bearer (if Auth Enabled) | Yes | CORS Restricted | Hard dimension check (10240px) + streaming cap + path traversal guard. |
| `/api/satellite/samples` | GET | Public Read | None | Yes | CORS Restricted | List bundled real SAR scenes. |
| `/api/satellite/sample` | POST | State-Changing | Bearer (if Auth Enabled) | Yes | CORS Restricted | Analyze bundled scene with path traversal prevention (`os.path.basename`). |
| `/api/jobs/investigation` | POST | State-Changing Async | Bearer (if Auth Enabled) | Yes | CORS Restricted | Async worker dispatch with owner tracking. |
| `/api/jobs/{job_id}` | GET | Authenticated Read | Bearer (if Auth Enabled) | Yes | CORS Restricted | Enforces `check_ownership(job.owner, current_user)`. |
| `/api/jobs/{job_id}/cancel` | POST | State-Changing Mutation | Bearer (if Auth Enabled) | Yes | CORS Restricted | Enforces `check_ownership(job.owner, current_user)`. |
| `/api/ml/status` | GET | Public Read | None | Yes | CORS Restricted | Honest model provenance and runtime status. |
| `/api/ml/training-history` | GET | Public Read | None | Yes | CORS Restricted | Training curves from verified log files. |
| `/api/ml/metrics` | GET | Public Read | None | Yes | CORS Restricted | Evaluation report metrics without fabrication. |
| `/api/ml/dataset-status` | GET | Public Read | None | Yes | CORS Restricted | Real Zenodo intake status on disk. |
| `/api/spill/{id}` | GET | Authenticated Read | Bearer (if Auth Enabled) | Yes | CORS Restricted | Enforces `check_ownership(inv.owner, current_user)`. |
| `/api/spill/{id}/geometry` | GET | Authenticated Read | Bearer (if Auth Enabled) | Yes | CORS Restricted | Enforces `check_ownership(inv.owner, current_user)`. |
| `/api/spill/{id}/hindcast` | GET | Authenticated Read | Bearer (if Auth Enabled) | Yes | CORS Restricted | Enforces `check_ownership(inv.owner, current_user)`. |
| `/api/spill/{id}/forecast` | GET | Authenticated Read | Bearer (if Auth Enabled) | Yes | CORS Restricted | Enforces `check_ownership(inv.owner, current_user)`. |
| `/api/ais/candidates` | GET | Authenticated Read | Bearer (if Auth Enabled) | Yes | CORS Restricted | Enforces `check_ownership(inv.owner, current_user)`. |
| `/api/ais/analyze` | POST | State-Changing Mutation | Bearer (if Auth Enabled) | Yes | CORS Restricted | Re-filters AIS; persists to DB; isolated per user. |
| `/api/vessels/ranking` | GET | Authenticated Read | Bearer (if Auth Enabled) | Yes | CORS Restricted | Enforces `check_ownership(inv.owner, current_user)`. |
| `/api/vessels/{mmsi}` | GET | Authenticated Read | Bearer (if Auth Enabled) | Yes | CORS Restricted | Enforces `check_ownership(inv.owner, current_user)`. |
| `/api/attribution/analyze` | POST | State-Changing Mutation | Bearer (if Auth Enabled) | Yes | CORS Restricted | Re-scores weights; persists to DB; isolated per user. |
| `/api/report/{id}` | GET | Authenticated Read | Bearer (if Auth Enabled) | Yes | CORS Restricted | Generates MD/CSV/PDF; enforces `check_ownership`. |
| `/api/export/{id}/*` | GET | Authenticated Read | Bearer (if Auth Enabled) | Yes | CORS Restricted | GeoJSON/CSV exports; enforces `check_ownership`. |

---

## 2. Threat & Vulnerability Checklist

- [x] **Authentication Bypasses & Privilege Escalation**: Tokens are signed with HMAC-SHA256 over `<username>.<expiry_unix_ts>`. Forged or expired tokens are rejected. Startup validates that insecure default secret is warned against in deploy logs.
- [x] **Insecure Direct Object References (IDOR)**: Fixed in SEC-02 (`_USER_UPLOADED_AIS` isolation) and SEC-03 (`/api/investigations` listing scoped to `current_user`). All `/api/spill/{id}/*` endpoints enforce `check_ownership` returning 404 (non-disclosing).
- [x] **SQL / PostGIS Injection**: All queries in `app/persistence/db.py` use parameterized placeholders (`?` translated to `%s` for PostgreSQL). No user content is concatenated into SQL.
- [x] **Path Traversal & Unsafe File Handling**:
  - Uploaded files are written to randomized temporary names via `tempfile.NamedTemporaryFile`.
  - Filename extensions are strictly allowlisted (`.tif`, `.tiff`, `.geotiff`, `.png`, `.jpg`, `.jpeg`, `.webp`, `.csv`).
  - Sample scenes use `os.path.basename()` to prevent directory traversal.
- [x] **SSRF & Unsafe URL Fetching**: Live weather provider URL (`ENV_LIVE_PROVIDER_URL`) is strictly operator-configured via environment variables; never accepted from caller query parameters.
- [x] **Token & Secret Leakage in Logs / Bundles**:
  - `_redact_credentials()` masks passwords in database URLs.
  - Logging middleware logs method, path, status, and duration, never authorization headers or body payloads.
  - Frontend production bundle inspects only `VITE_API_BASE_URL`; no private backend secrets exist in frontend assets.
- [x] **CORS Configuration**: Restricted to explicit list from `CORS_ALLOWED_ORIGINS` (Vercel production URL + localhost). Verified in tests.
- [x] **File Upload Exhaustion & Decompression Bombs**:
  - Hard byte limit enforced by streaming chunk size counter (`_save_upload_with_size_limit`).
  - Hard dimension limit checked before decoding (`_check_image_dimensions` rejects dimensions exceeding `max_image_dimension_px = 10240px`).
- [x] **Information Disclosure in Error Handlers**:
  - Unhandled exceptions caught by global exception handler, logged server-side, and transformed to generic `{detail: "Internal server error", request_id: ...}` without Python tracebacks.
  - File upload errors sanitize server temporary filesystem paths, substituting original user filenames.
