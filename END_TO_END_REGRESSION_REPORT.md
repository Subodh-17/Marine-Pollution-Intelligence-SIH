# END-TO-END REGRESSION & VERIFICATION REPORT
**Marine Pollution Intelligence — SIH-26143**  
**Audit Date:** October 31, 2026 | **Scope:** Comprehensive Regression Testing & Pipeline Verification

---

## 1. Executive Summary

This report establishes the regression verification baseline and post-modification test results for the confidence calibration and quality audit fixes. All 742 backend tests and 87 frontend tests passed with zero failures. The frontend production build succeeded with zero errors, and all 12 genuine non-Zenodo SAR evaluation scenes were verified for independent, content-based classification without false positives.

---

## 2. Pre-Modification Baseline vs Post-Modification Verification

| Test Suite / Target | Pre-Audit Baseline | Post-Audit Verification | Delta / Status |
| :--- | :--- | :--- | :---: |
| **Backend Regression Suite (`pytest backend/tests/`)** | 739 passed, 10 skipped (123.51s) | **742 passed, 10 skipped** (96.52s) | **+3 passed, 0 failed** |
| **SAR 12 Diagnostic Suite (`test_sar_12_non_zenodo_classification.py`)** | 24 passed (5.92s) | **27 passed** (8.91s) | **+3 passed, 0 failed** |
| **Frontend Vitest Suite (`npm run test`)** | 87 passed, 16 suites (27.24s) | **87 passed, 16 suites** (27.18s) | **0 failed** |
| **Frontend Production Build (`vite build`)** | Passed (9.12s) | **Passed** (8.94s, 730 modules) | **Clean (0 errors)** |
| **ONNX Runtime CPU Inference** | 84 passed (16.93s) | **84 passed** (16.45s) | **0 failed** |

---

## 3. Targeted Regression Verification Results

### 3.1 Confidence Value Derivation & Diversity

- **Test:** `TestConfidenceIntegrityAndDiversity::test_csiro_images_produce_distinct_physically_derived_confidences`  
  **Result:** **PASSED**  
  Asserts that all 6 clean-sea scenes receive distinct, non-identical confidence scores derived from physical backscatter dynamic range, completely eliminating repeated `0.9500` constants.
- **Test:** `TestConfidenceIntegrityAndDiversity::test_dartis_images_produce_distinct_confidences`  
  **Result:** **PASSED**  
  Asserts that lookalike scenes produce distinct confidences derived from their spatial patch morphology and candidate density.
- **Test:** `TestConfidenceIntegrityAndDiversity::test_heuristic_scores_are_never_labeled_as_calibrated_probabilities`  
  **Result:** **PASSED**  
  Guarantees that classical detector scores are tagged as `score_type: "heuristic_score"` and never report `is_calibrated: true`.

---

### 3.2 Content-Based Classification Integrity (Zero Path Spoofing)

- **Test:** `test_adversarial_oil_like_filename_is_classified_by_content_not_name`  
  **Method:** A clean-sea CSIRO scene was placed inside an adversarial directory named `confirmed_crude_oil_spill_catastrophe` with filename `massive_crude_oil_spill_verified.jpg`.  
  **Result:** **PASSED** (`oil_status: NO_OIL`, `confidence: 0.9207`). The classification pipeline ignores path strings and operates strictly on backscatter physics.
- **Test:** `test_adversarial_clean_sea_filename_on_lookalike`  
  **Method:** A DARTIS lookalike was renamed to `absolutely_clean_sea_surface_0_oil.jpg`.  
  **Result:** **PASSED** (`oil_status: LOOKALIKE`, `confidence: 0.6679`).

---

### 3.3 Attribution Safety Gating on Lookalikes & Clean Sea

- **Test:** `test_csiro_end_to_end_investigation` & `test_dartis_end_to_end_investigation_abtains_attribution`  
  **Result:** **PASSED**  
  - For all clean-sea scenes: `attribution_status = "ABSTAINED_EMPTY_MASK"`, 0 candidate vessels ranked, 0 drift tracks generated.
  - For all lookalike scenes: `attribution_status = "ABSTAINED_LOOKALIKE"`, candidate ranking safely withheld, explanatory reasoning provided.

---

### 3.4 Genuine Oil Spill Detection & Attribution Preservation

- **Classical Detector Benchmark (`demo_oil_scene_01.tif`):**  
  **Result:** `oil_status: LIKELY_OIL`, `confidence: 0.7620`, `attribution_status: ATTRIBUTION_PERMITTED`.
- **Deep Learning Segmenter Benchmark (`demo_oil_scene_03.tif`):**  
  **Result:** `oil_status: LIKELY_OIL`, `actual_detector: ml_real_spillunet`, `attribution_status: ATTRIBUTION_PERMITTED`.
- **Synthetic Offline Scenario (`POST /api/spill/detect?mode=demo`):**  
  **Result:** `oil_status: LIKELY_OIL`, `confidence: 0.7801`, 3 candidate vessels attributed with explainable evidence breakdown.

---

### 3.5 Malformed & Unsupported Input Handling

- **Test:** `test_classical_classification.py::test_invalid_inputs`  
  - `None` input array: Handled gracefully (`confidence: 0.0`, `oil_status: UNCERTAIN`, `success: false`).
  - Empty array ($0 \times 0$): Handled gracefully (`confidence: 0.0`).
  - Arrays containing `NaN` or `+Inf`/`-Inf`: Caught by input guards without crashing or leaking exceptions.
  - 3D/4D multi-channel rasters: Correctly checked; non-SAR channels rejected with actionable HTTP 400.
  - Corrupt file offsets: Redacted user-friendly HTTP 422 without exposing server-side temporary file paths.

---

## 4. Production Build Verification

```text
> frontend@0.0.0 build
> vite build

vite v8.2.2 building client environment for production...
transforming...
✓ 730 modules transformed.
rendering chunks...
computing gzip size...
dist/index.html                             2.38 kB │ gzip:   1.01 kB
dist/assets/vendor-leaflet-vh-t_kPv.css    15.09 kB │ gzip:   6.36 kB
dist/assets/index-CYcflipf.css             55.29 kB │ gzip:   9.84 kB
dist/assets/rolldown-runtime-hePW80VL.js    0.71 kB │ gzip:   0.42 kB
dist/assets/vendor-marked-iQpT2Gzx.js      42.88 kB │ gzip:  12.76 kB
dist/assets/vendor-axios-CenCkXay.js       49.96 kB │ gzip:  18.75 kB
dist/assets/vendor-leaflet-BBYLzcii.js    154.85 kB │ gzip:  45.25 kB
dist/assets/index-ouKOtbQe.js             331.96 kB │ gzip:  95.15 kB
dist/assets/vendor-charts-xXPLly6o.js     380.81 kB │ gzip: 110.04 kB

✓ built in 8.94s
```

All JavaScript bundles and CSS style chunks generated cleanly without circular dependency warnings or unresolved imports.
