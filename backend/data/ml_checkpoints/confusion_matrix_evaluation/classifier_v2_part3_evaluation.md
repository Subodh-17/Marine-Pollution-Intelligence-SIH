# 3-Class Classifier Evaluation — SceneClassifier vs Zenodo Sentinel-1 SAR Oil Spill Dataset (Test Split)

**Evaluated at:** 2026-10-30T17:05:43.338061 UTC  
**Checkpoint:** `backend/data/ml_checkpoints/classifier_real_best.pth`  
**Checkpoint SHA-256:** `sha256:409109a444196aafb5fc12aedb095bdde4e9062b0911373510535dabb052a75e`  
**Checkpoint trained on:** REAL_ZENODO_SENTINEL1_SAR_OIL_SPILL_DATASET  
**Architecture:** SceneClassifier  
**Checkpoint epoch:** 17  
**Preprocessing version:** `sar_percentile_1_99_minmax_v1`  
**Class mapping:** {0: 'Oil', 1: 'No-Oil', 2: 'Lookalike'}  
**Test scenes evaluated:** 450

## Overall

| Metric | Value |
|---|---|
| accuracy | 0.4889 |
| macro_precision | 0.5064 |
| macro_recall | 0.4889 |
| macro_f1 | 0.4682 |
| weighted_precision | 0.5064 |
| weighted_recall | 0.4889 |
| weighted_f1 | 0.4682 |

## Per-class

| Class | Precision | Recall | F1 | Support |
|---|---|---|---|---|
| Oil | 0.6173 | 0.3333 | 0.4329 | 150 |
| No-Oil | 0.4781 | 0.8 | 0.5985 | 150 |
| Lookalike | 0.4237 | 0.3333 | 0.3731 | 150 |

## Confusion matrix (rows = actual, columns = predicted)

| Actual \ Predicted | Oil | No-Oil | Lookalike |
|---|---|---|---|
| **Oil** | 50 | 40 | 60 |
| **No-Oil** | 22 | 120 | 8 |
| **Lookalike** | 9 | 91 | 50 |

## Directional confusion counts

- Oil → No-Oil: 40
- Oil → Lookalike: 60
- No-Oil → Oil: 22
- No-Oil → Lookalike: 8
- Lookalike → Oil: 9
- Lookalike → No-Oil: 91

## Confidence summary

- Mean: 0.6262 · Median: 0.5984 · Min: 0.343 · Max: 0.9735
- Mean when correct: 0.6346 · Mean when incorrect: 0.6182

## Limitations

- This is the dataset's own published test split (Part III), never mixed into train/val (see app/ml/dataset.py build_manifests 'LEAKAGE PREVENTION').
- Softmax confidence is the model's own raw output, not a calibrated statistical probability - no calibration (e.g. temperature scaling, Platt scaling) is implemented anywhere in this codebase. Treat 'confidence' as a relative ranking signal only.
- This evaluation reports the classifier's OWN predicted label vs ground truth - it does not re-derive or validate ML_FALLBACK_CONFIDENCE_THRESHOLD (app/ml/detector.py), which governs a separate, orthogonal decision (whether to trust ANY ML result enough to skip the classical-detector fallback). See MODEL_CARD.md / README_ML_STATUS.md.
- A Lookalike/No-Oil scene correctly classified as such can still carry a non-empty segmenter mask (the two sub-models are architecturally independent) - this evaluation covers ONLY the classifier's own scene-level label, not the segmenter's mask quality (see app/ml/evaluate_real.py for that) or the orchestrator's Lookalike-safety gating (app/investigation/orchestrator.py, guarded by tests/test_lookalike_safety.py).
- Re-evaluate after any further training - these numbers describe this exact checkpoint file (see checkpoint_sha256) at this exact epoch only.
