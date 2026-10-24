"""
Unit tests for calibration and reliability metrics (ECE, Brier Score, Temperature Scaling, CIs).
"""
import numpy as np
import pytest

from app.evaluation.calibration import (
    compute_ece,
    compute_brier_score,
    compute_wilson_confidence_interval,
    TemperatureScalingCalibrator,
)


def test_perfect_calibration_ece():
    # 100 samples, predicted prob is 1.0, true label is 1.0
    y_true = [0] * 50 + [1] * 50
    probs = np.zeros((100, 2), dtype=np.float32)
    probs[:50, 0] = 1.0
    probs[50:, 1] = 1.0

    ece, mce, bins = compute_ece(y_true, probs, n_bins=10)
    assert ece == 0.0
    assert mce == 0.0


def test_overconfident_miscalibrated_ece():
    # Model is 100% confident (1.0), but only 50% accurate
    y_true = [0] * 50 + [1] * 50
    probs = np.zeros((100, 2), dtype=np.float32)
    # Model predicts class 0 for all 100 with prob 1.0
    probs[:, 0] = 1.0

    ece, mce, bins = compute_ece(y_true, probs, n_bins=10)
    # Mean confidence in bin [0.9, 1.0] is 1.0, accuracy is 0.5 -> gap is 0.5
    assert abs(ece - 0.5) < 1e-3
    assert abs(mce - 0.5) < 1e-3


def test_brier_score_bounds():
    # Perfect predictions have Brier score 0
    y_true = [0, 1]
    probs_perfect = np.array([[1.0, 0.0], [0.0, 1.0]])
    bs_perfect = compute_brier_score(y_true, probs_perfect)
    assert bs_perfect == 0.0

    # Completely wrong predictions have Brier score 2.0
    probs_wrong = np.array([[0.0, 1.0], [1.0, 0.0]])
    bs_wrong = compute_brier_score(y_true, probs_wrong)
    assert bs_wrong == 2.0


def test_wilson_confidence_intervals():
    # 130 successes out of 150 (oil scene recall 86.7%)
    lo, hi = compute_wilson_confidence_interval(130, 150, 0.95)
    assert 0.80 <= lo <= 0.82
    assert 0.90 <= hi <= 0.92

    # 6 false alarms out of 300 non-oil scenes (2.0%)
    lo_fa, hi_fa = compute_wilson_confidence_interval(6, 300, 0.95)
    assert 0.008 <= lo_fa <= 0.010
    assert 0.040 <= hi_fa <= 0.045


def test_temperature_scaling_optimization():
    # Create overconfident synthetic validation logits
    # True labels: 0 or 1
    y_val = [0] * 50 + [1] * 50
    # Overconfident logits with magnitude 10
    logits = np.zeros((100, 2))
    # 80 correct, 20 wrong, but all with huge logits
    for i in range(100):
        if i < 40:
            logits[i] = [5.0, -5.0]  # correct 0
        elif i < 50:
            logits[i] = [-5.0, 5.0]  # error on 0
        elif i < 90:
            logits[i] = [-5.0, 5.0]  # correct 1
        else:
            logits[i] = [5.0, -5.0]  # error on 1

    calibrator = TemperatureScalingCalibrator(temperature=1.0)
    T_opt = calibrator.fit_validation_logits(logits, y_val, max_iter=200, lr=0.05)
    # Temperature should increase above 1.0 to soften overconfident logits
    assert T_opt > 1.0
    assert calibrator.is_fitted

    calibrated_probs = calibrator.calibrate(logits)
    assert np.allclose(np.sum(calibrated_probs, axis=1), 1.0)
    # Top confidence should now be lower than the original overconfident ~0.9999
    assert np.max(calibrated_probs) < 0.99
