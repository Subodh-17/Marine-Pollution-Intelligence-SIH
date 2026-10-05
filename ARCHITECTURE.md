# ARCHITECTURE

## Pipeline (Section 4 of the brief)

```
Synthetic Sentinel-1 SAR scene (or, via extension point, a real GeoTIFF)
        │  app/satellite/synthetic.py
        ▼
Preprocessing (Lee-filter despeckle, normalize, sea/land mask)
        │  app/satellite/preprocessing.py
        ▼
Dark-spot detection + morphological cleanup + false-positive filtering
        │  app/satellite/detection.py
        ▼
Segmentation (connected-component mask)         → same module
        ▼
Geometric characterisation (area, length, width, orientation, centroid, polygon)
        │  app/satellite/characterization.py
        ▼
Single-scene age heuristic (explicitly low-confidence)
        │  app/satellite/age.py
        ▼
Environment conditions (wind/current/wave) — Real→Cached→Synthetic fallback
        │  app/environment/adapters.py
        ▼
Backward hindcast → probable origin + probable origin time
Forward forecast → predicted future positions
        │  app/drift/model.py
        ▼
AIS ingestion (MarineCadastre / generic CSV / synthetic demo fleet)
        │  app/ais/adapters.py, app/demo/scenario.py
        ▼
Cleaning → spatial filter → temporal filter → trajectory reconstruction
        │  app/ais/pipeline.py
        ▼
Behavioural anomaly detection (speed/course changes, loitering, AIS gaps)
        │  app/ais/behavior.py
        ▼
Explainable weighted attribution scoring → ranked candidates
        │  app/scoring/attribution.py
        ▼
Investigation object (app/investigation/orchestrator.py ties all of the above together)
        │  saved to SQLite on completion — app/persistence/db.py (Section 6)
        ▼
REST API (app/main.py)  ──▶  React console (frontend/src)
        │  optionally gated by app/auth.py (off by default); optionally run
        │  asynchronously via app/persistence/jobs.py's background job runner
        ▼
Markdown/PDF investigation report (app/reports/generator.py)
```

`investigation/orchestrator.py` is intentionally the *only* module that imports from every
other stage. Every stage module (`satellite/`, `environment/`, `drift/`, `ais/`, `scoring/`)
has no dependency on the orchestrator or on FastAPI, so each is independently unit-testable
(see `backend/tests/`) and independently reusable (e.g. in a batch/offline job, a notebook, or
a different API).

**Detector selection (real satellite uploads only).** The "Dark-spot detection" stage above is the
default (`detector=classical`). An optional `detector=ml` path exists alongside it
(`app/ml/detector.py`, a PyTorch CNN classifier + U-Net segmenter) for dual-pol uploads. The
orchestrator decides, per request, which detector's output actually becomes the pipeline's
detection result — it automatically substitutes the classical detector's output (tagged
`CLASSICAL_DARK_SPOT_ML_FALLBACK`) whenever the ML path is requested but unavailable, produces an
empty mask, or falls below a configured confidence threshold. See `MODEL_CARD.md`'s "ML →
Classical fallback" section and `app/investigation/orchestrator.py` for the exact decision logic.

**Evaluation (offline, not on the request path).** `app/evaluation/` is a standalone utility that
scores the classical detector against the bundled sample scenes' real ground-truth masks
(`python -m app.evaluation.evaluate_samples` from `backend/`). It has no dependency on, and is not
called by, the orchestrator or API — it exists purely for `MODEL_CARD.md`'s "Evaluation" section
and for anyone extending this project to plug in the real Zenodo dataset later.

## Extension points (where a production/real-data build would plug in)

| Extension point | Current implementation | To go live |
|---|---|---|
| `Sentinel1Adapter` | Not present — `synthetic.py` generates a scene | Add an adapter reading a Sentinel-1 GRD GeoTIFF into a `(ndarray, SceneGeoTransform)` tuple — identical shape to what `synthetic.py` returns, so `preprocessing.py`/`detection.py`/`characterization.py` need zero changes. Note: `app/satellite/geotiff_metadata.py` already reads a GeoTIFF's own embedded geographic CRS/geotransform when present (see the row below) — a real Sentinel-1 product's own geo tags would already be picked up, not just its pixels. |
| Embedded GeoTIFF georeferencing | **Implemented** (`app/satellite/geotiff_metadata.py`) for a geographic (EPSG:4326-style) CRS via `ModelPixelScaleTag`/`ModelTiepointTag`/`GeoKeyDirectoryTag`, read directly with `tifffile` (no GDAL/rasterio dependency) | A real embedded PROJECTED CRS (e.g. a UTM zone) is detected and its EPSG code reported, but not reprojected — add a `pyproj` dependency and a projected→geographic transform to close that gap. Multi-tiepoint (rubber-sheeted) files are also out of scope of the current reader. |
| Detection model | Classical hybrid dark-spot + morphology + texture filter (`detection.py`) | Swap `detect_oil_slick()` for a trained U-Net/CNN inference call fine-tuned on the Zenodo Sentinel-1 SAR Oil Spill Dataset. Must return the same `DetectionResult` (mask + confidence) — nothing downstream changes. |
| `RealWeatherOceanAdapter` | **Partially implemented**: attempts a real HTTP call (timeout/retry/response validation) when `ENV_LIVE_PROVIDER_URL` is configured (`app/environment/adapters.py`) — but unvalidated against any real provider, since this sandbox has no network egress to NOAA/Copernicus/any weather API | Point `ENV_LIVE_PROVIDER_URL` at a real provider and confirm its JSON response actually matches `{"wind_speed_ms", "wind_dir_deg", "current_speed_ms", "current_dir_deg", "wave_height_m"}` (adjust `_parse_response()` if not); do a real end-to-end test against it. The Real→Cached→Synthetic fallback chain in `resolve_environment_conditions()` already handles the failover. |
| `CachedEnvironmentAdapter` | Raises (no cache bundled) | Bundle a small GRIB/NetCDF snapshot and read it here for offline-but-real conditions. |
| `MarineCadastreAISAdapter` | Fully implemented against the documented CSV schema, reads any file dropped in `backend/data/sample_ais/` | Point it at a real MarineCadastre bulk-download extract — no code change needed. |
| `LiveAISProvider` | **Partially implemented**: attempts a real HTTP call (timeout/retry, timestamp normalization, de-duplication, invalid-coordinate/sentinel-value filtering) when `AIS_LIVE_PROVIDER_URL` is configured (`app/ais/live_provider.py`), reachable via `mode="live_ais"` | Same caveat as `RealWeatherOceanAdapter` above: unvalidated against any real AIS provider from this sandbox. Point `AIS_LIVE_PROVIDER_URL` at a real one and confirm its response shape matches `_parse_and_clean()`'s expectations. |
| Land/sea mask | Heuristic brightness threshold (`simple_sea_mask`) | Reproject an authoritative coastline layer (GSHHG / Natural Earth) onto the scene grid. |
| Spill-age model | Single-scene aspect-ratio/wind heuristic, explicitly low-confidence | Multi-temporal (repeat-pass) comparison, or a calibrated weathering model fit against ground truth. |
| Drift model | Simplified advection (current + 3% wind, single deflection angle) | A full particle-tracking model (e.g. OpenDrift/GNOME) with turbulent diffusion and Stokes drift. |
| Persistence | **Implemented**: SQLite (`app/persistence/db.py`), on by default | For a multi-instance production deployment, add a PostgreSQL client dependency and point `DATABASE_PATH`-equivalent config at it — no PostgreSQL code exists yet, only SQLite. |
| Authentication | **Implemented but off by default**: `app/auth.py`, PBKDF2/HMAC, `AUTH_ENABLED=true` to turn on | Add a frontend login screen (none exists yet — this is API-only); consider OAuth/OIDC for a real multi-tenant deployment instead of the current username/password scheme. |

## Frontend architecture

State lives in `App.jsx` (no external state library — the investigation object from a single
API call is the source of truth for every tab). Tabs are plain conditional renders keyed by an
`activeTab` string, not a router, since the whole app is a single investigation console with
no deep-linkable routes in this prototype.

```
App.jsx
├── Header            — brand, investigation id, Run Demo / Upload AIS / View report actions
├── Sidebar            — tab navigation (disabled until an investigation exists)
├── ProgressChecklist  — staged reveal of the pipeline's own progress_log during a run
├── tabs/DashboardTab   — KPI cards, data-source indicator, InteractiveMap
├── tabs/SatelliteTab   — original/processed/detection-overlay images, geometry, age
├── tabs/AisTab         — live radius/time-window filter controls, funnel, map, candidate list
├── tabs/RankingTab     — full candidate table
├── tabs/TimelineTab    — drift-stage timeline + vessel closest-approach axis
├── tabs/ReportTab      — rendered Markdown report + PDF download
├── VesselDetailDrawer  — full explainable evidence breakdown for one candidate
└── UploadAisModal      — custom AIS CSV upload flow
```

`InteractiveMap.jsx` is shared between the Dashboard and AIS tabs (different default layer
visibility per tab) and renders every layer named in Section 23 of the brief: satellite scene,
slick polygon/centroid, hindcast track + origin + uncertainty circles, forecast track +
uncertainty corridor, AIS candidate tracks, and the spatial search radius — all toggleable.

## Known non-goals of this prototype

- **Authentication is implemented but off by default** (`app/auth.py`, `AUTH_ENABLED=false`) —
  investigations are tagged with an `owner` (default `"anonymous"`) regardless, but per-owner
  access is only enforced when `AUTH_ENABLED=true`. No frontend login screen exists — turning
  auth on today is API-only (curl/API-docs), documented in `DEMO_GUIDE.md`. This remains a
  lightweight scheme suitable for a local prototype, not OAuth/OIDC or a production IdP.
- **Persistence is implemented and on by default** (`app/persistence/db.py`, SQLite) —
  investigations survive a backend restart. `DATABASE_ENABLED=false` reverts to the original
  in-process-`_STORE`-only behavior if that's specifically wanted for a quick throwaway session.
  No PostgreSQL code path exists — SQLite only, per the request this pass was built against.
- Basemap tiles (OpenStreetMap, with an automatic Wikimedia fallback) require internet; the
  investigation pipeline itself does not.
