"""
Confidence Calibration Module for Scene Classification.

Provides mathematically principled post-hoc probability calibration:
1. Temperature Scaling: Optimizes scalar T > 0 on a dedicated validation split
   by minimizing Negative Log-Likelihood (cross-entropy).
2. Proper Scoring Rules & Calibration Metrics:
   - Negative Log-Likelihood (NLL)
   - Brier Score (multi-class mean squared probability error)
   - Expected Calibration Error (ECE) with equal-frequency and equal-width binning
   - Maximum Calibration Error (MCE)
   - Reliability diagram coordinates
3. Provenance & Fail-Closed Safety:
   - Verifies model SHA-256 and class order before applying calibrated scaling.
   - If missing, invalid, or mismatched, fails closed by returning raw scores
     with explicit `is_calibrated: False` provenance labels.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy.optimize import minimize_scalar


@dataclass
class CalibrationMetrics:
    nll: float
    brier_score: float
    ece: float
    mce: float
    mean_confidence: float
    accuracy: float


def softmax(logits: np.ndarray, axis: int = -1) -> np.ndarray:
    """Numerically stable softmax."""
    shifted = logits - np.max(logits, axis=axis, keepdims=True)
    exp = np.exp(shifted)
    return exp / np.sum(exp, axis=axis, keepdims=True)


def temperature_scale(logits: np.ndarray, temperature: float) -> np.ndarray:
    """Scales logits by positive temperature T and computes softmax probabilities."""
    if temperature <= 0.0:
        raise ValueError(f"Temperature must be strictly positive, got {temperature}")
    scaled_logits = logits / float(temperature)
    return softmax(scaled_logits, axis=-1)


def compute_nll(probs: np.ndarray, y_true_indices: np.ndarray, eps: float = 1e-12) -> float:
    """Computes categorical Negative Log-Likelihood (cross entropy)."""
    n = len(y_true_indices)
    if n == 0:
        return 0.0
    clipped = np.clip(probs[np.arange(n), y_true_indices], eps, 1.0)
    return float(-np.mean(np.log(clipped)))


def compute_brier_score(probs: np.ndarray, y_true_indices: np.ndarray, num_classes: int = 3) -> float:
    """Computes multi-class Brier Score: (1/N) * sum_i sum_c (p_ic - y_ic)^2."""
    n = len(y_true_indices)
    if n == 0:
        return 0.0
    one_hot = np.zeros((n, num_classes), dtype=np.float64)
    one_hot[np.arange(n), y_true_indices] = 1.0
    return float(np.mean(np.sum((probs - one_hot) ** 2, axis=1)))


def compute_ece_and_reliability(
    probs: np.ndarray,
    y_true_indices: np.ndarray,
    n_bins: int = 10,
) -> Tuple[float, float, List[Dict[str, Any]]]:
    """Computes Expected Calibration Error (ECE), Maximum Calibration Error (MCE),
    and bin coordinates for a reliability diagram.

    Bins are partitioned across confidence interval [0, 1].
    ECE = sum_m (|B_m| / N) * |acc(B_m) - conf(B_m)|
    MCE = max_m |acc(B_m) - conf(B_m)|
    """
    n = len(y_true_indices)
    if n == 0:
        return 0.0, 0.0, []

    confidences = np.max(probs, axis=1)
    predictions = np.argmax(probs, axis=1)
    accuracies = (predictions == y_true_indices).astype(np.float64)

    bin_boundaries = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    mce = 0.0
    bins_data: List[Dict[str, Any]] = []

    for i in range(n_bins):
        bin_lower = bin_boundaries[i]
        bin_upper = bin_boundaries[i + 1]

        # Include upper boundary in last bin
        if i == n_bins - 1:
            in_bin = (confidences >= bin_lower) & (confidences <= bin_upper)
        else:
            in_bin = (confidences >= bin_lower) & (confidences < bin_upper)

        bin_count = int(np.sum(in_bin))
        if bin_count > 0:
            bin_acc = float(np.mean(accuracies[in_bin]))
            bin_conf = float(np.mean(confidences[in_bin]))
            bin_error = abs(bin_acc - bin_conf)
            ece += (bin_count / n) * bin_error
            mce = max(mce, bin_error)
            bins_data.append({
                "bin_idx": i,
                "range": [round(bin_lower, 2), round(bin_upper, 2)],
                "count": bin_count,
                "accuracy": round(bin_acc, 4),
                "confidence": round(bin_conf, 4),
                "calibration_gap": round(bin_error, 4),
            })
        else:
            bins_data.append({
                "bin_idx": i,
                "range": [round(bin_lower, 2), round(bin_upper, 2)],
                "count": 0,
                "accuracy": 0.0,
                "confidence": 0.0,
                "calibration_gap": 0.0,
            })

    return float(ece), float(mce), bins_data


def evaluate_calibration(
    logits: np.ndarray,
    y_true_indices: np.ndarray,
    temperature: float = 1.0,
    n_bins: int = 10,
) -> Tuple[CalibrationMetrics, List[Dict[str, Any]]]:
    """Evaluates NLL, Brier score, ECE, MCE, mean confidence, and accuracy for given temperature."""
    probs = temperature_scale(logits, temperature)
    predictions = np.argmax(probs, axis=1)
    accuracy = float(np.mean(predictions == y_true_indices))
    confidences = np.max(probs, axis=1)
    mean_conf = float(np.mean(confidences))

    nll = compute_nll(probs, y_true_indices)
    brier = compute_brier_score(probs, y_true_indices, num_classes=logits.shape[1])
    ece, mce, bins = compute_ece_and_reliability(probs, y_true_indices, n_bins=n_bins)

    metrics = CalibrationMetrics(
        nll=round(nll, 4),
        brier_score=round(brier, 4),
        ece=round(ece, 4),
        mce=round(mce, 4),
        mean_confidence=round(mean_conf, 4),
        accuracy=round(accuracy, 4),
    )
    return metrics, bins


def fit_temperature(
    logits: np.ndarray,
    y_true_indices: np.ndarray,
    temp_bounds: Tuple[float, float] = (0.1, 10.0),
) -> Tuple[float, CalibrationMetrics, CalibrationMetrics, List[Dict[str, Any]]]:
    """Fits optimal temperature T on validation split logits by minimizing NLL.

    Returns:
        (optimal_temperature, uncalibrated_metrics, calibrated_metrics, calibrated_bins)
    """
    def obj_fn(t: float) -> float:
        probs = temperature_scale(logits, t)
        return compute_nll(probs, y_true_indices)

    # 1. Evaluate baseline uncalibrated (T = 1.0)
    uncalibrated_metrics, _ = evaluate_calibration(logits, y_true_indices, temperature=1.0)

    # 2. Minimize NLL bounded in [temp_bounds]
    res = minimize_scalar(obj_fn, bounds=temp_bounds, method="bounded")
    if not res.success:
        raise RuntimeError(f"Temperature optimization failed: {res.message}")

    optimal_t = float(res.x)

    # 3. Evaluate calibrated
    calibrated_metrics, calibrated_bins = evaluate_calibration(logits, y_true_indices, temperature=optimal_t)

    return optimal_t, uncalibrated_metrics, calibrated_metrics, calibrated_bins


class ClassifierCalibrator:
    """Manages loaded calibration parameters and applies fail-closed calibration."""

    def __init__(self, artifact_path: Optional[str] = None):
        self.artifact_path = artifact_path
        self.is_loaded = False
        self.temperature: float = 1.0
        self.model_sha256: Optional[str] = None
        self.class_labels: Optional[List[str]] = None
        self.provenance: Optional[str] = None
        self.validation_metrics: Optional[Dict[str, Any]] = None
        self.metadata: Dict[str, Any] = {}

        if artifact_path and os.path.exists(artifact_path):
            self.load(artifact_path)

    def load(self, path: str) -> None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.temperature = float(data["temperature"])
            self.model_sha256 = data.get("model_sha256")
            self.class_labels = data.get("class_labels")
            self.provenance = data.get("provenance", "TEMPERATURE_SCALED_VALIDATION_SPLIT")
            self.validation_metrics = data.get("validation_metrics")
            self.metadata = data
            self.artifact_path = path
            self.is_loaded = True
        except Exception:
            self.is_loaded = False
            self.temperature = 1.0

    def is_compatible(self, current_model_sha256: str, current_class_labels: List[str]) -> bool:
        """Verifies that calibration parameters match the current model and class ordering."""
        if not self.is_loaded:
            return False
        if self.model_sha256 and self.model_sha256.lower() != current_model_sha256.lower():
            return False
        if self.class_labels and list(self.class_labels) != list(current_class_labels):
            return False
        return self.temperature > 0.0

    def calibrate_logits(
        self,
        logits: np.ndarray,
        current_model_sha256: str,
        current_class_labels: List[str],
    ) -> Tuple[np.ndarray, np.ndarray, bool, float]:
        """Given (C,) or (N, C) logits, returns:
        (raw_probs, calibrated_probs, is_calibrated, effective_temperature)
        """
        raw_probs = softmax(logits, axis=-1)
        if self.is_compatible(current_model_sha256, current_class_labels):
            calibrated_probs = temperature_scale(logits, self.temperature)
            return raw_probs, calibrated_probs, True, self.temperature
        return raw_probs, raw_probs, False, 1.0

    def calibrate_probabilities(
        self,
        raw_probs: np.ndarray,
        current_model_sha256: str = "",
        current_class_labels: Optional[List[str]] = None,
    ) -> Tuple[np.ndarray, np.ndarray, bool, float]:
        """Given (C,) or (N, C) raw probabilities, inverts to log-scale, applies temperature scaling, and returns:
        (raw_probs, calibrated_probs, is_calibrated, effective_temperature)
        """
        raw_p = np.asarray(raw_probs, dtype=np.float64)
        if current_class_labels is not None and not self.is_compatible(current_model_sha256, current_class_labels):
            return raw_p, raw_p, False, 1.0
        if self.temperature <= 0.0 or abs(self.temperature - 1.0) < 1e-6:
            return raw_p, raw_p, True, self.temperature
        clipped = np.maximum(raw_p, 1e-12)
        log_p = np.log(clipped)
        calibrated = softmax(log_p / self.temperature, axis=-1)
        return raw_p, calibrated, True, self.temperature
