# COMPLETE WEBSITE & BACKEND QUALITY AUDIT REPORT
**Marine Pollution Intelligence — SIH-26143**  
**Audit Date:** October 31, 2026 | **Scope:** Comprehensive Frontend & Backend Engineering Audit

---

## 1. Executive Summary

A comprehensive quality, reliability, and security audit was conducted across the entire Marine Pollution Intelligence web application and REST API backend. The audit examined every frontend view, component, modal, chart, form, and error boundary, as well as every backend API endpoint, exception handler, upload pipeline, persistence transaction, and background job manager.

All identified vulnerabilities, edge cases, and synchronization issues were investigated, documented, and verified against automated regression suites.

---

## 2. Frontend Comprehensive Quality Audit

### 2.1 Route & Deep-Linking Architecture

The frontend operates as a single-page application with bidirectional URL state synchronization (`frontend/src/lib/routing.js` and `frontend/src/App.jsx`):
- **`/` (Dashboard / Overview):** Primary operational workspace with Leaflet tactical map, surveillance posture cards, and live incident telemetry.
- **`/satellite`:** Satellite SAR ingestion view with full-resolution despeckled imagery, dark-spot overlay, geometric characterization table, and sensor metadata.
- **`/ais`:** AIS fleet reconstruction view with spatio-temporal filter controls, target vessel tracks, and data completeness metrics.
- **`/ranking`:** Explainable vessel attribution leaderboard displaying multi-factor scoring breakdowns.
- **`/timeline`:** Interactive chronological reconstruction of the incident from AIS vessel transit to satellite acquisition.
- **`/report`:** Formal incident report generator supporting Markdown preview, GeoJSON exports, CSV vessel dumps, and printable PDF generation.
- **`/mltraining`:** Deep learning telemetry dashboard with ONNX runtime status, sidecar model card inspector, and multi-component training history charts.
- **`/routeplanner`:** Spill-aware maritime route planner with fuel consumption optimization and avoidance corridor simulation.

**Audit Findings & Verification:**
- Direct URL entry (e.g. `https://domain/ml-training` or `/routeplanner`) cleanly deep-links to the target view on refresh without 404 or state loss.
- Browser back/forward navigation triggers smooth view transitions via `window.history.replaceState`.

---

### 2.2 Re-Entrancy, Double-Click & Race-Condition Guards

Prior prototypes risk duplicate request dispatch when users double-click submit buttons before React renders disabled states.
- **`UploadSatelliteModal.jsx`:** Employs a synchronous `submittingRef` alongside React's asynchronous `busy` state. Rapid multi-clicks within the same event tick are synchronously intercepted:
  ```javascript
  if (submittingRef.current) return
  submittingRef.current = true
  setBusy(true)
  ```
- **`UploadAisModal.jsx` & `App.jsx` (`runDemo`):** Protected with corresponding `runDemoInFlightRef` and `runCustomAisInFlightRef` synchronous re-entrancy guards.
- **Automated Test Coverage:** Verified by `src/test/satelliteUploadFlow.test.jsx`:
  *"a rapid double-click on 'Upload & run' while the request is still pending sends exactly one request" (PASSED)*.

---

### 2.3 Error Boundaries & Failure Recovery

- **Granular Error Containment:** The main content view is wrapped in an `ErrorBoundary` keyed dynamically on `${activeTab}:${investigation?.id ?? 'none'}`. An unexpected data structure in one tab cannot crash the global navigation shell.
- **Retry Mechanism:** Failed uploads surface an actionable error banner with an explicit `↻ Retry` button that retains user parameters (detector mode, coordinates, file handle).
- **Transient Polling Error Resilience:** In `App.jsx`, background job status polling allows up to 5 consecutive transient network errors with exponential backoff before failing, preventing momentary wifi blips from terminating long-running SAR investigations.

---

### 2.4 Component-by-Component Health Matrix

| Component | Audit Scope | Verified Behavior | Status |
| :--- | :--- | :--- | :---: |
| **`ClassificationScorecard.jsx`** | Confidence formatting & accessibility | Safely handles `NaN`/`null`, formats to 1 decimal place, renders ARIA progressbars with accessible text. | **HEALTHY** |
| **`InteractiveMap.jsx`** | Leaflet rendering & geometry | Automatic `invalidateSize` on container resize, fallback tile providers, bounds auto-fitting for slick polygons. | **HEALTHY** |
| **`KpiCards.jsx`** | Telemetry metrics & units | Correct units for area ($\text{km}^2$), perimeter ($\text{km}$), confidence, and spill age caveats. | **HEALTHY** |
| **`VesselDetailDrawer.jsx`** | Attribution breakdown | Explains distance, time delta, speed consistency, route alignment, and AIS data completeness warnings. | **HEALTHY** |
| **`MlTrainingTab.jsx`** | Recharts responsive rendering | Supports model component switching (Classifier vs Segmenter), handles empty/unavailable metrics honestly. | **HEALTHY** |
| **`FuelIndicationMeter.jsx`** | Route planner fuel comparison | Accurately calculates baseline vs spill-avoidance fuel burn, distance deltas, and percentage penalties. | **HEALTHY** |
| **`ThemeToggle.jsx`** | Dark/light theme persistence | Persists user preference to `localStorage`, applies CSS variables across all panels without style flash. | **HEALTHY** |

---

## 3. Backend Comprehensive Quality Audit

### 3.1 Input Validation & File Handling

- **File Extension Whitelist:** `allowed_satellite_extensions = {".tif", ".tiff", ".geotiff", ".png", ".jpg", ".jpeg", ".webp"}` enforced in `backend/app/main.py`.
- **Upload Size Limit:** Enforces `max_upload_size_bytes` (100 MB default) via streaming byte counters in `_save_upload_with_size_limit`. Exceeding files are truncated immediately with HTTP 413.
- **Filesystem Security:** Filenames are never used as filesystem paths. Files are written to isolated UUID temporary files in the OS temp directory.
- **Resource Cleanup Guarantee:** Temporary upload files are guaranteed to be unlinked in `finally` blocks for both synchronous investigations and asynchronous worker threads.

---

### 3.2 Path Sanitization & Information Disclosure Prevention

Error messages from low-level libraries (e.g. PIL `cannot identify image file 'C:\Users\...'`) could expose server-side filesystem layouts.
- **Regex Sanitization:**
  ```python
  _safe = _re.sub(r"'[^']*[/\\][^']*'", f"'{_user_name}'", str(e))
  if _safe == str(e):
      _safe = str(e).replace(tmp_path, _user_name)
  ```
  Replaces server internal temporary file paths with the user's uploaded filename before leaving the server.

---

### 3.3 Asynchronous Job Execution & Concurrency

- **`JobManager` (`backend/app/jobs.py`):**
  - Thread pool executor with bounded queue capacity (`max_queue_depth=50`).
  - Graceful rejection with HTTP 429 (`JobQueueCapacityExceeded`) and `Retry-After: 5` header when saturated.
  - Job progress stages (`real_satellite_scene_loaded`, `sar_preprocessing_completed`, `oil_slick_detected`, etc.) stream to client polling handlers.
  - Interrupted jobs on server restarts are marked as `interrupted` rather than hanging indefinitely.

---

### 3.4 Database Integrity & Persistence

- **SQLite Database (`backend/app/persistence/db.py`):**
  - Atomic transactions with WAL (Write-Ahead Logging) mode enabled.
  - Foreign key constraints enabled (`PRAGMA foreign_keys = ON`).
  - Thread-safe connection handling using per-thread SQLite connections.
  - Investigation payloads stored as validated JSON with timestamp indexes.

---

### 3.5 Health & Readiness Monitoring

- **`GET /health`:** HTTP 200 `{"status": "ok", "version": "0.1.0", "env": "production"}`.
- **`GET /ready`:** HTTP 200 verifying synthetic demo pipeline, ML ONNX runtime availability, and database connectivity.
- **`GET /api/ml/status`:** Detailed telemetry regarding ONNX model weights, SHA-256 checksums, and temperature calibration status.

---

## 4. Audit Summary & Regression Matrix

| Audit Domain | Pre-Audit Risk | Remediation Implemented | Regression Test |
| :--- | :--- | :--- | :--- |
| **Confidence Integrity** | Repeated 0.95 scores | Dynamic range backscatter derivation | `test_csiro_images_produce_distinct_confidences` |
| **Attribution Safety** | Accusation on lookalikes | Fail-closed attribution gating (`ABSTAINED`) | `test_dartis_end_to_end_investigation_abtains_attribution` |
| **Frontend Re-entrancy** | Double-click upload race | Synchronous ref submission guards | `satelliteUploadFlow.test.jsx` |
| **Path Exposure** | Temp paths in error details | Regex filesystem path sanitization | `test_api.py::test_upload_invalid_file` |
| **Offline Resilience** | Missing network data crash | Graceful fallback and caveat notes | `KpiCardsMetoceanStatus.test.jsx` |
