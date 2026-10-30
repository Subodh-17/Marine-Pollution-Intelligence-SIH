# Smart India Hackathon (SIH) — Final System Architecture
## Marine Pollution Intelligence & Spill-Aware Maritime Navigation (SIH-26143)

**Architecture Version:** 2.0.0 (SIH Final Handover Release)  
**Core Technologies:** Sentinel-1 C-Band SAR, PyTorch / ONNX Runtime CPU, FastAPI, React 19, Leaflet GIS, Shapely, ReportLab  
**Deployment Infrastructure:** Vercel Global Edge CDN (Frontend) | Render Containerized Microservices (Backend) | Local Offline Air-Gapped Mirror

---

## 1. Unified End-to-End Operational Pipeline

The platform resolves the traditional disconnect between environmental satellite monitoring and commercial vessel navigation by establishing a single, closed-loop intelligence architecture:

```
  ┌────────────────────────────────────────────────────────────────────────┐
  │ 1. SATELLITE RADAR INGESTION & RADIOMETRIC NORMALIZATION               │
  │    Sentinel-1 C-Band SAR (IW GRD Dual-Pol VV & VH)                     │
  │    Sample-local 1st-99th percentile min-max scaling (0 data leakage)   │
  │    Lee adaptive spatial despeckling preserving sharp slick gradients   │
  └───────────────────────────────────┬────────────────────────────────────┘
                                      │ Normalized Tensor [256, 256, 2]
                                      ▼
  ┌────────────────────────────────────────────────────────────────────────┐
  │ 2. DUAL DEEP LEARNING INFERENCE ENGINE (ONNX RUNTIME CPU)              │
  │    A. SpillUNet: Dense pixel probability mask -> 61-vertex boundary    │
  │    B. CNN Classifier: 3-class prediction (Oil / Lookalike / Clean)     │
  │    Platt Temperature Scaling (T = 1.0879) fitted on validation split   │
  │    Expected Calibration Error (ECE): 8.66% -> 7.57% (Freq. confidence) │
  └───────────────────────────────────┬────────────────────────────────────┘
                                      │ Centroid, Geometry & Detection Timestamp
                                      ▼
  ┌────────────────────────────────────────────────────────────────────────┐
  │ 3. HYDRODYNAMIC DRIFT ENGINE & DISPERSION MODELING                     │
  │    Vector advection: V_drift = U_current + 0.03 * R(10°) * U_wind      │
  │    Backward Hindcast (12h): Advection reversal to origin window        │
  │    Forward Forecast (24h): Stochastic Gaussian dispersion ellipses     │
  │    Graceful Fallback: Regional climatological vectors if APIs time out │
  └───────────────────────────────────┬────────────────────────────────────┘
                                      │ Estimated Discharge Origin Window
                                      ▼
  ┌────────────────────────────────────────────────────────────────────────┐
  │ 4. BAYESIAN AIS SPATIO-TEMPORAL VESSEL ATTRIBUTION                     │
  │    50 NM corridor query & 12-hour temporal transponder correlation     │
  │    Ranking metric: Closest Point of Approach (CPA) distance & time,    │
  │    vessel risk type weighting (Tanker > Container > Bulk Carrier),     │
  │    and kinematic speed anomalies (deliberate tank-washing speed drops) │
  │    Strict Boundary: Investigative leads only; no legal conviction      │
  └───────────────────────────────────┬────────────────────────────────────┘
                                      │ Detected Active Hazard Boundary
                                      ▼
  ┌────────────────────────────────────────────────────────────────────────┐
  │ 5. SPILL-AWARE ROUTING & FUEL INTELLIGENCE SYSTEM                      │
  │    Automated polygon handoff (no manual coordinate entry)              │
  │    IMO-buffered convex hull (+1.5 NM safety zone)                      │
  │    Visibility-tangent waypoint graph around marine hazard boundary     │
  │    Naval Architecture Admiralty Fuel Physics: P = (Δ^(2/3) * V^3) / C  │
  │    Interactive Fuel Indication Meter (MT <-> L, speed elasticity)      │
  │    SOLAS Chapter V / IMO Resolution A.893(21) regulatory compliance    │
  └────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Technical Component Breakdown

### 2.1 Stage 1: Ingestion & Radiometric Normalization
- **Sensor:** Sentinel-1 C-Band (5.405 GHz) Synthetic Aperture Radar in Interferometric Wide (IW) swath mode, Ground Range Detected (GRD).
- **Polarization Bands:**
  - **VV (Vertical-Vertical):** High sensitivity to surface capillary gravity waves; petroleum dampens Bragg resonance, causing backscatter drops.
  - **VH (Vertical-Horizontal):** Cross-polarization channel providing volume scattering contrast to separate emulsified plumes from low-wind slicks.
- **Speckle Suppression:** Lee adaptive spatial filter using local variance weighting to suppress multiplicative speckle noise while preserving boundary edges.
- **Normalization Strategy (`sar_percentile_1_99_minmax_v1`):**
  $$x_{\text{norm}} = \frac{x - P_1(x)}{P_{99}(x) - P_1(x)}$$
  Computed strictly on a per-sample basis to prevent global population statistics leakage.

### 2.2 Stage 2: Dual Neural Inference Core (ONNX Runtime CPU)
- **SpillUNet (Segmentation):**
  - **Architecture:** Lightweight U-Net encoder-decoder with residual skip connections.
  - **Input Tensor:** $256 \times 256 \times 2$ (float32).
  - **Output:** Dense sigmoid probability mask thresholded at $0.5$ to extract WGS-84 vector polygons.
  - **Footprint & Latency:** 54 KB model size, execution latency $\sim 42\text{ ms}$ on standard 2-core CPU.
- **Scene Classifier (Classification):**
  - **Architecture:** Multi-class convolutional neural network with global average pooling.
  - **Classes:** `Oil Spill`, `Lookalike` (algal bloom, calm sea, upwelling), `Clean Sea`.
  - **Temperature Scaling Calibration:**
    $$\hat{p}_i = \frac{\exp(z_i / T)}{\sum_j \exp(z_j / T)}, \quad T = 1.0879$$
    Fitted strictly on 386 validation scenes; reduces Expected Calibration Error (ECE) to $7.57\%$.
  - **Footprint & Latency:** 50 KB model size, execution latency $\sim 38\text{ ms}$ on CPU.

### 2.3 Stage 3: Hydrodynamic Drift & Dispersion Engine
- **Governing Physics:** Empirical maritime advection vector superposition:
  $$\vec{V}_{\text{drift}} = \vec{U}_{\text{current}} + \alpha \mathbf{R}(\theta) \vec{U}_{\text{wind}}$$
  where $\alpha = 0.03$ (3% wind leeway), and $\mathbf{R}(\theta)$ is a $10^\circ$ clockwise rotation matrix representing Northern Hemisphere Coriolis deflection.
- **Temporal Modeling:**
  - **Hindcast (Origin Reconstruction):** Reverses advection vector across 12 hours to project the slick centroid back to its initial discharge coordinates.
  - **Forecast (Coastal Impact):** Advances advection forward across 24 hours, computing expanding Gaussian spatial uncertainty ellipses governed by turbulent horizontal diffusion ($K_h \approx 10-50\text{ m}^2/\text{s}$).

### 2.4 Stage 4: Bayesian AIS Spatio-Temporal Attribution
- **Transponder Mining:** Ingests historical AIS messages within a 50 NM bounding box and $\pm 12\text{ h}$ temporal window around the hindcasted discharge origin.
- **Scoring Breakdown:**
  $$S_{\text{vessel}} = w_1 S_{\text{spatial}} + w_2 S_{\text{temporal}} + w_3 S_{\text{risk}} + w_4 S_{\text{kinematic}}$$
  - **Spatial ($w_1 = 0.40$):** Orthodromic Great-Circle distance to closest approach.
  - **Temporal ($w_2 = 0.30$):** Time delta between vessel CPA and estimated discharge time.
  - **Vessel Type Risk ($w_3 = 0.20$):** Crude/Chemical Tanker ($1.0$) vs Container ($0.5$) vs Bulk Carrier ($0.3$) vs Passenger ($0.05$).
  - **Kinematic Anomaly ($w_4 = 0.10$):** Course changes or slow-steaming characteristic of tank deballasting.
- **Legal Safeguard:** The system outputs **investigative prioritization leads**; physical hydrocarbon sampling (gas chromatography) remains mandatory for judicial proceedings under MARPOL Annex I.

### 2.5 Stage 5: Spill-Aware Routing & Fuel Intelligence
- **Graph Solver:** Constructs a tangent-waypoint visibility graph around the convex hull of the oil spill buffered by an IMO safety margin (default $+1.5\text{ NM}$).
- **Admiralty Power Physics:**
  $$P_{\text{propulsion}} = \frac{\Delta^{2/3} \cdot V^3}{C_{\text{adm}}}$$
  $$\dot{m}_{\text{fuel}} = (P_{\text{propulsion}} \cdot \text{SFOC}_{\text{main}} + P_{\text{aux}} \cdot \text{SFOC}_{\text{aux}}) \times 10^{-6}\text{ [MT/h]}$$
  where $\Delta$ is displacement tonnage, $V$ is speed through water, $C_{\text{adm}}$ is the calibrated Admiralty coefficient, and $\text{SFOC}$ is specific fuel consumption ($175-190\text{ g/kWh}$).
- **Emissions Factor:** Fuel mass multiplied by IMO carbon factor ($3.114\text{ MT CO}_2 / \text{MT IFO380}$).

---

## 3. Deployment Architecture & Dual-Stack Topology

```
  ┌────────────────────────────────────────────────────────────────────────┐
  │                           PRODUCTION CLOUD                             │
  │                                                                        │
  │   [ Vercel Global Edge CDN ]  ──────>  [ Render Docker Container ]     │
  │   • React 19 Single Page App           • FastAPI Python 3.11 Microsvc  │
  │   • Code-split ES modules              • ONNX Runtime CPU Inference    │
  │   • Leaflet GIS Map Client             • SQLite / PostgreSQL Storage   │
  │   • Tailwind Tactical Theme            • ReportLab 5.0.1 PDF Engine    │
  └────────────────────────────────────────────────────────────────────────┘
                                      ▲
                                      │ Identical Codebase & Execution
                                      ▼
  ┌────────────────────────────────────────────────────────────────────────┐
  │                       OFFLINE STANDALONE MIRROR                        │
  │                                                                        │
  │   [ Localhost:5173 (Vite) ]   ──────>  [ 127.0.0.1:8000 (Uvicorn) ]    │
  │   • Vite Dev Server with Proxy         • Local FastAPI Server          │
  │   • Cached Leaflet Basemap Tiles       • Local ONNX Model Weights      │
  │   • Local Sample SAR GeoTIFFs          • Local SQLite `app.db`         │
  └────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Hardware & Microservice Performance Profile

| Metric | Measured Specification | Evaluation Context |
|---|---|---|
| **SpillUNet Inference Latency** | **$42\text{ ms}$** | Standard x86-64 2-Core CPU (ONNX Runtime) |
| **Classifier Inference Latency** | **$38\text{ ms}$** | Standard x86-64 2-Core CPU (ONNX Runtime) |
| **Avoidance Route Graph Solve** | **$48\text{ ms}$** | Tangent-waypoint Dijkstra with 61 vertices |
| **End-to-End Pipeline Duration**| **$384\text{ ms}$** | Complete investigation execution (Demo mode) |
| **Total Memory Consumption** | **$< 320\text{ MB}$** | FastAPI + ONNX Runtime active resident memory |
| **GPU Requirement** | **ZERO (0 MB VRAM)** | Fully optimized for low-cost CPU microservices |
