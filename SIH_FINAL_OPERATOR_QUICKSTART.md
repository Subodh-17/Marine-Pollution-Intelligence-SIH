# Smart India Hackathon (SIH) — Final Operator Quickstart Guide
## Marine Pollution Intelligence & Spill-Aware Maritime Navigation (SIH-26143)

**Document Purpose:** High-tempo operational reference card for Speaker 1 (Narrator/Domain Expert) and Speaker 2 (Live System Operator) during the 5-minute SIH Grand Finale evaluation.

---

## 1. Pre-Flight Timeline & Readiness Protocol

```
  T - 15 Min: Boot laptops, connect HDMI projector (1920x1080), open browser tabs.
  T - 10 Min: Run Cloud Warm-Up Script (prevents Render container cold-start delay).
  T - 5  Min: Verify local fallback backend (127.0.0.1:8000) and frontend (localhost:5173).
  T - 0  Min: Begin 5-minute presentation strictly following the timed script.
```

### 1.1 Cloud Warm-Up Command (Run at T - 10 Minutes)
Run this single command in PowerShell 10 minutes prior to your presentation slot:
```powershell
python -c "import urllib.request; [urllib.request.urlopen(f'https://marine-pollution-intelligence-api.onrender.com{ep}', timeout=25) for ep in ['/health', '/ready', '/api/routes/ports', '/api/routes/spills']]; print('Render Cloud Container is Warm and Ready!')"
```

### 1.2 Browser Tab Arrangement
Arrange four clean browser tabs in Google Chrome (set zoom to 100% or 90% for standard 1080p projectors):

| Tab | URL | Screen Context | Presenter Role |
|---|---|---|---|
| **Tab 1 (Main Stage)** | `https://marine-pollution-intelligence-sih.vercel.app` | Tactical Dashboard / Satellite Intelligence Tab | Speaker 2 operates live SAR analysis |
| **Tab 2 (Route Engine)**| `https://marine-pollution-intelligence-sih.vercel.app/routeplanner` | Spill-Aware Route Planner & Fuel Meter | Speaker 2 operates routing & speed slider |
| **Tab 3 (Scientific ML)**| `https://marine-pollution-intelligence-sih.vercel.app` (ML Training Tab) | Calibration & Benchmark Confusion Matrix | Ready for judge Q&A on model metrics |
| **Tab 4 (Offline Mirror)**| `http://localhost:5173` | Local Air-Gapped Standalone Mirror | Zero-network fallback if Wi-Fi fails |

---

## 2. High-Tempo 5-Minute Cue Card (Speaker 1 & 2 Coordination)

| Minute | Stage & Action | Speaker 1 (Spoken Narration) | Speaker 2 (Screen Action) |
|---|---|---|---|
| **0:00 – 0:35** | **The Crisis & Scale** | Indian EEZ scale (2.3M km²); 70% slicks caused by deliberate bilge dumping at night, not publicized tanker crashes. | Display Tab 1 (Dashboard). Point to Indian EEZ shipping lanes on dark Leaflet map. |
| **0:35 – 1:15** | **SAR Physics & AI** | Sentinel-1 C-band SAR penetrates clouds/night; Bragg wave damping produces dark patches. Local percentile normalization + Lee despeckling. | Click **Upload Satellite SAR** $\rightarrow$ select **Sample Benchmark Scenes** $\rightarrow$ `Oil Spill Scene 1 (Zenodo Sentinel-1 Dual-Pol)`. Display VV/VH backscatter bands. |
| **1:15 – 1:55** | **Segmentation & Calibration** | SpillUNet segments boundary in 45ms CPU; CNN classifies oil vs lookalike with Platt temperature scaling ($T=1.0879$, ECE $7.57\%$). | Click **Run Pipeline Analysis**. Show red 61-vertex boundary ($20.02\text{ km}^2$) and point out **Classification Scorecard** (Oil Spill, 92.8% confidence). |
| **1:55 – 2:35** | **Hydrodynamic Drift** | Advection $\vec{V} = \vec{U}_{\text{current}} + 0.03\mathbf{R}(10^\circ)\vec{U}_{\text{wind}}$. Hindcast (12h origin) and Forecast (24h dispersion cone). | Switch to **Timeline & Drift Tab**. Toggle hindcast (blue dashed) and forecast (amber solid) with Gaussian dispersion ellipses. |
| **2:35 – 3:15** | **AIS Vessel Attribution** | Bayesian ranking (CPA distance, temporal alignment, vessel type). **Legal Boundary:** Investigative leads for aerial patrol, NOT criminal proof. | Switch to **AIS Attribution Tab**. Point to top vessel (*Stolt Tenacity* / *MV KONKAN STAR*, 82.5% likelihood). Open Evidence Drawer and highlight Legal Disclaimer. |
| **3:15 – 4:15** | **Judge Feature: Routing & Fuel** | Direct baseline intersects slick. Visibility-tangent avoidance graph (+1.5 NM buffer) calculates safest detour. Admiralty cubic physics ($P \propto V^3$) estimates bunker consumption and emissions. | Switch to Tab 2 (`/routeplanner`). Show Mumbai $\rightarrow$ Arabian Sea route. Point out **Fuel Indication Meter**, toggle **MT $\longleftrightarrow$ L**, slide speed from 14.5 to 12.0 kts to show fuel cost recovery. |
| **4:15 – 4:45** | **Scientific Rigor & Gates** | 450 held-out Zenodo scenes (0 data leakage). 96.00% lookalike rejection. Multiscale segmentation rejected via fail-closed safety gate. | Switch to Tab 3 (ML Status Tab). Point to confusion matrix, checksums, and temperature calibration curve. |
| **4:45 – 5:00** | **SOLAS & Conclusion** | SOLAS Chapter V Regulation 34 advisory notice. Unified loop: **Detect $\rightarrow$ Trace $\rightarrow$ Correlate $\rightarrow$ Avoid $\rightarrow$ Optimize $\rightarrow$ Estimate Fuel.** | Scroll to SOLAS advisory notice at bottom of Route Planner. Conclude and open floor for jury Q&A. |

---

## 3. Keyboard Shortcuts & Operational Tips

- **Fullscreen Browser:** Press `F11` in Chrome to hide browser address bars and maximize tactical display space.
- **Instant Local Fallback:** If cloud hangs during demo, press `Ctrl + 4` to switch to Tab 4 (`http://localhost:5173`) without announcing a problem.
- **Map Recentering:** Double-click the map canvas or click any port marker to automatically center and zoom the view.
- **Unit Toggle:** Click the **Metric Tonnes / Litres** button on the Fuel Meter to showcase flexibility for both commercial bunkering officers and naval engineers.
