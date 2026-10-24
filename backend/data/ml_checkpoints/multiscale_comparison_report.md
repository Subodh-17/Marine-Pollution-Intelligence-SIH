# Multiscale Segmentation Validation & Three-Tier Release Gate Report

**Evaluated at:** 2026-10-20T09:36:14.541411+00:00  
**Engine Runtime:** onnxruntime_cpu  
**Promoted Checkpoint Hashes:**
- Classifier: `sha256:dc68400ca1b54116e75dc900afb63ac5f0cb48db452892c0ac8e401c44fdf6b7`
- Segmenter: `sha256:da94d5af9b68155a883365b8a2eb615cc296f12df1926c4f612ea1bcb7656d8c`

---

## 1. Three-Tier Release Gate Verdicts

| Gate | Verdict | Scope | Summary |
|---|:---:|---|---|
| **1. Controlled Engineering Test Gate** | **PASSED** | 9 Controlled Diagnostic Cases | Mathematical correctness, boundary blooming elimination, and numerical stability verified. |
| **2. Real-Data Accuracy Gate** | **FAILED** | Zenodo Part III (450 Scenes) | Real-Data Accuracy Gate FAILED: Did not meet one or more promotion criteria. |
| **3. Demonstration Readiness Gate** | **CONDITIONALLY_READY (EXPERIMENTAL DEMONSTRATION FEATURE)** | Live Command Center Demo | Multiscale active as an experimental feature with verified zero-risk rollback route. |

### Real-Data Accuracy Gate Criteria Breakdown:

| ID | Criterion | Target | Measured | Status |
|---|---|---|---|:---:|
| `REAL-01` | Zero Split Leakage | 0 scenes overlap | `0 overlap` | ✅ PASSED |
| `REAL-02` | Preprocessing Parity | real_image_v1 | `sar_percentile_1_99_minmax_v1` | ✅ PASSED |
| `REAL-03` | Pixel-Pooled IoU Gain | Positive gain | `+0.0540` | ✅ PASSED |
| `REAL-04` | Q4 Large Slick Recovery | >= +5% IoU/Recall gain | `Recall: +0.0980, IoU: +0.0975` | ✅ PASSED |
| `REAL-05` | Q1 Blooming Control | Area ratio gain <= +0.08 | `Ratio gain: +0.0057` | ✅ PASSED |
| `REAL-06` | Negative False Alarm Rate | <= +0.05% | `+0.1631%` | ❌ FAILED |

---

## 2. Controlled Scientific Benchmarks (`evidence_type: CONTROLLED_SYNTHETIC_BENCHMARK`)

*Notice: These cases evaluate mathematical behavior under controlled conditions. They are NEVER combined with real-world accuracy scores.*

| Case ID | Description | GT Pixels | Baseline Recall | Direct Max Recall | Boundary Preserved Recall | Blooming FP Regression | Gate Status |
|---|---|:---:|:---:|:---:|:---:|:---:|:---:|
| `CASE_01` | Clean Calm Sea | 0 | 1.0 (0 FP) | 1.0 (0 FP) | **1.0 (0 FP)** | 0.0% | ✅ PASSED |
| `CASE_02` | Small Slick (314 px) | 314 | 1.0 (60 FP) | 1.0 (85 FP) | **1.0 (60 FP)** | **0.0% (vs +68.8% direct)** | ✅ PASSED |
| `CASE_03` | Large Slick (32,400 px) | 32,400 | 0.395 (Hollow) | 1.000 (Infilled) | **1.000 (100% Infilled)** | 0.0% | ✅ PASSED |
| `CASE_04` | Fragmented Slicks (4 pcs) | 1,808 | 1.0 (4 comps) | 1.0 (4 comps) | **1.0 (4 comps)** | 0.0% | ✅ PASSED |
| `CASE_05` | Narrow Filament (4 px) | 560 | 1.0 (280 FP) | 1.0 (280 FP) | **1.0 (280 FP)** | 0.0% | ✅ PASSED |
| `CASE_06` | Boundary Corner Slick | 2,500 | 1.0 | 1.0 | **1.0 (Exact shape)** | 0.0% | ✅ PASSED |
| `CASE_07` | Dark Lookalike (P=0.35) | 0 | 1.0 (0 FP) | 1.0 (0 FP) | **1.0 (0 FP)** | 0.0% | ✅ PASSED |
| `CASE_08` | Non-Square Odd (317x429) | N/A | Preserved | Preserved | **Preserved [317, 429]** | N/A | ✅ PASSED |
| `CASE_09` | Non-Finite Input (NaNs) | N/A | Clamped | Clamped | **Clamped to 0.0** | N/A | ✅ PASSED |

---

## 3. Unlabeled Demonstration Scenes (`evidence_type: UNLABELED_SAMPLE_SCENE`)

| Scene | Dimensions | Baseline Latency | Multiscale Latency | Overhead | Fallback State |
|---|---|:---:|:---:|:---:|:---:|
| `demo_lookalike_scene_01.tif` | [512, 512] | 503.95 ms | 578.82 ms | 1.15x | `None` |
| `demo_no_oil_scene_01.tif` | [512, 512] | 399.51 ms | 474.81 ms | 1.19x | `None` |
| `demo_oil_scene_01.tif` | [512, 512] | 394.22 ms | 479.13 ms | 1.22x | `None` |
| `demo_oil_scene_02.tif` | [512, 512] | 392.35 ms | 477.24 ms | 1.22x | `None` |

---

## 4. Rollback Configuration & Verification

To revert immediately to baseline single-scale execution:
```bash
# Method A: Disable multiscale entirely
export ML_MULTISCALE_SEGMENTATION=false

# Method B: Switch fusion strategy to single-scale baseline only
export ML_MULTISCALE_FUSION_STRATEGY=base_only
```
Verified by unit test `test_base_only_strategy_guarantees_exact_single_scale_rollback`:
Output is bitwise identical to single-scale baseline without coarse pass execution.