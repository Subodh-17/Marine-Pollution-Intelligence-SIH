# Phase 4.8.38 — Root-Cause Diagnosis: N/A Spill Age & Probable Origin in Investigation #99c16c10

**Project**: Marine Pollution Intelligence SIH  
**Repository**: `Subodh-17/Marine-Pollution-Intelligence-SIH` (`E:\Projects\Marine_Pollution_Intelligence_SIH_FINAL`)  
**Production Frontend**: `https://marine-pollution-intelligence-sih.vercel.app`  
**Production Backend**: `https://marine-pollution-intelligence-api.onrender.com`  
**Target Investigation ID**: `#99c16c10`  
**Reference Investigation ID**: `#207806cd`  
**Audit Date**: October 25, 2026  
**Auditor**: Principal Systems, Metocean Reliability & Data Integrity Auditor  

---

## 1. Executive Summary & Diagnostic Verdict

### Diagnostic Verdict: **EXPECTED SYSTEM REFUSAL (EXTERNAL 429 RATE LIMIT + STRICT SCIENTIFIC HONESTY GUARD)**

The observed dashboard display for investigation `#99c16c10`:
- **Estimated Age**: `N/A`
- **Probable Origin**: `N/A`
- **Environment**: `UNAVAILABLE — Operational mode refused synthetic fallback`
- **Drift**: `NOT_RUN (environmental data unavailable)`

is **NOT a bug, NOT a regression from the performance optimizations merged in PR #3, and NOT a data-corruption incident**. 

Instead, it is the **expected, scientifically valid behavior** specified by the system's core architecture when operating in `real_satellite` mode:
1. **External Rate-Limit Failure**: During analysis execution at `2026-10-25T14:36:00Z`, the upstream Open-Meteo Weather Forecast API rejected requests from the backend with **HTTP 429 Too Many Requests**. After 3 backoff attempts over 5.07 seconds, `OpenMeteoOceanAdapter` exhausted its retry budget.
2. **Strict Operational Safety Refusal**: Unlike `demo` mode (which falls back to a trigonometric synthetic wind field for offline demonstrations), `real_satellite` mode activates a **zero-fabrication scientific honesty gate**. The system explicitly refused to substitute mock/synthetic weather data into a real satellite scene analysis.
3. **Legitimate Downstream Cascades**:
   - **Spill Age Estimation**: The Fay/Lehr spreading heuristic requires ambient wind speed ($U_{10}$). Without valid metocean wind data, age cannot be physically calculated and evaluated to `null`.
   - **Hindcast & Probable Origin**: Advection vector integration requires verified wind leeway and CMEMS ocean current velocity. Without environmental fields, drift simulation was skipped (`NOT_RUN`), and probable origin evaluated to `null`.
4. **Truthful UI Presentation**: The frontend accurately reflected backend `null` values as `N/A`, reported `Drift unavailable`, showed the uncorrupted satellite detection polygon on the tactical map, and displayed the explicit refusal reason in the data provenance badge and timeline tab.

---

## 2. Forensic Investigation of Affected Record `#99c16c10`

### 2.1 Record Provenance and Metadata
- **Investigation ID**: `99c16c10`
- **Created At**: `2026-10-25T14:36:00.786343+00:00`
- **Operational Mode**: `real_satellite`
- **Input Image**: Dual-polarization VV/VH SAR GeoTIFF (`tmpsmd8o7j8.tif`)
- **Geographic Location**: Southeastern Kalimantan / Makassar Strait, Indonesia
  - Slick Centroid: Latitude `-3.94824°`, Longitude `116.51978°`
  - Scene Bounds: North `-3.82424°`, South `-4.07224°`, East `116.64378°`, West `116.39578°`
  - Geolocation Source: Embedded GeoTIFF metadata (`geolocation_is_assumed: false`)
- **ML Detection Result**:
  - Model: PyTorch SpillUNet (`ML_REAL_ZENODO_TRAINED`)
  - Confidence: `0.9996` (99.96%)
  - Spill Area: $1.84\text{ km}^2$ (1,840 pixels, 2.81% of scene)
  - Morphometry: Length $2.14\text{ km}$, Width $0.66\text{ km}$, Aspect Ratio $3.25$

### 2.2 Environment Resolution Trace
At stage 5 of the pipeline (`orchestrator.py:476`), the orchestrator queried environmental conditions for `(-3.94824, 116.51978)` at `2026-10-25T14:35:40Z`:
```python
env = resolve_environment_conditions(
    geometry.centroid_lat,
    geometry.centroid_lon,
    observed_time,
    allow_synthetic=(mode != "real_satellite"),
    mode=mode,
)
```
1. **CMEMS Grid Snapping**: Snapped coordinates to the nearest Copernicus CMEMS $1/12^\circ$ node:
   $$\text{node\_lat} = \frac{\text{round}(-3.94824 \times 12)}{12} = -3.9167^\circ, \quad \text{node\_lon} = \frac{\text{round}(116.51978 \times 12)}{12} = 116.5000^\circ$$
2. **Endpoint Routing**: Target timestamp was `2026-10-25` (current day). The router selected the operational forecast endpoint (`https://api.open-meteo.com/v1/forecast`).
3. **HTTP Query**:
   `GET https://api.open-meteo.com/v1/forecast?latitude=-3.9167&longitude=116.5&hourly=wind_speed_10m%2Cwind_direction_10m&wind_speed_unit=ms&start_date=2026-10-25&end_date=2026-10-25&timezone=UTC`
4. **Failure Condition**:
   Open-Meteo returned **`HTTP 429 Too Many Requests`**.
5. **Retry Sequence**:
   - Attempt 1: HTTP 429 $\to$ Jittered backoff sleep (~1.0s)
   - Attempt 2: HTTP 429 $\to$ Jittered backoff sleep (~2.0s)
   - Attempt 3: HTTP 429 $\to$ Max retries (2) exhausted. Total duration: **`5066.88 ms`**.
6. **Secondary Provider**:
   `CachedEnvironmentAdapter` was invoked $\to$ raised `"No cached environmental snapshot bundled for this scenario."`
7. **Operational Honesty Guard Enforcement**:
   In `app/environment/adapters.py:234-238`:
   ```python
   is_operational = (mode == "real_satellite") or (not allow_synthetic)
   if is_operational:
       raise EnvironmentDataUnavailable(
           f"Operational mode refused synthetic environmental fallback: {'; '.join(reasons)}"
       )
   ```
   The backend caught this exception, logged:
   `"Environmental data unavailable (synthetic substitution refused in real_satellite mode)"`,
   and left `env = None`.

### 2.3 Cascading Effect on Age Estimate & Drift Reconstruction
- **Spill Age** (`orchestrator.py:502`):
  ```python
  age = estimate_age(geometry.aspect_ratio, geometry.area_km2, env.wind_speed_ms) if env else None
  ```
  Because `env` was `None`, `age` was set to `None`.
- **Drift Reconstruction** (`orchestrator.py:540-565`):
  Because `env` was `None`, the orchestrator skipped numerical integration:
  ```python
  origin_lat, origin_lon = geometry.centroid_lat, geometry.centroid_lon
  origin_time = observed_time
  drift_dict = {
      "observed": {"t_offset_hours": 0.0, "timestamp": observed_time.isoformat(), ...},
      "backward_track": [],
      "forward_track": [],
      "probable_origin": None,
      "origin_confidence": "none",
      "environment_source": "UNAVAILABLE",
      "drift_status": "UNAVAILABLE",
      "notes": [
          "Drift reconstruction unavailable: real metocean data (wind/ocean current) could not be retrieved... "
          "Per scientific safety mandate, synthetic demonstration fields are not substituted in real_satellite mode."
      ],
  }
  ```
- **Data Sources Output**:
  - `data_sources.environment`: `"UNAVAILABLE - Operational mode refused synthetic environmental fallback: OpenMeteoOceanAdapter: Failed to retrieve wind data from https://api.open-meteo.com/v1/forecast: Open-Meteo request to https://api.open-meteo.com/v1/forecast failed after 3 attempt(s): Client error '429 Too Many Requests'..."`
  - `data_sources.drift_method`: `"NOT_RUN (environmental data unavailable)"`

---

## 3. Side-by-Side Comparison: `#99c16c10` vs. `#207806cd`

To rule out regressions, the affected record was compared against the successful post-restoration benchmark `#207806cd`:

| Dimension | Affected Record `#99c16c10` | Benchmark Record `#207806cd` | Technical Divergence |
| :--- | :--- | :--- | :--- |
| **Operational Mode** | `real_satellite` (user GeoTIFF upload) | `demo` (preset scenario) | **Crucial distinction**: `real_satellite` strictly forbids synthetic weather substitution. |
| **Observation Timestamp** | `2026-10-25T14:35:40Z` (real-time) | `2026-08-28T06:00:00Z` (historical reference) | Different temporal targeting. |
| **Endpoint Queried** | Open-Meteo **Forecast API** | Open-Meteo **Archive API** (`archive-api.open-meteo.com`) | Historical cutoff ($T - 2\text{ days}$) routed `#207806cd` to Archive. |
| **Geographic Target** | `-3.94824°, 116.51978°` (Kalimantan) | `19.03876°, 71.19603°` (Mumbai High) | Different CMEMS model grid nodes. |
| **Open-Meteo API Response** | **HTTP 429 Too Many Requests** (3 attempts failed) | **HTTP 200 OK** (retrieved on first attempt) | Render egress IP was throttled at 14:36 UTC, but was clear at 07:44 UTC. |
| **Resolved Environment** | `UNAVAILABLE` (`wind_speed_ms: 0.0`) | `REAL_OPEN_METEO` (`wind_speed_ms: 10.48`) | Environmental conditions missing in `#99c16c10`. |
| **Estimated Age** | `null` | `21.0 hours` (range 11.5–35.7h, low conf.) | Age calculation bypassed due to missing wind. |
| **Probable Origin** | `null` | `(19.10708°, 70.79687°)` at T-24.0h | Advection hindcast bypassed due to missing currents. |
| **Drift Status** | `UNAVAILABLE` | `COMPLETED` | Truthful status reporting. |
| **Attribution Search Origin** | Defaulted to detected slick centroid | Anchored to hindcast probable origin | Safe fallback behavior when origin is unknown. |

---

## 4. Evaluation of Technical Hypotheses

### Hypothesis A: Expected Refusal Due to Missing Real Data
**VERDICT: CONFIRMED.**  
The backend logs, progress log, and payload explicitly record that Open-Meteo returned HTTP 429. The pipeline followed its documented architecture: when real data cannot be fetched and no cached real data exists, `real_satellite` operational mode refuses synthetic fallbacks. Fabricating a drift origin without real wind and currents would violate the system's core scientific validity guarantee.

### Hypothesis B: Provider Routing Bug
**VERDICT: REFUTED.**  
The endpoint selection logic in `OpenMeteoOceanAdapter._determine_endpoints()` uses:
$$\text{historical\_cutoff} = \text{now}() - 2\text{ days}$$
For timestamp `2026-10-25`, the query date matches `now()`, correctly selecting `open_meteo_weather_url` (`https://api.open-meteo.com/v1/forecast`). An independent test query using the exact URL generated by the backend was executed during this audit and returned **HTTP 200 OK** with valid wind vectors (`wind_speed_10m: [4.11, 4.45, ...]`). The routing logic is sound; the failure was purely transient IP-level rate-limiting.

### Hypothesis C: Stale Frontend State
**VERDICT: REFUTED.**  
The frontend state mirrors the backend database record identically. An inspection of the Network response for `/api/spill/99c16c10` confirmed that the frontend received `age_estimate: null` and `probable_origin: null`. The frontend did not fail to render existing data; it faithfully rendered the absence of data.

### Hypothesis D: Regression from Merged Optimization (PR #3)
**VERDICT: REFUTED.**  
PR #3 introduced `cmems_grid_node()` to map coordinates to multiples of $1/12^\circ$. For `-3.94824°`, the mapped node is `-3.9167°`, which is mathematically exact ($-47/12 = -3.91666...$). This grid snapping reduces redundant HTTP calls during Euler drift simulation from 286 to $\le 54$. In `#99c16c10`, the pipeline failed on the very first HTTP query before Euler integration even began, due to external HTTP 429. The optimization played no role in causing the rate limit.

---

## 5. Frontend Presentation Audit

Inspection of `frontend/src/components/` confirmed that UI components handled the null values safely without crashing:

1. **`KpiCards.jsx`**:
   - `ageLabel`: Evaluated `age ? ~${age.hours} : 'N/A'` $\to$ **`N/A`** (Correct).
   - `probable_origin`: Evaluated `origin ? ... : 'N/A'` $\to$ **`N/A`** (Correct).
   - Subtitle: Evaluated `geo ? 'Drift unavailable' : 'No slick detected'` $\to$ **`Drift unavailable`** (Correct).
   - *Minor Cosmetic Polish Identified*: Subtitle for age currently evaluates `Confidence: ${age?.confidence}`, rendering `Confidence: undefined` when `age` is null.
2. **`DataSourceIndicator.jsx`**:
   - Displays `Environment: UNAVAILABLE - Operational mode refused...` and `Drift: NOT_RUN (environmental data unavailable)`.
   - Tooltip contains the full error string including the HTTP 429 trace.
3. **`TimelineTab.jsx`**:
   - Reliably detects `drift.drift_status === 'UNAVAILABLE'`.
   - Renders: *"Drift reconstruction unavailable: real metocean data (wind and ocean currents) could not be retrieved. To maintain scientific validity, synthetic trajectories are refused for satellite observations."*
4. **`InteractiveMap.jsx`**:
   - Safely suppresses hindcast and forecast polylines when `origin` is null.
   - Accurately renders the observed slick polygon and centroid marker at `(-3.94824, 116.51978)`.
   - Centers the search radius on the observed slick position.

---

## 6. Recommended Corrective Actions (Proposals Only — No Code Changed)

To increase operational resilience against public API rate limits while maintaining scientific integrity, the following targeted enhancements are recommended for a subsequent phase:

### Action 1: Configurable Open-Meteo Commercial API Key / Proxy
- **Rationale**: Open-Meteo's free tier imposes per-minute and per-day IP limits. Shared cloud egress IPs (Render/AWS) frequently face shared IP rate limiting.
- **Proposed Change**: Support an optional `OPEN_METEO_API_KEY` environment variable in `backend/app/config.py` and pass `&apikey=` in `OpenMeteoOceanAdapter` queries.

### Action 2: Increased 429 Retry Budget with Exponential Backoff
- **Rationale**: Currently, `env_live_max_retries` defaults to 2, exhausting all retries in ~5 seconds. Short bursts of rate limiting often clear within 10–15 seconds.
- **Proposed Change**: For HTTP 429 responses specifically, allow up to 3–4 retries with progressive backoff (e.g., 2s, 4s, 8s) if within the total request budget.

### Action 3: Frontend Subtitle Null-Safety Polish
- **Rationale**: Prevent `Confidence: undefined` from displaying in the KPI card when `age_estimate` is null.
- **Proposed Change** in `frontend/src/components/KpiCards.jsx`:
  ```javascript
  sub={age ? (age.confidence === 'unavailable' ? 'Unavailable / low confidence' : `Confidence: ${age.confidence}`) : 'Awaiting metocean data'}
  ```

### Action 4: Clearer Satellite Tab Copy for Detected Slicks
- **Rationale**: In `frontend/src/components/tabs/SatelliteTab.jsx`, line 280 states: `'Age estimate unavailable: no slick was detected in this scene.'` when `age` is null, which is misleading when a slick was detected but weather data failed.
- **Proposed Change**: Distinguish `!geo` (no slick detected) from `!env` (metocean data unavailable).

---

## 7. Conclusion

Investigation `#99c16c10` is a working demonstration of **fail-safe scientific data governance**. When external live metocean feeds failed with HTTP 429, the system refused to invent fake weather or hallucinate a false drift origin for a real satellite observation, faithfully communicating data availability to the user across the UI and API. No code or database changes were made during this diagnostic phase.
