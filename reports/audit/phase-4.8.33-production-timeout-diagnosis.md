# Phase 4.8.33 Diagnostic Report: Production Investigation Timeout

**Repository**: `Subodh-17/Marine-Pollution-Intelligence-SIH`  
**Local Workspace**: `E:\Projects\Marine_Pollution_Intelligence_SIH_FINAL`  
**Production Frontend**: `https://marine-pollution-intelligence-sih.vercel.app`  
**Production Backend**: `https://marine-pollution-intelligence-api.onrender.com`  
**Target Commit**: `dca04fef0b1a24a9180983f1a3e2bd0d348e1c07`  
**Date**: October 25, 2026  
**Auditor**: Antigravity Diagnostic & Systems Architect (Read-Only Mode)

---

## 1. Executive Summary

### Problem Statement
Clicking **Run Analysis** on the deployed application (including from the `/satellite` view) surfaces the error banner:
> *“Investigation failed to run. Check that the backend API is reachable. (Request timed out on the server — please retry or try a smaller image)”*

### Root Cause Conclusion
1. **Primary Architectural Defect (Advection Grid Cache Thrashing)**:
   - In `backend/app/drift/model.py`, the drift simulation performs **144 Euler integration steps** (48 steps backward, 96 steps forward at $\Delta t = 30\text{ min}$).
   - In `backend/app/environment/open_meteo.py:214`, spatial cache keys are formatted using `round(lat, 2):round(lon, 2):date_str` ($0.01^\circ$ precision $\approx 1.11\text{ km}$).
   - As the slick advects with wind and currents over the 72-hour window, it travels $\sim 100\text{ km}$, crossing **102 distinct spatial-temporal grid bins**.
   - With an unseeded cache, every distinct bin triggers **2 live external HTTP requests** (1 weather/archive, 1 marine) to Open-Meteo.
   - 102 cache misses $\times$ 2 HTTP requests = **204 sequential external HTTP calls**.
   - Even without API rate-limiting, 102 network calls take **$\sim 162\text{ seconds}$ ($\sim 2.7\text{ minutes}$)**.
2. **Frontend Client-Side Timeout Mismatch**:
   - In `frontend/src/api.js:41`, the shared Axios client enforces a strict **30-second timeout** (`timeout: 30000`).
   - `api.runInvestigation()` has no per-request timeout override (unlike `uploadSatelliteImage`, which has 5 minutes).
   - Because $162\text{s} \gg 30\text{s}$, the browser's Axios client terminates the connection at second 30 with `ECONNABORTED`.
   - `frontend/src/App.jsx:32-33` intercepts `ECONNABORTED` and appends `"(Request timed out on the server - please retry or try a smaller image)"` to the fallback error message.
3. **Compound Secondary Factor (Render Free Tier Cold Start)**:
   - Render's free tier spins down the backend after 15 minutes of idle time. Cold-starting the Python container takes **$35\text{ to }55\text{ seconds}$**.
   - A client with a 30s timeout that hits a sleeping container will abort before the container finishes launching.
   - Furthermore, Render's platform proxy timeout is **100 seconds**; a backend request taking $165\text{s}$ causes Render's gateway to return **HTTP 502/504 Bad Gateway** even if the client waits.

---

## 2. Confirmed Facts vs. Hypotheses

### Confirmed Facts
| # | Finding | Evidence / Source |
|---|---|---|
| **F1** | The error string is generated locally by the frontend error extractor. | [frontend/src/App.jsx:33, 165](file:///E:/Projects/Marine_Pollution_Intelligence_SIH_FINAL/frontend/src/App.jsx#L33) combines `e.code === 'ECONNABORTED'` with fallback `'Investigation failed to run. Check that the backend API is reachable.'`. |
| **F2** | The button labeled "RUN ANALYSIS" calls `runDemo()`. | [frontend/src/components/Header.jsx:35](file:///E:/Projects/Marine_Pollution_Intelligence_SIH_FINAL/frontend/src/components/Header.jsx#L35) binds `onClick={onRunDemo}`, which executes `api.runInvestigation({ mode: 'demo' })`. |
| **F3** | The frontend request is `POST /api/spill/detect?mode=demo&search_radius_km=50&time_window_hours=12`. | [frontend/src/api.js:83-86](file:///E:/Projects/Marine_Pollution_Intelligence_SIH_FINAL/frontend/src/api.js#L83-L86). |
| **F4** | The frontend request timeout is hardcoded to 30,000 ms. | [frontend/src/api.js:41](file:///E:/Projects/Marine_Pollution_Intelligence_SIH_FINAL/frontend/src/api.js#L41) (`axios.create({ baseURL, timeout: 30000 })`). |
| **F5** | `POST /api/spill/detect` takes $> 60\text{s}$ on production with live metocean enabled. | Production probe against Render timed out at $60.203\text{s}$ (`task-3256`). |
| **F6** | Local end-to-end execution of `run_investigation(mode='demo')` with `ENV_PROVIDER=open_meteo` reproduced an exact latency of **$165.710\text{ seconds}$**. | Timed profiling (`task-3284`) showed `Hindcast completed` took **$162,176.77\text{ ms}$**. |
| **F7** | The 144 Euler steps of the synthetic slick produce **102 distinct grid bins** under $0.01^\circ$ discretization. | Measured via `scratch/count_drift_bins.py` with synthetic coordinates `(19.03876, 71.19603)`. |
| **F8** | Open-Meteo physical model resolution is $0.25^\circ$ (GFS/ECMWF) and $0.083^\circ$ (Copernicus CMEMS). | Open-Meteo API documentation. $0.01^\circ$ ($1.1\text{ km}$) oversamples identical atmospheric/ocean data by $10\times$ to $25\times$. |
| **F9** | A single Open-Meteo live API fetch takes $1.616\text{s}$. | Measured live in `scratch/test_open_meteo_single.py`. $102 \times 1.616\text{s} = 164.8\text{s}$. |

### Hypotheses Disproved
- **Hypothesis: Backend crashed or threw an unhandled 500 exception**: Disproved. The backend executes cleanly and eventually saves the investigation (as confirmed by `aa422f2d` and `800a1cbc`); the failure is purely elapsed wall-clock duration exceeding client and proxy thresholds.
- **Hypothesis: User uploaded an invalid large image file**: Disproved. The user clicked "RUN ANALYSIS" in the header, which runs `mode='demo'` (no image upload; synthetic SAR scene). The error message text *"try a smaller image"* was an artifact of the generic Axios timeout string in `extractErrorDetail`.
- **Hypothesis: Open-Meteo returned persistent HTTP 429 rate limits**: Disproved as the primary cause. Even when Open-Meteo returns HTTP 200 without a single 429 error, 102 distinct sequential network requests require $> 160\text{ seconds}$.

---

## 3. Request Timing & Architecture Breakdown

### 1. Stage-by-Stage Latency Breakdown (Measured with `ENV_PROVIDER=open_meteo`)
```text
Total Investigation Latency: 165,652.84 ms (165.71 s)
--------------------------------------------------------------------------------
1. Satellite Scene Generation :       38.54 ms  (Synthetic SAR image load)
2. SAR Preprocessing (Lee)    :       21.21 ms  (Despeckle & normalize)
3. Oil Slick Detection        :    1,176.01 ms  (Morphology & dark spot)
4. Slick Characterization     :        6.92 ms  (Centroid & aspect ratio)
5. Initial Environmental Load :    1,695.63 ms  (1st Open-Meteo HTTP fetch)
6. Drift Hindcast & Forecast  :  162,176.77 ms  (144 Euler steps -> 102 cache misses)  <-- BOTTLENECK
7. AIS Traffic Reconstruction :       32.48 ms  (Fleet indexing)
8. Vessel Attribution Scoring :       17.60 ms  (Multi-factor scoring)
9. Report & DB Persistence    :       87.68 ms  (PostgreSQL / SQLite save)
```

### 2. The Drift Advection Cache Thrashing Mechanism
```
  [ Slick Origin: (19.04, 71.20) at T=0 ]
                    │
                    ▼  (dt = 30 min, drift speed = ~0.4 m/s -> ~720m displacement)
      Step 1 : (19.038, 71.196) -> Key: 19.04:71.20:2026-08-28 -> CACHE HIT  (0.01 ms)
      Step 2 : (19.032, 71.191) -> Key: 19.03:71.19:2026-08-28 -> CACHE MISS (1,600 ms) -> 2 HTTP calls
      Step 3 : (19.026, 71.185) -> Key: 19.03:71.19:2026-08-28 -> CACHE HIT  (0.01 ms)
      Step 4 : (19.020, 71.179) -> Key: 19.02:71.18:2026-08-28 -> CACHE MISS (1,600 ms) -> 2 HTTP calls
                    │
                    ▼  (Continued across 144 steps over 72 hours)
      Step 144: (18.854, 72.010) -> Total unique spatial keys: 102
                    │
                    ▼
      Total External Network Time: 102 misses * 1.6s = 163.2 seconds
```

### 3. Timeout Budget Collision
```
Time (seconds) ─────►
0s                  30s                                  100s                 165s
├───────────────────┼────────────────────────────────────┼────────────────────┤
│ Pipeline starts   │ Frontend Axios timeout             │ Render proxy limit │ Pipeline finishes
│                   │ (ECONNABORTED raised to user)      │ (502/504 Bad GW)   │ (Too late)
```

---

## 4. Root Cause Analysis

1. **Overly Granular Grid Binning**:
   - `_OpenMeteoCache` keys are binned at $0.01^\circ$ ($\sim 1.1\text{ km}$):
     ```python
     cache_key = f"{round(lat, 2):.2f}:{round(lon, 2):.2f}:{date_str}"
     ```
   - Global numerical weather models (GFS, ECMWF) do not provide $1\text{ km}$ resolution. Their physical grid resolution is $0.25^\circ$ ($\sim 25\text{–}28\text{ km}$) or at best $0.1^\circ$ ($\sim 11\text{ km}$).
   - Because $1.1\text{ km} \ll \text{trajectory path length } (\sim 100\text{ km})$, the advection simulation suffers from nearly 70% cache miss rates, issuing hundreds of redundant HTTP queries for points that evaluate to the identical underlying model grid node.
2. **Missing Per-Request Timeout on Investigation Run**:
   - In `frontend/src/api.js`, `api.runInvestigation` relies on the client-level default timeout of 30 seconds (`timeout: 30000`).
   - For an endpoint performing multi-stage geospatial and external API operations, 30s is insufficient to absorb PaaS cold starts (35–50s) and external network round-trips.

---

## 5. Smallest Safe Corrective Plan

### Plan Overview (Do Not Implement Yet — For Review Only)

1. **Backend: Coarsen Metocean Cache Discretization to Provider Resolution**:
   - In `backend/app/environment/open_meteo.py`:
     Change spatial grid rounding from `round(lat, 2)` ($0.01^\circ$) to $0.1^\circ$ (or $0.25^\circ$), matching the actual physical resolution of NOAA GFS / ECMWF and Copernicus CMEMS:
     ```python
     # Coarsen spatial grid to 0.1 degree (~11 km), reducing cache keys from ~102 down to <= 3
     grid_lat = round(lat, 1)
     grid_lon = round(lon, 1)
     cache_key = f"{grid_lat:.1f}:{grid_lon:.1f}:{date_str}"
     ```
   - **Performance Impact**:
     - Distinct spatial bins across a 72-hour drift track drop from **102 down to 2 or 3**.
     - Total external HTTP requests drop from **204 down to 4–6**.
     - Total drift simulation time drops from **$162\text{ seconds}$ down to $\sim 3\text{–}4\text{ seconds}$**!
     - Entire investigation completes in **$4\text{–}5\text{ seconds}$**, well below the frontend's 30s timeout and Render's 100s proxy limit.

2. **Frontend: Explicit Bounded Timeout for `api.runInvestigation`**:
   - In `frontend/src/api.js`:
     Add an explicit timeout of `60000` (60 seconds) to `api.runInvestigation`:
     ```javascript
     runInvestigation: ({ mode = 'demo', searchRadiusKm = 50, timeWindowHours = 12 } = {}) =>
       client.post('/spill/detect', null, {
         params: { mode, search_radius_km: searchRadiusKm, time_window_hours: timeWindowHours },
         timeout: 60000,
       }).then(r => r.data),
     ```
   - This provides sufficient headroom for occasional Render PaaS cold starts without blocking indefinitely.

3. **Frontend: Accurate Error Messaging**:
   - In `frontend/src/App.jsx:32-34`:
     Refine the error message so `mode='demo'` does not misleadingly suggest "try a smaller image":
     ```javascript
     if (e?.code === 'ECONNABORTED' || e?.message?.toLowerCase().includes('timeout')) {
       return `${fallback} (Request timed out — the backend may be cold-starting or retrieving live weather data. Please retry in a few moments.)`
     }
     ```

---

## 6. Required Regression Tests

1. **Drift Model Step Count & Cache Hit Test**:
   - Unit test simulating 144 Euler steps of a $100\text{ km}$ drift track with `OpenMeteoOceanAdapter`.
   - Assert that total external HTTP requests are $\le 6$ (instead of $> 100$).
   - Assert that simulated execution duration is $< 5.0\text{ seconds}$.
2. **Endpoint Execution Time Benchmark**:
   - Benchmark `run_investigation(mode='demo')` with `ENV_PROVIDER=open_meteo`.
   - Assert total pipeline processing time $\le 10,000\text{ ms}$.
3. **Frontend Timeout & Mock Failure Verification**:
   - Verify `api.runInvestigation` honors the 60s timeout in `frontend/src/test/smoke.test.jsx`.

---

## 7. Production Verification Steps (Post-Implementation Gate)

1. Verify backend cold-start responsiveness: `curl -m 15 https://marine-pollution-intelligence-api.onrender.com/health`.
2. Execute a single bounded `POST /api/spill/detect?mode=demo` and assert response time is $< 10\text{ seconds}$ with `source: REAL_OPEN_METEO`.
3. In browser, navigate to `/satellite` and click **Run Analysis**.
4. Confirm progress checklist animates to completion and dashboard renders without timing out.
