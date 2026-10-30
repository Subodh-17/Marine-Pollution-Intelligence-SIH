# SEGMENTATION FAILURE AND GENERALIZATION REPORT
**Marine Pollution Intelligence — Scientific Audit of SpillUNet Generalization & Receptive Field Limits**
**Date:** 2026-10-30 | **Status:** Audited & Disclosed

---

## 1. Executive Summary

This report documents the deep-dive failure analysis of the promoted **SpillUNet** semantic segmentation model on the held-out Zenodo Part III test dataset ($N = 450$ scenes). 

While SpillUNet exhibits high precision (**93.22%**) and excellent recall (**>85%**) on small-to-medium oil slicks, it suffers from a pronounced and reproducible **receptive-field failure on large contiguous oil slicks** (Quartile 4). Rather than masking this phenomenon behind an aggregate metric, this report documents the empirical evidence, root cause, mitigation trade-offs, and resulting operational safeguards.

---

## 2. Quartile-by-Quartile Slick Size Breakdown

To isolate the boundary conditions of SpillUNet, all $N = 150$ genuine oil scenes in the held-out test split were partitioned into four equal quartiles based on the true pixel area of the slicks:

| Slick Size Quartile | Scene Count | Frame Coverage (% of $256 \times 256$) | Share of Total Oil Pixels | Mean Pixel Recall | Mean IoU | Pooled Precision | Predicted-to-True Area Ratio |
|---|---|---|---|---|---|---|---|
| **Q1 (Smallest)** | 37 | $< 1.9\%$ | 2.58% | **86.50%** | **0.7321** | 85.17% | **1.0390** |
| **Q2 (Small-Medium)** | 37 | $1.9\% - 4.3\%$ | 7.27% | **85.73%** | **0.7765** | 87.47% | **0.9753** |
| **Q3 (Medium-Large)** | 38 | $4.3\% - 11.0\%$ | 17.32% | **73.69%** | **0.6908** | 90.12% | **0.7812** |
| **Q4 (Large Slicks)** | 38 | $> 11.0\%$ | **72.83%** | **33.54%** | **0.3245** | **94.88%** | **0.2621** |

### Key Empirical Findings:
1. **Q1 and Q2 Excellence:** For compact and localized slicks ($< 4.3\%$ frame coverage), SpillUNet performs exceptionally well. Slicks are identified with $>85\%$ recall, and the predicted area matches ground truth almost exactly (area ratios of $1.039$ and $0.975$).
2. **Q4 Severe Under-Segmentation:** For large slicks ($> 11\%$ frame coverage), pixel recall plummets to **33.54%**, and the model predicts only **26.21%** of the true slick area. Because Q4 slicks contain $72.83\%$ of all oil pixels in the test dataset, this localized failure pulls down the unweighted overall pixel-pooled recall to **39.49%**.

---

## 3. Scientific Root Cause Analysis

### 3.1 Local Receptive Field vs. Global Homogeneity
SpillUNet is trained with fixed $256 \times 256$ patches. Its standard convolutional architecture relies on local contrast between the dark slick surface and the surrounding bright, capillary-wave-roughened sea surface. 
- In small slicks, almost every pixel is within the kernel receptive field of a slick-water boundary.
- In sprawling Q4 slicks, the interior of the slick covers entire receptive windows with uniform, low-backscatter values. The network lacks the global spatial context to differentiate a massive oil slick interior from naturally low-backscatter sea regions (such as zero-wind calm zones or radar shadow).

### 3.2 False Positive Trade-Off on Natural Lookalikes
Natural lookalikes (algal blooms, biogenic slicks, grease ice, and internal waves) also create low-backscatter regions. SpillUNet's high training penalty on false positives caused it to favor conservative boundary predictions, leading to interior "hollowing" on massive slicks.

---

## 4. Multi-Scale Tiling Evaluation & Decision Record

To address Q4 under-segmentation, an experimental multi-scale segmentation pipeline was developed (`backend/app/ml/tiling.py:stitch_multiscale_segmentation`) combining a downsampled full-scene coarse pass with full-resolution patch passes.

### 4.1 Comparative Benchmark Results

| Metric | Single-Scale SpillUNet (Production) | Multi-Scale Pipeline (Experimental) | Delta | Safety Threshold |
|---|---|---|---|---|
| **Q4 Large Slick Recall** | 33.54% | **58.20%** | +24.66% | Improved |
| **Overall Oil Macro IoU** | 0.6324 | **0.6710** | +0.0386 | Improved |
| **Lookalike False Positive Rate** | **3.80%** | **8.74%** | +4.94% | **VIOLATION (> 4.5% Gate)** |
| **Lookalike FP Pixels** | 85,187 | **196,440** | +130.6% | Severe False Alarm Risk |

### 4.2 Engineering Decision: FAIL-CLOSED GATE
> [!CAUTION]
> **Production Policy:** Because maritime law enforcement requires minimizing false accusations against innocent vessels, the system enforces a strict **False Positive Gate ($\le 4.5\%$ Lookalike FPR)**. Because the multi-scale pipeline elevated Lookalike FPR to **8.74%**, it was rejected for automated production activation and remains disabled (`ml_multiscale_segmentation: bool = False`).

---

## 5. UI Transparency & Operational Warning Safeguard

To ensure maritime operational safety without fabricating metrics, the platform implements runtime transparency:
1. **Slick Area Warning:** Whenever a segmented slick exceeds $10\%$ of the scene area, an advisory notification informs the analyst:
   > *"Notice: Large continuous slick detected (>10% scene coverage). SpillUNet's localized receptive field may underestimate total slick perimeter. Analytical area represents confirmed core slick."*
2. **Dual-Model Verification:** The system presents SceneClassifierV2's scene-level classification alongside SpillUNet's pixel map, alerting the user that high classification confidence ($>90\%$) combined with partial segmentation indicates a sprawling slick.
