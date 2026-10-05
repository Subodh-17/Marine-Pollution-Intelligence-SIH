"""
Tests for the evaluation utility (app/evaluation/) - real precision/recall/
F1/IoU against the bundled sample SAR scenes' actual ground-truth masks.

These assert the utility runs and returns well-formed, bounded numbers - not
what those numbers "should" be, since asserting a specific accuracy figure
here would itself be exactly the kind of unearned metric this project's
policy is to avoid. What IS asserted is the honesty scaffolding: the
ambiguous "lookalike" scene must never be scored, and the no-oil scene must
never be blended into the same aggregate as scenes with a real positive
class.
"""
import os

import pytest

from app.evaluation.metrics import binary_mask_metrics, multiclass_classification_metrics
from app.evaluation.evaluate_samples import run_classical_detector_evaluation, SAMPLE_DIR


class TestBinaryMaskMetrics:
    def test_perfect_match(self):
        import numpy as np
        mask = np.zeros((10, 10), dtype=bool)
        mask[2:5, 2:5] = True
        m = binary_mask_metrics(mask, mask)
        assert m.precision == 1.0 and m.recall == 1.0 and m.f1 == 1.0 and m.iou == 1.0

    def test_both_empty_is_perfect_by_convention(self):
        import numpy as np
        empty = np.zeros((10, 10), dtype=bool)
        m = binary_mask_metrics(empty, empty)
        assert m.precision == 1.0 and m.recall == 1.0 and m.iou == 1.0

    def test_false_positive_on_empty_ground_truth(self):
        import numpy as np
        gt = np.zeros((10, 10), dtype=bool)
        pred = np.zeros((10, 10), dtype=bool)
        pred[0, 0] = True
        m = binary_mask_metrics(pred, gt)
        assert m.precision == 0.0
        assert m.fp == 1

    def test_shape_mismatch_raises(self):
        import numpy as np
        with pytest.raises(ValueError):
            binary_mask_metrics(np.zeros((10, 10), dtype=bool), np.zeros((5, 5), dtype=bool))


class TestMulticlassClassificationMetrics:
    """Cross-checked by hand against sklearn.metrics.classification_report /
    confusion_matrix on the same inputs during development (see the PR/audit
    note in app/evaluation/metrics.py) - sklearn itself is not a project
    dependency, so it is not imported here, but the expected numbers below
    are exactly what it produces for these inputs."""

    LABELS = ["Oil", "No-Oil", "Lookalike"]

    def test_perfect_predictions(self):
        y = ["Oil", "No-Oil", "Lookalike"] * 3
        m = multiclass_classification_metrics(y, y, self.LABELS)
        assert m.accuracy == 1.0
        assert m.macro_f1 == 1.0 and m.weighted_f1 == 1.0
        for label in self.LABELS:
            assert m.per_class[label] == {"precision": 1.0, "recall": 1.0, "f1": 1.0, "support": 3}
        assert m.confusion == [[3, 0, 0], [0, 3, 0], [0, 0, 3]]

    def test_known_confusion_matches_hand_computed_sklearn_equivalent(self):
        y_true = ["Oil"] * 3 + ["No-Oil"] * 3 + ["Lookalike"] * 3
        y_pred = ["Oil", "Oil", "No-Oil", "No-Oil", "No-Oil", "Oil", "Lookalike", "Oil", "Lookalike"]
        m = multiclass_classification_metrics(y_true, y_pred, self.LABELS)
        assert m.confusion == [[2, 1, 0], [1, 2, 0], [1, 0, 2]]
        assert m.accuracy == round(6 / 9, 4)
        assert m.per_class["Oil"] == {"precision": 0.5, "recall": round(2 / 3, 4), "f1": round(4 / 7, 4), "support": 3}
        assert m.per_class["Lookalike"] == {"precision": 1.0, "recall": round(2 / 3, 4), "f1": 0.8, "support": 3}
        assert m.macro_precision == round((0.5 + 2 / 3 + 1.0) / 3, 4)
        # Equal support per class here means weighted == macro, a useful
        # cross-check that the support-weighting arithmetic didn't silently
        # skew the aggregate.
        assert m.weighted_precision == m.macro_precision
        assert m.weighted_f1 == m.macro_f1

    def test_never_predicted_class_has_zero_precision_not_nan(self):
        """A class with zero predicted instances must report precision 0.0
        (an honest, bounded number for a real degenerate case), never a
        silent NaN from a 0/0 division."""
        y_true = ["Oil", "No-Oil", "Lookalike"]
        y_pred = ["No-Oil", "No-Oil", "No-Oil"]  # "Oil"/"Lookalike" never predicted
        m = multiclass_classification_metrics(y_true, y_pred, self.LABELS)
        assert m.per_class["Oil"] == {"precision": 0.0, "recall": 0.0, "f1": 0.0, "support": 1}
        assert m.per_class["Lookalike"] == {"precision": 0.0, "recall": 0.0, "f1": 0.0, "support": 1}
        assert m.per_class["No-Oil"]["precision"] == round(1 / 3, 4)
        assert m.per_class["No-Oil"]["recall"] == 1.0

    def test_mismatched_lengths_raise(self):
        with pytest.raises(ValueError):
            multiclass_classification_metrics(["Oil", "No-Oil"], ["Oil"], self.LABELS)

    def test_unknown_label_raises(self):
        with pytest.raises(ValueError):
            multiclass_classification_metrics(["Oil"], ["Unknown-Class"], self.LABELS)


@pytest.mark.skipif(not os.path.isdir(SAMPLE_DIR), reason="bundled sample SAR data not present")
class TestClassicalDetectorEvaluation:
    def test_runs_and_returns_bounded_real_metrics(self):
        result = run_classical_detector_evaluation()
        assert result["available"] is True
        assert len(result["oil_scenes"]) == 2
        for scene in result["oil_scenes"]:
            assert scene["available"] is True
            for metric in ("precision", "recall", "f1", "iou"):
                assert 0.0 <= scene[metric] <= 1.0
        agg = result["aggregate_over_oil_scenes"]
        for metric in ("precision", "recall", "f1", "iou"):
            assert 0.0 <= agg[metric] <= 1.0

    def test_no_oil_scene_is_a_separate_false_positive_control_not_blended_in(self):
        """Regression guard: a scene with zero ground-truth positive pixels
        must never be averaged into the same aggregate as scenes with a real
        positive class - the degenerate precision/F1 there isn't a graded
        score and would silently distort the aggregate."""
        result = run_classical_detector_evaluation()
        fpc = result["false_positive_control"]
        assert fpc["scene"] == "demo_no_oil_scene_01"
        assert "false_positive_pixels" in fpc
        assert "precision" not in fpc  # never blended as if it were a graded score
        oil_scene_names = {s["scene"] for s in result["oil_scenes"]}
        assert "demo_no_oil_scene_01" not in oil_scene_names

    def test_ambiguous_lookalike_scene_is_never_scored(self):
        """Regression guard against fabricating a metric for a scene whose
        ground-truth mask semantics aren't established in this build."""
        result = run_classical_detector_evaluation()
        excluded_names = {s["scene"] for s in result["excluded_scenes"]}
        assert "demo_lookalike_scene_01" in excluded_names
        oil_scene_names = {s["scene"] for s in result["oil_scenes"]}
        assert "demo_lookalike_scene_01" not in oil_scene_names
        excluded = next(s for s in result["excluded_scenes"] if s["scene"] == "demo_lookalike_scene_01")
        assert "precision" not in excluded and "recall" not in excluded and "f1" not in excluded
        assert "reason_excluded" in excluded

    def test_missing_sample_dir_reports_unavailable_not_a_crash(self, tmp_path):
        result = run_classical_detector_evaluation(sample_dir=str(tmp_path / "does_not_exist"))
        assert result["available"] is False
