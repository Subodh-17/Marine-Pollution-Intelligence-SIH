# Marine Pollution Intelligence

**Satellite-Based Marine Pollution Detection & Vessel Attribution Platform**

[![CI](https://github.com/Subodh-17/marine-pollution-intelligence/actions/workflows/ci.yml/badge.svg)](https://github.com/Subodh-17/marine-pollution-intelligence/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11-blue.svg)](https://www.python.org/)
[![React](https://img.shields.io/badge/react-19.2-cyan.svg)](https://react.dev/)
[![ONNX Runtime](https://img.shields.io/badge/inference-ONNX%20Runtime%20CPU-yellowgreen.svg)](https://onnxruntime.ai/)

An end-to-end maritime surveillance and intelligence pipeline: Sentinel-1 Synthetic Aperture Radar (SAR) oil-slick detection → geometric characterisation → oceanographic/meteorological drift hindcasting → probable spill origin → historic AIS traffic reconstruction → spatio-temporal vessel attribution scoring → comprehensive investigation reports.

An intuitive, dark/light nautical console interface ties the entire investigation workflow together with an interactive Leaflet map, multi-component diagnostics, 3-step imagery overlays, and explainable attribution evidence.

---

## Live Deployments

- **Web Application (Vercel):** [https://marine-pollution-intelligence-sih.vercel.app](https://marine-pollution-intelligence-sih.vercel.app)
- **API Backend (Render):** [https://marine-pollution-intelligence-api.onrender.com](https://marine-pollution-intelligence-api.onrender.com)
- **API Documentation:** [https://marine-pollution-intelligence-api.onrender.com/docs](https://marine-pollution-intelligence-api.onrender.com/docs)
- **Canonical Repository:** [https://github.com/Subodh-17/marine-pollution-intelligence](https://github.com/Subodh-17/marine-pollution-intelligence)

---

## Core Capabilities

1. **Dual-Polarization SAR Detection:** Real-time deep learning segmentation (`SpillUNet`) and scene classification (`SceneClassifierV2`) powered by ONNX Runtime CPU.
2. **Transparent 3-Way Detector Routing:** Automatic routing (`Auto`) that selects deep learning models for dual-polarization (VV+VH) rasters and classical dark-spot heuristics for single-band inputs, with explicit detector provenance.
3. **Fail-Closed Attribution Safety Gating:** Centralized policy safeguards that automatically abstain from vessel attribution when dark spots are classified as natural lookalikes or when masks are empty, preventing false vessel accusations.
4. **Hydrodynamic Drift Hindcasting & Forecasting:** Physics-based advection modeling incorporating ocean surface currents and wind leeway vectors to trace the slick back to its release origin.
5. **Explainable AIS Vessel Correlation:** Multi-criteria trajectory scoring that ranks candidate vessels based on distance to hindcast origin, speed consistency, heading anomaly, and data completeness.
6. **Production-Grade Design System:** Complete light, dark, and system themes targeting WCAG 2.2 AA contrast standards, responsive layouts, and tabular numerals.

---

## System Architecture

```
marine-pollution-intelligence/
├── backend/app/
│   ├── satellite/        SAR raster decoding (GeoTIFF/PNG), downsampling, Lee speckle filtering,
│   │                     adaptive dark-spot detection, geometric analysis, PNG overlay rendering
│   ├── ml/               Deep learning models (SceneClassifierV2 & SpillUNet), ONNX Runtime engine,
│   │                     dataset manifests, evaluation gates, and offline PyTorch training pipelines
│   ├── environment/      Metocean wind/current adapters (Open-Meteo, MET Norway, and cached fallbacks)
│   ├── drift/            Physics-based Lagrangian drift advection (hindcast & forward forecast)
│   ├── ais/              MarineCadastre schema normalization, spatial-temporal filtering,
│   │                     trajectory interpolation, and behavioral anomaly detection
│   ├── scoring/          Explainable weighted multi-criteria vessel attribution engine
│   ├── investigation/    Central orchestrator wiring pipeline stages and enforcing safety gating
│   ├── persistence/      SQLite investigation store, background thread-pool job runner, and schema
│   ├── reports/          Automated Markdown and PDF executive investigation report generation
│   └── main.py           FastAPI REST application, dependency injection, and endpoint routing
│
├── frontend/src/
│   ├── components/       Header, Sidebar, InteractiveMap, ClassificationScorecard, KpiCards,
│   │                     VesselDetailDrawer, UploadSatelliteModal, UploadAisModal, DataSourceIndicator
│   ├── components/tabs/  Dashboard, Satellite, AIS, Ranking, Timeline, Report, ML Training
│   ├── styles/           Design tokens (tokens.css), shell layout, and responsive components
│   └── api.js            Axios API client with background job polling and error handling
```

---

## Machine Learning & Evaluation Evidence

Production inference runs via **ONNX Runtime CPU** (`CPUExecutionProvider`) without requiring a heavyweight PyTorch or GPU environment, ensuring memory safety within 512 MB cloud container limits.

### Verified Model Artifacts
- **Scene Classifier:** `backend/data/ml_checkpoints/classifier_real_best.onnx` (Lightweight CNN SceneClassifierV2, Dual-Pol VV/VH, 256x256).
- **Spill Segmenter:** `backend/data/ml_checkpoints/spill_unet_real_best.onnx` (4-level SpillUNet with skip connections, epoch 18).
- **Preprocessing:** `sar_percentile_1_99_minmax_v1` (1st to 99th percentile backscatter normalization).

### Independent Evaluation on Held-Out Test Set (450 Zenodo Test Scenes)
The models were evaluated against the published, held-out test split (Part III) of the Zenodo Sentinel-1 SAR Oil Spill Dataset (150 Oil, 150 Lookalike, 150 No-Oil scenes):

| Model / Detector | Task | Evaluation Metric | Measured Value | Sample Size | Dataset Provenance |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **SceneClassifierV2** | Scene Classification | Overall Accuracy | **78.44%** | 450 scenes | Zenodo Part III Test Split |
| **SceneClassifierV2** | Scene Classification | Macro Precision / Recall / F1 | **80.3% / 78.4% / 77.8%** | 450 scenes | Zenodo Part III Test Split |
| **SceneClassifierV2** | Oil Scene Detection | Oil Precision / Recall / F1 | **95.6% / 86.7% / 90.9%** | 150 oil scenes | Zenodo Part III Test Split |
| **SceneClassifierV2** | False Alarm Control | False Positive Rate on Non-Oil | **2.0%** (6/300) | 300 non-oil scenes | Zenodo Part III Test Split |
| **SpillUNet (Real)** | Slick Segmentation | Macro Oil-Scene Precision / Recall | **93.22% / 68.86%** | 150 oil scenes | Zenodo Part III Test Split |
| **SpillUNet (Real)** | Slick Segmentation | Macro F1 (Dice) / Mean IoU | **73.82% / 63.24%** | 150 oil scenes | Zenodo Part III Test Split |
| **SpillUNet (Real)** | Slick Segmentation | Pixel-Pooled IoU / Dice | **35.77% / 52.69%** | 29.5M pixels | Zenodo Part III Test Split |
| **Classical SAR** | Dark-Spot Detection | Synthetic Benchmark IoU / F1 | **99.39% / 99.69%** | 2 oil scenes | Bundled Benchmark Samples |

> [!IMPORTANT]
> **Scientific Clarity on Confidence vs Accuracy:**
> A headline score of 94.2% on an individual slick is an **uncalibrated model softmax probability** for that single scene, **not** a calibrated statistical probability and **not** overall system accuracy. In the UI and API, model probabilities are explicitly distinguished from heuristic scores.

### Known Scientific Limitations
1. **Quartile Q4 Under-Segmentation:** On very large slicks covering >11% of the scene frame (Q4 quartile), mean recall is ~33.5% with predicted area capturing ~26% of true spill extent due to receptive field limits. High precision (~99%) confirms the model under-segments rather than hallucinates.
2. **Dual-Polarization Requirement:** The deep learning model strictly requires dual-polarization (VV+VH) inputs. Single-band rasters are routed to classical heuristics and are never converted into fake dual-pol data.

---

## Quick Start & Local Development

### Prerequisites
- Python 3.10 or 3.11
- Node.js 18+ and npm

### 1. Backend Setup

```bash
cd backend
python -m venv .venv

# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate

pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Verify backend health at `http://localhost:8000/health`.

### 2. Frontend Setup

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173` in your browser. The Vite development server automatically proxies `/api` requests to port 8000.

---

## Automated Test Suites

### Backend Tests (Pytest)
```bash
cd backend
pytest tests/ -v
```
All 664 tests pass with 0 failures, including automated database safety guards verifying production databases remain untouched.

### Frontend Tests (Vitest & Lint)
```bash
cd frontend
npm run lint    # oxlint: 0 warnings, 0 errors
npm run test    # vitest: 79 passed across 15 suites
npm run build   # vite production client build
```

---

## Configuration & Environment Variables

Copy `.env.example` to `.env` to customize settings. All variables have safe production defaults:

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `DATABASE_ENABLED` | `true` | Enables SQLite persistence for investigation history. |
| `DATABASE_URL` | `sqlite:///./data/app.db` | Database connection string. |
| `AUTH_ENABLED` | `false` | When true, enforces JWT bearer tokens on write endpoints. |
| `MAX_IMAGE_DIMENSION_PX` | `8000` | Decompression-bomb defense limit. |
| `MAX_IMAGE_UPLOAD_MB` | `50` | Maximum allowed upload file size. |
| `CORS_ALLOWED_ORIGINS` | `http://localhost:5173,...` | Comma-separated list of permitted CORS origins. |
| `ENV_PROVIDER` | `auto` | Metocean weather/current provider (`auto`, `open_meteo`, `synthetic`). |

---

## Scientific & Legal Disclaimer

Marine Pollution Intelligence is a decision-support, research, and investigative attribution platform. Spatial-temporal correlation between vessel trajectories and slick drift hindcasts provides probabilistic ranking and investigative leads; it does not constitute standalone legal proof of liability.
