# Release & Production Verification Report

**Release Date & Time**: 2026-10-24 19:05 IST  
**Repository**: `Subodh-17/Marine-Pollution-Intelligence-SIH`  
**Target Branch**: `main`  
**Base Commit**: `f98a416cd348f529baf3af2a39aac36ab2e5da12`  
**Published Commit**: `048f1319eb1196a099b71f3e155f14948f325484`  
**Verdict**: **DEPLOYED — VALIDATION LIMITATIONS REMAIN**

---

## 1. Publication Commit Summary

The audited remediations and regression test suite were cleanly committed and pushed to `origin/main` via normal fast-forward.

- **Commit Message**: `fix: harden investigation persistence, AIS isolation, and database error handling`
- **Commit SHA**: `048f1319eb1196a099b71f3e155f14948f325484`
- **Parent SHA**: `f98a416cd348f529baf3af2a39aac36ab2e5da12`
- **Files Changed (11)**:
  - `backend/app/auth.py` (MRO-based database-agnostic integrity exception handling)
  - `backend/app/investigation/orchestrator.py` (Thread-safe bounded LRU store + float sanitization)
  - `backend/app/main.py` (AIS re-analysis persistence, per-user temp AIS isolation, investigations listing ownership filter)
  - `frontend/src/components/tabs/MlTrainingTab.jsx` (ResponsiveContainer explicit min-dimensions)
  - `frontend/src/lib/theme.jsx` (useSyncExternalStore migration for system theme preference)
  - `backend/tests/test_audit_remediation_regressions.py` (9 targeted regression tests)
  - `reports/audit/MASTER_AUDIT.md` (Master audit catalog)
  - `reports/audit/PRODUCTION_READINESS.md` (Release gate assessment)
  - `reports/audit/REMEDIATION_LOG.md` (Granular iteration log)
  - `reports/audit/SECURITY_REVIEW.md` (Endpoint matrix & threat checklist)
  - `reports/audit/TEST_EVIDENCE.md` (Test evidence and probe matrix)

---

## 2. GitHub Actions CI Verification

- **Workflow Name**: `CI`
- **Run ID**: `40098447453`
- **Run URL**: https://github.com/Subodh-17/Marine-Pollution-Intelligence-SIH/actions/runs/40098447453
- **Trigger**: Push to `main` (`048f131`)
- **Overall Status**: **PASSED (Exit Code 0)**

### Job Details:
1. **Backend Pytest Suite** (`ID 120885489840`):
   - Duration: 1m 21s
   - Status: **SUCCESS**
   - Result: `450 passed, 28 skipped, 5 warnings in 52.99s`
2. **Frontend Lint, Test & Build** (`ID 120885490148`):
   - Duration: 38s
   - Status: **SUCCESS**
   - Result:
     - Oxlint: `Found 0 warnings and 0 errors across 40 files`
     - Vitest: `52 passed across 11 test files in 23.76s`
     - Vite Build: `✓ built in 429ms`

---

## 3. Skipped-Test Accounting & Environment Boundaries

The 28 skipped backend tests were audited for environmental necessity:
1. **Offline ML Training & Evaluation (21 tests)**:
   - Tests covering PyTorch checkpoint training (`train.py`), scene classifier training (`train_scene_classifier.py`), augmentation, and loss weight calculations.
   - *Rationale*: PyTorch (~2GB) is deliberately excluded from `backend/requirements.txt` to keep the production Docker/Render container lightweight and performant under Render Free tier's 512MB RAM cap. Production serves models exclusively via ONNX Runtime CPU (`onnxruntime>=1.17`). PyTorch is isolated in `requirements-ml.txt`.
2. **Multi-Store PostgreSQL Integration (7 tests)**:
   - `backend/tests/test_persistence_postgres.py` (7 tests)
   - *Rationale*: Skipped via `pytestmark = pytest.mark.skipif(not _postgres_reachable())` because `psycopg2` is not installed in the standard lightweight environment. SQLite provides full persistence by default.

---

## 4. Live Production Smoke Test Execution

A non-destructive smoke probe suite was executed against the live production instances:
- **Production Frontend**: `https://marine-pollution-intelligence-sih.vercel.app`
- **Production Backend**: `https://marine-pollution-intelligence-api.onrender.com`

| Target / Endpoint | Probe Method | HTTP Status | Evidence / Payload Received | Outcome |
|---|---|:---:|---|:---:|
| `GET /health` | Liveness Ping | **200 OK** | `{"status":"ok","version":"0.1.0","env":"production"}` | **PASS** |
| `GET /ready` | Readiness Probe | **200 OK** | `{"status":"ready","ml_available":true,"checks":{"synthetic_pipeline":"ok","ml_runtime":"onnxruntime_cpu","ml_available":true,"database":"ok"}}` | **PASS** |
| `GET /api/satellite/samples` | Bundled Scenes Listing | **200 OK** | 4 sample SAR scenes enumerated | **PASS** |
| `POST /api/satellite/sample?sample_name=../../bad.tif` | Path Traversal Guard | **404 Not Found** | `{"detail":"Sample SAR scene 'bad.tif' not found.","code":"HTTP_404"}` (Path traversal stripped safely) | **PASS** |
| `POST /api/ais/upload` | Content-Type Guard | **422 Unprocessable** | `{"detail":"file: Field required"}` (Non-multipart request rejected without server crash) | **PASS** |
| `GET https://marine-pollution-intelligence-sih.vercel.app/` | SPA Root HTML | **200 OK** | 1,883 bytes HTML shell with `<div id="root">` | **PASS** |
| `POST /api/spill/detect?mode=demo` | End-to-End Investigation | **200 OK** | Investigation `e93035a8` completed; 3 candidate vessels scored; full telemetry returned | **PASS** |

**Summary**: 7/7 production smoke tests passed cleanly without modifying production storage.

---

## 5. Remaining Security, Scientific, and Operational Limitations

In accordance with Section 19 and honest disclosure standards:
1. **Scientific / Heuristic Nature**:
   - The classical detector is a morphological dark-spot detection heuristic, not an absolute proof of oil presence. Organic slicks, biogenic slicks, and wind lookalikes can generate false positives.
   - The ONNX-served ML model was trained on Zenodo Sentinel-1 SAR tiles; it is an experimental demonstration model and should not be relied upon as certified legal evidence.
   - The attribution score is a multi-criteria heuristic index (0–100), not a calibrated probability of guilt.
2. **Operational Constraints**:
   - Single-node SQLite persistence is the default on ephemeral hosting unless an external PostgreSQL database is provisioned.
   - Render Free tier spins down during inactivity; cold starts can take 45–60 seconds on initial health pings.

---

## 6. Verdict Sign-Off

### **DEPLOYED — VALIDATION LIMITATIONS REMAIN**

- Publication fast-forward to `main`: **SUCCESS**
- GitHub Actions CI Run (`40098447453`): **SUCCESS (450 backend, 52 frontend passed)**
- Production Smoke Tests: **SUCCESS (7/7 passed)**
- Disclosures & Honesty Constraints: **VERIFIED**
