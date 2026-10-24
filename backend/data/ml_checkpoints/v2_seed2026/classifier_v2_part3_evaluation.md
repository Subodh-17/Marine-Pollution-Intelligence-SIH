# 3-Class Classifier Evaluation — SceneClassifier vs Zenodo Sentinel-1 SAR Oil Spill Dataset (Test Split)

**Evaluated at:** 2026-10-08T15:50:35.991864 UTC  
**Checkpoint:** `backend/data/ml_checkpoints/v2_seed2026/classifier_real_best.pth`  
**Checkpoint SHA-256:** `sha256:1102282acbf06a4cc8b54f51bdf995a63c9695c1bc4631c5deede99ac4645ba7`  
**Checkpoint trained on:** REAL_ZENODO_SENTINEL1_SAR_OIL_SPILL_DATASET  
**Architecture:** SceneClassifierV2  
**Checkpoint epoch:** 49  
**Preprocessing version:** `sar_percentile_1_99_minmax_v1`  
**Class mapping:** {0: 'Oil', 1: 'No-Oil', 2: 'Lookalike'}  
**Test scenes evaluated:** 450

## Overall

| Metric | Value |
|---|---|
| accuracy | 0.7844 |
| macro_precision | 0.7962 |
| macro_recall | 0.7844 |
| macro_f1 | 0.7724 |
| weighted_precision | 0.7962 |
| weighted_recall | 0.7844 |
| weighted_f1 | 0.7724 |

## Per-class

| Class | Precision | Recall | F1 | Support |
|---|---|---|---|---|
| Oil | 0.8986 | 0.8867 | 0.8926 | 150 |
| No-Oil | 0.7005 | 0.9667 | 0.8123 | 150 |
| Lookalike | 0.7895 | 0.5 | 0.6122 | 150 |

## Confusion matrix (rows = actual, columns = predicted)

| Actual \ Predicted | Oil | No-Oil | Lookalike |
|---|---|---|---|
| **Oil** | 133 | 1 | 16 |
| **No-Oil** | 1 | 145 | 4 |
| **Lookalike** | 14 | 61 | 75 |

## Directional confusion counts

- Oil → No-Oil: 1
- Oil → Lookalike: 16
- No-Oil → Oil: 1
- No-Oil → Lookalike: 4
- Lookalike → Oil: 14
- Lookalike → No-Oil: 61

## Confidence summary

- Mean: 0.8972 · Median: 0.9633 · Min: 0.4259 · Max: 1.0
- Mean when correct: 0.9189 · Mean when incorrect: 0.8185

## Limitations

- This is the dataset's own published test split (Part III), never mixed into train/val (see app/ml/dataset.py build_manifests 'LEAKAGE PREVENTION').
- Softmax confidence is the model's own raw output, not a calibrated statistical probability - no calibration (e.g. temperature scaling, Platt scaling) is implemented anywhere in this codebase. Treat 'confidence' as a relative ranking signal only.
- This evaluation reports the classifier's OWN predicted label vs ground truth - it does not re-derive or validate ML_FALLBACK_CONFIDENCE_THRESHOLD (app/ml/detector.py), which governs a separate, orthogonal decision (whether to trust ANY ML result enough to skip the classical-detector fallback). See MODEL_CARD.md / README_ML_STATUS.md.
- A Lookalike/No-Oil scene correctly classified as such can still carry a non-empty segmenter mask (the two sub-models are architecturally independent) - this evaluation covers ONLY the classifier's own scene-level label, not the segmenter's mask quality (see app/ml/evaluate_real.py for that) or the orchestrator's Lookalike-safety gating (app/investigation/orchestrator.py, guarded by tests/test_lookalike_safety.py).
- Re-evaluate after any further training - these numbers describe this exact checkpoint file (see checkpoint_sha256) at this exact epoch only.
