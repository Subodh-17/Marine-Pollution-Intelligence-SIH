# Marine Pollution Intelligence — Production Deployment Report

**Release Date & Timestamp:** 2026-10-24T15:52:00+05:30  
**Project Workspace:** `E:\Projects\Marine_Pollution_Intelligence_SIH_FINAL`  
**Engineer Roles:** Senior Full-Stack Engineer, DevOps Engineer, Security Reviewer, Release Manager  
**Environment Targets:** GitHub (`origin/main`) · Render (`marine-pollution-intelligence-api`) · Vercel (`marine-pollution-intelligence-sih`)

---

## 1. Executive Summary & Verification Highlights

The Marine Pollution Intelligence platform has been successfully audited, hardened, synchronized to GitHub, and verified end-to-end across its live production deployment:

- **GitHub Source Control:** Synchronized to `https://github.com/Subodh-17/Marine-Pollution-Intelligence-SIH` on branch `main` at commit `d7b716a`.
- **GitHub Actions CI:** Workflow `CI` Run `#40085381747` passed 100% (Backend Pytest Suite in 1m26s, Frontend Lint, Test & Build in 36s).
- **Backend on Render:** Verified live at `https://marine-pollution-intelligence-api.onrender.com` (Uvicorn + FastAPI, ONNX Runtime CPU, SQLite persistent store, CORS configured).
- **Frontend on Vercel:** Verified live at `https://marine-pollution-intelligence-sih.vercel.app` (React 19, Vite 8, production bundle targeting Render API, zero localhost leaks).
- **End-to-End Pipeline:** Real pipeline run executed live on Render (`POST /api/spill/detect?mode=demo`), producing investigation `567dd028` with 3 ranked vessels, full geometric polygon, drift tracks, and an 8.5 KB markdown report.

---

## 2. Baseline Project Architecture & Findings

### Architecture
- **Frontend:** React 19.2.8, Vite 8.2.2, Leaflet/React-Leaflet 5.0, Recharts 3.10, Axios 1.20, Oxlint, Vitest 4.1.
- **Backend:** FastAPI, Uvicorn, Python 3.11, ONNX Runtime 1.31 (CPU Execution Provider), NumPy, SciPy.
- **Database:** SQLite local persistent storage (`backend/data/app.db`) with graceful in-memory degradation, PostgreSQL compatible via `DATABASE_URL`.
- **ML Engine:** SpillUNet segmentation and SceneClassifierV2 3-class classification exported to ONNX format with cryptographic SHA-256 validation.

### Pre-Deployment Audit Findings
1. **Render Endpoint Gap:** The previously deployed backend on Render (commit `219256f`) lacked the `/api/command-center` endpoint, returning HTTP 404 to the dashboard.
2. **Lookalike Fail-Closed Policy:** The existing deployment could score vessels on lookalike dark spots; local updates enforce a fail-closed safety policy (`ABSTAINED_LOOKALIKE`) preventing false accusations.
3. **Test Suite Isolation:** Pytest tests calling `/api/ml/dataset-status` without an explicit mock previously scanned 6,044 files on the host's `D:\zenodo_sar` drive. This was isolated in `backend/conftest.py` via `DATASET_ROOT` monkeypatching, reducing test suite execution from ~10 minutes to under 2.5 minutes.
4. **CI Node Version Mismatch:** GitHub Actions runners using Node 20 suffered a TypeError with `undici` in `package-lock.json` (`webidl.util.markAsUncloneable`); updating CI to Node 22 completely resolved the issue.

---

## 3. Files Changed & Reasons

| File | Change Nature | Justification |
|---|---|---|
| `render.yaml` | New | Render Blueprint configuring Python web service, start command, health checks, environment variables, and CORS. |
| `vercel.json` | New | Root Vercel configuration for SPA routing rewrites and static asset caching headers. |
| `frontend/vercel.json` | New | Subdirectory Vercel configuration ensuring clean build and routing when root directory is set to `frontend`. |
| `.github/workflows/ci.yml` | New | Automated CI workflow validating backend ONNX verification, pytest, frontend lint, vitest, and build on Node 22. |
| `backend/conftest.py` | Modified | Added `DATASET_ROOT` isolation fixture preventing tests from scanning real multi-gigabyte disk directories. |
| `backend/tests/test_lookalike_study.py` | Modified | Added graceful `pytest.skip` when optional PyTorch library is absent, matching base requirements. |
| `frontend/src/components/tabs/RankingTab.jsx` | Modified | Removed unused `isGated` variable for lint hygiene. |
| `frontend/src/components/tabs/SatelliteTab.jsx` | Modified | Removed unused `modelInfo` variable for lint hygiene. |
| `frontend/src/components/tabs/DashboardTab.jsx` | Modified | Removed unused `loadingCommand`, `dataSources`, and `sceneClf` declarations. |
| `frontend/src/components/tabs/MlTrainingTab.jsx` | Modified | Fixed unused parameters and unreferenced labels. |
| `docs/audits/BASELINE_PRODUCTION_AUDIT.md` | New | Formal audit documentation establishing repository, framework, and deployment baseline. |
| `docs/audits/DEPLOYMENT_REPORT_20261024.md` | New | Comprehensive release and deployment report. |

---

## 4. Test Verification & Results

### Local Tests
- **Backend Test Suite (Pytest 9.1.1):**
  - **431 passed**, 28 skipped, 0 failed (459 total items).
  - Skipped tests are offline PyTorch-only training scripts (`requirements-ml.txt`).
- **Frontend Test Suite (Vitest 4.1.11):**
  - **49 passed**, 0 failed across **10 test files**.
  - Verified tests include `apiBaseUrl.test.jsx`, `smoke.test.jsx`, `satelliteUploadFlow.test.jsx`, `MlTrainingTab.test.jsx`, and `ErrorBoundary.test.jsx`.
- **Frontend Linter (Oxlint 1.79):**
  - **0 errors** across 38 files.
- **Frontend Build (Vite 8.2.2):**
  - Production build completed in 9.74s generating minified assets in `frontend/dist/`.
- **ONNX Verification (`verify_onnx.py`):**
  - Verified across 64x64, 128x128, 256x256, and 128x192 tensor dimensions with 0 failures.

### GitHub Actions CI
- **Run ID:** `40085381747` on branch `main`
- **Backend Pytest Suite:** Passed in 1m 26s
- **Frontend Lint, Test & Build:** Passed in 36s

---

## 5. Deployment Evidence & Verified URLs

### GitHub
- **Repository:** `https://github.com/Subodh-17/Marine-Pollution-Intelligence-SIH`
- **Branch:** `main`
- **Latest Commit SHA:** `d7b716ae0b0b8c8d83a152f505ea1fc0f8a97ec0`
- **Commit Message:** `fix(vercel): add root vercel.json for SPA fallback and asset caching`

### Render Backend Web Service
- **Service Name:** `marine-pollution-intelligence-api`
- **Deployment Status:** `Live / Operational`
- **Public URL:** `https://marine-pollution-intelligence-api.onrender.com`
- **Health Check Endpoint:** `https://marine-pollution-intelligence-api.onrender.com/health` (HTTP 200)
- **Readiness Check Endpoint:** `https://marine-pollution-intelligence-api.onrender.com/ready` (HTTP 200, `ml_available: true`, `database: ok`)
- **Command Center Endpoint:** `https://marine-pollution-intelligence-api.onrender.com/api/command-center` (HTTP 200, returned active incidents and honest metrics)
- **CORS Allowed Origins:** `access-control-allow-origin: *` (verified with `Origin: https://marine-pollution-intelligence-sih.vercel.app`)

### Vercel Frontend Application
- **Project Name:** `marine-pollution-intelligence-sih`
- **Owner Team:** `subodhs-projects-c39a0359` (`subodh171011`)
- **Deployment Status:** `● Ready` (Build duration: 7s)
- **Latest Deployment URL:** `https://marine-pollution-intelligence-62f4namlt.vercel.app`
- **Production Alias URL:** `https://marine-pollution-intelligence-sih.vercel.app`
- **Configured Build Env Var:** `VITE_API_BASE_URL=https://marine-pollution-intelligence-api.onrender.com/api`
- **Static Asset Bundle:** `dist/assets/index-CMg8oOMQ.js` (verified containing deployed Render API origin with 0 localhost leaks)

---

## 6. End-to-End Workflow Verification

The following live HTTP workflow was executed against the production Render API:
1. `POST https://marine-pollution-intelligence-api.onrender.com/api/spill/detect?mode=demo`
   - Response: HTTP 200 OK
   - Investigation ID: `567dd028`
   - Candidates Scored & Ranked: 3 vessels
   - Processing Duration: 9.77 seconds
2. `GET https://marine-pollution-intelligence-api.onrender.com/api/report/567dd028?fmt=markdown`
   - Response: HTTP 200 OK
   - Markdown Report Generated: 8,509 bytes with full attribution breakdown
3. `GET https://marine-pollution-intelligence-api.onrender.com/api/export/567dd028/spill_polygon.geojson`
   - Response: HTTP 200 OK
   - GeoJSON FeatureCollection returned with 70 coordinate vertices representing the slick boundary.

---

## 7. Security & Compliance Checklist

- [x] No secrets, private API keys, or JWT tokens embedded in git commits, logs, or frontend client bundles.
- [x] `.env` and `.env.local` files excluded via `.gitignore`.
- [x] Ephemeral filesystem persistence documented; PostgreSQL connection enabled via `DATABASE_URL` when needed.
- [x] Maximum image upload sizes guarded (`MAX_UPLOAD_SIZE_MB=50`, `MAX_IMAGE_DIMENSION_PX=8000`).
- [x] ML inference memory constrained via `ML_MAX_INFERENCE_PIXELS=262144` to protect against container OOM crashes.
- [x] Fail-closed attribution policy active: Lookalike satellite detections are labeled `ABSTAINED_LOOKALIKE` and abstain from scoring or attributing vessels.

---

## 8. Remaining Operational Notes

- **Render Free Tier Cold Starts:** Render free-tier web services spin down after 15 minutes of inactivity. The initial request after idle requires approximately 30–50 seconds for container spin-up. The frontend handles this gracefully via request timeouts and loading indicators.
- **Real Satellite Dual-Pol Requirement:** Full ML segmentation requires dual-polarization (VV + VH) GeoTIFF input. Single-polarization inputs transparently use the classical dark-spot detector and report explicit provenance (`ML_FALLBACK`).
