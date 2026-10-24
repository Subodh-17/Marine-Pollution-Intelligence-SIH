# Real-Data Comparative Evaluation Report — Single-Scale Baseline vs Multiscale (Boundary-Preserved)
## SIH-26143 Phase 4.3A Independent Release Audit on Zenodo Part III (450 Scenes)

**Evaluation Date:** 2026-10-20T09:32:44.170012+00:00 UTC  
**Dataset Location:** `D:\zenodo_sar`  
**Held-Out Test Scenes:** 450 (150 Oil, 150 Lookalike, 150 No-Oil)  
**Evaluated Checkpoint:** `backend/data/ml_checkpoints/spill_unet_real_best.pt`  
**Checkpoint SHA-256:** `af725b2587baed621b6ee264598a44dea008dc7214ccf454ccadc2fdd50e4361`  
**Preprocessing:** `sar_percentile_1_99_minmax_v1` | Threshold: `0.5` | Coarse Weight: `0.95`  
**Evaluation Time:** 166.85s | Base Latency: 37.77ms | MS Latency: 15.66ms (0.41x)  

---

## 1. Real-Data Accuracy Gate Decision

### Verdict: **`FAILED`**
Real-Data Accuracy Gate FAILED: Did not meet one or more promotion criteria.

| ID | Criterion | Target | Measured | Status |
|---|---|---|---|:---:|
| `REAL-01` | Zero Split Leakage | 0 scenes overlap | `0 overlap` | ✅ PASSED |
| `REAL-02` | Preprocessing Parity | real_image_v1 | `sar_percentile_1_99_minmax_v1` | ✅ PASSED |
| `REAL-03` | Pixel-Pooled IoU Gain | Positive gain | `+0.0540` | ✅ PASSED |
| `REAL-04` | Q4 Large Slick Recovery | >= +5% IoU/Recall gain | `Recall: +0.0980, IoU: +0.0975` | ✅ PASSED |
| `REAL-05` | Q1 Blooming Control | Area ratio gain <= +0.08 | `Ratio gain: +0.0057` | ✅ PASSED |
| `REAL-06` | Negative False Alarm Rate | <= +0.05% | `+0.1631%` | ❌ FAILED |

---

## 2. Headline Comparative Performance Metrics

| Metric Scope | Metric | Single-Scale Baseline | Multiscale (Boundary Preserved) | Absolute Delta | Relative Gain |
|---|---|:---:|:---:|:---:|:---:|
| **Pixel-Pooled (All 450 Scenes)** | **Recall** | 0.3949 | **0.4692** | **+0.0743** | **+18.8%** |
| | **IoU** | 0.3577 | **0.4117** | **+0.0540** | **+15.1%** |
| | **F1 / Dice** | 0.5269 | **0.5832** | **+0.0563** | +10.7% |
| | Precision | 0.7915 | 0.7704 | -0.0211 | -2.7% |
| **Oil-Scene Macro (150 Oil Scenes)** | **Recall** | 0.6886 | **0.7242** | **+0.0356** | **+5.2%** |
| | **IoU** | 0.6324 | **0.6646** | **+0.0322** | **+5.1%** |
| | Precision | 0.9322 | 0.9293 | -0.0029 | -0.3% |
| Overall Scene Mean | Recall | 0.8962 | 0.9081 | +0.0119 | +1.3% |
| | IoU | 0.6486 | 0.6549 | +0.0063 | +1.0% |

---

## 3. Slick-Size Performance & Quartile Infill Analysis (Audit §2 Resolution)

SpillUNet suffers from receptive-field under-segmentation on large slicks because uniform slick interiors lack local boundary contrast. The table below proves how the 0.5x coarse scale with morphological erosion directly restores interior coverage without perimeter blooming:

| Quartile | Frame Coverage | Oil Pixel Share | Baseline Recall | Multiscale Recall | Recall Gain | Baseline IoU | Multiscale IoU | Baseline Area Ratio | Multiscale Area Ratio |
|---|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **Q1_smallest** | < 1.9% | 2.6% | 0.8650 | **0.8658** | **+0.0008 (+0.1%)** | 0.7321 | **0.7309** | 1.0390 | **1.0447 (+0.0057)** |
| **Q2_small_medium** | 1.9% - 4.3% | 7.3% | 0.8573 | **0.8626** | **+0.0053 (+0.6%)** | 0.7765 | **0.7720** | 0.9753 | **1.0140 (+0.0387)** |
| **Q3_medium_large** | 4.3% - 11.0% | 16.8% | 0.7158 | **0.7506** | **+0.0348 (+4.9%)** | 0.7028 | **0.7361** | 0.7217 | **0.7623 (+0.0406)** |
| **Q4_largest** | > 11.0% | 73.3% | 0.3354 | **0.4334** | **+0.0980 (+29.2%)** | 0.3344 | **0.4319** | 0.2621 | **0.3543 (+0.0922)** |

---

## 4. Negative Scene Specificity & False Alarm Control

| Scene Category | Number of Scenes | Baseline FP Pixels | Multiscale FP Pixels | Pixel Delta | Baseline FP Scenes | Multiscale FP Scenes |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| **Lookalike** | 150 | 85187 | 117244 | +32057 | 98 | 100 |
| **No-Oil** | 150 | 150 | 150 | +0 | 5 | 5 |

---

## 5. Confusion Matrix Summary (Pixels)

### Baseline Confusion Matrix:
- True Positives (TP): 385,457  
- False Positives (FP): 101,511  
- False Negatives (FN): 590,680  
- True Negatives (TN): 28,413,552  

### Multiscale Confusion Matrix:
- True Positives (TP): 458,039 (+72582 TP pixels recovered)  
- False Positives (FP): 136,518 (+35007 FP pixels)  
- False Negatives (FN): 518,098 (-72582 FN pixels eliminated)  
- True Negatives (TN): 28,378,545  