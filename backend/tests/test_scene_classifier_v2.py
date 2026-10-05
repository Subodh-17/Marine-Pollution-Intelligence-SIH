"""
Regression tests for `SceneClassifierV2` (app/ml/architectures.py) and the
architecture-aware checkpoint loading it required in app/ml/train.py,
app/ml/evaluate_classifier.py, app/ml/detector.py, and app/ml/export_onnx.py.

Context (see architectures.py::SceneClassifierV2's own docstring and
README_ML_STATUS.md for the full account): a genuine, reported independent
Part III evaluation of the original `SceneClassifier` found 37.56% accuracy
and 0.67% Oil recall - a real failure this project's own sandbox cannot
reproduce directly (no real dataset here), but the architecture itself has
two concrete, auditable structural weaknesses for this specific problem
(global-average-pooling with almost no spatial hierarchy ahead of it, no
max-pooling branch to stay sensitive to a small localized signal). These
tests do NOT claim SceneClassifierV2 achieves any particular real-world
accuracy - no real dataset is available here to measure that - they instead
prove: the architecture is sound (shapes, gradients, a reasonable parameter
budget), and the new "which architecture does this checkpoint use"
machinery threaded through four different call sites is genuinely wired up
correctly everywhere a classifier checkpoint gets loaded, for BOTH
architectures, so a checkpoint trained with either one works everywhere.
"""
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import pytest

try:
    import torch
    import torch.nn.functional as F
    import tifffile
except (ImportError, OSError) as _torch_err:
    pytest.skip(f"torch/tifffile unavailable or blocked by OS policy: {_torch_err}", allow_module_level=True)

from app.ml.architectures import SceneClassifier, SceneClassifierV2
from app.ml import dataset as ds
from app.ml import train as trainmod
from app.ml import evaluate_classifier as evalmod
from app.ml import detector as detector_module
from app.ml import export_onnx as export_onnx_mod


class TestSceneClassifierV2Architecture:
    """Pure architecture checks - no dataset, no training, just the module
    itself. These are the "is the code correct" checks; the synthetic
    training experiment that motivated the redesign (small-signal-vs-
    global-average-pooling) is documented, not re-asserted here, since a
    toy benchmark tuned until it "proves" the point would itself be a kind
    of overfitting to a synthetic proxy - see README_ML_STATUS.md for that
    experiment's honestly-reported, inconclusive result."""

    def test_output_shape_and_class_count(self):
        m = SceneClassifierV2(in_channels=2, num_classes=3)
        x = torch.randn(4, 2, 256, 256)
        out = m(x)
        assert out.shape == (4, 3)

    def test_works_at_non_256_input_sizes(self):
        """Fully convolutional + adaptive pooling - must not hardcode 256."""
        m = SceneClassifierV2(in_channels=2, num_classes=3)
        for h, w in [(64, 64), (128, 192), (32, 32)]:
            out = m(torch.randn(1, 2, h, w))
            assert out.shape == (1, 3)

    def test_gradients_flow_to_every_parameter(self):
        m = SceneClassifierV2(in_channels=2, num_classes=3)
        out = m(torch.randn(2, 2, 64, 64))
        out.sum().backward()
        for name, p in m.named_parameters():
            assert p.grad is not None, f"{name} received no gradient"

    def test_parameter_budget_is_still_lightweight(self):
        """More capacity than the original (23,859 params) by design, but
        must stay well within CPU-friendly, ONNX-exportable territory -
        not a ResNet50. Asserts an order-of-magnitude ceiling, not an exact
        count, so minor architectural tweaks don't make this brittle."""
        m = SceneClassifierV2(in_channels=2, num_classes=3)
        total = sum(p.numel() for p in m.parameters())
        assert total > sum(p.numel() for p in SceneClassifier(in_channels=2, num_classes=3).parameters()), \
            "V2 should have more capacity than the original"
        assert total < 2_000_000, f"{total} params is no longer 'lightweight' for this use case"

    def test_pools_with_both_average_and_max(self):
        """The specific structural fix this redesign is about: confirms
        both pooling branches genuinely exist and are concatenated (not,
        say, one silently unused) by checking the fc layer's input width
        is exactly 2x the final conv channel count."""
        m = SceneClassifierV2(in_channels=2, num_classes=3)
        assert isinstance(m.avg_pool, torch.nn.AdaptiveAvgPool2d)
        assert isinstance(m.max_pool, torch.nn.AdaptiveMaxPool2d)
        final_channels = m.features[-2].conv[-2].num_features  # last BatchNorm2d in the last DoubleConv
        assert m.fc.in_features == final_channels * 2


def _write_tiff(path, arr):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tifffile.imwrite(path, arr)


def _make_fixture_dataset(root: str, size: int = 32):
    rng = np.random.default_rng(0)

    def _write(part_dir, prefix, n, oil_positive):
        img_dir = os.path.join(root, part_dir, f"{prefix}_images")
        mask_dir = os.path.join(root, part_dir, f"{prefix}_mask")
        for i in range(n):
            stem = f"{i + 1:04d}"
            img = rng.normal(size=(size, size, 2)).astype(np.float32)
            mask = np.zeros((size, size), dtype=np.uint8)
            if oil_positive:
                mask[size // 3: size // 2, size // 3: size // 2] = 1
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
def v2_trained_checkpoint(tmp_path):
    """A genuinely trained (1 epoch, tiny synthetic fixture) SceneClassifierV2
    checkpoint, plus the dataset/manifest it came from - proves the whole
    train -> save -> reload chain for the new architecture, not just the
    module in isolation."""
    root = str(tmp_path / "zenodo_sar")
    _make_fixture_dataset(root)
    manifest_dir = os.path.join(root, "manifests")
    ds.build_manifests(root, output_dir=manifest_dir, val_fraction=0.5, seed=1)
    ckpt_dir = str(tmp_path / "checkpoints")
    summary = trainmod.train_model(
        dataset_root=root, model_kind="classifier", architecture="scene_classifier_v2",
        manifest_dir=manifest_dir, epochs=1, batch_size=2, image_size=32, seed=1,
        checkpoint_dir=ckpt_dir,
    )
    best_path = os.path.join(ckpt_dir, "classifier_real_best.pth")
    # Mirror app/ml/promote.py's own effect (a plain file copy) so
    # detector.py's checkpoint discovery - which only looks for the
    # *_promoted filename - finds it, exactly like a real promoted model.
    promoted_path = os.path.join(ckpt_dir, "classifier_real_promoted.pth")
    shutil.copy2(best_path, promoted_path)
    return root, manifest_dir, ckpt_dir, best_path, promoted_path, summary


class TestArchitectureIsRecordedAndReadBack:
    def test_train_py_records_architecture_in_checkpoint(self, v2_trained_checkpoint):
        _, _, _, best_path, _, _ = v2_trained_checkpoint
        raw = torch.load(best_path, map_location="cpu", weights_only=False)
        assert raw["architecture"] == "SceneClassifierV2"

    def test_default_architecture_is_unchanged_original(self, tmp_path):
        """No --architecture flag -> the original SceneClassifier, exactly
        as before this round - an existing script/CI invocation with no
        awareness of this new flag must keep training what it always did."""
        root = str(tmp_path / "zenodo_sar")
        _make_fixture_dataset(root)
        manifest_dir = os.path.join(root, "manifests")
        ds.build_manifests(root, output_dir=manifest_dir, val_fraction=0.5, seed=1)
        ckpt_dir = str(tmp_path / "checkpoints")
        trainmod.train_model(
            dataset_root=root, model_kind="classifier", manifest_dir=manifest_dir,
            epochs=1, batch_size=2, image_size=32, seed=1, checkpoint_dir=ckpt_dir,
        )
        raw = torch.load(os.path.join(ckpt_dir, "classifier_real_best.pth"), map_location="cpu", weights_only=False)
        assert raw["architecture"] == "SceneClassifier"

    def test_evaluate_classifier_loads_v2_checkpoint_correctly(self, v2_trained_checkpoint):
        root, manifest_dir, _, best_path, _, _ = v2_trained_checkpoint
        result = evalmod.evaluate_checkpoint(
            dataset_root=root, checkpoint_path=best_path, manifest_dir=manifest_dir, image_size=32,
        )
        assert result["architecture"] == "SceneClassifierV2"
        assert result["num_test_scenes"] == 6

    def test_evaluate_classifier_still_loads_original_architecture_checkpoint(self, tmp_path):
        root = str(tmp_path / "zenodo_sar")
        _make_fixture_dataset(root)
        manifest_dir = os.path.join(root, "manifests")
        ds.build_manifests(root, output_dir=manifest_dir, val_fraction=0.5, seed=1)
        ckpt_dir = str(tmp_path / "checkpoints")
        trainmod.train_model(
            dataset_root=root, model_kind="classifier", manifest_dir=manifest_dir,
            epochs=1, batch_size=2, image_size=32, seed=1, checkpoint_dir=ckpt_dir,
        )
        result = evalmod.evaluate_checkpoint(
            dataset_root=root, checkpoint_path=os.path.join(ckpt_dir, "classifier_real_best.pth"),
            manifest_dir=manifest_dir, image_size=32,
        )
        assert result["architecture"] == "SceneClassifier"

    def test_detector_engine_loads_v2_checkpoint_with_correct_shape(self, v2_trained_checkpoint):
        """The critical integration point: MLDetectorEngine must instantiate
        the RIGHT architecture class before calling load_state_dict(strict=
        True) - instantiating the wrong one would raise a shape-mismatch
        error and silently fall back to classifier_loaded=False, which is
        exactly the kind of masked failure this test exists to catch."""
        _, _, ckpt_dir, _, _, _ = v2_trained_checkpoint
        engine = detector_module.MLDetectorEngine(checkpoint_dir=ckpt_dir)
        assert engine.classifier_loaded is True, f"load_errors: {engine.load_errors}"
        assert engine.checkpoint_metadata.get("architecture") == "SceneClassifierV2"
        assert type(engine.classifier).__name__ == "SceneClassifierV2"

    def test_detector_engine_still_loads_original_architecture_checkpoint(self, tmp_path):
        root = str(tmp_path / "zenodo_sar")
        _make_fixture_dataset(root)
        manifest_dir = os.path.join(root, "manifests")
        ds.build_manifests(root, output_dir=manifest_dir, val_fraction=0.5, seed=1)
        ckpt_dir = str(tmp_path / "checkpoints")
        trainmod.train_model(
            dataset_root=root, model_kind="classifier", manifest_dir=manifest_dir,
            epochs=1, batch_size=2, image_size=32, seed=1, checkpoint_dir=ckpt_dir,
        )
        shutil.copy2(os.path.join(ckpt_dir, "classifier_real_best.pth"),
                      os.path.join(ckpt_dir, "classifier_real_promoted.pth"))
        engine = detector_module.MLDetectorEngine(checkpoint_dir=ckpt_dir)
        assert engine.classifier_loaded is True, f"load_errors: {engine.load_errors}"
        assert type(engine.classifier).__name__ == "SceneClassifier"

    def test_export_onnx_uses_correct_architecture_for_v2_checkpoint(self, v2_trained_checkpoint):
        _, _, ckpt_dir, _, _, _ = v2_trained_checkpoint
        rc = export_onnx_mod.main(["--checkpoint-dir", ckpt_dir])
        assert rc == 0
        with open(os.path.join(ckpt_dir, "onnx_export_manifest.json")) as f:
            manifest = json.load(f)
        assert manifest["models"]["classifier_real_best.onnx"]["architecture"] == "SceneClassifierV2"
        # And the exported ONNX graph is actually loadable and runs -
        # proves load_state_dict(strict=True) genuinely succeeded against
        # the right shape, not just that the manifest *says* V2.
        import onnxruntime as ort
        sess = ort.InferenceSession(os.path.join(ckpt_dir, "classifier_real_best.onnx"))
        input_name = sess.get_inputs()[0].name
        x = np.random.randn(1, 2, 32, 32).astype(np.float32)
        out = sess.run(None, {input_name: x})
        assert out[0].shape == (1, 3)
