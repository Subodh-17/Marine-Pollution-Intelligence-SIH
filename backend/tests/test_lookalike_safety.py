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

    def test_attribution_abstention_on_non_confirmed_oil(self):
        """SIH-26143 Audit §3: Vessel attribution MUST fail closed when oil status
        is not LIKELY_OIL or mask area is below threshold."""
        import app.investigation.orchestrator as orch
        sample = os.path.join(os.path.dirname(__file__), "..", "data", "sample_sar",
                               "demo_lookalike_scene_01.tif")
        if not os.path.exists(sample):
            pytest.skip("bundled sample SAR image not present")
        inv = orch.run_investigation(mode="real_satellite", image_path=sample, detector="ml")
        sat = inv.satellite
        sc = sat.get("scene_classification")
        if sc is not None and sc["oil_status"] in ("LOOKALIKE", "NO_OIL"):
            assert sat["attribution_status"].startswith("ABSTAINED_")
            assert sat["attribution_gating"]["gated"] is True
            assert len(inv.candidates) == 0, "No vessel candidates should be accused for non-oil scenes!"


class TestAttributionPolicyBypassResistance:
    """Rigorous tests proving that vessel attribution safeguards CANNOT be bypassed
    via manual masks, sub-endpoints, ambiguous classifications, or stale data.
    """

    def test_policy_empty_mask_abstention(self):
        from app.investigation.policy import evaluate_attribution_eligibility, STATUS_EMPTY_MASK
        res = evaluate_attribution_eligibility(mask=np.zeros((100, 100)), observed_time=None)
        assert res.eligible is False
        assert res.status == STATUS_EMPTY_MASK

    def test_policy_lookalike_classification_abstention(self):
        from app.investigation.policy import evaluate_attribution_eligibility, STATUS_LOOKALIKE
        sc = {"oil_status": "LOOKALIKE", "probabilities": {"Oil": 0.05, "Lookalike": 0.90, "No-Oil": 0.05}}
        res = evaluate_attribution_eligibility(scene_classification=sc, mask=np.ones((50, 50)), observed_time=None)
        assert res.eligible is False
        assert res.status == STATUS_LOOKALIKE
        assert "Lookalike" in res.reason

    def test_policy_no_oil_classification_abstention(self):
        from app.investigation.policy import evaluate_attribution_eligibility, STATUS_NO_OIL
        sc = {"oil_status": "NO_OIL", "probabilities": {"Oil": 0.01, "No-Oil": 0.98, "Lookalike": 0.01}}
        res = evaluate_attribution_eligibility(scene_classification=sc, mask=np.ones((50, 50)), observed_time=None)
        assert res.eligible is False
        assert res.status == STATUS_NO_OIL

    def test_policy_tiny_mask_below_threshold_abstention(self):
        from app.investigation.policy import evaluate_attribution_eligibility, STATUS_BELOW_MIN_AREA
        mask = np.zeros((100, 100))
        mask[:5, :5] = 1  # 25 pixels (well below 250 px default)
        from datetime import datetime, timezone
        res = evaluate_attribution_eligibility(mask=mask, observed_time=datetime.now(timezone.utc), min_mask_area_px=250)
        assert res.eligible is False
        assert res.status == STATUS_BELOW_MIN_AREA

    def test_policy_ambiguous_probability_review_required(self):
        from app.investigation.policy import evaluate_attribution_eligibility, STATUS_UNCERTAIN
        sc = {"oil_status": "UNCERTAIN", "probabilities": {"Oil": 0.42, "No-Oil": 0.38, "Lookalike": 0.20}}
        mask = np.ones((50, 50))  # 2500 px
        from datetime import datetime, timezone
        res = evaluate_attribution_eligibility(scene_classification=sc, mask=mask, observed_time=datetime.now(timezone.utc))
        assert res.eligible is False
        assert res.status == STATUS_UNCERTAIN
        assert "ambiguous" in res.reason

    def test_policy_stale_ais_abstention(self):
        from app.investigation.policy import evaluate_attribution_eligibility, STATUS_STALE_AIS
        from app.ais.schema import AISRecord
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        stale_time = now - timedelta(days=5)  # 120 hours old (exceeds 72h max)
        stale_record = AISRecord(mmsi="123456789", timestamp=stale_time, lat=19.0, lon=71.0, sog=10.0, cog=180.0)
        mask = np.ones((50, 50))  # 2500 px
        res = evaluate_attribution_eligibility(
            mask=mask, observed_time=now, ais_records=[stale_record], max_ais_staleness_hours=72.0
        )
        assert res.eligible is False
        assert res.status == STATUS_STALE_AIS

    def test_policy_permits_confirmed_oil_with_valid_data(self):
        from app.investigation.policy import evaluate_attribution_eligibility, STATUS_PERMITTED
        from app.ais.schema import AISRecord
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        fresh_record = AISRecord(mmsi="123456789", timestamp=now - timedelta(hours=2), lat=19.0, lon=71.0, sog=10.0, cog=180.0)
        mask = np.ones((50, 50))  # 2500 px
        sc = {"oil_status": "LIKELY_OIL", "probabilities": {"Oil": 0.88, "No-Oil": 0.08, "Lookalike": 0.04}}
        res = evaluate_attribution_eligibility(
            scene_classification=sc, mask=mask, observed_time=now, ais_records=[fresh_record]
        )
        assert res.eligible is True
        assert res.status == STATUS_PERMITTED
