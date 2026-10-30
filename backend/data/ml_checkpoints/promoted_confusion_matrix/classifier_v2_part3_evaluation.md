# 3-Class Classifier Evaluation — SceneClassifier vs Zenodo Sentinel-1 SAR Oil Spill Dataset (Test Split)

**Evaluated at:** 2026-10-30T17:18:57.684348 UTC  
**Checkpoint:** `backend/data/ml_checkpoints/classifier_real_promoted.pth`  
**Checkpoint SHA-256:** `sha256:0f587cb7604047247424bf7a79a9f07ae0ab66b9e4a3070cf32c2c1957fed1ee`  
**Checkpoint trained on:** REAL_ZENODO_SENTINEL1_SAR_OIL_SPILL_DATASET  
**Architecture:** SceneClassifierV2  
**Checkpoint epoch:** 32  
**Preprocessing version:** `sar_percentile_1_99_minmax_v1`  
**Class mapping:** {0: 'Oil', 1: 'No-Oil', 2: 'Lookalike'}  
**Test scenes evaluated:** 450

## Overall

| Metric | Value |
|---|---|
| accuracy | 0.7844 |
| macro_precision | 0.8034 |
| macro_recall | 0.7844 |
| macro_f1 | 0.7779 |
| weighted_precision | 0.8034 |
| weighted_recall | 0.7844 |
| weighted_f1 | 0.7779 |

## Per-class

| Class | Precision | Recall | F1 | Support |
|---|---|---|---|---|
| Oil | 0.9559 | 0.8667 | 0.9091 | 150 |
| No-Oil | 0.6777 | 0.9533 | 0.7922 | 150 |
| Lookalike | 0.7767 | 0.5333 | 0.6324 | 150 |

## Confusion matrix (rows = actual, columns = predicted)

| Actual \ Predicted | Oil | No-Oil | Lookalike |
|---|---|---|---|
| **Oil** | 130 | 4 | 16 |
| **No-Oil** | 0 | 143 | 7 |
| **Lookalike** | 6 | 64 | 80 |

## Directional confusion counts

- Oil → No-Oil: 4
- Oil → Lookalike: 16
- No-Oil → Oil: 0
- No-Oil → Lookalike: 7
- Lookalike → Oil: 6
- Lookalike → No-Oil: 64

## Confidence summary

- Mean: 0.8781 · Median: 0.9642 · Min: 0.4614 · Max: 1.0
- Mean when correct: 0.9116 · Mean when incorrect: 0.7562

## Limitations

- This is the dataset's own published test split (Part III), never mixed into train/val (see app/ml/dataset.py build_manifests 'LEAKAGE PREVENTION').
- Softmax confidence is the model's own raw output, not a calibrated statistical probability - no calibration (e.g. temperature scaling, Platt scaling) is implemented anywhere in this codebase. Treat 'confidence' as a relative ranking signal only.
- This evaluation reports the classifier's OWN predicted label vs ground truth - it does not re-derive or validate ML_FALLBACK_CONFIDENCE_THRESHOLD (app/ml/detector.py), which governs a separate, orthogonal decision (whether to trust ANY ML result enough to skip the classical-detector fallback). See MODEL_CARD.md / README_ML_STATUS.md.
- A Lookalike/No-Oil scene correctly classified as such can still carry a non-empty segmenter mask (the two sub-models are architecturally independent) - this evaluation covers ONLY the classifier's own scene-level label, not the segmenter's mask quality (see app/ml/evaluate_real.py for that) or the orchestrator's Lookalike-safety gating (app/investigation/orchestrator.py, guarded by tests/test_lookalike_safety.py).
- Re-evaluate after any further training - these numbers describe this exact checkpoint file (see checkpoint_sha256) at this exact epoch only.
