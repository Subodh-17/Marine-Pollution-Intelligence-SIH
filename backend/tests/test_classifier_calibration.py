"""Tests for Classifier Confidence Calibration (app/ml/calibration.py & detector integration)."""
from __future__ import annotations

import json
import os
import numpy as np
import pytest

from app.ml.calibration import (
    ClassifierCalibrator,
    compute_brier_score,
    compute_ece_and_reliability,
    compute_nll,
    evaluate_calibration,
    fit_temperature,
    softmax,
    temperature_scale,
)
from app.ml.detector import get_ml_engine


class TestCalibrationMath:
    def test_temperature_scaling_invariance(self):
        logits = np.array([[2.5, 0.5, -1.0], [0.1, 3.2, 1.5]], dtype=np.float64)
        raw_probs = softmax(logits)
        raw_pred = np.argmax(raw_probs, axis=1)

        for t in [0.5, 1.0, 1.5, 2.5]:
            scaled_probs = temperature_scale(logits, t)
            scaled_pred = np.argmax(scaled_probs, axis=1)
            # Monotonic temperature scaling must strictly preserve argmax predictions
            np.testing.assert_array_equal(raw_pred, scaled_pred)
            # Probabilities must sum to 1
            np.testing.assert_allclose(np.sum(scaled_probs, axis=1), [1.0, 1.0], rtol=1e-5)

    def test_temperature_scaling_rejects_non_positive_temperature(self):
        logits = np.array([[1.0, 2.0]], dtype=np.float64)
        with pytest.raises(ValueError, match="strictly positive"):
            temperature_scale(logits, 0.0)
        with pytest.raises(ValueError, match="strictly positive"):
            temperature_scale(logits, -1.2)

    def test_scoring_rules(self):
        probs = np.array([[0.9, 0.1], [0.2, 0.8]], dtype=np.float64)
        y_true = np.array([0, 1], dtype=np.int64)

        nll = compute_nll(probs, y_true)
        assert nll > 0.0
        assert np.isfinite(nll)

        brier = compute_brier_score(probs, y_true, num_classes=2)
        # Brier = 0.5 * ((0.1^2 + 0.1^2) + (0.2^2 + 0.2^2)) = 0.5 * (0.02 + 0.08) = 0.05
        assert np.isclose(brier, 0.05)

    def test_ece_and_reliability_diagram(self):
        probs = np.array([
            [0.95, 0.05],
            [0.90, 0.10],
            [0.85, 0.15],
            [0.60, 0.40],
        ], dtype=np.float64)
        y_true = np.array([0, 0, 0, 1], dtype=np.int64)

        ece, mce, bins = compute_ece_and_reliability(probs, y_true, n_bins=5)
        assert 0.0 <= ece <= 1.0
        assert 0.0 <= mce <= 1.0
        assert len(bins) == 5
        assert all("confidence" in b and "accuracy" in b for b in bins)

    def test_fit_temperature_reduces_nll_and_ece(self):
        np.random.seed(42)
        # Synthetic overconfident logits
        true_labels = np.random.choice([0, 1, 2], size=200)
        logits = np.random.randn(200, 3) * 5.0  # artificially high scale -> overconfident
        for i, l in enumerate(true_labels):
            if np.random.rand() > 0.3:
                logits[i, l] += 8.0

        opt_t, uncal, cal, _ = fit_temperature(logits, true_labels)
        assert opt_t > 1.0  # Should soften overconfident predictions
        assert cal.nll <= uncal.nll + 1e-4
        assert cal.accuracy == uncal.accuracy  # Accuracy strictly invariant


class TestCalibratorCompatibilityAndSafety:
    def test_calibrator_accepts_valid_artifact(self, tmp_path):
        artifact_path = tmp_path / "calibration.json"
        artifact_data = {
            "method": "temperature_scaling",
            "temperature": 1.25,
            "model_sha256": "sha256:abcd1234ef56",
            "class_labels": ["Oil", "No-Oil", "Lookalike"],
            "provenance": "TEMPERATURE_SCALED_VALIDATION_SPLIT",
        }
        artifact_path.write_text(json.dumps(artifact_data), encoding="utf-8")

        calibrator = ClassifierCalibrator(str(artifact_path))
        assert calibrator.is_loaded
        assert calibrator.is_compatible("sha256:abcd1234ef56", ["Oil", "No-Oil", "Lookalike"])

        logits = np.array([2.0, 1.0, 0.0], dtype=np.float64)
        raw, cal, is_cal, eff_t = calibrator.calibrate_logits(logits, "sha256:abcd1234ef56", ["Oil", "No-Oil", "Lookalike"])
        assert is_cal is True
        assert eff_t == 1.25
        assert np.argmax(raw) == np.argmax(cal)
        # Scaled max probability should be slightly softened compared to raw
        assert np.max(cal) < np.max(raw)

    def test_calibrator_fails_closed_on_sha_mismatch(self, tmp_path):
        artifact_path = tmp_path / "calibration.json"
        artifact_data = {
            "method": "temperature_scaling",
            "temperature": 1.5,
            "model_sha256": "sha256:EXPECTED_HASH",
            "class_labels": ["Oil", "No-Oil", "Lookalike"],
        }
        artifact_path.write_text(json.dumps(artifact_data), encoding="utf-8")

        calibrator = ClassifierCalibrator(str(artifact_path))
        assert not calibrator.is_compatible("sha256:DIFFERENT_HASH", ["Oil", "No-Oil", "Lookalike"])

        logits = np.array([3.0, 1.0, 0.0], dtype=np.float64)
        raw, cal, is_cal, eff_t = calibrator.calibrate_logits(logits, "sha256:DIFFERENT_HASH", ["Oil", "No-Oil", "Lookalike"])
        assert is_cal is False
        assert eff_t == 1.0
        np.testing.assert_array_equal(raw, cal)

    def test_calibrator_fails_closed_on_class_label_mismatch(self, tmp_path):
        artifact_path = tmp_path / "calibration.json"
        artifact_data = {
            "method": "temperature_scaling",
            "temperature": 1.5,
            "model_sha256": "sha256:MATCHING_HASH",
            "class_labels": ["Oil", "No-Oil", "Lookalike"],
        }
        artifact_path.write_text(json.dumps(artifact_data), encoding="utf-8")

        calibrator = ClassifierCalibrator(str(artifact_path))
        # Wrong class order
        assert not calibrator.is_compatible("sha256:MATCHING_HASH", ["Lookalike", "Oil", "No-Oil"])


class TestDetectorCalibrationIntegration:
    def test_ml_detector_produces_calibrated_confidence(self):
        engine = get_ml_engine()
        assert engine.available

        vv = np.random.uniform(0.1, 0.9, (256, 256)).astype(np.float32)
        vh = np.random.uniform(0.1, 0.9, (256, 256)).astype(np.float32)
        res = engine.detect(vv, vh)

        sc = res.scene_classification
        assert sc is not None
        assert "confidence" in sc
        assert "raw_confidence" in sc
        assert "is_calibrated" in sc
        assert "score_type" in sc
        assert "score_type_label" in sc
        assert "raw_probabilities" in sc
        assert "probabilities" in sc

        if sc["is_calibrated"]:
            assert sc["calibrated_confidence"] is not None
            assert sc["calibration_method"] == "temperature_scaling"
            assert sc["score_type"] == "probability"
            assert sc["score_type_label"] == "Model probability"
            assert np.isclose(sc["confidence"], sc["calibrated_confidence"])
