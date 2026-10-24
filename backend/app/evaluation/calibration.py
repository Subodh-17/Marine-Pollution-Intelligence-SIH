"""
Calibration & Reliability Metrics for Maritime Scene Classification and Detection.
Implements:
- Expected Calibration Error (ECE)
- Multi-class Brier Score
- Reliability Diagram Data Generation
- Validation-Only Temperature Scaling (Platt scaling extension)
- Wilson / Binomial Confidence Intervals

SCIENTIFIC CONTRACT (Phase 2 Priority 4):
Calibration parameters (e.g. Temperature T) MUST be fit on validation data ONLY,
never on held-out test data. Uncalibrated scores are explicitly distinguished
from calibrated probabilities.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Tuple
import numpy as np


@dataclass
class ReliabilityBin:
    bin_index: int
    bin_lower: float
    bin_upper: float
    sample_count: int
    mean_confidence: float
    accuracy: float
    calibration_gap: float


@dataclass
class CalibrationResult:
    ece: float
    mce: float  # Maximum Calibration Error
    brier_score: float
    reliability_bins: List[ReliabilityBin]
    sample_count: int
    is_calibrated: bool
    temperature: Optional[float] = None
    confidence_interval_95: Optional[Tuple[float, float]] = None


def compute_brier_score(y_true_idx: List[int], probs: np.ndarray) -> float:
    """Computes multiclass Brier score:
    BS = (1/N) * sum_i sum_k (p_ik - y_ik)^2
    Range: [0, 2], lower is better. A perfectly calibrated deterministic predictor achieves 0.
    """
    n = len(y_true_idx)
    if n == 0 or probs.shape[0] != n:
        return 0.0
    k = probs.shape[1]
    one_hot = np.zeros((n, k), dtype=np.float64)
    for i, idx in enumerate(y_true_idx):
        if 0 <= idx < k:
            one_hot[i, idx] = 1.0
    diff = probs - one_hot
    return float(np.mean(np.sum(diff ** 2, axis=1)))


def compute_ece(
    y_true_idx: List[int],
    probs: np.ndarray,
    n_bins: int = 10
) -> Tuple[float, float, List[ReliabilityBin]]:
    """Computes Expected Calibration Error (ECE) and Maximum Calibration Error (MCE)
    for multi-class predictions using the top-1 predicted confidence.

    ECE = sum_m (|B_m| / N) * |acc(B_m) - conf(B_m)|
    MCE = max_m |acc(B_m) - conf(B_m)|
    """
    n = len(y_true_idx)
    if n == 0 or len(probs) != n:
        return 0.0, 0.0, []

    confidences = np.max(probs, axis=1)
    predictions = np.argmax(probs, axis=1)
    accuracies = (predictions == np.array(y_true_idx)).astype(float)

    bin_edges = np.linspace(0.0, 1.0, n_bins + 1)
    bins: List[ReliabilityBin] = []
    ece = 0.0
    mce = 0.0

    for m in range(n_bins):
        lo = bin_edges[m]
        hi = bin_edges[m + 1]
        if m == n_bins - 1:
            in_bin = (confidences >= lo) & (confidences <= hi)
        else:
            in_bin = (confidences >= lo) & (confidences < hi)

        count = int(np.sum(in_bin))
        if count > 0:
            mean_conf = float(np.mean(confidences[in_bin]))
            mean_acc = float(np.mean(accuracies[in_bin]))
            gap = abs(mean_acc - mean_conf)
            ece += (count / n) * gap
            mce = max(mce, gap)
        else:
            mean_conf = float((lo + hi) / 2.0)
            mean_acc = 0.0
            gap = 0.0

        bins.append(ReliabilityBin(
            bin_index=m,
            bin_lower=round(float(lo), 3),
            bin_upper=round(float(hi), 3),
            sample_count=count,
            mean_confidence=round(mean_conf, 4),
            accuracy=round(mean_acc, 4),
            calibration_gap=round(gap, 4),
        ))

    return round(float(ece), 4), round(float(mce), 4), bins


def compute_wilson_confidence_interval(
    successes: int,
    total: int,
    confidence: float = 0.95
) -> Tuple[float, float]:
    """Computes Wilson score interval for binomial proportions.
    Robust for small sample counts and proportions near 0 or 1.
    """
    if total <= 0:
        return (0.0, 1.0)
    # Z-scores for standard intervals
    z_map = {0.90: 1.6449, 0.95: 1.95996, 0.99: 2.5758}
    z = z_map.get(confidence, 1.96)
    p_hat = successes / total
    denom = 1.0 + (z ** 2) / total
    center = (p_hat + (z ** 2) / (2.0 * total)) / denom
    margin = (z / denom) * math.sqrt((p_hat * (1.0 - p_hat) / total) + (z ** 2) / (4.0 * (total ** 2)))
    lo = max(0.0, center - margin)
    hi = min(1.0, center + margin)
    return (round(lo, 4), round(hi, 4))


class TemperatureScalingCalibrator:
    """Post-hoc temperature scaling for multi-class classifiers.
    p_i = softmax(logits / T)

    Fitted strictly via NLL minimization on validation data logits.
    Never applied to or fitted on held-out test sets.
    """

    def __init__(self, temperature: float = 1.0):
        self.temperature = max(0.01, float(temperature))
        self.is_fitted = False

    def fit_validation_logits(
        self,
        val_logits: np.ndarray,
        val_labels: List[int],
        max_iter: int = 100,
        lr: float = 0.01
    ) -> float:
        """Optimizes temperature T using gradient descent on validation NLL."""
        logits = np.asarray(val_logits, dtype=np.float64)
        labels = np.asarray(val_labels, dtype=np.int64)
        n, k = logits.shape

        t = 1.0
        for _ in range(max_iter):
            # Scaled logits
            scaled = logits / t
            # Softmax
            exp_s = np.exp(scaled - np.max(scaled, axis=1, keepdims=True))
            probs = exp_s / np.sum(exp_s, axis=1, keepdims=True)

            # Gradient of NLL w.r.t temperature:
            # dNLL/dT = (1/T^2) * sum_k (probs_ik - y_ik) * z_ik
            one_hot = np.zeros((n, k))
            one_hot[np.arange(n), labels] = 1.0
            error = probs - one_hot
            grad = -np.sum(error * logits) / (t ** 2 * n)

            # Update step with momentum clipping
            step = lr * grad
            t = max(0.1, min(10.0, t - step))
            if abs(step) < 1e-6:
                break

        self.temperature = float(t)
        self.is_fitted = True
        return self.temperature

    def calibrate(self, logits: np.ndarray) -> np.ndarray:
        """Applies fitted temperature to raw logits and returns calibrated probabilities."""
        scaled = np.asarray(logits, dtype=np.float64) / self.temperature
        exp_s = np.exp(scaled - np.max(scaled, axis=-1, keepdims=True))
        return (exp_s / np.sum(exp_s, axis=-1, keepdims=True)).astype(np.float32)
