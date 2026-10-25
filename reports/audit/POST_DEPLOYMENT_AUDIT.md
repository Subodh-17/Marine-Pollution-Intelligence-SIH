# Post-Deployment Audit & Release Verification Report

**Date & Time**: 2026-10-24 19:15 IST  
**Repository**: `Subodh-17/Marine-Pollution-Intelligence-SIH`  
**Target Branch**: `main`  
**Published Commit**: `048f1319eb1196a099b71f3e155f14948f325484`  
**Base Commit**: `f98a416cd348f529baf3af2a39aac36ab2e5da12`  
**Verdict**: **OPERATIONALLY VERIFIED — SCIENTIFIC VALIDATION INCOMPLETE**

---

## 1. Tool Availability & Review Methodology

In accordance with Section 1 of the audit directive:
- **CodeRabbit CLI / Roo Code / GSD**: Not installed as standalone CLI binaries on this system PATH (`Get-Command` returned not found).
- **Primary Orchestration**: Antigravity IDE.
- **Methodology Applied**: Antigravity conducted an independent diff review, bounded multi-tier security inspection, and test-driven regression verification conforming to the Ralph Loop protocol.

---

## 2. Release & Deployment State Verification

- **Remote Branch & HEAD**: `origin/main` is identical to local `main` at `048f1319eb1196a099b71f3e155f14948f325484`.
- **GitHub Actions CI Run**:
  - Run ID: `40098447453`
  - URL: https://github.com/Subodh-17/Marine-Pollution-Intelligence-SIH/actions/runs/40098447453
  - Status: **PASSED (Exit Code 0)**
  - Backend Suite: 450 passed, 28 skipped in 52.99s.
  - Frontend Suite: 52 passed, 0 lint errors, build completed in 429ms.
- **Vercel Production Frontend** (`https://marine-pollution-intelligence-sih.vercel.app`):
  - Verified live bundle `assets/index-CzIukL6s.js`.
  - Confirmed inclusion of `useSyncExternalStore` (theme-provider refactoring) and `minHeight:240` (Recharts fix).
  - Status: 200 OK.
- **Render Production Backend** (`https://marine-pollution-intelligence-api.onrender.com`):
  - Live probe `/health`: HTTP 200 `{"status":"ok","version":"0.1.0","env":"production"}`.
  - Live probe `/ready`: HTTP 200 `{"status":"ready","ml_available":true,"checks":{"synthetic_pipeline":"ok","ml_runtime":"onnxruntime_cpu","ml_available":true,"database":"ok"}}`.
  - Live probe `/api/config`: HTTP 200 (Non-secret configuration).

---

## 3. End-to-End Workflow Verification Matrix

| Workflow / Capability | Test Method | HTTP Status | Evidence / Outcome | Status |
|---|---|:---:|---|:---:|
| **Sample Scene 1 (Classical)** | `POST /api/satellite/sample?sample_name=demo_oil_scene_01.tif&detector=classical` | **200 OK** | Classical dark spot detector identified slick; 3 candidate vessels ranked in 2.80s | **PASS** |
| **Sample Scene 1 (ML)** | `POST /api/satellite/sample?sample_name=demo_oil_scene_01.tif&detector=ml` | **200 OK** | Deep CNN abstained on synthetic white noise; transparent fallback to `CLASSICAL_DARK_SPOT_ML_FALLBACK` in 5.03s | **PASS** |
| **Sample Scene 2 (Classical)** | `POST /api/satellite/sample?sample_name=demo_oil_scene_02.tif&detector=classical` | **200 OK** | Slick segmented; 3 candidate vessels ranked in 2.63s | **PASS** |
| **Sample Scene 3 (Lookalike)** | `POST /api/satellite/sample?sample_name=demo_lookalike_scene_01.tif` | **200 OK** | Attribution Safety Policy gated attribution; 0 candidate vessels attributed in 4.87s | **PASS** |
| **Sample Scene 4 (Clean Sea)** | `POST /api/satellite/sample?sample_name=demo_no_oil_scene_01.tif` | **200 OK** | No oil detected; 0 candidate vessels attributed in 5.57s | **PASS** |
| **Valid PNG Upload** | `POST /api/satellite/upload` (64x64 synthetic PNG) | **200 OK** | Ingested and analyzed successfully without errors | **PASS** |
| **Valid GeoTIFF Upload** | `POST /api/satellite/upload` (64x64 float32 GeoTIFF) | **200 OK** | Decoded and processed successfully | **PASS** |
| **Malformed TIFF Guard** | `POST /api/satellite/upload` (Corrupt header) | **422 Unprocessable** | Safely rejected: `"not a TIFF file"` without server traceback | **PASS** |
| **Unsupported Extension Guard** | `POST /api/satellite/upload` (`.exe` payload) | **400 Bad Request** | Safely rejected: `"Unsupported file type '.exe'"` | **PASS** |
| **Valid AIS CSV Upload** | `POST /api/ais/upload` (Multipart CSV) | **200 OK** | Uploaded and registered under user context | **PASS** |
| **AIS Re-Analysis Persistence** | `POST /api/ais/analyze?search_radius_km=75.0` | **200 OK** | Modified radius persisted to DB and survived subsequent `GET /api/spill/{id}` fetch | **PASS** |
| **Attribution Rescoring Persistence** | `POST /api/attribution/analyze` (Custom weights) | **200 OK** | Custom scored candidates persisted onto investigation and reflected in `/api/vessels/ranking` | **PASS** |
| **Markdown Report Export** | `GET /api/report/{id}?fmt=markdown` | **200 OK** | 8,478-byte comprehensive markdown report generated | **PASS** |
| **CSV Summary Export** | `GET /api/report/{id}?fmt=csv` | **200 OK** | Flat key/value CSV with model provenance and disclaimer generated | **PASS** |
| **Spill Polygon GeoJSON** | `GET /api/export/{id}/spill_polygon.geojson` | **200 OK** | FeatureCollection with valid polygon coordinates | **PASS** |
| **Vessel Tracks GeoJSON** | `GET /api/export/{id}/vessel_tracks.geojson` | **200 OK** | FeatureCollection with reconstructed vessel paths | **PASS** |

---

## 4. Performance & Memory Profile

- **Local Investigation Benchmark (5 runs)**:
  - Mean latency: `1950.0 ms`
  - Min latency: `1643.3 ms`
  - Max latency: `3007.5 ms`
  - Peak memory: `57.31 MB`
- **Cloud Latency (Render Free Tier)**:
  - Classical sample investigation: `2.6s – 2.8s`
  - ML inference with fallback: `5.03s`
  - Report / Export downloads: `< 200ms`
- **Memory Containment**:
  - `_BoundedInvestigationStore` limits cache retention to 100 entries with thread-safe LRU eviction and on-demand database rehydration.
  - Peak memory allocated during investigation fits safely within Render's 512MB RAM cap.

---

## 5. Summary of Findings & Next Actions

- **Operational Health**: All API endpoints, frontend routing, satellite sample analyses, and reporting exports function reliably end-to-end.
- **Scientific Caveat**: The bundled sample scenes rely on synthetic procedural Gaussian white noise, which causes deep CNN kernels trained on real Sentinel-1 SAR speckle to abstain and safely trigger classical fallback. Quantitative validation on independent real-world field benchmarks remains an ongoing research objective.
