# 3-Class Classifier Evaluation — SceneClassifier vs Zenodo Sentinel-1 SAR Oil Spill Dataset (Test Split)

**Evaluated at:** 2026-10-11T10:19:00.837694 UTC  
**Checkpoint:** `backend/data/ml_checkpoints/experiments/lookalike_study/control/classifier_real_best.pth`  
**Checkpoint SHA-256:** `sha256:dc7ffeb72fbf481f863a8592c19effb75593f3a45edf2a6ce04e8be75566d387`  
**Checkpoint trained on:** REAL_ZENODO_SENTINEL1_SAR_OIL_SPILL_DATASET  
**Architecture:** SceneClassifierV2  
**Checkpoint epoch:** 18  
**Preprocessing version:** `sar_percentile_1_99_minmax_v1`  
**Class mapping:** {0: 'Oil', 1: 'No-Oil', 2: 'Lookalike'}  
**Test scenes evaluated:** 450

## Overall

| Metric | Value |
|---|---|
| accuracy | 0.7844 |
| macro_precision | 0.8052 |
| macro_recall | 0.7844 |
| macro_f1 | 0.7787 |
| weighted_precision | 0.8052 |
| weighted_recall | 0.7844 |
| weighted_f1 | 0.7787 |

## Per-class

| Class | Precision | Recall | F1 | Support |
|---|---|---|---|---|
| Oil | 0.9603 | 0.8067 | 0.8768 | 150 |
| No-Oil | 0.6916 | 0.9867 | 0.8132 | 150 |
| Lookalike | 0.7636 | 0.56 | 0.6462 | 150 |

## Confusion matrix (rows = actual, columns = predicted)

| Actual \ Predicted | Oil | No-Oil | Lookalike |
|---|---|---|---|
| **Oil** | 121 | 4 | 25 |
| **No-Oil** | 1 | 148 | 1 |
| **Lookalike** | 4 | 62 | 84 |

## Directional confusion counts

- Oil → No-Oil: 4
- Oil → Lookalike: 25
- No-Oil → Oil: 1
- No-Oil → Lookalike: 1
- Lookalike → Oil: 4
- Lookalike → No-Oil: 62

## Confidence summary

- Mean: 0.8817 · Median: 0.9431 · Min: 0.4482 · Max: 1.0
- Mean when correct: 0.9097 · Mean when incorrect: 0.7798

## Limitations

- This is the dataset's own published test split (Part III), never mixed into train/val (see app/ml/dataset.py build_manifests 'LEAKAGE PREVENTION').
- Softmax confidence is the model's own raw output, not a calibrated statistical probability - no calibration (e.g. temperature scaling, Platt scaling) is implemented anywhere in this codebase. Treat 'confidence' as a relative ranking signal only.
- This evaluation reports the classifier's OWN predicted label vs ground truth - it does not re-derive or validate ML_FALLBACK_CONFIDENCE_THRESHOLD (app/ml/detector.py), which governs a separate, orthogonal decision (whether to trust ANY ML result enough to skip the classical-detector fallback). See MODEL_CARD.md / README_ML_STATUS.md.
- A Lookalike/No-Oil scene correctly classified as such can still carry a non-empty segmenter mask (the two sub-models are architecturally independent) - this evaluation covers ONLY the classifier's own scene-level label, not the segmenter's mask quality (see app/ml/evaluate_real.py for that) or the orchestrator's Lookalike-safety gating (app/investigation/orchestrator.py, guarded by tests/test_lookalike_safety.py).
- Re-evaluate after any further training - these numbers describe this exact checkpoint file (see checkpoint_sha256) at this exact epoch only.
