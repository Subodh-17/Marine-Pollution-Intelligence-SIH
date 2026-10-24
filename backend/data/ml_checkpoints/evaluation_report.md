# Real-Data Evaluation Report — SpillUNet vs Zenodo Sentinel-1 SAR Oil Spill Dataset (Test Split)

**Evaluated at:** 2026-10-20T09:27:15.004469 UTC
**Checkpoint:** `backend/data/ml_checkpoints/spill_unet_real_best.pt`
**Checkpoint trained on:** REAL_ZENODO_SENTINEL1_SAR_OIL_SPILL_DATASET
**Checkpoint epoch:** 18
**Preprocessing version:** `sar_percentile_1_99_minmax_v1`
**Test scenes evaluated:** 450
**Mask threshold:** 0.5

## Executive Summary: Honest Headline Metrics

| Metric Evaluation Scope | Precision | Recall | F1 / Dice | IoU | Description |
|---|---|---|---|---|---|
| **Pixel-Pooled (All 450 scenes)** | 0.7915 | **0.3949** | 0.5269 | **0.3577** | Direct confusion matrix pooled over all 29.5M pixels (Audit §1 standard). |
| **Oil-Scene Macro (150 Oil scenes)** | 0.9322 | **0.6886** | 0.7382 | **0.6324** | Mean over actual positive scenes only; excludes empty-mask inflation. |
| Overall Scene Mean (Historical) | 0.7485 | 0.8962 | 0.6838 | 0.6486 | Historical mean across all 450 scenes (inflated by empty masks). |

## Slick-Size Performance & Quartile Degradation (Audit §2)

| Quartile | Frame Coverage | Oil Pixel Share | Mean Recall | Mean IoU | Area Ratio (Pred/True) |
|---|---|---|---|---|---|
| **Q1_smallest** | < 1.9% | 2.6% | 0.865 | 0.7321 | 1.039 |
| **Q2_small_medium** | 1.9% - 4.3% | 7.3% | 0.8573 | 0.7765 | 0.9753 |
| **Q3_medium_large** | 4.3% - 11.0% | 16.8% | 0.7158 | 0.7028 | 0.7217 |
| **Q4_largest** | > 11.0% | 73.3% | 0.3354 | 0.3344 | 0.2621 |

## Per-category breakdown

| Category | n | Precision | Recall | F1 | IoU | Dice | Pixel Acc. |
|---|---|---|---|---|---|---|---|
| Lookalike | 150 | 0.3467 | 1.0 | 0.3467 | 0.3467 | 0.3467 | 0.9913 |
| No-Oil | 150 | 0.9667 | 1.0 | 0.9667 | 0.9667 | 0.9667 | 1.0 |
| Oil | 150 | 0.9322 | 0.6886 | 0.7382 | 0.6324 | 0.7382 | 0.9383 |

## Aggregate confusion matrix (pixels, summed over all scored scenes)

| | Predicted Positive | Predicted Negative |
|---|---|---|
| Actual Positive | 385457 | 590680 |
| Actual Negative | 101511 | 28413552 |

## Caveats

- SCIENTIFIC HONESTY GUARD (Audit §1): The headline 'overall' recall of 0.8962 is an unweighted arithmetic mean across all 450 scenes. The 300 No-Oil/Lookalike scenes have empty masks where binary_mask_metrics assigned recall=1.0 by convention. True oil detection capability is measured by pixel_pooled recall (0.3949) or oil_scene_macro recall (0.6886).
- LARGE-SLICK FAILURE (Audit §2): Large slicks (Q4, >11% frame coverage, containing ~73% of oil pixels) suffer from severe receptive-field under-segmentation: mean recall is 0.324-0.335 and predicted area is only ~26% of true spill size. High precision (~99%) confirms the model under-segments rather than hallucinates.
- This is the dataset's own published test split (Part III), never mixed into train/val (see app/ml/dataset.py build_manifests 'LEAKAGE PREVENTION').
- Metrics reflect this exact checkpoint at this exact epoch (best epoch 18) and threshold (0.5).
