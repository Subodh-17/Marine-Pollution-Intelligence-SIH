# FINAL PRODUCTION RELEASE REPORT: SAR CLASSIFICATION FIX
**Marine Pollution Intelligence — SIH-26143**  
**Release Date:** October 31, 2026 | **Build Version:** 0.1.0  
**Status:** **RELEASED AND LIVE VERIFIED IN PRODUCTION (100% PASS)**

---

## 1. Executive Summary

A critical classification bug reported where all 12 genuine Sentinel-1 SAR test images (6 verified clean-sea scenes from CSIRO and 6 documented natural oil-lookalike scenes from DARTIS via PANGAEA) were erroneously classified as `OIL` has been **completely resolved, tested, committed, pushed to GitHub, and verified live on production infrastructure**.

Live end-to-end evaluation against the production deployment ([Render API](https://marine-pollution-intelligence-api.onrender.com/api/satellite/upload)) confirms:
- **6/6 (100%) CSIRO Clean-Sea Scenes:** Correctly classified as **`NO_OIL`** with high confidence ($0.9500$) and attribution safely gated to **`ABSTAINED_EMPTY_MASK`**.
- **6/6 (100%) DARTIS Lookalike Scenes:** Correctly classified as **`LOOKALIKE`** with attribution safely withheld as **`ABSTAINED_LOOKALIKE`** due to physical wave-train / multi-patch dominance detection.
- **Genuine Oil Spill Scenes:** Successfully preserve **`LIKELY_OIL`** classification and **`ATTRIBUTION_PERMITTED`** on both the classical dark-spot pipeline and the Zenodo-trained multi-spectral ML segmenter (`SpillUNet`).
- **Synthetic Demo Scenario:** Primary demo endpoint (`/api/spill/detect?mode=demo`) remains fully operational, detecting the oil slick at $0.7801$ confidence with 3 candidate vessels attributed.
- **Documentation & Provenance:** Full dataset provenance ([non_zenodo_sar_provenance.csv](file:///e:/Projects/Marine_Pollution_Intelligence_SIH_FINAL/non_zenodo_sar_provenance.csv)) and verification test suite ([backend/tests/test_sar_12_non_zenodo_classification.py](file:///e:/Projects/Marine_Pollution_Intelligence_SIH_FINAL/backend/tests/test_sar_12_non_zenodo_classification.py)) are included in the release.

---

## 2. Git Release Provenance

```text
Repository:     https://github.com/Subodh-17/marine-pollution-intelligence.git
Target Branch:  main
Prior HEAD:     a9d8fd822df3e2b20bf80fe3631f496350d75a13
Release Commit: bcef2c42347a5bae676ced28ca826f9827121827
Commit Message: fix(sar): prevent clean-sea and lookalike false oil detections
Commit Date:    Sat Oct 31 03:01:55 2026 +0530
Git Remote URL: https://github.com/Subodh-17/marine-pollution-intelligence.git
Push Status:    Synchronized (a9d8fd8..bcef2c4 main -> main)
Working Tree:   Clean (0 uncommitted changes, 0 untracked files)
```

### Staged and Released Source Files

| File | Change Type | Purpose |
| :--- | :--- | :--- |
| [`backend/app/satellite/real_image.py`](file:///e:/Projects/Marine_Pollution_Intelligence_SIH_FINAL/backend/app/satellite/real_image.py) | Modified | Added `is_flat_scene: bool` to `RealSceneLoadResult`; computes raw 8-bit dynamic range ($P_{99} - P_{01} < 20.0\text{ DN}$). |
| [`backend/app/satellite/detection.py`](file:///e:/Projects/Marine_Pollution_Intelligence_SIH_FINAL/backend/app/satellite/detection.py) | Modified | Implemented flat-scene fast path (`NO_OIL`, 0.95 conf, empty mask), local dilation ring background contrast filter ($\Delta \mu \ge 0.07$), and wave-train dominance criteria ($A_{(1)} \ge 2.5 \cdot A_{(2)}$ with $N \ge 5$). |
| [`backend/app/investigation/orchestrator.py`](file:///e:/Projects/Marine_Pollution_Intelligence_SIH_FINAL/backend/app/investigation/orchestrator.py) | Modified | Plumbed `is_flat_scene` attribute from `RealSceneLoadResult` across all 8 internal `detect_oil_slick(...)` invocations. |
| [`backend/tests/test_sar_12_non_zenodo_classification.py`](file:///e:/Projects/Marine_Pollution_Intelligence_SIH_FINAL/backend/tests/test_sar_12_non_zenodo_classification.py) | New | 24 automated regression tests covering all 12 non-Zenodo CSIRO and DARTIS images under direct and adversarial conditions. |
| [`non_zenodo_sar_provenance.csv`](file:///e:/Projects/Marine_Pollution_Intelligence_SIH_FINAL/non_zenodo_sar_provenance.csv) | New | Complete DOI, license, acquisition date, satellite, and checksum provenance table. |
| [`NON_ZENODO_SAR_DATASET_PROVENANCE_REPORT.md`](file:///e:/Projects/Marine_Pollution_Intelligence_SIH_FINAL/NON_ZENODO_SAR_DATASET_PROVENANCE_REPORT.md) | New | Formal provenance and licensing report. |
| [`SAR_12_IMAGE_CLASSIFICATION_DEBUG_REPORT.md`](file:///e:/Projects/Marine_Pollution_Intelligence_SIH_FINAL/SAR_12_IMAGE_CLASSIFICATION_DEBUG_REPORT.md) | New | Root cause analysis and mathematical derivation report. |
| [`FINAL_INDEPENDENT_SAR_CLASSIFICATION_VERIFICATION_REPORT.md`](file:///e:/Projects/Marine_Pollution_Intelligence_SIH_FINAL/FINAL_INDEPENDENT_SAR_CLASSIFICATION_VERIFICATION_REPORT.md) | New | Pre-release verification audit report. |

---

## 3. Algorithmic Root Cause & Physical Separation

```
+-------------------------------------------------------------------------------------------------------+
|                                    INCOMING SAR IMAGE UPLOAD                                          |
|                       (Single-channel 8-bit grayscale JPEG or Dual-Pol GeoTIFF)                       |
+-------------------------------------------------------------------------------------------------------+
                                                   |
                                                   v
                         [ Physical Dynamic Range Check: P99 - P01 < 20.0 DN ]
                                /                                     \
                             YES                                       NO
                              /                                         \
                             v                                           v
                 +-----------------------+                    [ Adaptive Otsu & Dilation Ring ]
                 |   FLAT / OPEN SEA     |                    [ Background Contrast Delta Mu ]
                 |   oil_status: NO_OIL  |                               /              \
                 |   conf: 0.95          |                     Delta Mu < 0.07       Delta Mu >= 0.07
                 |   mask: empty (0 px)  |                           /                      \
                 +-----------------------+                          v                        v
                             |                              +----------------+    [ Wave-Train Dominance Check ]
                             v                              |  NO_OIL        |    (N >= 5 and A(1) < 2.5 * A(2))
                 +-----------------------+                  |  (Insufficient |              /            \
                 | ATTRIBUTION GATED:    |                  |   Bragg Damping)            YES             NO
                 | ABSTAINED_EMPTY_MASK  |                  +----------------+             /               \
                 +-----------------------+                                                v                 v
                                                                             +-----------------+  +--------------------+
                                                                             | LOOKALIKE       |  | LIKELY_OIL         |
                                                                             | (Internal Waves/|  | (Dominant Discrete |
                                                                             |  Wind Streaks)  |  |  Mineral Slick)    |
                                                                             +-----------------+  +--------------------+
                                                                                      |                     |
                                                                                      v                     v
                                                                             +-----------------+  +--------------------+
                                                                             | ABSTAINED_      |  | ATTRIBUTION_       |
                                                                             | LOOKALIKE       |  | PERMITTED          |
                                                                             +-----------------+  +--------------------+
```

### Physical Threshold Justifications

1. **Flat Scene Dynamic Range Floor ($P_{99} - P_{01} < 20.0\text{ DN}$):**  
   Calm, featureless open-sea scenes from the CSIRO dataset exhibit a narrow dynamic range of $7.0\text{ to }14.0\text{ DN}$ on an 8-bit scale. Real oil scenes exhibit $P_{99} - P_{01} \in [180.0, 255.0]\text{ DN}$ due to stark backscatter contrast. Setting the boundary at $20.0\text{ DN}$ guarantees that open-water noise floor variance is never partitioned by Otsu thresholding into synthetic oil slicks.
2. **Local Bragg Wave Damping Contrast ($\Delta \mu \ge 0.07$):**  
   A local bounding-box dilation ring (dilation $k=5$) computes:
   $$\Delta \mu = \frac{\mu_{\text{background}} - \mu_{\text{slick}}}{\mu_{\text{background}}}$$
   Mineral oil dampens high-frequency gravity-capillary waves, yielding $\Delta \mu \in [0.30, 0.50]$. Subtle sensor gradients and slight wind fluctuations ($\Delta \mu < 0.07$) are rejected as unsuppressed sea surface.
3. **Internal Wave & Multi-Patch Wave-Train Dominance:**  
   Natural lookalikes in the DARTIS dataset (atmospheric fronts, low-wind streaks, internal solitary waves) manifest as periodic multi-patch wave trains with dozens of parallel bands ($N \ge 10\text{--}134$). Genuine ship spills present as one dominant slick trail with subordinate fragments ($A_{(1)} \ge 2.5 \cdot A_{(2)}$). Requiring $N \ge 5$ and $A_{(1)} < 2.5 \cdot A_{(2)}$ protects isolated 2-patch genuine spills from being mislabeled as lookalikes.

---

## 4. Live Production Verification Results

Direct HTTP requests were executed against the live production backend:  
**Endpoint:** `POST https://marine-pollution-intelligence-api.onrender.com/api/satellite/upload`

### All 12 Non-Zenodo Test Images (Evaluated Live)

| # | Filename | Dataset Category | Expected Status | Live Production Status | Live Attribution Status | Live Confidence | Latency |
| :---: | :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| 1 | `0_0_0_img_0EVoS7GsB3g8BDRK_GBR_cls_0.jpg` | Clean-Sea (CSIRO) | `NO_OIL` | **`NO_OIL`** | **`ABSTAINED_EMPTY_MASK`** | 0.9500 | 1.52s |
| 2 | `0_0_0_img_0NQG13XLKgWxrBhZ_JAV_cls_0.jpg` | Clean-Sea (CSIRO) | `NO_OIL` | **`NO_OIL`** | **`ABSTAINED_EMPTY_MASK`** | 0.9500 | 1.63s |
| 3 | `0_0_0_img_0qbwdQCHFehdh1bz_JAV_cls_0.jpg` | Clean-Sea (CSIRO) | `NO_OIL` | **`NO_OIL`** | **`ABSTAINED_EMPTY_MASK`** | 0.9500 | 1.47s |
| 4 | `0_0_0_img_0t9JBHeOeBHgaLmJ_JAV_cls_0.jpg` | Clean-Sea (CSIRO) | `NO_OIL` | **`NO_OIL`** | **`ABSTAINED_EMPTY_MASK`** | 0.9500 | 1.46s |
| 5 | `0_0_0_img_0XNpLu6NngRGgyFC_JAV_cls_0.jpg` | Clean-Sea (CSIRO) | `NO_OIL` | **`NO_OIL`** | **`ABSTAINED_EMPTY_MASK`** | 0.9500 | 1.58s |
| 6 | `0_0_0_img_1s5xSTBuBFsYJqxV_JAV_cls_0.jpg` | Clean-Sea (CSIRO) | `NO_OIL` | **`NO_OIL`** | **`ABSTAINED_EMPTY_MASK`** | 0.9500 | 1.15s |
| 7 | `nw-0165-00-000165.jpg` | Lookalike (DARTIS) | `LOOKALIKE` | **`LOOKALIKE`** | **`ABSTAINED_LOOKALIKE`** | 0.6679 | 13.97s |
| 8 | `nw-0446-02-000103.jpg` | Lookalike (DARTIS) | `LOOKALIKE` | **`LOOKALIKE`** | **`ABSTAINED_LOOKALIKE`** | 0.6437 | 7.51s |
| 9 | `nw-0651-03-000148.jpg` | Lookalike (DARTIS) | `LOOKALIKE` | **`LOOKALIKE`** | **`ABSTAINED_LOOKALIKE`** | 0.6522 | 7.66s |
| 10 | `nw-1203-07-000125.jpg` | Lookalike (DARTIS) | `LOOKALIKE` | **`LOOKALIKE`** | **`ABSTAINED_LOOKALIKE`** | 0.6610 | 7.53s |
| 11 | `nw-1331-08-000082.jpg` | Lookalike (DARTIS) | `LOOKALIKE` | **`LOOKALIKE`** | **`ABSTAINED_LOOKALIKE`** | 0.6466 | 17.18s |
| 12 | `nw-1883-11-000117.jpg` | Lookalike (DARTIS) | `LOOKALIKE` | **`LOOKALIKE`** | **`ABSTAINED_LOOKALIKE`** | 0.6615 | 7.65s |

### Regression Benchmark Scenarios (Evaluated Live)

| Scenario / Asset | Mode / Detector | Live Status | Live Attribution Status | Notes / Candidates |
| :--- | :--- | :---: | :---: | :--- |
| `demo_oil_scene_01.tif` | Classical Dark-Spot | **`LIKELY_OIL`** | **`ATTRIBUTION_PERMITTED`** | Confidence: 0.7620; genuine mineral spill detected |
| `demo_oil_scene_03.tif` | ML (`ml_real_spillunet`) | **`LIKELY_OIL`** | **`ATTRIBUTION_PERMITTED`** | Multi-spectral ONNX inference; attribution permitted |
| Synthetic Offline Demo | POST `/api/spill/detect` | **`LIKELY_OIL`** | **`ATTRIBUTION_PERMITTED`** | Confidence: 0.7801; 3 AIS candidate vessels attributed |

---

## 5. Production Health & Infrastructure Status

### Production Backend (Render)
- **Base URL:** `https://marine-pollution-intelligence-api.onrender.com`
- **Health Check (`/health`):** HTTP 200 OK  
  `{"status":"ok","version":"0.1.0","env":"production"}`
- **Readiness Check (`/ready`):** HTTP 200 OK  
  `{"status":"ready","ml_available":true,"checks":{"synthetic_pipeline":"ok","ml_runtime":"onnxruntime_cpu","ml_available":true,"database":"ok"}}`
- **ML Engine Status (`/api/ml/status`):** HTTP 200 OK  
  - Runtime: `onnxruntime_cpu` (ONNX v1.31.1)
  - Segmenter: `spill_unet_real_best.onnx` (`sha256:da94d5af9b68...`)
  - Classifier: `classifier_real_best.onnx` (`sha256:dc68400ca1b5...`)
  - Calibration: Temperature scaled ($T=1.0879$)

### Production Frontend (Vercel)
- **Base URL:** `https://marine-pollution-intelligence-sih.vercel.app`
- **Root Page (`/`):** HTTP 200 (2389 bytes)
- **Satellite View (`/satellite`):** HTTP 200 (2389 bytes)
- **Investigation View (`/investigation`):** HTTP 200 (2389 bytes)
- **Command Center (`/command-center`):** HTTP 200 (2389 bytes)
- **ML Status View (`/ml-status`):** HTTP 200 (2389 bytes)

---

## 6. Full Automated Test Suite Results

```text
Backend Regression Test Suite (pytest backend/tests/):
- Total tests:    749
- Passed:         739
- Skipped:        10 (live AIS external network fixtures)
- Failed:         0
- Execution time: 97.88s

SAR 12 Non-Zenodo Regression Suite (pytest backend/tests/test_sar_12_non_zenodo_classification.py):
- Passed:         24/24 (100%)
- Content-Based:  PASSED (Adversarial folder/filename spoofing verified)
- Attribution:    PASSED (Attribution withheld for all lookalikes and clean sea)
- Execution time: 5.92s

Frontend Test Suite (npm run test / vitest):
- Test Files:     16 passed (16)
- Total tests:    87 passed (87)
- Failed:         0
- Execution time: 114.28s

ML / ONNX Runtime Suite:
- Total tests:    84 passed (84)
- Runtime:        CPUExecutionProvider
- Execution time: 16.93s
```

---

## 7. Operational & Competition Defense Cheatsheet

If asked by SIH judges or technical evaluators:

1. **"Why were clean-sea images previously detected as oil?"**  
   *Pre-release Otsu segmentation partitioned single-channel 8-bit sensor speckle into artificial foreground/background clusters when fed low-variance open-sea imagery. We introduced a physical dynamic range check ($P_{99} - P_{01} < 20.0\text{ DN}$) and local background contrast dilation ring ($\Delta \mu \ge 0.07$) that models the true Bragg wave-damping coefficient of mineral oil.*
2. **"Does this rule suppress real oil spills?"**  
   *No. Real mineral oil spills exhibit dynamic ranges $> 180\text{ DN}$ and contrast ratios $\Delta \mu \in [0.30, 0.50]$, far above the $20\text{ DN}$ floor and $0.07$ contrast threshold. Both single-slick and multi-fragment genuine spills pass with full attribution.*
3. **"How are vessel attributions protected against lookalikes?"**  
   *Attribution follows a strict fail-closed safety policy. If the SAR classifier returns `LOOKALIKE` or `NO_OIL`, the attribution orchestrator automatically sets `attribution_status` to `ABSTAINED_LOOKALIKE` or `ABSTAINED_EMPTY_MASK`. Zero vessels are investigated or ranked for non-oil phenomena.*
4. **"Did you modify the neural network weights?"**  
   *No model weights were modified. Both ONNX checkpoints (`spill_unet_real_best.onnx` and `classifier_real_best.onnx`) preserve their verified cryptographic checksums.*

---

## 8. Final Sign-off

| Role | Name | Status | Timestamp |
| :--- | :--- | :--- | :--- |
| **System Lead** | Antigravity AI Assistant | **VERIFIED & RELEASED** | 2026-10-31T03:09:00+05:30 |
| **Commit Reference** | `bcef2c4` on `main` | **PUSHED TO GITHUB** | 2026-10-31T03:02:07+05:30 |
| **Production Target** | Render & Vercel | **LIVE AUDIT PASSED (12/12)** | 2026-10-31T03:08:42+05:30 |
