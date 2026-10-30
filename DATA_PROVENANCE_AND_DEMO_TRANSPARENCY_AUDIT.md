# DATA PROVENANCE AND DEMO TRANSPARENCY AUDIT
**Marine Pollution Intelligence — Comprehensive Audit of Data Sources, Replays, and Demonstrations**
**Date:** 2026-10-30 | **Status:** Audited & Disclosed

---

## 1. Executive Summary

In compliance with the Smart India Hackathon integrity standards and international scientific transparency mandates, this report details the exact origin, collection methodology, and synthetic/historical status of all data utilized across the **Marine Pollution Intelligence** system.

The platform strictly differentiates between:
1. **Genuine Scientific Data:** Published, peer-reviewed satellite SAR imagery and held-out test splits.
2. **Historical Replay Fixtures:** Real historical AIS broadcasts and metocean fields packaged for deterministic reproducibility during live demonstrations.
3. **Parametric Engineering Physics:** Standard naval architecture coefficients for maritime routing and bunker fuel consumption.

---

## 2. Satellite SAR Imagery Provenance

### 2.1 Zenodo Benchmark Dataset
The platform's neural models are trained and benchmarked against the open-access **Sentinel-1 SAR Oil Spill Dataset** hosted on Zenodo:
- **Part I (Training Corpus):** DOI `10.5281/zenodo.8346860` (Sentinel-1 C-band SAR Level-1 Ground Range Detected [GRD] imagery).
- **Part II (Training & Validation):** DOI `10.5281/zenodo.8253899`.
- **Part III (Strictly Held-Out Test Set):** DOI `10.5281/zenodo.13761290`. Consists of exactly 450 scenes ($256 \times 256$ patches) balanced evenly across Oil ($N=150$), No-Oil ($N=150$), and Lookalike ($N=150$). Zero overlap exists between training scenes and Part III.

### 2.2 Built-In Demonstration Fixtures
The application repository includes five pre-configured GeoTIFF demonstration scenes (`backend/data/sample_scenes/`):
- `demo_oil_scene_01.tif`: Authentic Sentinel-1 SAR acquisition off Mumbai High oil field.
- `demo_oil_scene_02.tif`: Large cargo vessel bilge discharge slick along the international shipping corridor.
- `demo_oil_scene_03.tif`: Standard calibration scene with verified oil slick ground truth.
- `demo_oil_scene_04.tif`: Natural biogenic lookalike (internal wave and low-wind calm sea area).
- `demo_oil_scene_05.tif`: Clean open ocean baseline scene (No-Oil).

*Disclosure:* These files are extracted directly from the Zenodo dataset with preserved 16-bit radiometric calibration and GeoTIFF geocoding tags.

---

## 3. AIS Vessel Traffic Data

### 3.1 Historical Broadcast Replay vs. Live Feeds
> [!NOTE]
> **Operational Disclosure:** For demonstration stability, system resilience, and privacy compliance, AIS vessel tracks within default investigations operate in **deterministic historical replay mode**, derived from actual historical AIS broadcasts collected via terrestrial and satellite receivers in the Arabian Sea.

### 3.2 Spatiotemporal Correlation Integrity
- Vessel trajectories correspond to actual IMO-registered commercial vessels traversing the Gulf of Khambhat and Arabian Sea shipping lanes.
- Temporal interpolation utilizes spherical linear interpolation between recorded transponder pings.
- The platform explicitly labels AIS correlation scores as **Investigative Leads** under UNCLOS Article 217 / Indian Merchant Shipping Act Section 19, never claiming definitive proof of guilt.

---

## 4. Metocean & Oceanographic Drift Data

### 4.1 Hybrid Live and Climatological Fallback Architecture
The system integrates live atmospheric and oceanographic APIs with local physical fallback models:
1. **Live Atmospheric / Surface Winds:** Queries Open-Meteo 10m surface wind vectors ($u_{10}, v_{10}$) via HTTPS when connectivity is available.
2. **Deterministic Climatological Ocean Currents:** When external APIs are rate-limited, unreachable, or executing in air-gapped demo environments, the system falls back to a 1/12° spatial resolution monthly mean surface current matrix derived from the **ECMWF ERA5 & Global Drifter Program** reanalysis.
3. **Drift Physics:** Oil slick trajectory forecasting utilizes standard leeway drift formulation:
   $$\vec{u}_{\text{slick}} = \vec{u}_{\text{current}} + \alpha \cdot \mathbf{R}(\theta) \cdot \vec{u}_{\text{wind}}$$
   Where leeway coefficient $\alpha = 0.035$ (3.5% wind drift) and wind deflection angle $\theta = 15^\circ$ (Coriolis steering in the Northern Hemisphere).

---

## 5. Maritime Routing & Bunker Physics Baseline

### 5.1 Navigational Waypoints & Bathymetry
- **Port Coordinates:** Real-world WGS-84 coordinates for major Indian and international ports (e.g., Mumbai/JNPT: $18.940^\circ\text{N}, 72.840^\circ\text{E}$; Mormugao/Goa: $15.415^\circ\text{N}, 73.790^\circ\text{E}$; Kandla: $23.003^\circ\text{N}, 70.218^\circ\text{E}$).
- **Geodesic Computation:** Great Circle nautical distance computed via the Vincenty inverse geodesic formula on the WGS-84 ellipsoid.

### 5.2 Naval Architecture & Propulsion Coefficients
Vessel characteristics and fuel equations are drawn from authoritative naval architecture standards:
- **Displacement & Dimensions:** Sourced from standard Lloyd's Register vessel design classes (Aframax Tanker: 115,000 DWT, 245m LOA, 14.5m draft).
- **Admiralty Formula:** Shaft power estimated via $P = \frac{\Delta^{2/3} \cdot V^3}{C_{\text{adm}}}$ with empirical $C_{\text{adm}} = 450$.
- **Specific Fuel Oil Consumption (SFOC):** Baseline 172 g/kWh for modern two-stroke marine diesel engines (MAN Energy Solutions 6S60ME-C specification).
- **Emissions & Bunker Costs:** $3.114 \text{ MT CO}_2$ per MT of Heavy Fuel Oil (IMO GHG Study 4); bunker fuel benchmarked at $\$650 / \text{MT}$ (Rotterdam/Singapore IFO 380 average).
