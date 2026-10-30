# FINAL MODEL PERFORMANCE AND CLASSICAL DETECTOR BENCHMARK
**Marine Pollution Intelligence — Smart India Hackathon Defensible Performance Benchmark**
**Date of Audit:** 2026-10-30 | **Status:** Defensible, Verified & Reconciled Against Ground Truth

---

## 1. Summary of Model Components & Provenance

The Marine Pollution Intelligence platform integrates three analytical engines for satellite SAR oil spill analysis:

1. **SceneClassifierV2 (Deep Learning Classifier):** 295,107 parameters, trained on Zenodo Sentinel-1 SAR Oil Spill Dataset (Parts I & II, 7,163 scenes), evaluated on the completely held-out Zenodo Part III test set ($N = 450$ scenes, perfectly balanced: 150 Oil, 150 No-Oil, 150 Lookalike).
2. **SpillUNet (Deep Learning Semantic Segmenter):** Fully convolutional U-Net with skip connections, trained to epoch 18 on real Sentinel-1 SAR imagery, operating at 256×256 patch resolution.
3. **Classical SAR Dark-Spot Detector:** Deterministic image processing pipeline utilizing adaptive thresholding, morphological filtering, border contrast gradients, and shape compactness.

---

## 2. SceneClassifierV2 Performance Benchmark

### 2.1 Authoritative Production Benchmark (ONNX Runtime CPU)
In production on Render, inference executes via **ONNX Runtime CPU**. Below are the exact metrics computed from cached model logits across the $N = 450$ held-out Part III test scenes:

- **Overall Accuracy:** **79.33%** (357 / 450 correct predictions)
- **95% Confidence Interval (Wilson Score):** **[75.35%, 82.82%]**
- **Balanced Accuracy:** **79.33%**
- **Macro Precision:** 0.8105 | **Macro Recall:** 0.7933 | **Macro F1:** **0.7882**
- **Weighted F1 Score:** **0.7882**
- **Macro ROC-AUC (One-vs-Rest):** **0.9360**
- **Negative Log-Likelihood (NLL):** 0.5274
- **Expected Calibration Error (ECE):** **0.0866** (Platt temperature scaling $T = 1.0879$, fitted on validation split)
- **Oil False Alarm Rate:** **1.67%** (5 false positive oil predictions across 300 non-oil scenes)

### 2.2 Confusion Matrix (ONNX Runtime, $N = 450$)

| True Class \ Predicted Class | Predicted: Oil | Predicted: No-Oil | Predicted: Lookalike | Total (Support) |
|---|---|---|---|---|
| **True Oil** | **130** | 3 | 17 | 150 |
| **True No-Oil** | 0 | **143** | 7 | 150 |
| **True Lookalike** | 5 | 61 | **84** | 150 |
| **Total Predicted** | 135 | 207 | 108 | 450 |

### 2.3 Detailed Per-Class Breakdown

| Class | Support | TP | FP | FN | Precision | Recall | F1-Score | Specificity |
|---|---|---|---|---|---|---|---|---|
| **Oil** | 150 | 130 | 5 | 20 | **96.30%** | **86.67%** | **0.9123** | 98.33% |
| **No-Oil** | 150 | 143 | 64 | 7 | **69.08%** | **95.33%** | **0.8011** | 78.67% |
| **Lookalike** | 150 | 84 | 24 | 66 | **77.78%** | **56.00%** | **0.6512** | 92.00% |

### 2.4 Reconciliation: PyTorch vs. ONNX Runtime Floating-Point Variance
An audit of historical reports identified two figures: **78.44%** and **79.33%**. 

**Scientific Root Cause:**
Both figures evaluate the exact same training checkpoint (`classifier_real_promoted.pth`, SHA-256: `0f587cb7...`):
- **PyTorch Runtime (32-bit CUDA/CPU):** Evaluated at 353 / 450 = **78.44%** accuracy.
  - Oil FP: 6, Lookalike TP: 80.
- **ONNX Runtime (Optimized CPU Graph):** Evaluated at 357 / 450 = **79.33%** accuracy.
  - Oil FP: 5, Lookalike TP: 84.
- Across 4 borderline scenes, minor sub-threshold floating-point rounding shifts the softmax argmax. Because ONNX Runtime is the runtime actively executing on production Render servers, **79.33%** is established as the authoritative figure. Both figures are transparently recorded in the application metadata.

---

## 3. SpillUNet Semantic Segmentation Benchmark

The SpillUNet model segments oil slicks at pixel level. Evaluated on the $N = 450$ held-out Part III scenes at standard decision threshold $0.50$:

### 3.1 Macro & Pooled Performance Metrics

| Evaluation Mode | Scope | Precision | Recall | F1 / Dice | IoU | Pixel Accuracy |
|---|---|---|---|---|---|---|
| **Oil Scenes Macro** | $N = 150$ scenes containing oil | **93.22%** | **68.86%** | **0.7382** | **0.6324** | **93.83%** |
| **Pixel Pooled** | All $N = 450$ test scenes (all pixels) | **79.15%** | **39.49%** | **0.5269** | **0.3577** | **97.65%** |

### 3.2 Aggregate Confusion Matrix (Pixels)
- **True Positives (TP):** 385,457 pixels
- **False Positives (FP):** 101,511 pixels (16,174 in Oil scenes; 85,187 in Lookalike scenes; 150 in No-Oil scenes)
- **False Negatives (FN):** 590,680 pixels
- **True Negatives (TN):** 28,413,552 pixels

### 3.3 Large-Slick Receptive Field Limitation
- For small and medium slicks (Q1 & Q2, $< 4.3\%$ frame coverage), the model achieves high recall (**86.5%** and **85.7%**) with mean IoU $> 0.73$.
- For large slicks (Q4, $> 11.0\%$ frame coverage, representing ~73% of all oil pixels), pixel recall drops to **33.54%** and predicted area ratio is **0.2621** (severe under-segmentation). This is an intrinsic architectural limitation of the fixed 256×256 receptive field when encountering sprawling slicks spanning the entire SAR swath.

---

## 4. Classical SAR Detector Benchmark & Operational Status

The classical detector (`backend/app/satellite/detection.py`) provides a robust, non-neural dark-spot segmentation alternative.

### 4.1 Benchmark Status: NOT ESTABLISHED
> [!IMPORTANT]
> **Explicit Disclosure:** The classical SAR detector has **NOT** been evaluated against labeled ground-truth masks on the 450-scene held-out Zenodo test split. Its statistical accuracy, precision, and recall are unbenchmarked.

### 4.2 Mathematical Methodology
The classical detector computes an uncalibrated confidence score $S \in [0.0, 1.0]$ based on:
$$S = 0.35 \cdot C_{\text{ratio}} + 0.30 \cdot G_{\text{border}} + 0.20 \cdot \gamma_{\text{compact}} + 0.15 \cdot A_{\text{norm}}$$
Where:
- $C_{\text{ratio}}$ = Dark-spot to surrounding background intensity contrast ratio.
- $G_{\text{border}}$ = Magnitude of border gradient (edge transition sharpness).
- $\gamma_{\text{compact}}$ = Isoperimetric quotient ($4\pi \cdot \text{Area} / \text{Perimeter}^2$).
- $A_{\text{norm}}$ = Normalized spot area.

### 4.3 Operational Safeguards
1. **Prominent UI Badge:** The UI explicitly displays the **HEURISTIC SCORE** badge whenever the classical detector executes.
2. **Provenance Distinction:** The system separates classical heuristic scores from ML calibrated probabilities in all API responses and intelligence dossiers.

---

## 5. Artifact Hashes & Dataset DOIs

- **Promoted PyTorch Classifier:** `backend/data/ml_checkpoints/classifier_real_promoted.pth` (SHA-256: `0f587cb7604047247424bf7a79a9f07ae0ab66b9e4a3070cf32c2c1957fed1ee`)
- **Promoted ONNX Classifier:** `backend/data/ml_checkpoints/classifier_real_best.onnx` (SHA-256: `dc68400ca1b54116e75dc900afb63ac5f0cb48db452892c0ac8e401c44fdf6b7`)
- **Promoted ONNX Segmenter:** `backend/data/ml_checkpoints/spill_unet_real_best.onnx` (SHA-256: `da94d5af9b68155a883365b8a2eb615cc296f12df1926c4f612ea1bcb7656d8c`)
- **Zenodo Sentinel-1 Dataset DOIs:**
  - Part I (Training): `10.5281/zenodo.8346860`
  - Part II (Training & Validation): `10.5281/zenodo.8253899`
  - Part III (Held-Out Test Set): `10.5281/zenodo.13761290`
