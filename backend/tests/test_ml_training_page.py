"""
Tests for the ML Training page's backend surface:

  GET /api/ml/status              (extended - see app/main.py)
  GET /api/ml/training-history    (new)
  GET /api/ml/metrics             (new)

These enforce the same honesty contract as the rest of the ML subsystem
(see app/ml/README_ML_STATUS.md): a missing, malformed, or not-yet-real
checkpoint/history/evaluation-report must always report itself as
unavailable with an explicit reason - NEVER a fabricated epoch, loss, or
metric. Where torch is available, some tests also exercise the real
training -> evaluation -> promotion pipeline end to end (not mocked) so a
genuinely real-Zenodo-trained checkpoint is asserted against real numbers.
"""
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.ml import detector as detector_module
from app.ml import onnx_engine as onnx_engine_module

client = TestClient(app)

SHIPPED_CHECKPOINT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "ml_checkpoints")


# ---------------------------------------------------------------------------
# Response-shape tests - must hold regardless of torch/checkpoint state.
# ---------------------------------------------------------------------------

class TestResponseShapes:
    def test_status_endpoint_shape_includes_new_fields(self):
        r = client.get("/api/ml/status")
        assert r.status_code == 200
        body = r.json()
        assert "genuine_evaluation_metrics_available" in body
        assert "num_test_samples" in body
        assert "provenance" in body
        assert "model_info" in body

    def test_training_history_endpoint_shape(self):
        r = client.get("/api/ml/training-history")
        assert r.status_code == 200
        body = r.json()
        for key in ("available", "provenance", "model_component", "source_file",
                    "metric_name", "epochs", "best_epoch", "total_epochs", "reason"):
            assert key in body
        assert isinstance(body["epochs"], list)

    def test_metrics_endpoint_shape(self):
        r = client.get("/api/ml/metrics")
        assert r.status_code == 200
        body = r.json()
        for key in ("metrics_available", "provenance", "reason", "precision", "recall",
                    "f1", "iou", "dice", "pixel_accuracy", "evaluation_dataset",
                    "evaluation_split", "evaluation_date", "num_evaluated_samples",
                    "per_category", "caveats"):
            assert key in body


# ---------------------------------------------------------------------------
# torch-unavailable fallback - mirrors /api/ml/status's own established
# pattern (see app/main.py) rather than inventing a different shape.
# ---------------------------------------------------------------------------

class TestTorchUnavailableFallback:
    """`get_ml_engine()` (app/ml/detector.py) is a lazy MODULE-LEVEL
    singleton: it is built once, on first call, and every later call
    (regardless of which test makes it) returns that same cached instance.
    By the time this class runs, an earlier test file
    (test_ml_and_real_satellite.py, which genuinely exercises
    `/api/satellite/upload?detector=ml` against the real bundled
    checkpoints) has already triggered that first real build - with
    whatever `TORCH_AVAILABLE` was at THAT moment, not whatever a later
    test monkeypatches it to. Without resetting the singleton, the
    monkeypatch below would silently do nothing (the cached engine, already
    genuinely loaded, is handed back as-is) and every test in this class
    would be asserting against stale state, not the torch-unavailable
    fallback path it's named for and claims to exercise.

    This fixture forces exactly one fresh `MLDetectorEngine()` build per
    test - under the patched `TORCH_AVAILABLE` - and restores whatever
    engine existed before, so this class's forced-unavailable engine never
    leaks into any other test file's real-checkpoint assumptions.

    IMPORTANT - this class patches away BOTH runtimes, not just torch:
    once real ONNX artifacts + onnx_export_manifest.json exist (the normal
    state after `python -m app.ml.export_onnx`, see app/ml/onnx_engine.py),
    `TORCH_AVAILABLE = False` alone no longer makes the engine unavailable
    - the engine's own documented "auto" preference tries the ONNX runtime
    FIRST and only needs torch as the LOCAL/dev fallback (see
    MLDetectorEngine.__init__'s "Runtime selection" comment). That is
    correct, intended behavior, not a regression: it's the whole point of
    shipping ONNX at all (a 512 MB/0.1 CPU production target that cannot
    install PyTorch). So genuinely testing "no working ML runtime at all"
    requires patching `onnx_engine.ONNX_AVAILABLE` to False too - see
    TestOnnxServesWithoutTorch below for the complementary, equally
    important case (torch absent, ONNX present -> still genuinely
    available), which is what actually runs in this project's real
    production deployment.
    """

    @pytest.fixture(autouse=True)
    def _fresh_engine_per_test(self):
        previous_engine = detector_module._engine
        detector_module._engine = None
        yield
        detector_module._engine = previous_engine

    def test_training_history_reports_unavailable_without_torch(self, monkeypatch):
        monkeypatch.setattr(detector_module, "TORCH_AVAILABLE", False)
        monkeypatch.setattr(onnx_engine_module, "ONNX_AVAILABLE", False)
        r = client.get("/api/ml/training-history")
        body = r.json()
        assert body["available"] is False
        assert body["epochs"] == []
        assert body["reason"]

    def test_metrics_reports_unavailable_without_torch(self, monkeypatch):
        monkeypatch.setattr(detector_module, "TORCH_AVAILABLE", False)
        monkeypatch.setattr(onnx_engine_module, "ONNX_AVAILABLE", False)
        r = client.get("/api/ml/metrics")
        body = r.json()
        assert body["metrics_available"] is False
        assert body["precision"] is None
        assert body["reason"]

    def test_status_reports_unavailable_without_torch(self, monkeypatch):
        monkeypatch.setattr(detector_module, "TORCH_AVAILABLE", False)
        monkeypatch.setattr(onnx_engine_module, "ONNX_AVAILABLE", False)
        r = client.get("/api/ml/status")
        body = r.json()
        assert body["torch_available"] is False
        assert body["genuine_evaluation_metrics_available"] is False
        assert body["model_info"] is None


# ---------------------------------------------------------------------------
# The complementary, equally important case: torch absent, ONNX genuinely
# present - this IS this project's real production shape (see
# requirements.txt: onnxruntime ships in the base install, torch only in
# requirements-ml.txt). Added alongside the class above specifically
# because exporting real ONNX artifacts (python -m app.ml.export_onnx) is
# what exposed TestTorchUnavailableFallback's outdated "no torch -> nothing
# works" premise - this asserts the CORRECT replacement premise instead of
# just deleting the coverage.
# ---------------------------------------------------------------------------

class TestOnnxServesWithoutTorch:
    """Requires the real .onnx artifacts + manifest to actually be present
    (produced by `python -m app.ml.export_onnx`) - skips honestly, never
    fabricating this scenario, if they are not."""

    @pytest.fixture(autouse=True)
    def _fresh_engine_per_test(self):
        previous_engine = detector_module._engine
        detector_module._engine = None
        yield
        detector_module._engine = previous_engine

    def _onnx_artifacts_present(self):
        ckpt_dir = detector_module.CHECKPOINT_DIR
        manifest = os.path.join(ckpt_dir, "onnx_export_manifest.json")
        clf = os.path.join(ckpt_dir, "classifier_real_best.onnx")
        seg = os.path.join(ckpt_dir, "spill_unet_real_best.onnx")
        return os.path.exists(manifest) and os.path.exists(clf) and os.path.exists(seg)

    def test_ml_status_is_available_via_onnx_even_with_torch_unavailable(self, monkeypatch):
        if not self._onnx_artifacts_present():
            pytest.skip("exported .onnx artifacts + manifest not present - run "
                        "`python -m app.ml.export_onnx` (needs PyTorch, offline) to produce them")
        monkeypatch.setattr(detector_module, "TORCH_AVAILABLE", False)
        r = client.get("/api/ml/status")
        body = r.json()
        # The headline claim this test exists to prove: a torch-free
        # process still gets a genuinely working, genuinely real ML engine,
        # because it is served by ONNX Runtime instead.
        assert body["torch_available"] is False
        assert body["classifier_loaded"] is True
        assert body["segmenter_loaded"] is True
        assert body["provenance"] == detector_module.PROVENANCE_ML_REAL
        engine = detector_module.get_ml_engine()
        assert engine.runtime == "onnxruntime_cpu"


# ---------------------------------------------------------------------------
# Shipped synthetic checkpoint - the actual state of this project as
# submitted: no evaluation report ships with it, so genuine metrics must
# never be fabricated for it.
# ---------------------------------------------------------------------------

class TestShippedSyntheticCheckpoint:
    def test_no_genuine_metrics_fabricated_for_shipped_checkpoint(self):
        if not detector_module.TORCH_AVAILABLE:
            pytest.skip("requires torch to load the shipped checkpoint")
        engine = detector_module.get_ml_engine()
        if engine.provenance != detector_module.PROVENANCE_ML_SYNTHETIC:
            pytest.skip("shipped checkpoint state was replaced with a real one in this environment")

        status = client.get("/api/ml/status").json()
        metrics = client.get("/api/ml/metrics").json()

        assert status["genuine_evaluation_metrics_available"] is False
        assert metrics["metrics_available"] is False
        assert metrics["precision"] is None and metrics["iou"] is None
        assert "evaluation" in metrics["reason"].lower() or "report" in metrics["reason"].lower()

    def test_shipped_synthetic_training_history_is_the_real_shipped_file(self):
        if not detector_module.TORCH_AVAILABLE:
            pytest.skip("requires torch to load the shipped checkpoint")
        engine = detector_module.get_ml_engine()
        if engine.provenance != detector_module.PROVENANCE_ML_SYNTHETIC:
            pytest.skip("shipped checkpoint state was replaced with a real one in this environment")

        body = client.get("/api/ml/training-history").json()
        assert body["available"] is True
        assert body["model_component"] == "classifier"
        assert body["source_file"] == "classifier_training_history.json"
        assert body["total_epochs"] == len(body["epochs"]) > 0
        # Every loss/metric value must be a genuine number carried over from
        # the shipped JSON file, never null/fabricated, for a file that DOES
        # exist and parses cleanly.
        for e in body["epochs"]:
            assert isinstance(e["train_loss"], (int, float))
            assert isinstance(e["val_loss"], (int, float))


# ---------------------------------------------------------------------------
# Malformed / missing file handling - must degrade to "unavailable" with a
# reason, never crash the request or guess a value.
# ---------------------------------------------------------------------------

class TestMalformedAndMissingFiles:
    def _engine_with_shipped_checkpoints(self, tmp_path):
        ckpt_dir = str(tmp_path / "checkpoints")
        os.makedirs(ckpt_dir)
        shutil.copy2(os.path.join(SHIPPED_CHECKPOINT_DIR, "classifier_checkpoint.pth"),
                     os.path.join(ckpt_dir, "classifier_checkpoint.pth"))
        shutil.copy2(os.path.join(SHIPPED_CHECKPOINT_DIR, "spill_unet.pt"),
                     os.path.join(ckpt_dir, "spill_unet.pt"))
        return ckpt_dir

    def test_malformed_training_history_json_reports_unavailable_not_crash(self, tmp_path, monkeypatch):
        if not detector_module.TORCH_AVAILABLE:
            pytest.skip("requires torch")
        ckpt_dir = self._engine_with_shipped_checkpoints(tmp_path)
        with open(os.path.join(ckpt_dir, "classifier_training_history.json"), "w") as f:
            f.write("{this is not valid json")
        engine = detector_module.MLDetectorEngine(checkpoint_dir=ckpt_dir)
        monkeypatch.setattr(detector_module, "get_ml_engine", lambda: engine)

        r = client.get("/api/ml/training-history")
        assert r.status_code == 200
        body = r.json()
        assert body["available"] is False
        assert "parsed" in body["reason"]

    def test_missing_checkpoint_reports_not_trained_state(self, tmp_path, monkeypatch):
        if not detector_module.TORCH_AVAILABLE:
            pytest.skip("requires torch")
        ckpt_dir = str(tmp_path / "empty_checkpoints")
        os.makedirs(ckpt_dir)
        engine = detector_module.MLDetectorEngine(checkpoint_dir=ckpt_dir)
        monkeypatch.setattr(detector_module, "get_ml_engine", lambda: engine)

        status = client.get("/api/ml/status").json()
        history = client.get("/api/ml/training-history").json()
        metrics = client.get("/api/ml/metrics").json()

        assert status["classifier_loaded"] is False
        assert status["segmenter_loaded"] is False
        assert status["provenance"] == "ML_UNAVAILABLE"
        assert history["available"] is False
        assert metrics["metrics_available"] is False

    def test_malformed_evaluation_report_reports_unavailable_not_crash(self, tmp_path, monkeypatch):
        if not detector_module.TORCH_AVAILABLE:
            pytest.skip("requires torch")
        ckpt_dir = self._engine_with_shipped_checkpoints(tmp_path)
        with open(os.path.join(ckpt_dir, "evaluation_report.json"), "w") as f:
            f.write("not json at all {{{")
        engine = detector_module.MLDetectorEngine(checkpoint_dir=ckpt_dir)
        monkeypatch.setattr(detector_module, "get_ml_engine", lambda: engine)

        r = client.get("/api/ml/metrics")
        assert r.status_code == 200
        body = r.json()
        assert body["metrics_available"] is False
        assert "parsed" in body["reason"]

    def test_evaluation_report_for_untrusted_source_is_refused(self, tmp_path, monkeypatch):
        if not detector_module.TORCH_AVAILABLE:
            pytest.skip("requires torch")
        ckpt_dir = self._engine_with_shipped_checkpoints(tmp_path)
        with open(os.path.join(ckpt_dir, "evaluation_report.json"), "w") as f:
            json.dump({
                "checkpoint_trained_on": "NOT_THE_REAL_DATASET",
                "overall": {"precision": 0.99, "recall": 0.99, "f1": 0.99, "iou": 0.99,
                            "dice": 0.99, "pixel_accuracy": 0.99, "n_scenes": 100},
            }, f)
        engine = detector_module.MLDetectorEngine(checkpoint_dir=ckpt_dir)
        monkeypatch.setattr(detector_module, "get_ml_engine", lambda: engine)

        body = client.get("/api/ml/metrics").json()
        assert body["metrics_available"] is False
        assert body["precision"] is None
        assert "checkpoint_trained_on" in body["reason"]


# ---------------------------------------------------------------------------
# End-to-end real-Zenodo-trained state - a genuinely trained + evaluated +
# promoted checkpoint (not mocked), so these assert against real numbers.
#
# The torch/tifffile imports needed for this are deliberately scoped to the
# fixture below (not module level) - an environment missing either one must
# only skip THESE end-to-end tests, never the response-shape/fallback tests
# above, which don't need them.
# ---------------------------------------------------------------------------

def _write_tiff(path, arr):
    import tifffile
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tifffile.imwrite(path, arr)


def _make_fixture_dataset(root, size=16):
    rng = np.random.default_rng(0)

    def _write(part_dir, prefix, n, oil_positive):
        img_dir = os.path.join(root, part_dir, f"{prefix}_images")
        mask_dir = os.path.join(root, part_dir, f"{prefix}_mask")
        for i in range(n):
            stem = f"{i + 1:04d}"
            img = rng.normal(size=(size, size, 2)).astype(np.float32)
            mask = np.zeros((size, size), dtype=np.uint8)
            if oil_positive:
                mask[size // 3:size // 2, size // 3:size // 2] = 1
            _write_tiff(os.path.join(img_dir, f"{stem}.tif"), img)
            _write_tiff(os.path.join(mask_dir, f"{stem}.tif"), mask)

    _write("part1", "01_Train_Val_Oil_Spill", 4, True)
    _write("part2", "01_Train_Val_Lookalike", 4, False)
    _write("part2", "01_Train_Val_No_Oil", 4, False)
    base = os.path.join(root, "part3", "02_Test_images_and_ground_truth")
    for subfolder, oil_positive in (("Oil", True), ("Lookalike", False), ("No_oil", False)):
        for i in range(2):
            stem = f"{i + 1:04d}"
            img = rng.normal(size=(size, size, 2)).astype(np.float32)
            mask = np.zeros((size, size), dtype=np.uint8)
            if oil_positive:
                mask[1:3, 1:3] = 1
            _write_tiff(os.path.join(base, "Images", subfolder, f"{stem}.tif"), img)
            _write_tiff(os.path.join(base, "Mask", subfolder, f"{stem}.tif"), mask)


@pytest.fixture
def real_trained_evaluated_promoted_engine(tmp_path):
    """A genuinely trained + evaluated + promoted checkpoint - not a mock -
    so tests using this exercise the real training/evaluation/promotion
    pipeline end to end, the same way test_promote_and_precedence.py does."""
    try:
        import torch  # noqa: F401
        import tifffile  # noqa: F401
    except (ImportError, OSError) as _e:
        pytest.skip(f"torch/tifffile unavailable or blocked by OS policy: {_e}")
    from app.ml import dataset as ds
    from app.ml import train as trainmod
    from app.ml import evaluate_real as evalmod
    from app.ml import promote as promotemod

    root = str(tmp_path / "zenodo_sar")
    _make_fixture_dataset(root)
    manifest_dir = os.path.join(root, "manifests")
    ds.build_manifests(root, output_dir=manifest_dir, val_fraction=0.5, seed=1)
    ckpt_dir = str(tmp_path / "checkpoints")
    trainmod.train_model(dataset_root=root, model_kind="segmenter", manifest_dir=manifest_dir,
                          epochs=2, batch_size=2, image_size=16, seed=1, checkpoint_dir=ckpt_dir)
    trainmod.train_model(dataset_root=root, model_kind="classifier", manifest_dir=manifest_dir,
                          epochs=2, batch_size=2, image_size=16, seed=1, checkpoint_dir=ckpt_dir)
    evalmod.main([
        "--dataset-root", root, "--manifest-dir", manifest_dir,
        "--checkpoint", os.path.join(ckpt_dir, "spill_unet_real_best.pt"),
        "--image-size", "16", "--output-dir", ckpt_dir,
    ])
    rc = promotemod.main(["--checkpoint-dir", ckpt_dir, "--i-understand-this-changes-inference-behavior"])
    assert rc == 0
    return detector_module.MLDetectorEngine(checkpoint_dir=ckpt_dir)


class TestRealZenodoTrainedEndToEnd:
    def test_status_reports_real_zenodo_trained(self, real_trained_evaluated_promoted_engine, monkeypatch):
        monkeypatch.setattr(detector_module, "get_ml_engine", lambda: real_trained_evaluated_promoted_engine)
        body = client.get("/api/ml/status").json()
        assert body["provenance"] == "ML_REAL_ZENODO_TRAINED"
        assert body["genuine_evaluation_metrics_available"] is True
        assert body["num_test_samples"] == 6
        assert body["model_info"]["is_real_data_trained"] is True
        assert body["model_info"]["dataset_type"] == "real_sentinel1_sar_geotiff"

    def test_training_history_reports_segmenters_own_real_history(self, real_trained_evaluated_promoted_engine, monkeypatch):
        monkeypatch.setattr(detector_module, "get_ml_engine", lambda: real_trained_evaluated_promoted_engine)
        body = client.get("/api/ml/training-history").json()
        assert body["available"] is True
        assert body["provenance"] == "ML_REAL_ZENODO_TRAINED"
        # Must read the SEGMENTER's own real history file - never silently
        # substitute the shipped classifier's synthetic history just
        # because that file happens to also exist on disk.
        assert body["model_component"] == "segmenter"
        assert body["source_file"] == "spill_unet_real_training_history.json"
        assert body["metric_name"] == "iou"
        assert body["total_epochs"] == 2
        assert body["best_epoch"] is not None

    def test_metrics_reports_genuine_real_evaluation(self, real_trained_evaluated_promoted_engine, monkeypatch):
        monkeypatch.setattr(detector_module, "get_ml_engine", lambda: real_trained_evaluated_promoted_engine)
        body = client.get("/api/ml/metrics").json()
        assert body["metrics_available"] is True
        assert body["reason"] is None
        assert body["precision"] is not None
        assert body["evaluation_split"] == "test"
        assert body["num_evaluated_samples"] == 6
        assert body["per_category"] is not None

    def test_metrics_refused_when_report_no_longer_matches_active_checkpoint(self, real_trained_evaluated_promoted_engine, monkeypatch):
        # Simulate a later retrain that reused the `_best.pt` filename
        # without re-running evaluate_real.py: the PROMOTED checkpoint
        # (and its checksum, fixed at engine-load time) is untouched, but
        # the file the stale report says it evaluated has now changed.
        engine = real_trained_evaluated_promoted_engine
        best_path = os.path.join(engine.checkpoint_dir, "spill_unet_real_best.pt")
        with open(best_path, "wb") as f:
            f.write(b"completely different bytes - simulates an untracked retrain")

        monkeypatch.setattr(detector_module, "get_ml_engine", lambda: engine)
        body = client.get("/api/ml/metrics").json()
        assert body["metrics_available"] is False
        assert "different checkpoint" in body["reason"]
        # Never falls back to showing the old numbers anyway.
        assert body["precision"] is None
