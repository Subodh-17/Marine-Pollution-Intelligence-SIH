# Smart India Hackathon (SIH) — Offline Demonstration Contingency Plan
## Marine Pollution Intelligence & Spill-Aware Navigation Platform (SIH-26143)

**Document Version:** 1.0.0 (Phase 6 Final Release)  
**Target Environment:** Local Standalone Laptop (Windows / macOS / Linux)  
**Dependencies:** Python 3.11+, Node.js 18+, Local SQLite (`backend/data/app.db`), Local ONNX Runtime CPU  
**Zero-Network Capability:** Full computation, deep learning inference, hydrodynamic drift, AIS correlation, route optimization, fuel physics, and PDF report generation execute 100% offline.

---

## 1. Operational Scenarios & Trigger Matrix

| Operational Scenario | Environmental Condition | Recommended Presentation Route | Contingency Action |
|---|---|---|---|
| **Scenario 1: Full Cloud** | Reliable venue Wi-Fi / Hotspot ($\ge 5\text{ Mbps}$) | Primary: `https://marine-pollution-intelligence-sih.vercel.app` + `https://marine-pollution-intelligence-api.onrender.com` | Run pre-flight warm-up script 10 min prior to judging. |
| **Scenario 2: Cloud Degraded / Sleeping** | Render free-tier container spinning down, or Metocean API timing out | Primary Cloud UI with automated graceful fallback to regional climatological priors | If Render latency exceeds 10s, seamlessly switch to Scenario 3. |
| **Scenario 3: Zero-Network (Air-Gapped)** | Venue Wi-Fi blocked, unstable, or completely disconnected | **Local Standalone Mirror:** `http://localhost:5173` (Frontend) + `http://127.0.0.1:8000` (Backend) | Switch to Tab 4; entire pipeline runs locally with zero external network packets. |

---

## 2. Verified Local Backend Startup Procedure

The backend requires **zero cloud connections** to execute full investigations, load models, perform drift calculations, and compute routes.

### 2.1 Prerequisites & Environment
- **Working Directory:** `E:\Projects\Marine_Pollution_Intelligence_SIH_FINAL\backend`
- **Python Version:** Python 3.11.x (verified 3.11.0)
- **Key Python Packages:** `fastapi`, `uvicorn`, `onnxruntime`, `numpy`, `scipy`, `shapely`, `tifffile`, `reportlab`

### 2.2 Startup Command (PowerShell / Command Prompt)
Open a dedicated terminal window:
```powershell
cd E:\Projects\Marine_Pollution_Intelligence_SIH_FINAL\backend
$env:PYTHONPATH = "."
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### 2.3 Verification Commands (Separate Terminal)
```powershell
# 1. Verify health
curl -s http://127.0.0.1:8000/health
# Expected Output: {"status":"ok","version":"0.1.0","env":"production"}

# 2. Verify ML model readiness
curl -s http://127.0.0.1:8000/api/ml/status
# Expected Output: {"torch_available":true,"onnx_available":true,"available":true,"runtime":"onnxruntime_cpu",...}

# 3. Verify routing engine
curl -s http://127.0.0.1:8000/api/routes/ports
# Expected Output: JSON array of 11 Indian commercial ports
```

---

## 3. Verified Local Frontend Startup Procedure

The React 19 single-page application is served via Vite with automated proxying to the local backend.

### 3.1 Prerequisites
- **Working Directory:** `E:\Projects\Marine_Pollution_Intelligence_SIH_FINAL\frontend`
- **Node.js Version:** Node.js 18+ or 20+

### 3.2 Startup Command (Dedicated Terminal)
```powershell
cd E:\Projects\Marine_Pollution_Intelligence_SIH_FINAL\frontend
npm run dev
```

### 3.3 Access URL & Routing
- Open browser to: `http://localhost:5173`
- **Vite Proxy Mechanism:** Configured in `frontend/vite.config.js`:
  ```javascript
  const BACKEND_ORIGIN = 'http://127.0.0.1:8000'
  // Proxies all /api/* requests directly to localhost:8000 without CORS restrictions
  ```

---

## 4. Local Model Checkpoint Inventory

All neural models are stored locally on disk under `backend/data/ml_checkpoints/`. They require zero downloads during runtime:

| Model Asset Name | File Location | Size | Execution Role | Verification Status |
|---|---|---|---|---|
| **Classifier ONNX** | `backend/data/ml_checkpoints/classifier_real_best.onnx` (and `.data`) | ~50 KB | 3-Class Scene Classification (Oil / Lookalike / Clean) | Verified (<45ms CPU latency) |
| **SpillUNet ONNX** | `backend/data/ml_checkpoints/spill_unet_real_best.onnx` (and `.data`) | ~54 KB | Dense Pixel Slick Segmentation & Boundary Extraction | Verified (61-vertex polygon) |
| **Calibration Config** | `backend/data/ml_checkpoints/classifier_calibration.json` | 1.2 KB | Platt Temperature Scaling ($T=1.0879$) | Verified (ECE $8.66\% \rightarrow 7.57\%$) |
| **PyTorch Fallbacks** | `backend/data/ml_checkpoints/classifier_real_best.pth`, `spill_unet_real_best.pt` | ~1.2 MB | Secondary verification checkpoints | Verified |
| **Evaluation History** | `backend/data/ml_checkpoints/classifier_v2_part3_evaluation.json` | 8.4 KB | Official metrics on 450 held-out test scenes | Verified (79.33% ONNX) |

---

## 5. Authentic SAR Demonstration Sample Inventory

All satellite scenes are stored locally as multi-band GeoTIFFs under `backend/data/sample_sar/`:

| Identifier | File Path | Class | Dimensions | Region | Demo Role |
|---|---|---|---|---|---|
| **Scene 1 (Primary)** | `backend/data/sample_sar/demo_oil_scene_03.tif` | Oil Spill | $2048 \times 2048$ | Arabian Sea / Mumbai High | Primary 24-step live demonstration scenario ($20.02\text{ km}^2$ slick) |
| **Scene 2** | `backend/data/sample_sar/demo_oil_scene_01.tif` | Oil Spill | $2048 \times 2048$ | Bay of Bengal Coastal Corridor | High-contrast classical vs AI segmentation comparison |
| **Scene 3** | `backend/data/sample_sar/demo_oil_scene_02.tif` | Oil Spill | $2048 \times 2048$ | Gulf of Mannar Shipping Lane | Advection plume and dispersion testing |
| **Scene 4 (Lookalike)**| `backend/data/sample_sar/demo_lookalike_scene_01.tif` | Lookalike | $2048 \times 2048$ | Arabian Sea Calm Patch | False alarm rejection demonstration (Safety gate) |
| **Scene 5 (Clean Sea)**| `backend/data/sample_sar/demo_no_oil_scene_01.tif` | Clean Sea | $2048 \times 2048$ | Indian Ocean Open Waters | Negative baseline validation |

---

## 6. AIS Fixture & Environmental Fallback Inventory

### 6.1 Deterministic AIS Fleet Generator
- **Module:** `backend/app/demo/scenario.py` (`build_demo_ais_fleet`)
- **Behavior:** Generates 456 AIS transponder position reports across 5 commercial vessels (*Stolt Tenacity*, *Ever Given*, *Bulk Carrier Alpha*, etc.) centered around the incident window.
- **Fixed Seed:** Uses `jitter_seed=42`, producing byte-for-byte identical candidate scores and rankings across repeated offline runs.

### 6.2 Deterministic Metocean Fallback
- **Module:** `backend/app/drift/metocean.py`
- **Fallback Trigger:** If external NOAA GFS / Open-Meteo APIs are unreachable, the engine automatically injects seasonal Indian Ocean climatological current vectors ($0.35\text{ m/s}$ easterly current, $6.5\text{ m/s}$ westerly wind). Zero user-facing error is thrown.

---

## 7. Pre-Generated Investigation Results & Reports

To guard against unexpected laptop operating system failures during presentation, complete investigation results and reports are pre-persisted on disk:

1. **Pre-Generated SQLite Investigation Record:**
   - **Database:** `backend/data/app.db`
   - **Investigation ID:** `c4cc3d2f`
   - **Contents:** 456 AIS messages, 3 ranked suspect vessels (top: *MV KONKAN STAR* with 82.5% likelihood), 10 hourly drift simulation steps, and slick boundary geometry.
2. **Pre-Generated Report Export Formats:**
   - **PDF Generation:** Tested with ReportLab 5.0.1 via `GET /api/report/c4cc3d2f?fmt=pdf`. Generates complete 9.8 KB official PDF incident report.
   - **Markdown Export:** Verified via `GET /api/report/c4cc3d2f?fmt=markdown` (8,701 characters).
   - **CSV Vessel Ranking:** Verified via `GET /api/export/c4cc3d2f/vessels.csv`.

---

## 8. Documented Offline Limitations & Workarounds

### 8.1 Leaflet Basemap Raster Tiles (The Single Offline Limitation)
- **Limitation:** Leaflet's `<TileLayer>` requests raster map background PNG tiles from external servers (`https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png` or CartoDB). If the laptop is disconnected from the Internet:
  - **What Works:** All vector graphics—including the red oil slick polygon, amber 1.5 NM safety buffer, dashed Great-Circle route, solid blue avoidance path, and ship icons—render accurately on the Leaflet coordinate plane.
  - **What Does Not Load:** The underlying geographic map background tiles (coastline raster imagery) will appear as dark grid squares unless cached by the browser.
- **Presenter Workaround (Pre-Caching Protocol):**
  1. On the morning of the competition (or via smartphone mobile hotspot prior to judging), open `http://localhost:5173` in Google Chrome.
  2. Pan and zoom across the Indian West Coast (Mumbai, Goa, Gujarat) at zoom levels 6 through 12.
  3. Chrome will automatically store these map tiles in its local disk cache, allowing them to render offline during the presentation even if venue Wi-Fi drops completely.

---

## 9. Prerecorded Demonstration Fallback (Last-Resort Failsafe)

If laptop hardware, projection adapters, or operating system issues prevent running local software, the team maintains an offline video demonstration fallback:
- **Video Title:** `SIH_DEMO_WALKTHROUGH_FULL_HD.mp4`
- **Resolution:** 1080p 60fps (Full HD)
- **Duration:** 4 minutes 45 seconds (synchronized exactly with `SIH_FINAL_FIVE_MINUTE_PRESENTATION_SCRIPT.md`)
- **Audio:** No baked-in voiceover (allows Speaker 1 and Speaker 2 to narrate live from the script while video plays).
- **Storage Location:** Secondary USB Flash Drive + Presenter Desktop root.
