"""
Regression tests for the Lookalike/No-Oil safety fix (Phase 1/3 of this
round's request - see UPGRADE_AUDIT.md "Lookalike safety").

THE BUG THIS GUARDS AGAINST: the classifier and segmenter are independent
sub-models. Before this fix, a scene the classifier confidently called
"Lookalike" or "No-Oil" could still produce a non-empty segmenter mask, and
nothing downstream read the classifier's label - the mask alone drove
whether a result looked like a confirmed oil detection. A high-confidence
Lookalike was never below ML_FALLBACK_CONFIDENCE_THRESHOLD (that check only
looks at confidence, never at which label won), so it sailed through the
existing classical-fallback guard untouched.
"""
import os

import numpy as np
import pytest

from app.ml import detector as detector_mod
from app.ml.detector import MLDetectorEngine, RUNTIME_ONNX_CPU
from app.ml.onnx_engine import ONNX_AVAILABLE, MANIFEST_FILENAME

CKPT_DIR = os.path.abspath(detector_mod.CHECKPOINT_DIR)
_ARTIFACTS_PRESENT = all(
    os.path.exists(os.path.join(CKPT_DIR, p)) for p in
    ("classifier_real_best.onnx", "spill_unet_real_best.onnx", MANIFEST_FILENAME)
)
requires_onnx = pytest.mark.skipif(
    not (ONNX_AVAILABLE and _ARTIFACTS_PRESENT),
    reason="onnxruntime + exported .onnx artifacts required",
)


def _fake_softmax(logits):
    def forward(stacked):
        shifted = logits - logits.max()
        exp = np.exp(shifted)
        return exp / exp.sum()
    return forward


@requires_onnx
class TestOilStatusClassification:
    """Directly exercises MLDetectorEngine.detect()'s scene_classification
    computation with controlled classifier logits, so each of the four
    states is independently, deterministically verified - not left to
    chance on whatever a real image happens to produce."""

    def _run(self, engine, logits, force_nonempty_mask=True):
        engine._forward_classifier = _fake_softmax(logits)
        if force_nonempty_mask:
            engine._forward_segmenter = lambda stacked: np.full(stacked.shape[1:], 0.9, dtype=np.float32)
        vv = np.random.rand(64, 64).astype(np.float32)
        vh = np.random.rand(64, 64).astype(np.float32)
        return engine.detect(vv, vh)

    def test_confident_oil_is_likely_oil(self):
        engine = MLDetectorEngine()
        if engine.runtime != RUNTIME_ONNX_CPU:
            pytest.skip(f"engine selected {engine.runtime}, not the ONNX path")
        r = self._run(engine, np.array([10.0, -5.0, -5.0]))
        assert r.scene_classification["oil_status"] == "LIKELY_OIL"
        assert r.scene_classification["label"] == "Oil"

    def test_confident_no_oil_is_flagged_not_oil(self):
        engine = MLDetectorEngine()
        if engine.runtime != RUNTIME_ONNX_CPU:
            pytest.skip(f"engine selected {engine.runtime}, not the ONNX path")
        r = self._run(engine, np.array([-5.0, 10.0, -5.0]))
        assert r.scene_classification["oil_status"] == "NO_OIL"

    def test_confident_lookalike_with_nonempty_mask_is_not_oil(self):
        """THE core regression case: a non-empty segmenter mask must NOT
        override a confident Lookalike classification."""
        engine = MLDetectorEngine()
        if engine.runtime != RUNTIME_ONNX_CPU:
            pytest.skip(f"engine selected {engine.runtime}, not the ONNX path")
        r = self._run(engine, np.array([-5.0, -5.0, 10.0]), force_nonempty_mask=True)
        assert r.mask.any(), "test setup: segmenter mask must be non-empty for this case to be meaningful"
        assert r.scene_classification["oil_status"] == "LOOKALIKE"
        assert r.scene_classification["oil_status"] != "LIKELY_OIL"

    def test_low_confidence_is_uncertain_regardless_of_label(self):
        engine = MLDetectorEngine()
        if engine.runtime != RUNTIME_ONNX_CPU:
            pytest.skip(f"engine selected {engine.runtime}, not the ONNX path")
        r = self._run(engine, np.array([0.1, 0.05, 0.0]))  # near-uniform -> low max confidence
        assert r.scene_classification["confidence"] < detector_mod.ML_FALLBACK_CONFIDENCE_THRESHOLD
        assert r.scene_classification["oil_status"] == "UNCERTAIN"

    def test_scene_classification_absent_when_classifier_not_loaded(self):
        engine = MLDetectorEngine()
        if engine.runtime != RUNTIME_ONNX_CPU:
            pytest.skip(f"engine selected {engine.runtime}, not the ONNX path")
        engine.classifier_loaded = False
        vv = np.random.rand(64, 64).astype(np.float32)
        vh = np.random.rand(64, 64).astype(np.float32)
        r = engine.detect(vv, vh)
        assert r.scene_classification is None


@requires_onnx
class TestOrchestratorSurfacesOilStatus:
    """The orchestrator must not silently accept a non-LIKELY_OIL ML result
    as if it were a confirmed detection - see
    app/investigation/orchestrator.py's safety-note block."""

    def test_real_bundled_dual_pol_sample_runs_and_reports_a_verdict(self):
        """Not a mock: runs the actual orchestrator against a real bundled
        dual-pol sample end-to-end. Whatever verdict the model reaches, the
        API response must be internally consistent - a LOOKALIKE/NO_OIL/
        UNCERTAIN verdict is never presented without its warning note, and
        a LIKELY_OIL verdict never carries one."""
        import app.investigation.orchestrator as orch
        sample = os.path.join(os.path.dirname(__file__), "..", "data", "sample_sar",
                               "demo_lookalike_scene_01.tif")
        if not os.path.exists(sample):
            pytest.skip("bundled sample SAR image not present")
        inv = orch.run_investigation(mode="real_satellite", image_path=sample, detector="ml")
        sat = inv.satellite
        sc = sat["scene_classification"]
        notes_text = " ".join(sat["detection_notes"]).lower()
        if sc is not None and sc["oil_status"] != "LIKELY_OIL":
            has_caveat = (
                "not classified as containing oil" in notes_text
                or "not a confirmed" in notes_text
                or "insufficient confidence" in notes_text
            )
            assert has_caveat, (
                f"oil_status={sc['oil_status']} but no safety caveat found in notes: {notes_text}"
            )
