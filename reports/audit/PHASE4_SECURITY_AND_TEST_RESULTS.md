# Phase 4.1 — Security, Resource Limits & Full Test Results

**Repository**: `Marine-Pollution-Intelligence-SIH`  
**Baseline Commit**: `048f1319eb1196a099b71f3e155f14948f325484`  
**Test Suite**: Backend (Pytest) + Frontend (Oxlint, Vitest, Vite Build)  
**Date**: October 2026  
**Auditor**: Antigravity Quality & Application Security Lead  

---

## 1. Security & Resource Limit Audit

### 1.1 Upload & File Processing Controls
- **Payload Size Enforcement**: Enforced via FastAPI body limits (50 MB max per upload). Files exceeding this limit are rejected before parsing.
- **Image Dimension Bounds**: Maximum image dimension capped at $8000 \times 8000$ pixels (`max_image_dimension_px`), mitigating decompressed-memory denial of service (decompression bombs).
- **Format Validation**: Only valid GeoTIFF (`image/tiff`) and PNG (`image/png`) files are decoded. Unknown binaries or text files are rejected with HTTP 400.
- **Path Traversal Sanitization**: Filenames in sample endpoints and file handlers are strictly passed through `os.path.basename()` and verified against whitelisted parent directories. Traversal sequences (`../`, `..\\`) are stripped.

### 1.2 Authentication & Multi-Tenant Access
- **Ownership Verification**: All investigation and report endpoints verify the calling user's token (`sub` claim) matches the investigation record's `owner`.
- **Public Demo Sandboxing**: Requests without authentication tokens operate in a sandboxed demo session, preventing access to private user records.
- **CORS Configuration**: Controlled via `CORS_ALLOWED_ORIGINS` environment variable, preventing arbitrary origin cross-site access in production.

---

## 2. Full Test Suite Verification

### 2.1 Backend Test Results (Pytest)
Command:
```bash
python -m pytest backend/tests -q
```
**Results**:
- **Passed**: 460 tests
- **Skipped**: 28 tests (Optional external integrations: live Kafka broker, external Copernicus hub credentials, live PostgreSQL instance)
- **Failed**: 0 tests
- **Errors**: 0 errors
- **Execution Time**: 111.27 seconds

#### Targeted Phase 4 & 4.1 Regression Suite (`test_phase4_hardening_regressions.py`)
| Test Case | Objective | Result |
| :--- | :--- | :---: |
| `test_failed_db_persistence_returns_false_and_does_not_crash` | Verifies DB failure logs ERROR and returns False cleanly | **PASSED** |
| `test_rehydration_preserves_all_attributes_across_cache_wipe` | Verifies full investigation rehydration from database | **PASSED** |
| `test_rehydration_preserves_ownership_and_tenant_filtering` | Verifies cross-tenant isolation after DB rehydration | **PASSED** |
| `test_empty_ml_mask_never_reports_misleading_ml_provenance` | Verifies fallback is accurately labeled upon empty ML mask | **PASSED** |
| `test_lookalike_scene_attribution_strictly_gated` | Verifies attribution is refused on lookalike SAR anomalies | **PASSED** |
| `test_missing_ais_data_does_not_impute_guilt` | Verifies neutral scoring and missing data warnings | **PASSED** |
| `test_uncertain_attribution_fails_closed` | Verifies ambiguous model probabilities fail closed | **PASSED** |
| `test_unpersisted_investigation_evicted_from_memory_cache_on_db_failure` | Verifies in-memory cache consistency on DB save failure | **PASSED** |
| `test_mandatory_persistence_failure_raises_and_returns_500` | Verifies HTTP 500 when mandatory persistence fails on run | **PASSED** |
| `test_reanalysis_fails_with_500_when_mandatory_persistence_fails` | Verifies HTTP 500 when mandatory persistence fails on re-analysis | **PASSED** |

---

### 2.2 Frontend Test & Build Results

#### Static Analysis & Lint (Oxlint)
Command:
```bash
npm --prefix frontend run lint
```
**Output**:
- 0 warnings, 0 errors across 40 files (104 rules).

#### Unit & Component Tests (Vitest)
Command:
```bash
npm --prefix frontend test
```
**Output**:
- **Test Files**: 11 passed (11 total)
- **Tests**: 52 passed (52 total)
- **Duration**: 27.68 seconds

#### Production Bundle Build (Vite)
Command:
```bash
npm --prefix frontend run build
```
**Output**:
- `dist/index.html`: 1.88 kB
- `dist/assets/index-k0MbK6mK.css`: 51.10 kB
- `dist/assets/index-FiybgWwZ.js`: 913.45 kB (gzip: 270.33 kB)
- Status: **SUCCESS** in 626ms.

---

## 3. Secret & Credential Leakage Scan

- Git diff inspect: Zero API keys, production tokens, database passwords, or private URLs added.
- All configuration remains parameter-driven via environment variables (`DATABASE_URL`, `DATABASE_MANDATORY`, `AUTH_SECRET_KEY`, `CORS_ALLOWED_ORIGINS`).
