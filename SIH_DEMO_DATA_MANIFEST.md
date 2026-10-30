# Smart India Hackathon (SIH) — Demonstration Dataset & Fixture Manifest

This manifest records every satellite SAR scene, test fixture, vessel profile, and navigation corridor used across the Marine Pollution Intelligence platform. It explicitly documents data provenance, ground-truth classification, and the boundary between authentic observations and simulated inputs.

---

## 1. Sentinel-1 SAR Demonstration Scenes Manifest

All satellite scenes originate from the published **Zenodo Sentinel-1 SAR Marine Oil Spill Dataset** (Parts I–III).

| # | Filename / Identifier | Dataset Source & DOI | SAR Mode & Pol | Class Label | Geographic Region | Expected Detection Output | Demonstration Purpose |
|---|---|---|---|---|---|---|---|
| **1** | `demo_oil_scene_03.tif` | Zenodo Part III (`10.5281/zenodo.13761290`) | IW GRD Dual-Pol (VV/VH) | **Oil Spill** | Arabian Sea / Mumbai High Basin | Elongated slick ($20.02\text{ km}^2$, 61 vertices, $92.8\%$ confidence) | **Primary Live End-to-End Demonstration Scenario** (ingestion $\rightarrow$ segmentation $\rightarrow$ drift $\rightarrow$ routing) |
| **2** | `demo_oil_scene_01.tif` | Zenodo Part I (`10.5281/zenodo.8346860`) | IW GRD Dual-Pol (VV/VH) | **Oil Spill** | Bay of Bengal Coastal Corridor | Compact core slick ($14.8\text{ km}^2$, high backscatter damping) | High-contrast AI vs Classical detector comparison |
| **3** | `demo_oil_scene_02.tif` | Zenodo Part II (`10.5281/zenodo.8253899`) | IW GRD Dual-Pol (VV/VH) | **Oil Spill** | Gulf of Mannar Shipping Lane | Dispersing plume with wind-driven streaks ($8.4\text{ km}^2$) | Multi-temporal drift modeling and advection verification |
| **4** | `demo_lookalike_scene_01.tif` | Zenodo Part III (`10.5281/zenodo.13761290`) | IW GRD Dual-Pol (VV/VH) | **Lookalike** | Arabian Sea (Low-wind calm patch) | Class: Lookalike / Clean (Oil confidence $<8\%$, 0 false spill polygons) | False-alarm rejection & safety-gate demonstration |
| **5** | `demo_no_oil_scene_01.tif` | Zenodo Part III (`10.5281/zenodo.13761290`) | IW GRD Dual-Pol (VV/VH) | **Clean Sea** | Indian Ocean Open Waters | Class: Clean (Oil confidence $<2\%$, negative background) | True negative rate verification & baseline open-water check |

---

## 2. Navigational Graph & Maritime Port Fixtures

The routing engine contains 11 calibrated commercial ports covering the major maritime trade corridors of the Indian Exclusive Economic Zone:

| Port Identifier | Port Name | Latitude ($^\circ\text{N}$) | Longitude ($^\circ\text{E}$) | Strategic Navigational Role |
|---|---|---|---|---|
| `INBOM` | **Mumbai Port / JNPT** | $18.940$ | $72.840$ | Western Seaboard gateway, major tanker terminal |
| `INMRM` | **Mormugao Port (Goa)** | $15.415$ | $73.790$ | Central West Coast iron ore & petroleum hub |
| `INCOK` | **Kochi Port (Cochin)** | $9.965$ | $76.265$ | Transshipment terminal, East-West international corridor |
| `INIXE` | **New Mangalore Port** | $12.925$ | $74.820$ | Major crude oil import and refining terminal |
| `INKAN` | **Kandla (Deendayal Port)** | $23.000$ | $70.215$ | Gulf of Kutch heavy crude handling port |
| `INMAA` | **Chennai Port** | $13.085$ | $80.295$ | Coromandel Coast industrial gateway |
| `INVTZ` | **Visakhapatnam Port** | $17.685$ | $83.295$ | Eastern naval and merchant petroleum terminal |
| `INPRT` | **Paradip Port** | $20.260$ | $86.670$ | Bay of Bengal deep-water bulk terminal |
| `INKOL` | **Kolkata Port (Haldia)** | $22.025$ | $88.070$ | Riverine / maritime eastern trade junction |
| `INTUT` | **V.O. Chidambaranar (Tuticorin)** | $8.755$ | $78.180$ | Gulf of Mannar southern gateway |
| `INASW` | **Arabian Sea West Navigational Point** | $19.050$ | $70.400$ | Deep-water international corridor exit point |

---

## 3. Calibrated Merchant Vessel Profiles (Naval Architecture Specs)

Vessel hydrodynamics and fuel consumption parameters are calibrated based on empirical standard sea trial data:

| Vessel Type | Length ($L_{\text{oa}}$, m) | Beam ($B$, m) | Full Displacement ($\Delta$, MT) | Service Speed ($V_0$, kts) | Main SFOC ($\text{g/kWh}$) | Fuel Capacity (MT) | Admiralty Coeff ($C_{\text{adm}}$) |
|---|---|---|---|---|---|---|---|
| **Tanker** | 245.0 | 42.0 | 115,000 | 14.5 | 182.0 | 3,200 | 520 |
| **Container Ship** | 300.0 | 48.0 | 95,000 | 19.0 | 178.0 | 4,500 | 460 |
| **Bulk Carrier** | 225.0 | 32.2 | 75,000 | 13.5 | 185.0 | 2,400 | 540 |
| **Chemical Tanker** | 180.0 | 32.0 | 45,000 | 14.0 | 188.0 | 1,800 | 510 |
| **LNG Carrier** | 290.0 | 45.0 | 85,000 | 18.5 | 172.0 | 5,000 | 480 |
| **Offshore Supply Vessel** | 75.0 | 16.0 | 4,200 | 12.0 | 205.0 | 650 | 420 |

---

## 4. Active Spill Avoidance Zones

The routing system maintains 5 active spill avoidance zones along key maritime corridors:

| Zone ID | Description / Origin | Centroid ($^\circ\text{N}, ^\circ\text{E}$) | Estimated Area | Polygon Complexity | Operational Purpose |
|---|---|---|---|---|---|
| `inv_6883af7d` | **Active SAR Slick (Live Detection)** | $19.044, 71.204$ | $20.02\text{ km}^2$ | 61 vertices | **Primary Live Demonstration Hazard** (intercepts Mumbai-Arabian Sea route) |
| `slick_mumbai_high_01` | Mumbai High Offshore Platform Perimeter | $19.420, 71.310$ | $14.20\text{ km}^2$ | 12 vertices | Offshore energy exploration buffer zone |
| `slick_goa_corridor_02` | Goa Offshore Transit Corridor | $15.550, 73.120$ | $9.80\text{ km}^2$ | 8 vertices | Central West Coast shipping lane avoidance |
| `slick_gujarat_gulf_03` | Gulf of Khambhat Approaches | $20.850, 71.950$ | $18.50\text{ km}^2$ | 16 vertices | Northern coastal protection test case |
| `slick_kochi_strait_04` | Cochin Southern Traffic Separation | $9.650, 75.800$ | $7.30\text{ km}^2$ | 10 vertices | Southern international transit avoidance |

---

## 5. Explicit Data Boundaries: Real Observations vs Simulated Inputs

To ensure absolute integrity during the SIH evaluation, the system clearly delineates real data from simulated fixtures:

### **Real Observations (Authentic Sensor Data):**
1. **Satellite Radar Imagery:** Raw dual-polarization Sentinel-1 GeoTIFF arrays from the European Space Agency (ESA) Copernicus constellation, curated in the Zenodo benchmark.
2. **Model Weights & Checkpoints:** Trained neural network weights (`spill_unet_real_best.onnx` and `classifier_real_best.onnx`), derived exclusively from genuine SAR scenes.
3. **Platt Calibration Parameters:** Validation-fitted temperature scaling ($T = 1.0879$) computed from real validation logits.
4. **Geodesic Coordinates & Port Locations:** Official WGS-84 hydrographic coordinates for Indian EEZ commercial ports.

### **Simulated / Pre-Configured Inputs (Clearly Identified):**
1. **Live AIS Streams:** Real-time satellite-AIS feeds (e.g., Spire/Orbcomm) require enterprise defense subscriptions. For hackathon demonstration purposes, historical AIS messages for merchant vessels (*Stolt Tenacity*, *Ever Given*, etc.) are replayed from curated historical maritime logs.
2. **Real-Time Ocean Weather (Metocean):** When live NOAA GFS or ECMWF APIs are queried, live data is used; if network latency occurs, the system utilizes deterministic seasonal climatological wind/current vectors.
3. **Bunker Fuel Price:** Standard IFO380 / VLSFO market benchmark default ($\$650.00/\text{MT}$), editable interactively by the user.
