# Marine Pollution Intelligence — Baseline Production Audit

**Timestamp:** 2026-10-24T15:40:00+05:30  
**Project Workspace:** `E:\Projects\Marine_Pollution_Intelligence_SIH_FINAL`  
**Engineer Role:** Senior Full-Stack, DevOps, Security Reviewer & Release Manager

---

## 1. Executive Summary

This audit establishes the baseline technical state of the Marine Pollution Intelligence platform prior to synchronizing repository changes to GitHub and updating deployments on Render and Vercel.

Both hosting platform configurations and existing projects have been identified and confirmed live:
- **Frontend (Vercel):** `https://marine-pollution-intelligence-sih.vercel.app` under project `marine-pollution-intelligence-sih` (account: `subodh171011`).
- **Backend (Render):** `https://marine-pollution-intelligence-api.onrender.com` (running Python Uvicorn + FastAPI with ONNX Runtime CPU).

The deployed backend currently runs commit `219256f`, which lacks the newly implemented `/api/command-center` endpoint, lookalike fail-closed safety gating, multiscale spatial fusion, and validation calibration. The local working tree contains these completed, fully-tested features.

---

## 2. Source Control & Repository State

- **Repository Root:** `E:\Projects\Marine_Pollution_Intelligence_SIH_FINAL`
- **Active Branch:** `main` (tracking `origin/main`)
- **Remote Fetch URL:** `https://github.com/Subodh-17/Marine-Pollution-Intelligence-SIH`
- **Remote Push URL:** `https://github.com/Subodh-17/Marine-Pollution-Intelligence-SIH`
- **Latest Commit SHA:** `219256f2c58522c6fcab26565d2661485aa1ae33` ("Initial commit for final release")
- **Authentication:** GitHub CLI (`gh`) authenticated as `Subodh-17` with `repo`, `workflow`, `gist` scopes.
- **Git Working Tree Status:**
  - 32 modified tracked files (Command Center API, calibration, lookalike safety policy, multiscale inference, frontend tabs, test suite).
  - Untracked production modules: `backend/app/evaluation/calibration.py`, `backend/app/investigation/policy.py`, evaluation reports, and independent dataset intake documentation.

---

## 3. Technology Stack & Architecture

### Frontend
- **Framework:** React 19.2.8
- **Tooling / Bundler:** Vite 8.2.2
- **Testing:** Vitest 4.1.11, Testing Library (React 16.3.3)
- **Linter:** Oxlint 1.79.0
- **Routing:** Tab-based single-page application with session persistence (`sessionStorage['mpi_active_investigation_id']`).
- **Target Host:** Vercel (`dist` output directory, Vite preset).

### Backend
- **Framework:** FastAPI 0.115+, Uvicorn 0.30+
- **Python Runtime:** Python 3.11.0
- **Testing:** Pytest 9.1.1
- **Database:** SQLite file by default (`backend/data/app.db`); PostgreSQL supported through `DATABASE_URL`.
- **Target Host:** Render Web Service (`marine-pollution-intelligence-api`).

### Machine Learning & Inference
- **Production Serving Runtime:** ONNX Runtime 1.31.0 (CPU execution provider).
- **Offline / Training Runtime:** PyTorch 2.x (isolated to `requirements-ml.txt`, never required at runtime on Render Free tier).
- **Shipped Checkpoints & Manifests:**
  - `classifier_real_best.onnx` + `classifier_real_best.onnx.data` (SceneClassifierV2: Oil, No-Oil, Lookalike).
  - `spill_unet_real_best.onnx` + `spill_unet_real_best.onnx.data` (SpillUNet binary oil segmentation).
  - `onnx_export_manifest.json` with cryptographic SHA-256 hashes verifying training provenance.
- **Inference Budgeting & Safeguards:**
  - `ML_MAX_INFERENCE_PIXELS=262144` (512x512 direct limit guarding 512 MB container RAM).
  - Spatial multiscale tiling enabled for larger scenes (`ML_TILING_ENABLED=true`).
  - Graceful fallback to classical dark-spot detector when inputs are single-pol or ML runtime is unavailable.

---

## 4. Current Deployed State vs. Local Enhancements

| Component | Currently Deployed (Render / Vercel) | Local Working Tree State |
|---|---|---|
| Frontend URL | `https://marine-pollution-intelligence-sih.vercel.app` (200 OK) | Built, verified, linted, test suite 100% passing |
| Backend URL | `https://marine-pollution-intelligence-api.onrender.com` (200 OK) | Built, verified, 431 test suite 100% passing |
| `/health` | Returns `{"status":"ok","version":"0.1.0","env":"development"}` | Updated to production standard with versioning |
| `/api/command-center` | **404 Not Found** | **Implemented** with incident metrics & model readiness |
| Lookalike Gating | Not implemented (could score vessels on lookalikes) | **Fail-closed safety policy** (`ABSTAINED_LOOKALIKE`) |
| Multiscale Tiling | Direct tiling | **Multiscale fusion** (`size_gated_closure`) |
| Scene Classification | Basic v1 | **SceneClassifierV2** with temperature calibration |

---

## 5. Local Verification Results

- **Backend Pytest Suite:**
  - Total tests executed: 459
  - Passed: **431**
  - Skipped: **28** (PyTorch-only offline training tests gracefully skipped in base runtime)
  - Failures: **0**
- **Frontend Vitest Suite:**
  - Test files: **10 passed (10)**
  - Tests: **49 passed (49)**
  - Failures: **0**
- **Frontend Lint (Oxlint):**
  - **0 errors** across 38 files.
- **Frontend Production Build:**
  - Succeeded in 9.74s generating minified production bundle in `frontend/dist/`.
- **ML ONNX Verification:**
  - `verify_onnx.py` verified across 64x64, 128x128, 256x256, 128x192 resolutions with 0 failures.

---

## 6. Identified Deployment Configurations

1. **Vercel (`frontend/vercel.json`):**
   - Framework: Vite
   - Root Directory: `frontend`
   - Build Command: `npm run build`
   - Output Directory: `dist`
   - Environment Variable: `VITE_API_BASE_URL=https://marine-pollution-intelligence-api.onrender.com/api`
   - SPA Routing: Add `rewrites` to `index.html` to guarantee client-side route persistence on refresh.

2. **Render (`render.yaml`):**
   - Service Name: `marine-pollution-intelligence-api`
   - Runtime: Python
   - Root Directory: `backend`
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - Health Check Path: `/health`
   - Environment Variables:
     - `APP_ENV=production`
     - `DATABASE_ENABLED=true`
     - `CORS_ALLOWED_ORIGINS=https://marine-pollution-intelligence-sih.vercel.app,http://localhost:5173,http://127.0.0.1:5173`
     - `ML_RUNTIME=auto`
     - `ML_MAX_INFERENCE_PIXELS=262144`
     - `ML_TILING_ENABLED=true`
