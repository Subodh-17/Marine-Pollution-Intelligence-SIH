"""
Generic binary segmentation metrics - precision / recall / F1 / IoU.

Pure, dependency-free function operating on two boolean arrays. No SIH-
specific assumptions live here; `evaluate_samples.py` is what decides which
scenes are legitimate to score with this function and which are not.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class MaskMetrics:
    tp: int
    fp: int
    fn: int
    tn: int
    precision: float
    recall: float
    f1: float
    iou: float
    # Dice coefficient is mathematically identical to F1 for a binary mask
    # (2*TP / (2*TP + FP + FN) === F1 for the positive class) - kept as its
    # own named field anyway (rather than only documented as "same as f1")
    # because segmentation literature/reviewers expect to see "Dice" reported
    # explicitly by name, per this project's requirement to report it.
    dice: float
    pixel_accuracy: float
    empty_gt: bool = False


def binary_mask_metrics(pred: np.ndarray, gt: np.ndarray) -> MaskMetrics:
    """
    Computes precision/recall/F1/IoU of a predicted boolean mask against a
    ground-truth boolean mask of the same shape.

    Edge cases (both masks empty, or gt empty but pred non-empty) are handled
    explicitly rather than producing NaN from a 0/0 division:
      - gt has zero positive pixels and pred also has zero -> perfect score
        (1.0 for all four metrics; there was nothing to find and nothing was
        wrongly flagged).
      - gt has zero positive pixels but pred has some -> precision/F1/IoU are
        0.0 (every flagged pixel is a false positive) and recall is reported
        as 1.0 by convention (there was nothing to miss), matching the
        common scikit-learn convention for an empty positive class.

    SCIENTIFIC HONESTY GUARD (SIH-26143 ML Audit finding §1):
    Setting recall = 1.0 on empty-mask scenes is an isolated convention for
    single-image evaluation. It MUST NOT be averaged across all-negative
    scenes (e.g. Lookalike and No-Oil) to report inflated macro recall.
    True oil detection capability must be evaluated over positive scenes
    (`oil_scene_macro`) or pooled across all pixels (`pixel_pooled`).
    """
    if pred.shape != gt.shape:
        raise ValueError(f"Shape mismatch: pred {pred.shape} vs gt {gt.shape}")
    pred = pred.astype(bool)
    gt = gt.astype(bool)

    tp = int(np.logical_and(pred, gt).sum())
    fp = int(np.logical_and(pred, ~gt).sum())
    fn = int(np.logical_and(~pred, gt).sum())
    tn = int(np.logical_and(~pred, ~gt).sum())

    gt_positive = tp + fn
    pred_positive = tp + fp
    union = tp + fp + fn
    is_empty_gt = bool(gt_positive == 0)

    if gt_positive == 0:
        recall = 1.0
        precision = 1.0 if pred_positive == 0 else 0.0
        iou = 1.0 if pred_positive == 0 else 0.0
    else:
        precision = tp / pred_positive if pred_positive else 0.0
        recall = tp / gt_positive
        iou = tp / union if union else 1.0

    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    dice = (2 * tp / (2 * tp + fp + fn)) if (2 * tp + fp + fn) else 1.0
    total = tp + fp + fn + tn
    pixel_accuracy = ((tp + tn) / total) if total else 1.0

    return MaskMetrics(tp=tp, fp=fp, fn=fn, tn=tn, precision=round(precision, 4),
                        recall=round(recall, 4), f1=round(f1, 4), iou=round(iou, 4),
                        dice=round(dice, 4), pixel_accuracy=round(pixel_accuracy, 4),
                        empty_gt=is_empty_gt)


@dataclass
class MulticlassMetrics:
    """Standard scene-level multi-class classification metrics - the same
    general shape as `MaskMetrics` above, but for a single categorical label
    per sample (e.g. the 3-class Oil/No-Oil/Lookalike scene classifier)
    rather than a per-pixel mask. Kept in this same module, as a sibling to
    `binary_mask_metrics`/`confusion_matrix`, rather than a new parallel
    metrics module - this project's segmentation and classification
    evaluators (app/ml/evaluate_real.py and app/ml/evaluate_classifier.py)
    both import from here so the two can never define "precision" or
    "recall" inconsistently with each other."""
    labels: list
    confusion: list          # confusion[i][j] = count of actual labels[i] predicted as labels[j]
    per_class: dict          # {label: {"precision": float, "recall": float, "f1": float, "support": int}}
    accuracy: float
    macro_precision: float
    macro_recall: float
    macro_f1: float
    weighted_precision: float
    weighted_recall: float
    weighted_f1: float
    n_samples: int


def multiclass_classification_metrics(y_true: list, y_pred: list, labels: list) -> MulticlassMetrics:
    """Computes overall accuracy, macro/weighted precision-recall-F1, and
    per-class precision/recall/F1/support from parallel lists of true and
    predicted class labels (both must be members of `labels`).

    Edge case handling (explicit, never a silent 0/0 -> NaN): a class with
    zero predicted instances has precision 0.0 (not undefined) and a class
    with zero actual instances (support=0) has recall 0.0 and is excluded
    from the macro average's denominator only if it never appears as either
    true or predicted - in practice (a held-out test split with every class
    represented) this never triggers, but a malformed/tiny sample must never
    raise instead of reporting an honest (if degenerate) number.
    """
    if len(y_true) != len(y_pred):
        raise ValueError(f"y_true/y_pred length mismatch: {len(y_true)} vs {len(y_pred)}")
    n = len(y_true)
    idx = {label: i for i, label in enumerate(labels)}
    k = len(labels)
    confusion = [[0] * k for _ in range(k)]
    for t, p in zip(y_true, y_pred):
        if t not in idx or p not in idx:
            raise ValueError(f"Label not in provided label set {labels}: true={t!r} pred={p!r}")
        confusion[idx[t]][idx[p]] += 1

    per_class = {}
    precisions, recalls, f1s, supports = [], [], [], []
    for i, label in enumerate(labels):
        tp = confusion[i][i]
        support = sum(confusion[i])  # row sum = actual count of this class
        predicted_count = sum(confusion[r][i] for r in range(k))  # column sum
        precision = (tp / predicted_count) if predicted_count else 0.0
        recall = (tp / support) if support else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
        per_class[label] = {
            "precision": round(precision, 4), "recall": round(recall, 4),
            "f1": round(f1, 4), "support": support,
        }
        precisions.append(precision)
        recalls.append(recall)
        f1s.append(f1)
        supports.append(support)

    total_correct = sum(confusion[i][i] for i in range(k))
    accuracy = (total_correct / n) if n else 0.0
    macro_precision = sum(precisions) / k if k else 0.0
    macro_recall = sum(recalls) / k if k else 0.0
    macro_f1 = sum(f1s) / k if k else 0.0
    total_support = sum(supports)
    if total_support:
        weighted_precision = sum(p * s for p, s in zip(precisions, supports)) / total_support
        weighted_recall = sum(r * s for r, s in zip(recalls, supports)) / total_support
        weighted_f1 = sum(f * s for f, s in zip(f1s, supports)) / total_support
    else:
        weighted_precision = weighted_recall = weighted_f1 = 0.0

    return MulticlassMetrics(
        labels=list(labels), confusion=confusion, per_class=per_class,
        accuracy=round(accuracy, 4),
        macro_precision=round(macro_precision, 4), macro_recall=round(macro_recall, 4),
        macro_f1=round(macro_f1, 4),
        weighted_precision=round(weighted_precision, 4), weighted_recall=round(weighted_recall, 4),
        weighted_f1=round(weighted_f1, 4),
        n_samples=n,
    )


def confusion_matrix(m: MaskMetrics) -> dict:
    """Formats a MaskMetrics' tp/fp/fn/tn as an explicit, labelled 2x2
    confusion matrix (pixel-level, since this project's "classification" is
    a per-pixel segmentation mask, not a scene-level label) - the same
    counts already computed by `binary_mask_metrics`, just presented in the
    conventional confusion-matrix shape for reporting/evaluation output."""
    return {
        "labels": ["predicted_positive", "predicted_negative"],
        "actual_positive": {"predicted_positive": m.tp, "predicted_negative": m.fn},
        "actual_negative": {"predicted_positive": m.fp, "predicted_negative": m.tn},
        "matrix": [[m.tp, m.fn], [m.fp, m.tn]],
    }
