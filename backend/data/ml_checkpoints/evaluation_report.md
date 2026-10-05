# Real-Data Evaluation Report — SpillUNet vs Zenodo Sentinel-1 SAR Oil Spill Dataset (Test Split)

**Evaluated at:** 2026-09-09T14:05:59.059420 UTC  
**Checkpoint:** `backend/data/ml_checkpoints/spill_unet_real_best.pt`  
**Checkpoint trained on:** REAL_ZENODO_SENTINEL1_SAR_OIL_SPILL_DATASET  
**Checkpoint epoch:** 18  
**Preprocessing version:** `sar_percentile_1_99_minmax_v1`  
**Test scenes evaluated:** 450  
**Mask threshold:** 0.5

## Overall (all test scenes)

| Metric | Value |
|---|---|
| precision | 0.7485 |
| recall | 0.8962 |
| f1 | 0.6838 |
| iou | 0.6486 |
| dice | 0.6838 |
| pixel_accuracy | 0.9765 |
| n_scenes | 450 |

## Per-category breakdown

| Category | n | Precision | Recall | F1 | IoU | Dice | Pixel Acc. |
|---|---|---|---|---|---|---|---|
| Lookalike | 150 | 0.3467 | 1.0 | 0.3467 | 0.3467 | 0.3467 | 0.9913 |
| No-Oil | 150 | 0.9667 | 1.0 | 0.9667 | 0.9667 | 0.9667 | 1.0 |
| Oil | 150 | 0.9322 | 0.6886 | 0.7382 | 0.6324 | 0.7382 | 0.9383 |

## Aggregate confusion matrix (pixels, summed over all scored scenes)

| | Predicted Positive | Predicted Negative |
|---|---|---|
| Actual Positive | 385466 | 590671 |
| Actual Negative | 101526 | 28413537 |

## Caveats

- This is the dataset's own published test split (Part III), never mixed into train/val (see app/ml/dataset.py build_manifests 'LEAKAGE PREVENTION').
- Metrics are only as good as the mask threshold used (0.5 default, see --threshold) and reflect this exact checkpoint at this exact epoch - re-evaluate after further training.
- Per-category numbers for No-Oil/Lookalike scenes measure false-positive behavior (their ground truth is an all-zero mask), not detection recall - a low IoU/Dice there without an accompanying pixel_accuracy check should be read as noisy on an all-negative scene, not as a missed detection.
