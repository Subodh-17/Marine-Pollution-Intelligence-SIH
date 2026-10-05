import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import pytest

try:
    import torch
    import tifffile
except (ImportError, OSError) as _torch_err:
    pytest.skip(f"torch/tifffile unavailable or blocked by OS policy: {_torch_err}", allow_module_level=True)

from app.ml import dataset as ds
from app.ml import train as trainmod
from app.ml import evaluate_real as evalmod
from app.ml import promote as promotemod
from app.ml.detector import MLDetectorEngine, PROVENANCE_ML_REAL, PROVENANCE_ML_SYNTHETIC


def _write_tiff(path, arr):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tifffile.imwrite(path, arr)


def _make_fixture_dataset(root: str, size: int = 16):
    rng = np.random.default_rng(0)

    def _write(part_dir, prefix, category, n, oil_positive):
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

    _write("part1", "01_Train_Val_Oil_Spill", ds.CATEGORY_OIL, 4, True)
    _write("part2", "01_Train_Val_Lookalike", ds.CATEGORY_LOOKALIKE, 4, False)
    _write("part2", "01_Train_Val_No_Oil", ds.CATEGORY_NO_OIL, 4, False)

    base = os.path.join(root, "part3", "02_Test_images_and_ground_truth")
    for category, subfolder, oil_positive in (
        (ds.CATEGORY_OIL, "Oil", True), (ds.CATEGORY_LOOKALIKE, "Lookalike", False), (ds.CATEGORY_NO_OIL, "No_oil", False),
    ):
        for i in range(2):
            stem = f"{i + 1:04d}"
            img = rng.normal(size=(size, size, 2)).astype(np.float32)
            mask = np.zeros((size, size), dtype=np.uint8)
            if oil_positive:
                mask[1:3, 1:3] = 1
            _write_tiff(os.path.join(base, "Images", subfolder, f"{stem}.tif"), img)
            _write_tiff(os.path.join(base, "Mask", subfolder, f"{stem}.tif"), mask)


@pytest.fixture
def trained_and_evaluated_checkpoint_dir(tmp_path):
    root = str(tmp_path / "zenodo_sar")
    _make_fixture_dataset(root)
    manifest_dir = os.path.join(root, "manifests")
    ds.build_manifests(root, output_dir=manifest_dir, val_fraction=0.5, seed=1)
    ckpt_dir = str(tmp_path / "checkpoints")
    trainmod.train_model(dataset_root=root, model_kind="segmenter", manifest_dir=manifest_dir,
                          epochs=1, batch_size=2, image_size=16, seed=1, checkpoint_dir=ckpt_dir)
    trainmod.train_model(dataset_root=root, model_kind="classifier", manifest_dir=manifest_dir,
                          epochs=1, batch_size=2, image_size=16, seed=1, checkpoint_dir=ckpt_dir)
    evalmod.main([
        "--dataset-root", root, "--manifest-dir", manifest_dir,
        "--checkpoint", os.path.join(ckpt_dir, "spill_unet_real_best.pt"),
        "--image-size", "16", "--output-dir", ckpt_dir,
    ])
    return ckpt_dir


class TestPromoteSafetyGate:
    def test_promote_refuses_without_confirmation_flag(self, trained_and_evaluated_checkpoint_dir):
        rc = promotemod.main(["--checkpoint-dir", trained_and_evaluated_checkpoint_dir])
        assert rc == 1
        assert not os.path.exists(os.path.join(trained_and_evaluated_checkpoint_dir, "spill_unet_real_promoted.pt"))

    def test_promote_refuses_without_evaluation_report(self, tmp_path):
        root = str(tmp_path / "zenodo_sar")
        _make_fixture_dataset(root)
        manifest_dir = os.path.join(root, "manifests")
        ds.build_manifests(root, output_dir=manifest_dir, val_fraction=0.5, seed=1)
        ckpt_dir = str(tmp_path / "checkpoints")
        trainmod.train_model(dataset_root=root, model_kind="segmenter", manifest_dir=manifest_dir,
                              epochs=1, batch_size=2, image_size=16, seed=1, checkpoint_dir=ckpt_dir)
        # No evaluate_real run -> no evaluation_report.json
        with pytest.raises(RuntimeError, match="evaluation"):
            promotemod.promote_segmenter(ckpt_dir)

    def test_promote_succeeds_with_confirmation_and_real_evaluation(self, trained_and_evaluated_checkpoint_dir):
        rc = promotemod.main([
            "--checkpoint-dir", trained_and_evaluated_checkpoint_dir,
            "--i-understand-this-changes-inference-behavior",
        ])
        assert rc == 0
        assert os.path.exists(os.path.join(trained_and_evaluated_checkpoint_dir, "spill_unet_real_promoted.pt"))
        assert os.path.exists(os.path.join(trained_and_evaluated_checkpoint_dir, "classifier_real_promoted.pth"))

    def test_promote_never_touches_shipped_synthetic_filenames(self, trained_and_evaluated_checkpoint_dir):
        ckpt_dir = trained_and_evaluated_checkpoint_dir
        shipped_seg = os.path.join(ckpt_dir, "spill_unet.pt")
        shipped_clf = os.path.join(ckpt_dir, "classifier_checkpoint.pth")
        with open(shipped_seg, "wb") as f:
            f.write(b"shipped synthetic segmenter - must never change")
        with open(shipped_clf, "wb") as f:
            f.write(b"shipped synthetic classifier - must never change")
        before_seg = open(shipped_seg, "rb").read()
        before_clf = open(shipped_clf, "rb").read()

        promotemod.main(["--checkpoint-dir", ckpt_dir, "--i-understand-this-changes-inference-behavior"])

        assert open(shipped_seg, "rb").read() == before_seg
        assert open(shipped_clf, "rb").read() == before_clf

    def test_revert_removes_promoted_files_only(self, trained_and_evaluated_checkpoint_dir):
        ckpt_dir = trained_and_evaluated_checkpoint_dir
        promotemod.main(["--checkpoint-dir", ckpt_dir, "--i-understand-this-changes-inference-behavior"])
        assert os.path.exists(os.path.join(ckpt_dir, "spill_unet_real_promoted.pt"))
        rc = promotemod.main(["--checkpoint-dir", ckpt_dir, "--revert"])
        assert rc == 0
        assert not os.path.exists(os.path.join(ckpt_dir, "spill_unet_real_promoted.pt"))
        assert not os.path.exists(os.path.join(ckpt_dir, "classifier_real_promoted.pth"))
        # The _best/_last real checkpoints themselves are untouched by revert.
        assert os.path.exists(os.path.join(ckpt_dir, "spill_unet_real_best.pt"))


class TestDetectorRealCheckpointPrecedence:
    def test_engine_prefers_promoted_real_checkpoint_over_shipped_synthetic(self, trained_and_evaluated_checkpoint_dir):
        ckpt_dir = trained_and_evaluated_checkpoint_dir
        # Simulate a shipped synthetic checkpoint sitting alongside a promoted real one.
        import shutil
        shutil.copy2(os.path.join(ckpt_dir, "spill_unet_real_best.pt"), os.path.join(ckpt_dir, "spill_unet.pt"))
        promotemod.main(["--checkpoint-dir", ckpt_dir, "--i-understand-this-changes-inference-behavior"])

        engine = MLDetectorEngine(checkpoint_dir=ckpt_dir)
        assert engine.segmenter_loaded is True
        assert engine.segmenter_provenance == PROVENANCE_ML_REAL
        assert engine.provenance == PROVENANCE_ML_REAL
        info = engine.get_model_info()
        assert info["is_real_data_trained"] is True
        assert "Zenodo" in info["dataset_used_for_training"]

    def test_engine_falls_back_to_synthetic_when_not_promoted(self, trained_and_evaluated_checkpoint_dir):
        ckpt_dir = trained_and_evaluated_checkpoint_dir
        import shutil
        # Only the shipped-style filename exists (no promotion happened).
        shutil.copy2(os.path.join(ckpt_dir, "spill_unet_real_best.pt"), os.path.join(ckpt_dir, "spill_unet.pt"))

        engine = MLDetectorEngine(checkpoint_dir=ckpt_dir)
        assert engine.segmenter_provenance == PROVENANCE_ML_SYNTHETIC
        assert engine.provenance == PROVENANCE_ML_SYNTHETIC
        info = engine.get_model_info()
        assert info["is_real_data_trained"] is False

    def test_detect_reports_real_provenance_end_to_end(self, trained_and_evaluated_checkpoint_dir):
        ckpt_dir = trained_and_evaluated_checkpoint_dir
        promotemod.main(["--checkpoint-dir", ckpt_dir, "--i-understand-this-changes-inference-behavior"])
        engine = MLDetectorEngine(checkpoint_dir=ckpt_dir)
        vv = np.random.rand(16, 16).astype("float32")
        vh = np.random.rand(16, 16).astype("float32")
        result = engine.detect(vv, vh)
        assert result.provenance == PROVENANCE_ML_REAL
        assert result.model_provenance_info is not None
        assert result.model_provenance_info["is_real_data_trained"] is True
        assert result.success is True
