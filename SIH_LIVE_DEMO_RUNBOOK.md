# Smart India Hackathon (SIH) — Live Demonstration Runbook

**Document Purpose:** Complete, reproducible, click-by-click operational runbook for delivering a flawless, end-to-end Smart India Hackathon demonstration of the Marine Pollution Intelligence & Maritime Route Planning Platform.

---

## 1. Pre-Demonstration Setup & Verification (T - 15 Minutes)

### 1.1 Production URLs & Local Fallbacks
| Environment | Frontend URL | Backend URL | Purpose |
|---|---|---|---|
| **Primary Live** | `https://marine-pollution-intelligence-sih.vercel.app` | `https://marine-pollution-intelligence-api.onrender.com` | Official live cloud demo |
| **Local Backup** | `http://localhost:5173` | `http://localhost:8000` | Zero-network fallback |

### 1.2 Pre-Flight Cloud Warm-Up Script
Run this single command 10 minutes prior to judging to ensure the Render free-tier container is warm and prevent cold-start latency:
```bash
python -c "
import urllib.request
for ep in ['/health', '/ready', '/api/routes/ports', '/api/satellite/samples']:
    r = urllib.request.urlopen(f'https://marine-pollution-intelligence-api.onrender.com{ep}', timeout=25)
    print(ep, r.status)
"
```

### 1.3 Recommended Browser Tabs
Arrange four clean browser tabs in order:
1. **Tab 1 (Main Stage):** `https://marine-pollution-intelligence-sih.vercel.app` (Dashboard Tab)
2. **Tab 2 (Route Planning):** `https://marine-pollution-intelligence-sih.vercel.app/routeplanner`
3. **Tab 3 (ML Architecture):** `https://marine-pollution-intelligence-sih.vercel.app` (ML Training Tab)
4. **Tab 4 (Offline Fallback Mirror):** `http://localhost:5173`

---

## 2. The 23-Step End-to-End Live Demonstration Sequence

### Step 1: Open the Platform Dashboard
- **Action:** Open Tab 1 (`/`).
- **Visual:** Marine Pollution Intelligence command center loads with dark maritime tactical UI, global KPI summary cards, and interactive Leaflet map.
- **Narrative:** *"Welcome to Marine Pollution Intelligence. The command center aggregates automated satellite alerts, detected slicks, and commercial vessel tracks across the Indian EEZ."*

### Step 2: Introduce the Marine Pollution Problem
- **Action:** Gesture to the active incidents counter and shipping corridors along the Mumbai-Goa-Gujarat coast.
- **Narrative:** *"Over 70% of marine oil slicks result from illicit bilge washing by passing merchant vessels under cloud cover or night, rather than publicized tanker collisions."*

### Step 3: Ingest Authentic Sentinel-1 SAR Image
- **Action:** Click the top-right button **Upload Satellite SAR** or navigate to the **Satellite Intelligence Tab**.
- **Action:** In the modal, select **Sample Benchmark Scenes** $\rightarrow$ choose **`Oil Spill Scene 1 (Zenodo Sentinel-1 Dual-Pol)`** (`demo_oil_scene_03.tif`).
- **Narrative:** *"We ingest a genuine Sentinel-1 SAR dual-polarization scene from the Zenodo scientific benchmark, capturing an actual marine petroleum release."*

### Step 4: Show Original SAR Imagery
- **Action:** Inspect the raw radar backscatter preview.
- **Visual:** Display dual channels: VV (Vertical-Vertical) and VH (Vertical-Horizontal).
- **Narrative:** *"Notice the dark patch where capillary gravity waves are suppressed by the surfactant oil slick, causing radar backscatter attenuation."*

### Step 5: Show Preprocessing and Despeckling
- **Action:** Highlight the preprocessing indicator: `sar_percentile_1_99_minmax_v1` with Lee adaptive filtering.
- **Narrative:** *"Raw SAR suffers from multiplicative speckle noise. Our sample-local percentile scaling normalizes the radar cross-section without leaking global dataset statistics."*

### Step 6: Run SpillUNet Segmentation
- **Action:** Click **Run Pipeline Analysis**.
- **Visual:** Progress bar advances smoothly: Ingestion $\rightarrow$ Normalization $\rightarrow$ SpillUNet $\rightarrow$ Classification $\rightarrow$ Drift Engine.
- **Narrative:** *"SpillUNet executes in 45 milliseconds on ONNX Runtime CPU, extracting a dense spatial probability mask."*

### Step 7: Show the Predicted Spill Boundary
- **Action:** The map automatically centers on the detected slick in the Arabian Sea off the Mumbai High offshore basin.
- **Visual:** A crisp red outline with 61 geographic vertices overlays the satellite scene.
- **Narrative:** *"The boundary polygon captures the elongated morphology of the slick, matching hydrodynamic dispersion."*

### Step 8: Show CNN Classification and Calibrated Probabilities
- **Action:** Point out the **Classification Scorecard**.
- **Visual:** Class: `Oil Spill` | Calibrated Confidence: `92.8%` | Platt Temperature: `T = 1.0879`.
- **Narrative:** *"Our CNN classifier confirms the slick is petroleum-derived with 92.8% calibrated probability, avoiding overconfident hallucinations."*

### Step 9: Compare AI Detector with Classical Detector
- **Action:** Toggle the **Classical vs AI Comparison** pill in the scorecard.
- **Visual:** The classical Otsu/adaptive thresholding detector displays elevated boundary noise and fragments on subtle sheen, whereas SpillUNet maintains topological integrity.
- **Narrative:** *"Classical thresholding struggles when sea clutter and wind gradients vary across the swath. The deep learning segmenter rejects speckle noise and preserves true boundaries."*

### Step 10: Show Spill Area and Geographic Location
- **Action:** Read the physical metrics from the scorecard:
  - **Estimated Area:** $20.02\text{ km}^2$
  - **Perimeter:** $96.18\text{ km}$
  - **Length / Width:** $16.85\text{ km} \times 2.86\text{ km}$
  - **Centroid Coordinates:** $19.044^\circ\text{N}, 71.204^\circ\text{E}$ (Arabian Sea)
- **Narrative:** *"The system converts pixel dimensions to WGS-84 geodesic measurements using Sentinel-1 corner coordinate tie-points."*

### Step 11: Run Drift Hindcasting and Forecasting
- **Action:** Click the **Timeline & Drift Tab**.
- **Visual:** The map draws two vector trajectories from the slick centroid:
  - **Blue dashed path (Hindcast):** Traces 12 hours backward in time to the probable discharge location.
  - **Amber solid path (Forecast):** Traces 24 hours forward in time toward the coast.
- **Narrative:** *"Our hydrodynamic engine integrates wind leeway (3%) and surface ocean currents with Coriolis deflection to back-calculate the origin point."*

### Step 12: Display Uncertainty and Predicted Movement
- **Action:** Toggle on the **Gaussian Dispersion Envelope**.
- **Visual:** Semi-transparent expanding error ellipses widen with each forecast hour.
- **Narrative:** *"We acknowledge epistemic uncertainty: turbulent horizontal diffusion widens the search cone over time, indicating coastal impact probabilities."*

### Step 13: Retrieve Relevant Historical AIS Vessel Records
- **Action:** Click the **AIS Vessel Attribution Tab**.
- **Visual:** Historical vessel transponder tracks overlay the map along the commercial shipping lane.
- **Narrative:** *"The platform queries historical AIS messages within a 50 NM corridor and 12-hour temporal window around the hindcasted release time."*

### Step 14: Rank Potential Vessel Candidates
- **Action:** Examine the **Vessel Ranking Table**.
- **Visual:** 
  - Rank 1: *Stolt Tenacity* (MMSI: `357123000`, Chemical/Oil Tanker, Likelihood Score: `88.4%`)
  - Rank 2: *Ever Given* (MMSI: `416000000`, Container Ship, Likelihood Score: `41.2%`)
  - Rank 3: *Bulk Carrier Alpha* (MMSI: `538000000`, Likelihood Score: `24.0%`)
- **Narrative:** *"Vessels are scored across temporal alignment, closest point of approach to the hindcasted release point, vessel type risk factor, and speed anomalies."*

### Step 15: Explain Why AIS Correlation Is Not Legal Proof
- **Action:** Click the top candidate to open the **Evidence Detail Drawer**. Highlight the **Legal Disclaimer Banner**.
- **Narrative:** *"We emphasize to maritime authorities: AIS correlation produces investigative leads, not criminal guilt. Clock jitter, transponder spoofing, or unrecorded events can occur. This gives the Coast Guard the exact target coordinates for physical aerial interception and sampling."*

### Step 16: Open the Maritime Route Planner
- **Action:** Click the **Route Planner Tab** (or switch to Tab 2: `/routeplanner`).
- **Visual:** Navigational chart interface opens with Indian EEZ shipping corridors, major ports, and active avoidance zones.
- **Narrative:** *"In response to SIH judges' feedback, we now demonstrate our newest operational module: Spill-Aware Maritime Route Optimization and Fuel Intelligence."*

### Step 17: Select Origin and Destination
- **Action:** In the planning panel:
  - **Origin:** Select `Mumbai Port` ($18.940^\circ\text{N}, 72.840^\circ\text{E}$)
  - **Destination:** Select `Arabian Sea West` ($19.050^\circ\text{N}, 70.400^\circ\text{E}$)
  - **Vessel Type:** Select `Tanker` (VLCC/Aframax profile)
- **Narrative:** *"We plan an outbound tanker voyage from Mumbai Port transiting west toward international Arabian Sea corridors."*

### Step 18: Automatically Retrieve the Detected Spill Polygon
- **Action:** Point to the active hazards list: `Active SAR Slick (6883af7d)` is already loaded with its 61-vertex boundary.
- **Narrative:** *"There is zero manual coordinate entry. The SAR-detected slick is automatically ingested directly into the routing graph."*

### Step 19: Generate the Baseline Route
- **Action:** Ensure **Avoid Spills** is checked, and review the baseline route.
- **Visual:** The direct Great-Circle baseline (shown as a dashed gray line, $138.90\text{ NM}$) cuts directly through the red oil slick.
- **Status:** `Baseline Intersects Spill: YES (CRITICAL HAZARD)`.
- **Narrative:** *"The conventional direct voyage passes straight through the hazardous oil slick, risking hull contamination, intake fouling, and legal violation."*

### Step 20: Generate the Spill-Avoiding Alternative
- **Action:** Inspect the solid blue **Recommended Route**.
- **Visual:** The blue line diverts around the buffered slick boundary ($+1.5\text{ NM}$ buffer), clearing all vertices with $8.94\text{ NM}$ clearance.
- **Narrative:** *"Our graph algorithm computes visibility tangents around the buffered convex hull, finding the shortest hazard-free corridor ($163.96\text{ NM}$)."*

### Step 21: Compare Distance, Travel Time and Fuel Consumption
- **Action:** Review the **Comparative Voyage Metrics Card**:
  - **Distance Delta:** $+25.06\text{ NM}$ ($+18.0\%$)
  - **Time Delta:** $+1.73\text{ hours}$
  - **Extra Fuel Burned:** $+4.69\text{ Metric Tonnes}$ ($5,453\text{ Litres}$)
  - **Voyage Cost Delta:** $+\$3,048.42$ (at standard IFO380 bunker price $\$650/\text{MT}$)
  - **Carbon Delta:** $+14.61\text{ MT CO}_2$
- **Narrative:** *"Naval architecture power laws calculate the exact cost of compliance. Master and charterer know the financial and environmental trade-off before leaving port."*

### Step 22: Demonstrate the Fuel Indication Meter
- **Action:** Focus on the **Fuel Indication Meter** component.
- **Action:** Toggle unit between **Metric Tonnes (MT)** and **Litres (L)**.
- **Action:** Adjust the vessel speed input from $14.5\text{ kts}$ to $12.0\text{ kts}$ and observe fuel consumption drop drastically.
- **Narrative:** *"Due to Admiralty cubic power physics ($P \propto V^3$), reducing speed to 12 knots recovers the fuel cost of the diversion entirely. The fuel meter confirms vessel endurance remains safely above reserve margins."*

### Step 23: Explain the Final Operational Recommendation
- **Action:** Scroll to the bottom of the Route Planner.
- **Visual:** Highlight the **SOLAS Chapter V / IMO Resolution A.893(21)** Advisory Recommendation.
- **Narrative:** *"The platform outputs an actionable recommendation: proceed via the spill-avoiding waypoint route, maintaining 1.5 NM minimum clearance, while respecting official nautical charts and master's navigation authority. This completes the end-to-end loop: Detect, Characterize, Trace, Correlate, Avoid, Optimize, and Estimate Fuel."*

---

## 3. Graceful Failure & Recovery Runbooks

### Incident A: Render Container Cold-Start Delay (>15 Seconds)
- **Symptom:** API calls hang with a spinner because Render has spun down the free container.
- **Action:** 
  1. Do not panic or refresh the page repeatedly.
  2. While the spinner resolves, explain: *"The microservices run on containerized cloud workers with zero idle resource waste; the container is initializing ONNX runtimes."*
  3. Pre-flight warm-up script in Section 1.2 prevents this entirely.

### Incident B: External Metocean API Timeout
- **Symptom:** Open-Meteo or NOAA GFS ocean current request exceeds 5-second deadline.
- **Built-in Handling:** The backend automatically falls back to Indian Ocean seasonal climatological vectors without user-facing errors.
- **Narrative:** *"The system incorporates resilient graceful degradation: when satellite weather feeds time out, it utilizes regional climatological hydrodynamic priors."*

### Incident C: Network Disconnection at Venue
- **Symptom:** Venue Wi-Fi drops completely.
- **Action:**
  1. Instantly switch to Tab 4 (`http://localhost:5173`).
  2. The local mirror runs from local SQLite database and pre-cached Zenodo scenes.
  3. All 23 steps execute identically with zero external network dependency.

### Incident D: Corrupted Polygon or Boundary Error
- **Symptom:** User supplies custom coordinates with invalid format.
- **Built-in Handling:** `_is_valid_polygon()` filters corrupt/out-of-bounds coordinates, falls back cleanly to baseline navigation, and logs an actionable warning.
