# Smart India Hackathon (SIH) — Presentation Day Master Checklist
## Marine Pollution Intelligence & Spill-Aware Maritime Navigation (SIH-26143)

**Document Purpose:** Unified, single-document operational preflight, runbook, failure-recovery guide, and judge defense cheatsheet for live Smart India Hackathon evaluation.  
**Production URLs:**
- **Frontend:** `https://marine-pollution-intelligence-sih.vercel.app`
- **Backend API:** `https://marine-pollution-intelligence-api.onrender.com`
- **Local Mirror:** `http://localhost:5173` (Frontend) | `http://127.0.0.1:8000` (Backend)

---

## 1. Previous-Day Preparation Checklist

| # | Exact Action | Expected Successful Result | What to Do If It Fails |
|---|---|---|---|
| **1.1** | Verify laptop battery charges to 100% and pack dedicated charger. | Battery icon shows 100% / Connected. | Acquire secondary power brick or swap laptop. |
| **1.2** | Verify HDMI / USB-C display adapter on external monitor. | 1080p display duplicates without flicker. | Test backup HDMI cable / dongle immediately. |
| **1.3** | Run full local test suite: `pytest backend/tests` and `npm test --prefix frontend`. | `714 passed, 10 skipped` in pytest; `86 passed` in vitest. | Inspect error log; do NOT commit or refactor code. |
| **1.4** | Check local SQLite database `backend/data/app.db`. | Database contains 171 investigations including `c4cc3d2f`. | Verify `backend/data/app.db` exists (size > 1.8 MB). |
| **1.5** | Ensure sample GeoTIFF rasters exist in `backend/data/sample_sar/`. | `demo_oil_scene_01.tif` through `03.tif`, `demo_lookalike_scene_01.tif`, `demo_no_oil_scene_01.tif` all present. | Restore missing rasters from project backup. |

---

## 2. Competition-Morning Preparation Checklist

| # | Exact Action | Expected Successful Result | What to Do If It Fails |
|---|---|---|---|
| **2.1** | Connect laptop to venue Wi-Fi or personal phone 4G/5G mobile hotspot. | Stable internet connectivity established. | Switch immediately to phone USB tethering. |
| **2.2** | Pre-cache OpenStreetMap Leaflet tiles locally: Open `http://localhost:5173` while online, navigate to Map, pan and zoom across Indian West Coast (Mumbai, Goa, Gujarat) at zoom levels 6–12. | Map tiles render crisply; browser disk cache stores tile images for offline use. | Repeat pan/zoom in Google Chrome to ensure cache is populated. |
| **2.3** | Set system display resolution: `Windows Settings` $\rightarrow$ `Display` $\rightarrow$ `1920 x 1080` at 100% scaling. | Clean 16:9 widescreen layout without OS scaling blur. | Adjust scaling to 100% to eliminate UI layout clipping. |
| **2.4** | Clean browser profile: Open dedicated Google Chrome window, clear extraneous tabs, set zoom to 100%. | Zero extension interference, clean full-width tactical dashboard. | Launch Chrome in incognito or guest profile if extensions interfere. |

---

## 3. T - 30 Minute Technical Preflight

| # | Exact Action | Expected Successful Result | What to Do If It Fails |
|---|---|---|---|
| **3.1** | Launch local backend in Terminal 1:  <br>`cd E:\Projects\Marine_Pollution_Intelligence_SIH_FINAL\backend`  <br>`$env:PYTHONPATH = "."`  <br>`uvicorn app.main:app --host 127.0.0.1 --port 8000` | Terminal displays: `Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)` | Check port collision: `netstat -ano \| findstr :8000` and kill conflicting PID. |
| **3.2** | Launch local frontend in Terminal 2:  <br>`cd E:\Projects\Marine_Pollution_Intelligence_SIH_FINAL\frontend`  <br>`npm run dev` | Terminal displays: `VITE v8.2.2 ready in ... ms`  <br>`➜ Local: http://localhost:5173/` | Run `npm install` if node_modules missing (do not modify code). |
| **3.3** | Verify local backend endpoints in Terminal 3:  <br>`curl -s http://127.0.0.1:8000/health`  <br>`curl -s http://127.0.0.1:8000/api/ml/status` | Health returns `{"status":"ok"}`; ML status returns `{"available":true,"runtime":"onnxruntime_cpu"}` | Verify Python 3.11 environment has `onnxruntime` and `fastapi` loaded. |
| **3.4** | Check HDMI projector connection: Press `Windows + P` $\rightarrow$ select **Duplicate**. | Both laptop display and projector display 1080p mirror. | Re-seat HDMI cable; toggle projector input source. |

---

## 4. T - 10 Minute Render Warm-Up

Render free-tier containers spin down after 15 minutes of inactivity. Awakening the container 10 minutes prior to judging eliminates cold-start latency.

| # | Exact Action | Expected Successful Result | What to Do If It Fails |
|---|---|---|---|
| **4.1** | Run warm-up command in PowerShell:  <br>`python -c "import urllib.request; [urllib.request.urlopen(f'https://marine-pollution-intelligence-api.onrender.com{ep}', timeout=30) for ep in ['/health', '/ready', '/api/routes/ports', '/api/routes/spills']]; print('Render Cloud Container is Warm!')"` | Terminal prints: `Render Cloud Container is Warm!` in 10–18 seconds. | If request times out (>30s), run command a second time; container is finishing spin-up. If it still fails, rely on Tab 4 (Local Standby). |
| **4.2** | Open primary production frontend in Chrome:  <br>`https://marine-pollution-intelligence-sih.vercel.app` | Tactical dashboard loads in < 1 second; status pills show green online state. | Press `Ctrl + F5` for hard reload. |

---

## 5. Local Backend and Frontend Startup Commands

Keep these exact startup commands accessible in a terminal window for instant execution:

```powershell
# =============================================================================
# TERMINAL 1 — LOCAL BACKEND (Python 3.11 + Uvicorn)
# =============================================================================
cd E:\Projects\Marine_Pollution_Intelligence_SIH_FINAL\backend
$env:PYTHONPATH = "."
uvicorn app.main:app --host 127.0.0.1 --port 8000

# =============================================================================
# TERMINAL 2 — LOCAL FRONTEND (Vite Dev Server)
# =============================================================================
cd E:\Projects\Marine_Pollution_Intelligence_SIH_FINAL\frontend
npm run dev
```

---

## 6. Browser Tab Arrangement

Arrange four clean browser tabs in Google Chrome in this exact order:

```
  ┌─────────────────┬─────────────────┬─────────────────┬─────────────────┐
  │  Tab 1 (Ctrl+1) │  Tab 2 (Ctrl+2) │  Tab 3 (Ctrl+3) │  Tab 4 (Ctrl+4) │
  │   MAIN STAGE    │  ROUTE & FUEL   │    ML RIGOR     │ OFFLINE MIRROR  │
  ├─────────────────┼─────────────────┼─────────────────┼─────────────────┤
  │ Live Dashboard  │ /routeplanner   │ ML Training Tab │ Localhost:5173  │
  │ (Satellite SAR) │ (Avoidance Map) │ (Confusion Mat) │ (Zero-Network)  │
  └─────────────────┴─────────────────┴─────────────────┴─────────────────┘
```

- **Fullscreen Toggle:** Press `F11` in Chrome to hide browser toolbars and present full-screen tactical command UI.
- **Presenter Split:** Speaker 1 narrates problem, science, and AI; Speaker 2 operates live keyboard/mouse.

---

## 7. Complete Five-Minute Presentation Sequence

| Minute | Operational Stage | Speaker 1 (Spoken Script) | Speaker 2 (Screen Action) |
|---|---|---|---|
| **0:00 – 0:35** | **The Crisis & Scale** | Indian EEZ scale (2.3M km²); 70% marine oil pollution stems from illicit night bilge dumping, not publicized tanker crashes. | Display Tab 1 (`/`). Gesture to Indian EEZ maritime transit corridors and active alerts. |
| **0:35 – 1:15** | **SAR Physics & AI** | Sentinel-1 C-band SAR penetrates clouds/night; wave damping causes dark backscatter patches. Local percentile normalization + Lee despeckling (0 data leakage). | Click **Upload Satellite SAR** $\rightarrow$ select **Sample Benchmark Scenes** $\rightarrow$ choose `Oil Spill Scene 1 (Zenodo Sentinel-1 Dual-Pol)`. Display VV/VH backscatter bands. |
| **1:15 – 1:55** | **Segmentation & Calibration** | SpillUNet extracts dense polygon boundary in 45ms CPU; CNN classifies slick with Platt temperature scaling ($T=1.0879$, ECE $7.57\%$). | Click **Run Pipeline Analysis**. Show red 61-vertex boundary ($20.02\text{ km}^2$). Point out **Classification Scorecard** (Oil Spill, 92.8% confidence). |
| **1:55 – 2:35** | **Hydrodynamic Drift** | Advection $\vec{V} = \vec{U}_{\text{current}} + 0.03\mathbf{R}(10^\circ)\vec{U}_{\text{wind}}$. 12h hindcast traces origin; 24h forecast models expanding Gaussian dispersion cone. | Click **Timeline & Drift Tab**. Toggle hindcast (blue dashed) and forecast (amber solid) with Gaussian uncertainty ellipses. |
| **2:35 – 3:15** | **AIS Vessel Attribution** | Bayesian ranking (CPA distance, temporal alignment, vessel type). **Legal Boundary:** Generates investigative leads for Coast Guard air patrol, NOT criminal proof. | Click **AIS Attribution Tab**. Point to top suspect (*Stolt Tenacity* / *MV KONKAN STAR*, 82.5% score). Open Evidence Drawer; highlight Legal Disclaimer. |
| **3:15 – 4:15** | **Judge Feature: Routing & Fuel** | Direct baseline intersects hazard. Visibility-tangent avoidance graph (+1.5 NM buffer) finds safe detour. Admiralty cubic physics ($P \propto V^3$) estimates bunker consumption and emissions. | Switch to Tab 2 (`/routeplanner`). Show Mumbai $\rightarrow$ Arabian Sea route. Point out **Fuel Indication Meter**, toggle **MT $\longleftrightarrow$ L**, slide speed from 14.5 to 12.0 kts to show fuel cost recovery. |
| **4:15 – 4:45** | **Scientific Rigor & Gates** | Evaluated on 450 held-out Zenodo scenes (0 leakage). 96.00% lookalike rejection. Multiscale segmentation rejected via fail-closed safety gate. | Switch to Tab 3 (ML Training Tab). Point to confusion matrix, cryptographic checksums, and temperature calibration curve. |
| **4:45 – 5:00** | **SOLAS & Conclusion** | SOLAS Chapter V Regulation 34 advisory notice. Unified loop: **Detect $\rightarrow$ Characterize $\rightarrow$ Trace $\rightarrow$ Correlate $\rightarrow$ Avoid $\rightarrow$ Optimize $\rightarrow$ Estimate Fuel.** | Scroll to SOLAS advisory notice at bottom of Route Planner. Conclude and open floor for jury Q&A. |

---

## 8. Exact Demo Data and Investigation Selection

| Asset Type | Selected Identifier | Physical File / Source | Expected Detection & Presentation Values |
|---|---|---|---|
| **Primary Live SAR Scene** | `demo_oil_scene_03.tif` | `backend/data/sample_sar/demo_oil_scene_03.tif` (Zenodo Part III, DOI `10.5281/zenodo.13761290`) | Area: $20.02\text{ km}^2$, 61 polygon vertices, Class: Oil Spill, Calibrated Confidence: $92.8\%$. Centroid: $19.044^\circ\text{N}, 71.204^\circ\text{E}$ (Arabian Sea / Mumbai High). |
| **Lookalike Test Scene (Safety Demo)** | `demo_lookalike_scene_01.tif` | `backend/data/sample_sar/demo_lookalike_scene_01.tif` (Zenodo Part III) | Rejects false alarm: Oil confidence $<8\%$, zero false spill polygons extracted. Demonstrates safety gate. |
| **Pre-Generated Offline Investigation** | `c4cc3d2f` | `backend/data/app.db` (SQLite storage) | 456 AIS messages, 3 ranked candidate vessels (top: *MV KONKAN STAR*, 82.5%), 10 hourly drift simulation steps. |
| **Pre-Configured Navigational Route** | Mumbai Port $\rightarrow$ Arabian Sea West | Origin: `INBOM` ($18.940^\circ\text{N}, 72.840^\circ\text{E}$)  <br>Dest: `INASW` ($19.050^\circ\text{N}, 70.400^\circ\text{E}$) | Baseline distance: $138.90\text{ NM}$ (Intersects slick).  <br>Avoidance route: $163.96\text{ NM}$ ($+25.06\text{ NM}$, $+4.69\text{ MT}$ fuel, clearance: $8.94\text{ NM}$). |

---

## 9. Internet Failure Recovery

| Trigger Condition | Immediate Symptom | Exact Recovery Action | Expected Outcome |
|---|---|---|---|
| Venue Wi-Fi drops completely | Browser shows network error or loading spinner | 1. Press `Ctrl + 4` to switch to Tab 4 (`http://localhost:5173`).  <br>2. Continue presentation uninterrupted from the local mirror. | All 24 steps execute locally from SQLite and local ONNX models with 0 external network packets. |
| Hotspot disconnects | API requests hang | Do NOT refresh Tab 1 repeatedly. Switch instantly to Tab 4. | Localhost responds in < 15ms. |

---

## 10. Render Cold-Start Recovery

| Trigger Condition | Immediate Symptom | Exact Recovery Action | Expected Outcome |
|---|---|---|---|
| API call hangs because Render container went to sleep | Loading spinner runs > 10 seconds on Tab 1 | 1. Narrate: *"The cloud architecture uses containerized serverless workers that scale down when idle; the runtime is mounting ONNX weights."*  <br>2. If spinner exceeds 15 seconds, press `Ctrl + 4` and execute on local mirror. | Eliminates awkward dead air; proves architecture agility. |
| Warm-up missed | First click latency | Running Action 4.1 at T - 10 minutes prevents this entirely. | Pre-flight prevents container sleep. |

---

## 11. Vercel Failure Recovery

| Trigger Condition | Immediate Symptom | Exact Recovery Action | Expected Outcome |
|---|---|---|---|
| Vercel CDN unreachable or DNS fails | Tab 1 fails to load | 1. Press `Ctrl + 4` to switch to Tab 4 (`http://localhost:5173`).  <br>2. Local Vite dev server serves identical compiled React bundle from laptop disk. | Immediate tactical UI render with zero CDN dependency. |

---

## 12. Local API Connection Verification

| # | Exact Action | Expected Successful Result | What to Do If It Fails |
|---|---|---|---|
| **12.1** | Run test curl in terminal:  <br>`curl -s http://127.0.0.1:8000/api/routes/ports` | Returns JSON array containing 11 ports starting with `[{"id":"INBOM","name":"Mumbai Port...` | Check if Uvicorn in Terminal 1 crashed. If so, restart with `$env:PYTHONPATH="."; uvicorn app.main:app --port 8000`. |
| **12.2** | Verify Vite proxy in browser: Navigate to `http://localhost:5173/api/routes/ports` in Chrome. | Returns identical JSON array through Vite proxy. | Check `frontend/vite.config.js`: Ensure `BACKEND_ORIGIN = 'http://127.0.0.1:8000'` is configured. |

---

## 13. Map Tile Offline Verification

| # | Exact Action | Expected Successful Result | What to Do If It Fails |
|---|---|---|---|
| **13.1** | Disconnect laptop Wi-Fi, open `http://localhost:5173`, and view Leaflet map. | Pre-cached raster map tiles render coastlines; vector polygons (slick, buffer, routes) render sharply on top. | If raster tiles show dark grid, vector overlays still function 100%. Explain: *"Vector spatial engine operates air-gapped; raster basemap tiles use browser disk cache."* |

---

## 14. PDF Report Export Fallback

| # | Exact Action | Expected Successful Result | What to Do If It Fails |
|---|---|---|---|
| **14.1** | On **Investigation Report Tab**, click **Export Official PDF Report**. | Browser downloads `investigation_report_c4cc3d2f.pdf` (~9.8 KB) generated live via ReportLab 5.0.1. | If download blocked by popup blocker, use direct local URL: `http://127.0.0.1:8000/api/report/c4cc3d2f?fmt=pdf`. |
| **14.2** | Pre-generated file fallback: Check pre-rendered PDF in temporary folder if ReportLab throws error. | Pre-rendered PDF opens in Adobe Reader or Chrome PDF viewer. | Keep a pre-exported copy on Desktop: `Desktop/investigation_report_c4cc3d2f.pdf`. |

---

## 15. Exact ML Metrics and Limitations to Communicate

Always cite these exact numbers during presentation and cross-examination. Never inflate or estimate:

```
  =============================================================================
  METRIC                        DEFENSIBLE VALUE    SCIENTIFIC PROVENANCE
  =============================================================================
  Scene Classifier Accuracy     79.33% (ONNX CPU)   450 Held-Out Zenodo Part III Scenes
  PyTorch Classifier Accuracy   78.44% (PyTorch)    Bilinear sub-pixel zoom difference
  Oil Spill Precision           96.30%              130 / 135 (95% CI: [91.4%, 98.5%])
  Oil Spill Recall              86.67%              130 / 150 (95% CI: [80.2%, 91.3%])
  Oil Spill F1 Score            91.23%              Harmonic mean of precision & recall
  Lookalike Rejection Rate      96.00%              144 / 150 lookalike scenes rejected
  Clean Sea Specificity         95.33%              143 / 150 clean scenes rejected
  Calibration Parameter         T = 1.0879          Platt Temperature Scaling (val split)
  Expected Calibration Error    7.57%               Reduced from 8.66% uncalibrated
  SpillUNet Macro IoU           63.24%              Evaluated on 150 oil masks
  SpillUNet Peak IoU (Q2)       77.65%              Cohesive slicks (5–20 km²)
  Large Slick Limitation (Q4)   33.54% Recall       256x256 receptive field limitation
  Classical Detector Accuracy   UNBENCHMARKED       Clearly labeled "HEURISTIC SCORE"
  Data Leakage Audit            0 Overlap, 0 Hash   Cryptographic SHA-256 byte verified
  =============================================================================
```

- **Fail-Closed Safety Gate Disclosure:** Explain that multiscale segmentation was engineered and tested, but was **disabled by default in production** because it generated 32,057 false positive pixels on lookalikes (8.74% false alarm rate vs $\le 4.5\%$ safety gate).

---

## 16. Judge Questions and Concise Answers

| # | Tough Jury Question | Defensible 30-Second Answer |
|---|---|---|
| **16.1** | *"How do you prove no data leakage occurred between training and testing?"* | *"Our test set consists of 450 scenes from Zenodo Part III (`10.5281/zenodo.13761290`), an independent benchmark. We verified **0 scene ID overlap and 0 SHA-256 hash collisions**. Our preprocessing calculates percentiles **strictly per-sample**, preventing any global dataset leakage."* |
| **16.2** | *"Why is ONNX accuracy 79.33% while PyTorch is 78.44%?"* | *"The 4-scene difference is caused by sub-pixel coordinate differences between `scipy.ndimage.zoom` (CPU inference) and `torch.nn.functional.interpolate` when downsampling $2048\times2048$ rasters to $256\times256$. Feeding identical tensors yields identical logits down to $10^{-5}$."* |
| **16.3** | *"Can AIS correlation convict a polluter in maritime court?"* | *"No, and we explicitly instruct operators that AIS correlation produces **investigative leads, not legal proof**. Court prosecution under MARPOL Annex I requires physical hydrocarbon sampling (gas chromatography) by Coast Guard aircraft to chemically match the slick."* |
| **16.4** | *"How does your routing system calculate fuel savings?"* | *"We use the naval architecture **Admiralty power formula**: $P = (\Delta^{2/3}\cdot V^3)/C_{\text{adm}}$. Because power scales with $V^3$, slowing from 14.5 to 12 knots reduces hourly bunker burn by over 40%, recovering the entire fuel cost of the 25 NM detour."* |
| **16.5** | *"Could your route direct a ship into shallow reefs or rocks?"* | *"Routes utilize pre-calibrated shipping corridors connecting 11 major Indian ports with standard deep-water Traffic Separation Schemes (TSS). Furthermore, every route embeds mandatory **SOLAS Chapter V Regulation 34** disclaimers upholding master's navigation authority."* |

---

## 17. Final Shutdown and Data-Preservation Procedure

| # | Exact Action | Expected Successful Result | What to Do If It Fails |
|---|---|---|---|
| **17.1** | After presentation concludes, close Chrome browser tabs. | Browser closes cleanly; active investigation remains in database. | Normal shutdown. |
| **17.2** | In Terminal 1 (Uvicorn), press `Ctrl + C` once. | Terminal logs: `Shutting down` $\rightarrow$ `Finished server process` | If process hangs, close terminal window. |
| **17.3** | In Terminal 2 (Vite), press `Ctrl + C` once. | Vite dev server terminates cleanly. | Close terminal window. |
| **17.4** | Verify working tree integrity in PowerShell:  <br>`git status` | Working tree is clean of unintended changes; only tracked modified files and documentation artifacts exist. | Do **NOT** run `git reset`, `git checkout`, or `git clean`. Leave all artifacts preserved. |
| **17.5** | Safely unmount HDMI and backup USB drives: Click **Safely Remove Hardware**. | OS confirms device can be safely unplugged. | Remove hardware once OS confirms safe removal. |
