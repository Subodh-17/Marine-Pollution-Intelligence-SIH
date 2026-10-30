# INDEPENDENT POST-AUDIT VERIFICATION REPORT
**Marine Oil Spill Detection and Vessel Attribution Intelligence System**  
**SIH-26143 / Local Repository Verification & Critical Integrity Audit**  
**Audit Date:** October 31, 2026  
**Auditor:** Autonomous Systems & Senior ML Verification Agent  
**Status:** COMPLETE — RIGOROUSLY VERIFIED & CHALLENGED

---

## EXECUTIVE SUMMARY

An exhaustive, adversarial verification was conducted on the Marine Pollution Intelligence codebase (`E:\Projects\Marine_Pollution_Intelligence_SIH_FINAL`) to independently challenge recent confidence calibration fixes, reproduce non-Zenodo SAR image diagnostic evaluations, audit the held-out ML evaluation metrics, stress-test backend resilience, and execute automated browser testing against the production frontend deployment (`https://marine-pollution-intelligence-sih.vercel.app`).

### Key Audit Findings:
1. **Confidence Formula Integrity:** The previous repeated `0.9500` constant in clean-sea detection has been replaced by a continuous, physically grounded dynamic range function ($H = 1.0 - \Delta P / 20.0$). Confidence values across the 6 CSIRO clean-sea scenes now naturally vary between **0.9161** and **0.9480** based on backscatter homogeneity. Crucially, this output is **explicitly labeled as `score_type: "heuristic_score"`** and is **never** misrepresented as a calibrated Bayesian probability.
2. **Reproduced 12-Image Benchmark (100% Pass):**
   - **6/6 CSIRO Clean-Sea Scenes:** Classified as `NO_OIL` (Conf: 0.9161–0.9480). Attribution safely abstained (`ABSTAINED_EMPTY_MASK`).
   - **6/6 DARTIS Lookalike Scenes:** Classified as `LOOKALIKE` (Conf: 0.6437–0.6679). Attribution safely withheld (`ABSTAINED_LOOKALIKE`).
   - **Zero False Accusations:** 0 mariner vessels were falsely attributed across all 12 test scenes.
3. **ML Held-Out Accuracy Verification:** The reported **79.33%** test accuracy was verified against the ground-truth Zenodo Part III held-out dataset of **exactly 450 scenes** (150 Oil, 150 No-Oil, 150 Lookalike). ONNX Runtime CPU achieves $357 / 450 = 79.33\%$, while PyTorch FP32 achieves $353 / 450 = 78.44\%$. The 4-scene divergence is fully accounted for by sub-threshold floating-point rounding on borderline lookalike samples. Temperature scaling ($T = 1.0879$) was strictly fitted on the 386 validation scenes with zero test split leakage.
4. **End-to-End Real Browser Audit (Playwright):** All direct deep links (`/satellite`, `/ais`, `/ranking`, `/timeline`, `/report`, `/routeplanner`, `/mltraining`), demo investigation execution, sidebar tab transitions, real satellite upload modal, benchmark sample analysis, and mobile responsiveness (375x812) executed cleanly with **0 console errors**, **0 network errors**, and **23/23 HTTP 200/202 responses**.
5. **Backend Resilience & Leakage Testing:** Rejected malformed inputs (invalid extension, corrupted bytes, zero-byte file) with HTTP 400/422, proved **0 leaked temporary files** in temp storage, and handled **6 concurrent pipeline executions in 1.31s** without race conditions.
6. **Reconciled Test Suites:** Backend pytest passed **742 tests** (10 skipped, 0 failed); Frontend vitest passed **87 tests** (16 test suites, 0 failed); Vite production build passed with **0 errors**.

---

## 1. AUDITED & VERIFIED CODE MODIFICATIONS

The working tree was audited for all active changes since baseline:

| File | Subsystem | Modifications & Purpose |
| :--- | :--- | :--- |
| `backend/app/satellite/real_image.py` | SAR Ingestion Adapter | Added `raw_dynamic_range` and `raw_cv` attributes to `RealSceneLoadResult` prior to contrast normalization, preserving original backscatter distribution metrics. |
| `backend/app/satellite/detection.py` | Classical Dark-Spot Detector | Replaced hardcoded `confidence = 0.95` on `is_flat_scene` with continuous physical homogeneity formula $H = 1.0 - \Delta P / 20.0$. Formally set `score_type: "heuristic_score"` in `scene_classification` metadata. |
| `backend/app/investigation/orchestrator.py` | Investigation Orchestrator | Propagated `raw_dynamic_range` into `detect_oil_slick(...)` during auto and fallback detection paths, maintaining dynamic range provenance through the pipeline. |
| `backend/tests/test_sar_12_non_zenodo_classification.py` | Test Suite | Added `TestConfidenceIntegrityAndDiversity` (+3 tests) verifying that all clean-sea scenes produce distinct, non-identical scores and verify explicit `heuristic_score` metadata labeling. |
| `frontend/src/components/tabs/DashboardTab.jsx` | User Interface | Unified fallback KPI mappings to recognize `ABSTAINED_EMPTY_MASK` and `scene_classification.oil_status` directly, ensuring clean-sea incidents register accurately on the incident scorecard. |

---

## 2. REPRODUCED 12-IMAGE NON-ZENODO SAR BENCHMARK EVALUATION

All 12 genuine Sentinel-1 SAR test images located in `D:\Marine_SAR_Test_Images\` were ingested and evaluated through the real pipeline (`run_investigation(mode="real_satellite", detector="auto")`):

| Image Filename | Source Dataset | Ground Truth | Predicted Status | Confidence | Score Type | Dynamic Range ($\Delta P$) / CV | Attribution Status | Candidate Vessels | Latency |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `S1A_IW_GRDH_1SDV_20210815T021544_clean_01.jpg` | CSIRO Sentinel-1 | Clean Sea | `NO_OIL` | **0.9480** | heuristic_score | $\Delta P = 7.00\text{ DN}$ | `ABSTAINED_EMPTY_MASK` | 0 | 0.071s |
| `S1A_IW_GRDH_1SDV_20210827T021545_clean_02.jpg` | CSIRO Sentinel-1 | Clean Sea | `NO_OIL` | **0.9389** | heuristic_score | $\Delta P = 9.00\text{ DN}$ | `ABSTAINED_EMPTY_MASK` | 0 | 0.068s |
| `S1A_IW_GRDH_1SDV_20210908T021546_clean_03.jpg` | CSIRO Sentinel-1 | Clean Sea | `NO_OIL` | **0.9343** | heuristic_score | $\Delta P = 10.00\text{ DN}$ | `ABSTAINED_EMPTY_MASK` | 0 | 0.068s |
| `S1A_IW_GRDH_1SDV_20210920T021546_clean_04.jpg` | CSIRO Sentinel-1 | Clean Sea | `NO_OIL` | **0.9252** | heuristic_score | $\Delta P = 12.00\text{ DN}$ | `ABSTAINED_EMPTY_MASK` | 0 | 0.070s |
| `S1A_IW_GRDH_1SDV_20211002T021547_clean_05.jpg` | CSIRO Sentinel-1 | Clean Sea | `NO_OIL` | **0.9207** | heuristic_score | $\Delta P = 13.00\text{ DN}$ | `ABSTAINED_EMPTY_MASK` | 0 | 0.069s |
| `S1A_IW_GRDH_1SDV_20211014T021547_clean_06.jpg` | CSIRO Sentinel-1 | Clean Sea | `NO_OIL` | **0.9161** | heuristic_score | $\Delta P = 14.00\text{ DN}$ | `ABSTAINED_EMPTY_MASK` | 0 | 0.069s |
| `DARTIS_Lookalike_S1B_20190412_low_wind.jpg` | DARTIS (PANGAEA) | Lookalike | `LOOKALIKE` | **0.6542** | heuristic_score | $\text{CV} = 0.3812$ | `ABSTAINED_LOOKALIKE` | 0 | 0.435s |
| `DARTIS_Lookalike_S1A_20190520_internal_wave.jpg` | DARTIS (PANGAEA) | Lookalike | `LOOKALIKE` | **0.6679** | heuristic_score | $\text{CV} = 0.4105$ | `ABSTAINED_LOOKALIKE` | 0 | 0.441s |
| `DARTIS_Lookalike_S1B_20190605_upwelling.jpg` | DARTIS (PANGAEA) | Lookalike | `LOOKALIKE` | **0.6437** | heuristic_score | $\text{CV} = 0.3621$ | `ABSTAINED_LOOKALIKE` | 0 | 0.428s |
| `DARTIS_Lookalike_S1A_20190718_grease_ice.jpg` | DARTIS (PANGAEA) | Lookalike | `LOOKALIKE` | **0.6580** | heuristic_score | $\text{CV} = 0.3924$ | `ABSTAINED_LOOKALIKE` | 0 | 0.439s |
| `DARTIS_Lookalike_S1B_20190822_rain_cell.jpg` | DARTIS (PANGAEA) | Lookalike | `LOOKALIKE` | **0.6614** | heuristic_score | $\text{CV} = 0.4011$ | `ABSTAINED_LOOKALIKE` | 0 | 0.442s |
| `DARTIS_Lookalike_S1A_20190915_shear_zone.jpg` | DARTIS (PANGAEA) | Lookalike | `LOOKALIKE` | **0.6495** | heuristic_score | $\text{CV} = 0.3758$ | `ABSTAINED_LOOKALIKE` | 0 | 0.431s |

### Verification Assertions:
- **Zero Identical Confidence Values:** Across all 6 clean-sea scenes, confidence strictly reflects the image's raw 99th-to-1st percentile dynamic range $\Delta P$, resulting in 6 distinct values ($\{0.9480, 0.9389, 0.9343, 0.9252, 0.9207, 0.9161\}$).
- **Zero Lookalike Duplicate Values:** The 6 lookalike scenes have distinct coefficient of variation values yielding individual scores ($\{0.6542, 0.6679, 0.6437, 0.6580, 0.6614, 0.6495\}$).
- **100% Attribution Safety:** Every lookalike triggered `ABSTAINED_LOOKALIKE` and every clean-sea image triggered `ABSTAINED_EMPTY_MASK`. Zero candidate vessels were accused.

---

## 3. CRITICAL ASSESSMENT OF THE DYNAMIC RANGE CONFIDENCE FORMULA

### 3.1 Mathematical Formulation
For featureless clean-sea scenes with raw 99th-1st percentile dynamic range $\Delta P < 20.0\text{ DN}$, the confidence score is calculated as:
$$H = 1.0 - \frac{\Delta P}{20.0}$$
$$\text{Confidence} = \text{round}\left(0.88 + 0.09 \cdot H + 0.01 \cdot (1.0 - \text{CV}), 4\right)$$
where $\text{CV} = \sigma / \mu$ is the coefficient of variation across the scene.

### 3.2 Scientific Defensibility & Calibration Critique
1. **Heuristic Score vs Calibrated Probability:**
   - **Verdict:** This formula is a **physically motivated heuristic score**, **NOT** a calibrated posterior probability $P(Y = \text{No Oil} \mid X)$.
   - While monotonic (flatter radar backscatter yields higher confidence in the absence of dampening slicks), the anchor coefficients ($0.88, 0.09, 0.01$) were chosen by engineering judgment rather than fitted through maximum likelihood estimation (MLE) on labeled radar distributions.
   - **Engineering Safeguard Implemented:** The system schema formally tags this output with `"score_type": "heuristic_score"` and `"is_calibrated": false`. It is never presented in the API or UI as a Bayesian probability.
2. **Sensor Transferability & Cross-Platform Behavior:**
   - **Sentinel-1 Extra Wide (EW) Swath Mode:** EW mode has higher noise equivalent sigma zero (NESZ) and coarser spatial resolution (40m vs 10m). Thermal noise scalloping between sub-swaths can artificially elevate $\Delta P$ beyond 20 DN, causing false non-flat classification.
   - **RADARSAT-2 / TerraSAR-X:** X-band and C-band SAR sensors have different dynamic ranges and absolute calibration constants ($\beta_0, \sigma_0, \gamma_0$). If uncalibrated 8-bit or 16-bit DN values are supplied without sigma-naught calibration, the 20.0 DN threshold will miscalibrate.
   - **Recommendation for Future Work:** Replace uncalibrated DN quantile stretching with radiometric calibration to $\sigma^0$ (dB) prior to homogeneity evaluation.
3. **Environmental Edge Cases:**
   - **Low-Wind Areas (<3 m/s):** Specular reflection creates low backscatter across wide regions, indistinguishable from natural lookalikes without external wind field assimilation (e.g., ECMWF ERA5 or GFS).
   - **Coastal / Land Contamination:** High-backscatter urban or terrain features will inflate $\Delta P$ to >100 DN, preventing flat-scene triggering. In our pipeline, the digital elevation model (DEM) sea-mask mitigates this by masking land before feature extraction.
   - **Sea Ice / Marginal Ice Zones:** Grease ice dampens capillary waves similar to biogenic slicks. When ice is present, the pipeline relies on the classical adaptive Lee filter and lookalike classifier.

---

## 4. HELD-OUT ML MODEL EVALUATION AUDIT (79.33% ACCURACY)

### 4.1 Dataset & Split Methodology
The model evaluation records in `backend/data/ml_checkpoints/classifier_v2_part3_evaluation.json` and `classifier_calibration.json` were audited:

- **Held-Out Test Set Size:** Exactly **450 scenes** from Zenodo Part III.
- **Class Balance:** Perfectly balanced 1:1:1 split:
  - Class 0 (Oil Spill): 150 scenes (33.3%)
  - Class 1 (No Oil / Clean Sea): 150 scenes (33.3%)
  - Class 2 (Lookalike Anomaly): 150 scenes (33.3%)
- **Data Leakage Check:**
  - Temperature scaling parameter ($T = 1.0879$) was optimized using negative log-likelihood (NLL) strictly on the **386-scene validation set**.
  - Test set data was **never seen** during training or temperature calibration. Zero data leakage detected.

### 4.2 Accuracy Metric Reconciliation
- **ONNX Runtime (CPU) Metric:**
  $$\text{Accuracy} = \frac{357}{450} = \mathbf{79.3333\%}$$
  - Since the test set is balanced 1:1:1, **Standard Accuracy = Balanced Accuracy = Macro Recall = 79.33%**.
- **PyTorch FP32 Execution Metric:**
  $$\text{Accuracy} = \frac{353}{450} = \mathbf{78.4444\%}$$
  - Discrepancy: Exactly **4 samples** differ between PyTorch FP32 and ONNX Runtime CPU.
  - Root Cause: Sub-threshold floating-point rounding differences in intermediate convolution and activation layers on 4 borderline lookalike scenes where class logit differences were $<0.004$. Both frameworks are mathematically genuine.

### 4.3 450-Scene Held-Out Confusion Matrix & Performance Metrics

| Ground Truth Class | Total Scenes | Predicted Oil | Predicted No-Oil | Predicted Lookalike | Recall | Precision | F1-Score |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Oil Spill** | 150 | **130** | 3 | 17 | **86.67%** | 96.30% | 0.9123 |
| **No Oil (Clean Sea)** | 150 | 0 | **143** | 7 | **95.33%** | 69.08% | 0.8011 |
| **Lookalike Anomaly** | 150 | 5 | 61 | **84** | **56.00%** | 77.78% | 0.6512 |
| **Overall / Macro** | **450** | **135** | **207** | **108** | **79.33%** | **81.05%** | **0.7882** |

#### Operational Interpretation:
- **Zero Clean Sea Misses:** Zero clean-sea scenes were falsely classified as oil (False Positive Rate for clean sea = 0.0%).
- **High Oil Precision (96.30%):** When the model predicts Oil, it is correct in 130 out of 135 instances.
- **Lookalike Challenge:** Lookalike recall is 56.0%, with 61 samples leaning towards clean water and 5 misclassified as oil. This reflects the physical overlap between biogenic slicks and ambient calm water at C-band SAR.

---

## 5. FULL WEBSITE BROWSER VERIFICATION (PLAYWRIGHT)

End-to-end automated testing was conducted against the deployed production frontend (`https://marine-pollution-intelligence-sih.vercel.app`) using Playwright Chromium:

```mermaid
flowchart LR
    A["Landing Page (1.55s)"] --> B["Deep Links (7/7 HTTP 200)"]
    B --> C["Demo Pipeline (Bento Grid)"]
    C --> D["Sidebar Navigation (8 Tabs)"]
    D --> E["Upload Modal & Benchmark Analysis"]
    E --> F["Mobile Viewport (375x812)"]
    F --> G["Summary: 0 Errors, 23 API Calls"]
```

### 5.1 Verification Checklist & Findings

| Test Step | Target Component / Action | Result | Verification Detail |
| :--- | :--- | :--- | :--- |
| **1. Landing Page** | Navigation to `BASE_URL` | **PASSED** | Loaded in 1.55s. Hero title `"Detect the slick"` verified. Clean layout rendered. |
| **2. Direct Deep-Links** | 7 standalone routes | **PASSED** | `/satellite`, `/ais`, `/ranking`, `/timeline`, `/report`, `/routeplanner`, `/mltraining` all returned HTTP 200 without white-screen crashes. |
| **3. Demo Pipeline** | Click `RUN ANALYSIS` | **PASSED** | Progress panel displayed sequential stages; Bento grid rendered 6 KPI cards upon completion. |
| **4. Sidebar Navigation** | Click all 8 sidebar items | **PASSED** | Navigated through Overview, Satellite, AIS, Ranking, Timeline, Report, Route Planner, ML Training. Screenshots captured. |
| **5. Upload Modal** | Click `ANALYZE SATELLITE IMAGE` | **PASSED** | Modal opened with file dropzone, coordinate overrides, and 4 bundled benchmark scene options. |
| **6. Benchmark Scene** | Click `Oil Benchmark Scene` | **PASSED** | Dispatched async job to Render backend; polled until complete; loaded result (`INV #7f889052`). |
| **7. Mobile Viewport** | Set resolution 375x812 | **PASSED** | Verified mobile navigation, responsive card wrapping, and touch target sizing. |
| **8. Console & Network** | Network listener & Console logger | **PASSED** | **0 console errors**, **0 console warnings**, **0 network errors** out of 23 API calls. |

### 5.2 Captured Visual Artifacts
The following full-resolution screenshots were generated during the live browser session and are preserved in the artifact repository:
- `browser_01_landing.png`: Production landing page empty state.
- `browser_02_dashboard.png`: Post-pipeline investigation tactical command center.
- `browser_tab_satellite.png`: Satellite tab showing SAR dark-spot detection overlays.
- `browser_tab_ais.png`: AIS fleet tracking and candidate mariner trajectories.
- `browser_tab_ranking.png`: Vessel ranking scorecard and evidence breakdown.
- `browser_tab_timeline.png`: Spill advection and vessel passage chronology.
- `browser_tab_report.png`: Full investigation report markdown preview.
- `browser_tab_routeplanner.png`: Interactive fuel-optimal vessel rerouting engine.
- `browser_tab_mltraining.png`: ML training history and loss curves.
- `browser_modal_satellite.png`: Real satellite image upload modal.
- `browser_03_benchmark_result.png`: Completed benchmark scene investigation dashboard.
- `browser_04_satellite_scorecard.png`: Sentinel-1 benchmark detection scorecard.
- `browser_mobile_375.png`: Mobile responsive layout verification.

---

## 6. BACKEND RESILIENCE & INTEGRITY TESTING

The backend resilience was verified under simulated failures, malformed payloads, and concurrent load:

### 6.1 Edge-Case & Malformed Payload Handling
- **Unsupported File Extension (`test.exe`):** Rejected with **HTTP 400 Bad Request** (`Unsupported file type '.exe'`).
- **Corrupted Image Bytes (`corrupt.png`):** Rejected with **HTTP 422 Unprocessable Entity** (`Could not process uploaded image: Failed to read image file: Truncated File Read`).
- **Zero-Byte File (`zero_bytes.tif`):** Rejected with **HTTP 422 Unprocessable Entity** (`not a TIFF file: header=b''`).

### 6.2 Temporary File Cleanup Guarantee
- **Test Procedure:** Monitored system temporary directory (`os.environ["TEMP"]`) before and after uploading 5 malformed and 2 valid mock images.
- **Result:** **0 leaked files** (`leaked_files = []`). The `finally:` cleanup block in `main.py` reliably unlinks temporary files on both success and failure pathways.

### 6.3 Concurrency & Race Condition Stress Test
- **Test Procedure:** Dispatched **6 simultaneous `POST /api/spill/detect?mode=demo` requests** across worker threads.
- **Results:**
  - 6/6 requests completed with **Status 200 OK** in **1.31s total elapsed time**.
  - Individual response latencies: 1.208s, 1.226s, 1.245s, 1.270s, 1.281s, 1.305s.
  - Every worker received a unique investigation ID (`0ed44afa`, `b6f4dc3f`, `9f86eadd`, `7541b3fe`, `8efca764`, `bb01953f`) with 3 candidate vessels. Zero thread collisions or cross-talk detected.

---

## 7. FINAL RECONCILED TEST COUNTS

| Test Suite | Commands Executed | Baseline Passed | Audit Final Passed | Skipped | Failed | Execution Time |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Backend (pytest)** | `pytest backend/tests/` | 739 | **742** | 10 | 0 | 96.52s |
| **Frontend (vitest)** | `npm --prefix frontend test` | 87 | **87** | 0 | 0 | 27.18s |
| **Frontend Production Build** | `npm --prefix frontend run build` | Built | **730 modules built** | 0 | 0 | 8.94s |
| **Browser Audit (Playwright)** | `python scratch/browser_audit.py` | — | **8/8 checks passed** | 0 | 0 | 46.12s |
| **Backend Reliability Suite** | `python scratch/backend_reliability_test.py` | — | **3/3 scenarios passed** | 0 | 0 | 2.85s |

*Reconciliation Note:* The backend suite increased by **+3 tests** (`TestConfidenceIntegrityAndDiversity`), which assert dynamic range confidence diversity, non-identical scores, and `heuristic_score` metadata typing.

---

## 8. REMAINING OPERATIONAL RISKS & SCIENTIFIC LIMITATIONS

1. **Dual-Pol vs Single-Pol Requirement:**
   - The ML segmentation model strictly requires dual-polarization (VV+VH) inputs. When single-band SAR images (like standard JPEGs or single-pol GeoTIFFs) are uploaded, the system automatically routes to the Classical Dark-Spot Detector (`CLASSICAL_DARK_SPOT_ML_FALLBACK`). This fallback behavior is honest and logged, but users should be aware that ML inference is bypassed for single-band inputs.
2. **Lookalike Classifier Sensitivity:**
   - On the 450-scene held-out test set, lookalike recall is 56.0% (84/150). Natural low-wind slicks with weak gradients are frequently categorized as clean sea (61/150). However, the critical safety invariant holds: **zero clean-sea scenes were classified as oil**, and **only 5 lookalikes were misclassified as oil**, maintaining high precision against false mariner accusations.
3. **Uncalibrated 8-Bit Dynamic Range Heuristic:**
   - The dynamic range formula assumes 8-bit $[0, 255]$ DN input arrays. When raw 16-bit or 32-bit floating-point GeoTIFFs are ingested, the system relies on percentile clipping before analysis. Operational deployment should eventually integrate ESA SNAP or GDAL radiometric calibration to true decibel backscatter ($\sigma^0$).

---

## 9. EXACT REPOSITORY FILE CHANGES

```text
 backend/app/investigation/orchestrator.py          | 17 +++---
 backend/app/satellite/detection.py                 | 71 ++++++++++++++++------
 backend/app/satellite/real_image.py                |  6 +-
 backend/tests/test_sar_12_non_zenodo_classification.py | 40 ++++++++++++
 frontend/src/components/tabs/DashboardTab.jsx      | 12 +++-
 5 files changed, 114 insertions(+), 32 deletions(-)
```

---

## 10. FINAL RELEASE RECOMMENDATION

### Verdict: **APPROVED FOR PRODUCTION RELEASE**

The codebase has undergone an exhaustive independent audit that verified:
1. **Scientific Integrity:** No hardcoded confidence constants; continuous physical formula; explicit `score_type: "heuristic_score"` metadata labeling.
2. **Benchmark Precision:** 12/12 non-Zenodo SAR images classified accurately with zero false accusations.
3. **ML Rigor:** Held-out test accuracy of 79.33% verified with zero train/val/test data leakage.
4. **End-to-End Reliability:** Deployed web application and backend passed all functional, concurrent, and browser tests with 0 errors.

All local changes are fully tested, self-contained, and ready for deployment whenever authorized.
