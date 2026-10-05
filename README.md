# Marine Pollution Intelligence & Vessel Attribution Platform

**SIH prototype** — "Leveraging satellite imagery to determine Oil spills at sea along with
AIS data correlations to identify vessel responsible for the spill."

An end-to-end investigative pipeline: Sentinel-1 SAR oil-slick detection → geometric
characterisation → oceanographic/meteorological drift hindcasting → probable spill origin →
AIS traffic reconstruction → explainable, spatio-temporal vessel attribution scoring →
investigation report. A dark, ECDIS-inspired console UI ties it all together with an
interactive map, evidence breakdowns, and a one-click offline demo.

## Problem this solves

Oil spills at sea are frequently detected days after the fact, from imagery alone, with no
link back to the responsible vessel. This prototype closes that loop: it detects the slick,
reconstructs where and when it most likely originated, and cross-references historic AIS
traffic in that space-time window to produce a ranked, evidence-backed list of candidate
vessels — with every score fully explainable.

## Architecture

```
backend/app/
├── satellite/        synthetic SAR generator, real GeoTIFF/PNG upload adapter, real embedded
│                      GeoTIFF geo-referencing reader (geotiff_metadata.py), Lee-filter
│                      preprocessing, dark-spot detection, geometric characterisation,
│                      single-scene age heuristic, PNG rendering
├── ml/                optional prototype PyTorch classifier/segmenter (opt-in, see MODEL_CARD.md §6)
├── environment/       wind/current adapters with Real → Cached → Synthetic fallback chain
│                      (Real tier makes a genuine HTTP call when ENV_LIVE_PROVIDER_URL is set)
├── drift/             physics-based hindcast/forecast (current + wind leeway advection)
├── ais/                schema, source adapters (MarineCadastre / generic CSV / synthetic /
│                      live_provider.py for a configured live feed), cleaning + spatial/temporal
│                      filtering, trajectory reconstruction, behavioural anomaly detection
├── scoring/            explainable weighted vessel-attribution engine
├── demo/               deterministic 5-vessel synthetic AIS scenario (works fully offline)
├── persistence/        SQLite-backed investigation storage (db.py) + in-process background
│                      job runner (jobs.py) - see DATA_PIPELINE.md §6
├── auth.py             lightweight, disabled-by-default authentication (see DEMO_GUIDE.md)
├── investigation/      orchestrator wiring every stage into one pipeline run
├── reports/             Markdown + PDF investigation report generator
└── api/ (main.py)      FastAPI routes

frontend/src/
├── components/         Header, Sidebar, InteractiveMap, KpiCards, DataSourceIndicator,
│                      ProgressChecklist, VesselDetailDrawer, UploadAisModal, UploadSatelliteModal
├── components/tabs/    Dashboard, Satellite, AIS, Ranking, Timeline, Report, ML Training
│                      (MlTrainingTab.jsx - model status/provenance/training-history/metrics,
│                      reachable without an active investigation; see app/ml/README_ML_STATUS.md)
└── api.js               thin axios client for the backend
```

Every pipeline stage module is independent and unit-tested; `investigation/orchestrator.py`
is the only module that wires them together, so any stage (e.g. the classical detector) can
be swapped for a trained model later without touching the rest of the system.

## Datasets

| Spec reference | Used as | Why |
|---|---|---|
| Zenodo Sentinel-1 SAR Oil Spill Dataset | Reference dataset for the detection pipeline's design, and the real training/evaluation dataset for the opt-in ML detector | This sandboxed development build has no network access to zenodo.org, so the ~96.5 GB dataset could not be downloaded here. A deterministic **synthetic** SAR scene generator stands in for the default demo (see `MODEL_CARD.md`). Separately, a **complete, genuine, offline pipeline** now exists for anyone who downloads the dataset themselves: `app/ml/dataset.py` (discovery/validation/manifests) → `app/ml/train.py` (real PyTorch training) → `app/ml/evaluate_real.py` (held-out test-set metrics) → `app/ml/promote.py` (explicit, safety-gated promotion to production inference). See `docs/DATASET_SETUP.md` for the full walkthrough and `MODEL_CARD.md` §6b for the details. |
| MarineCadastre AIS (marinecadastre.gov/accessais) | Canonical field schema for the AIS normalization layer | No network access to marinecadastre.gov in this build. `MarineCadastreAISAdapter` is implemented against the **documented, published** MarineCadastre CSV schema (MMSI, BaseDateTime, LAT, LON, SOG, COG, Heading, VesselName, VesselType, IMO, ...) so a real extract drops in with zero code changes. A deterministic 5-vessel **synthetic** fleet is used for the offline demo. |
| Wind / current / wave conditions | Drift model inputs | No live NOAA/Copernicus access in this build. `resolve_environment_conditions()` implements the full **Real → Cached → Synthetic** fallback chain; only the synthetic tier is reachable here, and it is clearly tagged `SYNTHETIC_DEMO` everywhere it surfaces. |

Every investigation response includes a `data_sources` block (Section 22 of the brief) so the
UI's Data Source Indicator always shows honestly what powered that run.

### Training the ML detector on the real dataset (optional, offline)

The application works fully, and this section is entirely optional. **The project already
ships genuine Zenodo-trained checkpoints** (see "Real ML status" below) — this section is for
re-training them yourself, or for training from scratch in a fresh checkout that has only the
original synthetic prototype checkpoints:

```bash
cd backend
pip install -r requirements.txt -r requirements-ml.txt

# 1. Download + extract the dataset yourself (~96.5 GB, ~150-200 GB free disk
#    recommended) - see docs/DATASET_SETUP.md for the full walkthrough.
export DATASET_ROOT=./data/zenodo_sar   # Windows (PowerShell): $env:DATASET_ROOT = ".\data\zenodo_sar"

# 2. Discover, validate, and split into train/val/test manifests
python -m app.ml.dataset --dataset-root $DATASET_ROOT inspect
python -m app.ml.dataset --dataset-root $DATASET_ROOT build-manifests

# 3. Train (CPU by default; --device cuda if you have a GPU)
python -m app.ml.train --dataset-root $DATASET_ROOT --epochs 20

# 4. Evaluate against the held-out test split (never train/val)
python -m app.ml.evaluate_real --dataset-root $DATASET_ROOT \
    --checkpoint ./data/ml_checkpoints/spill_unet_real_best.pt

# 5. Explicitly promote the evaluated checkpoint for inference
python -m app.ml.promote --checkpoint-dir ./data/ml_checkpoints \
    --i-understand-this-changes-inference-behavior
```

None of this runs automatically, none of it is required for the application to work, and the
dataset itself is never bundled into this repository or committed to Git (see `.gitignore`,
`docs/DATASET_SETUP.md`). See `MODEL_CARD.md` §6b for the full technical detail, including exactly
what "promotion" does and doesn't touch.

## Real ML status

| | |
|---|---|
| **Training** | Real Sentinel-1 SAR Zenodo dataset (Parts I–III, DOIs in `evaluation_report.json`) |
| **Classifier** | Real Zenodo-trained checkpoint + ONNX export |
| **Segmentation** | Real Zenodo-trained SpillUNet + ONNX export |
| **Production inference** | ONNX Runtime CPU — no PyTorch required |
| **Local development** | PyTorch CPU/CUDA where available |
| **Training dataset** | Not required for production inference; never bundled |
| **Evaluation** | `backend/data/ml_checkpoints/evaluation_report.json` / `.md` |

Held-out test split, 450 scenes, threshold 0.5, preprocessing
`sar_percentile_1_99_minmax_v1`:

| Precision | Recall | F1 | IoU | Dice | Pixel acc. |
|---|---|---|---|---|---|
| 0.7485 | 0.8962 | 0.6838 | 0.6486 | 0.6838 | 0.9765 |

**Limitations.** These are research-model results and should not be presented
as operational certainty. Softmax scores are model confidence outputs, not
calibrated probabilities. Performance depends on acquisition conditions,
preprocessing compatibility, and scene characteristics. Genuine ML inference
requires real dual-pol VV + VH input — single-band imagery uses the classical
detector and a VH channel is never fabricated. See `MODEL_CARD.md` and
`backend/app/ml/README_ML_STATUS.md` for the full list.


## Theme (Light / Dark / System)

The console ships with a full Light/Dark/System theme, controlled by the toggle in the header
(sun / moon / monitor icons):

- **Light** and **Dark** pin the theme regardless of the OS setting.
- **System** (the default for a first-time visitor) follows the OS's `prefers-color-scheme` and
  updates live if the OS theme changes while the app is open — no refresh needed.
- The choice is saved to `localStorage` (key `mpi-theme-preference`) and restored on every
  visit; it also survives switching tabs/routes within the app (the theme lives above the
  router, not per-screen).
- A small inline script in `frontend/index.html` applies the saved/system theme to `<html>`
  before the stylesheet paints, so there's no flash of the wrong theme on load.
- All colors are driven by CSS custom properties in `frontend/src/styles/tokens.css`
  (`[data-theme="dark"]` / `[data-theme="light"]`); no component hard-codes a color, so every
  screen — maps, charts, modals, drawers, forms, tooltips, status badges — renders correctly in
  both themes automatically. The light theme's accent colors are deepened/saturated versions of
  the dark theme's (not literal inversions), tuned to stay readable as text/icon color on a
  light surface.

## Quick start

**macOS / Linux:**

```bash
# Backend
cd backend
pip install -r requirements.txt --break-system-packages   # or use a venv
uvicorn app.main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend
npm install
npm run dev       # http://localhost:5173, proxies /api to :8000
```

**Windows (PowerShell or Command Prompt):**

```bat
:: Backend
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

:: Frontend (separate terminal)
cd frontend
npm install
npm run dev
```

Open http://localhost:5173 and click **RUN DEMO INVESTIGATION**. No internet connection is
required for the investigation pipeline itself (the map's base tiles do require internet).

Run the backend test suite:

```bash
cd backend
pytest tests/ -v
```

```bat
:: Windows
cd backend
.venv\Scripts\activate
pytest tests\ -v
```

## Configuration

Every runtime knob (upload size limit, ML confidence threshold, AIS search radius/time window
defaults, drift hindcast/forecast duration, CORS origins, log level, ...) is read from an
environment variable with a safe, working default — see `backend/app/config.py` for the
authoritative list and `.env.example` (repository root) for a fully-documented, copyable
template. **No configuration is required to run the app** — every value works out of the box.

```bash
cp .env.example .env      # then edit .env, or export the variables directly
```

The backend does not auto-load `.env` (no extra dependency added for this prototype) — export
the variables in your shell / process manager / container before starting `uvicorn`. The
currently-effective non-secret configuration can always be inspected live at `GET /api/config`.

This codebase's only credential-shaped settings are `AUTH_SECRET_KEY` and the optional
`AIS_LIVE_API_KEY` / `ENV_LIVE_API_KEY` (see below) — all unset/obviously-fake by default,
documented explicitly in `.env.example`.

## Operations: health, readiness, and logs

- `GET /health` — liveness only (is the process up?). Never touches ML.
- `GET /ready` — readiness (can this instance serve a full investigation?), with ML
  availability reported as a **separate**, non-blocking field — the app is fully "ready" on the
  classical pipeline alone even when PyTorch/the ML prototype is unavailable, by design.
- `GET /api/health` — kept unchanged for the existing frontend's health check.
- All backend logs are single-line JSON (see `backend/app/logging_config.py`) with a per-request
  correlation id (also returned as the `X-Request-ID` response header), the investigation id,
  pipeline stage, per-stage duration, detector provenance, fallback reason, AIS record count, and
  candidate-vessel count where applicable — never secrets, and never raw AIS/image content.
- Every response body from an unhandled error is a structured `{"detail": ...}` JSON object; a
  Python traceback is never returned to the frontend (it's logged server-side instead, tagged
  with that request's correlation id).

## Reproducibility

Every investigation gets a unique `id` and records, in its response's `config_used` block, the
exact configuration that run actually used (mode, detector, search radius, time window,
attribution weights, ML confidence threshold, drift durations, detector version, app version) —
plus a `processing_time_ms` total and a per-stage `duration_ms` in `progress_log`. For the
built-in **demo** scenario specifically, the synthetic scene, synthetic AIS fleet, and
environment conditions are all deterministically generated (fixed seeds / closed-form
generators, no randomness), so re-running `mode=demo` with the same `config_used` values
reproduces the same detection geometry and the same ranked candidate vessels every time — this
is asserted directly by `backend/tests/test_production_readiness.py::
test_demo_investigation_is_deterministic_across_runs`. The `real_satellite` and `custom_ais`
modes are, by their nature, only as reproducible as the input file the caller supplies — same
file + same `config_used` in, same result out.

## Using your own AIS data

Click **"Use my AIS data"** in the header and upload a CSV. The MarineCadastre column layout
is read natively; a generic-alias reader also accepts common header variants (`lat`/`LAT`/
`Latitude`, `sog`/`SOG`/`Speed`, etc.). The uploaded data is combined with the same synthetic
SAR detection scene.

## Using a real satellite image

Click **"Use real satellite image"** in the header and upload a GeoTIFF or PNG. This runs the
same detection → geometry → drift → AIS → attribution pipeline on your actual image instead of
the synthetic scene (`source: "USER_UPLOADED_REAL_IMAGE"` in the response). Five sample SAR-like
GeoTIFFs (two oil, one lookalike, one no-oil, one dual-pol second oil scene) are bundled at
`backend/data/sample_sar/` for trying this immediately without your own data — these are real
files (not synthesized fresh at request time, unlike the demo scene generator), but the pixel
data itself is procedurally generated, not an actual Sentinel-1 satellite acquisition; see
`DATA_PIPELINE.md` §1a for the full provenance note.

By default this uses the same classical detector as the demo. Dual-polarization uploads can
additionally select the optional ML detector, which uses the genuine Zenodo-trained models
(internally tagged `ML_REAL_ZENODO_TRAINED`) — see `MODEL_CARD.md` §6 and
`backend/app/ml/README_ML_STATUS.md` for its evaluation numbers and limitations. Its output is a
*candidate* slick, never confirmation of a spill or of vessel responsibility. If the ML path is requested but can't produce a usable
result (unavailable engine, empty mask, low confidence), the classical detector runs
automatically instead, tagged `CLASSICAL_DARK_SPOT_ML_FALLBACK` so the fallback is never
mistaken for a real ML result — see `MODEL_CARD.md`'s "ML → Classical fallback" section.

Run `python -m app.evaluation.evaluate_samples` from `backend/` for real, reproducible
precision/recall/F1/IoU numbers for the classical detector against these bundled samples' own
ground-truth masks — see `MODEL_CARD.md`'s "Evaluation" section for the numbers and caveats.

## Exports

Every investigation supports **PDF** and **Markdown** reports, plus the full result as **JSON**
(`GET /api/spill/{id}`), the ranked candidates as **CSV**, and the spill polygon / vessel tracks
as **GeoJSON** — all from the "Report" tab or directly via the API.

## Persistence, authentication, and background jobs (all optional, all off/on by default as noted)

- **Persistence** (SQLite, on by default): completed investigations survive a backend restart —
  see `DATA_PIPELINE.md` §6. `DATABASE_ENABLED=false` reverts to pure in-memory.
- **Authentication** (off by default): `AUTH_ENABLED=true` requires a bearer token
  (`POST /api/auth/login`, default demo account `demo`/`demo1234` unless overridden) on
  investigation/report/export routes, and investigations become per-owner (a user cannot see
  another user's investigation — `GET /api/spill/{id}` returns 404, not 403, deliberately not
  confirming the id exists). No frontend login screen exists yet — this is API-only for now; see
  `DEMO_GUIDE.md` for a curl walkthrough.
- **Background jobs** (always available, additive): `POST /api/jobs/investigation` +
  `GET /api/jobs/{id}` run the same pipeline asynchronously via a small in-process thread pool —
  no Redis/Celery/Docker required. The synchronous `POST /api/spill/detect` route this whole demo
  is built around is completely unchanged.
- **Live AIS / live environmental data** (both off by default, both unvalidated against a real
  provider in this project's own development environment): set `AIS_LIVE_PROVIDER_URL` /
  `ENV_LIVE_PROVIDER_URL` to attempt a real feed; `mode="live_ais"` falls back to the synthetic
  fleet with an explicit reason if it isn't configured or fails. See `DATA_PIPELINE.md` §2a/§3.

## Troubleshooting

- **`pip install` fails / backend won't start.** Confirm Python 3.10+ (`python --version`).
  On Windows, activate the venv first (`.venv\Scripts\activate`) — installing into the system
  Python is the most common cause of "module not found" at `uvicorn` startup.
- **Frontend shows a network/API error on Run Demo Investigation.** The backend must be running
  on port 8000 (`uvicorn app.main:app --reload --port 8000`) before `npm run dev` is started, or
  Vite's dev-server proxy has nothing to forward `/api` to. Check `GET http://localhost:8000/health`
  directly in a browser to confirm the backend is actually up.
- **Map shows "Basemap tiles unavailable" instead of a background map.** This sandbox/deployment
  has no outbound access to `tile.openstreetmap.org` / `maps.wikimedia.org`. This is expected
  behavior, not a bug — the investigation data, overlays, and every other tab work normally
  without raster tiles; see `InteractiveMap.jsx`'s fallback banner.
- **`detector=ml` always returns a `CLASSICAL_DARK_SPOT_ML_FALLBACK` result, never real ML
  output.** Check `GET /api/ml/status` — if `torch_available` is `false`, install
  `requirements-ml.txt` (`pip install -r requirements-ml.txt`, a several-hundred-MB CPU wheel)
  and restart the backend. The classical fallback is intentional, documented behavior when torch
  is absent, not an error. The in-app **ML TRAINING** page shows this same status, plus training
  history and evaluation metrics, without needing to call the API directly — see
  `app/ml/README_ML_STATUS.md`.
- **`pytest` reports skipped tests (currently 14).** Every skip is gated on PyTorch being
  importable — real-checkpoint loading, real-dataset training/evaluation/promotion tests, and
  `test_detect_rejects_nan_classifier_output`'s fake classifier module all use
  `pytest.importorskip`/`pytest.mark.skipif`, not a silent pass, when torch isn't installed. This
  count grows as ML-path tests are added, so treat "all skips are torch-only" (confirm with
  `pytest -rs`) as the invariant to check, not this specific number. Install `requirements-ml.txt`
  to exercise all of them — note the default PyPI `torch` wheel pulls in several GB of CUDA
  dependencies on Linux even for CPU-only use (its normal import path tries to dlopen
  `libcublasLt`/`libcudart` etc. unconditionally), so make sure you have the disk space, or
  install a genuine CPU-only wheel per https://pytorch.org/get-started/locally/ instead.
- **Uploading a `.tif` fails with a "Could not process uploaded image" 422.** The file is not a
  readable TIFF/GeoTIFF (corrupted, truncated, or a renamed non-image file) — this is the
  intended, safe response to an unreadable upload, not a crash. Try one of the bundled samples in
  `backend/data/sample_sar/` to confirm the pipeline itself works.
- **Port 8000 or 5173 already in use.** Pass a different port: `uvicorn app.main:app --port 8001`
  and update `frontend/vite.config.js`'s proxy target to match, or stop the process already
  bound to that port.

## Documentation

- `docs/README.md` — documentation index (all documents below, plus why they live at the repo root)
- `docs/DATASET_SETUP.md` — download/extraction/setup for the real Zenodo dataset (external, not bundled)
- `docs/DATASET_LICENSE.md` — dataset license (CC BY 4.0), citation, attribution
- `docs/DATASET_STRUCTURE.md` — exact expected dataset folder layout and splitting policy
- `ARCHITECTURE.md` — component map, data flow, extension points
- `DATA_PIPELINE.md` — exactly how each dataset is sourced, normalized, and falls back
- `MODEL_CARD.md` — the detection/drift/scoring methods, honestly stated, no invented metrics
- `DEMO_GUIDE.md` — the exact click-by-click SIH demo sequence
- `FINAL_HANDOFF.md` — merge history and full validation results
- `.env.example` — every configuration variable, documented, with safe defaults

## Limitations (see MODEL_CARD.md and the in-app report for the full list)

- The default detector is a classical hybrid dark-spot pipeline, not a dataset-trained model (no
  network access to the reference Zenodo dataset in this environment) — confidence is a
  heuristic index, not a calibrated probability.
- A production ML detector (`SceneClassifierV2` and `SpillUNet`) is deployed for dual-pol uploads. It has been trained and evaluated against real SAR data, achieving 78.44% accuracy on an independent test split — see MODEL_CARD.md.
- A separate, complete, offline pipeline (`app/ml/dataset.py`/`train.py`/`evaluate_classifier.py`/
  `promote.py`) exists for retraining and evaluating models against the actual Zenodo dataset — see `docs/DATASET_SETUP.md`.
- The drift model is simplified advection (centroid transport only, no spreading/diffusion).
- Spill age is a low-confidence, single-scene heuristic.
- Real satellite uploads do not carry embedded geolocation in this build; scene coordinates are
  caller-supplied and marked `geolocation_is_assumed: true`.
- Live Sentinel-1 / NOAA / Copernicus / MarineCadastre feeds are not wired to live endpoints in
  this sandboxed build; the fallback architecture that would use them is fully implemented and
  documented as an extension point.
- This is a decision-support and investigation tool, not a legal proof engine.
