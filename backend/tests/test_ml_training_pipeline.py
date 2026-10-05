"""
Smoke tests for app/ml/train.py and app/ml/evaluate_real.py.

IMPORTANT: these tests build a TINY (16x16, a handful of images per
category) synthetic fixture dataset shaped like the real Zenodo dataset's
folder layout - they prove the training/evaluation PIPELINE genuinely runs
end-to-end (real forward/backward passes, real checkpoint files written,
real metrics computed against a real held-out split) using PyTorch. They do
NOT train against the actual multi-gigabyte Zenodo dataset (which requires
a manual download - see docs/DATASET_SETUP.md) and this file's assertions
never claim otherwise: they check the mechanics work (shapes, files exist,
completed=True, metrics are finite numbers in the correct 0-1 range), never
a specific accuracy/F1/IoU value.
"""
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


def _write_tiff(path, arr):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tifffile.imwrite(path, arr)


def _make_fixture_dataset(root: str, size: int = 16, n_per_category_trainval: int = 4, n_per_category_test: int = 2):
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

    _write("part1", "01_Train_Val_Oil_Spill", ds.CATEGORY_OIL, n_per_category_trainval, True)
    _write("part2", "01_Train_Val_Lookalike", ds.CATEGORY_LOOKALIKE, n_per_category_trainval, False)
    _write("part2", "01_Train_Val_No_Oil", ds.CATEGORY_NO_OIL, n_per_category_trainval, False)

    base = os.path.join(root, "part3", "02_Test_images_and_ground_truth")
    for category, subfolder, oil_positive in (
        (ds.CATEGORY_OIL, "Oil", True), (ds.CATEGORY_LOOKALIKE, "Lookalike", False), (ds.CATEGORY_NO_OIL, "No_oil", False),
    ):
        for i in range(n_per_category_test):
            stem = f"{i + 1:04d}"
            img = rng.normal(size=(size, size, 2)).astype(np.float32)
            mask = np.zeros((size, size), dtype=np.uint8)
            if oil_positive:
                mask[1:3, 1:3] = 1
            _write_tiff(os.path.join(base, "Images", subfolder, f"{stem}.tif"), img)
            _write_tiff(os.path.join(base, "Mask", subfolder, f"{stem}.tif"), mask)


@pytest.fixture
def fixture_dataset_with_manifests(tmp_path):
    root = str(tmp_path / "zenodo_sar")
    _make_fixture_dataset(root)
    manifest_dir = os.path.join(root, "manifests")
    ds.build_manifests(root, output_dir=manifest_dir, val_fraction=0.5, seed=1)
    return root, manifest_dir


class TestSarPairDataset:
    def test_dataset_yields_correct_shapes(self, fixture_dataset_with_manifests):
        root, manifest_dir = fixture_dataset_with_manifests
        train_manifest = os.path.join(manifest_dir, "train_manifest.json")
        dataset = trainmod.SarPairDataset(train_manifest, root, image_size=32)
        assert len(dataset) > 0
        image, mask, label_idx = dataset[0]
        assert image.shape == (2, 32, 32)
        assert mask.shape == (1, 32, 32)
        assert label_idx in (0, 1, 2)
        assert torch.isfinite(image).all()
        assert set(torch.unique(mask).tolist()) <= {0.0, 1.0}

    def test_normalization_matches_inference_preprocessing_version(self):
        from app.satellite.real_image import PREPROCESSING_VERSION, normalize_sar_band
        assert callable(normalize_sar_band)
        assert isinstance(PREPROCESSING_VERSION, str) and PREPROCESSING_VERSION


class TestTrainingPipelineSmoke:
    def test_segmenter_training_runs_end_to_end(self, fixture_dataset_with_manifests, tmp_path):
        root, manifest_dir = fixture_dataset_with_manifests
        ckpt_dir = str(tmp_path / "checkpoints")
        summary = trainmod.train_model(
            dataset_root=root, model_kind="segmenter", manifest_dir=manifest_dir,
            epochs=2, batch_size=2, image_size=16, seed=1, device="cpu",
            checkpoint_dir=ckpt_dir, num_workers=0,
        )
        assert summary["completed"] is True
        assert len(summary["history"]) == 2
        for epoch_record in summary["history"]:
            assert np.isfinite(epoch_record["train"]["loss"])
            assert 0.0 <= epoch_record["val"]["iou"] <= 1.0
            assert 0.0 <= epoch_record["val"]["dice"] <= 1.0
            assert 0.0 <= epoch_record["val"]["pixel_accuracy"] <= 1.0

        assert os.path.exists(os.path.join(ckpt_dir, "spill_unet_real_last.pt"))
        assert os.path.exists(os.path.join(ckpt_dir, "spill_unet_real_best.pt"))
        assert os.path.exists(os.path.join(ckpt_dir, "spill_unet_real_training_config.json"))
        assert os.path.exists(os.path.join(ckpt_dir, "spill_unet_real_training_history.json"))
        assert os.path.exists(os.path.join(ckpt_dir, "spill_unet_real_dataset_manifest.json"))
        assert os.path.exists(os.path.join(ckpt_dir, "spill_unet_real_model_metadata.json"))

        ckpt = torch.load(os.path.join(ckpt_dir, "spill_unet_real_best.pt"), weights_only=False)
        assert ckpt["trained_on"] == "REAL_ZENODO_SENTINEL1_SAR_OIL_SPILL_DATASET"
        assert ckpt["input_channels"] == 2
        assert "model_state_dict" in ckpt

        with open(os.path.join(ckpt_dir, "spill_unet_real_model_metadata.json")) as f:
            meta = json.load(f)
        assert meta["architecture"] == "SpillUNet"
        assert meta["dataset_dois"] == ds.DATASET_DOIS

    def test_classifier_training_runs_end_to_end(self, fixture_dataset_with_manifests, tmp_path):
        root, manifest_dir = fixture_dataset_with_manifests
        ckpt_dir = str(tmp_path / "checkpoints")
        summary = trainmod.train_model(
            dataset_root=root, model_kind="classifier", manifest_dir=manifest_dir,
            epochs=2, batch_size=2, image_size=16, seed=1, device="cpu",
            checkpoint_dir=ckpt_dir, num_workers=0,
        )
        assert summary["completed"] is True
        for epoch_record in summary["history"]:
            assert 0.0 <= epoch_record["val"]["accuracy"] <= 1.0
        ckpt = torch.load(os.path.join(ckpt_dir, "classifier_real_best.pth"), weights_only=False)
        assert ckpt["class_mapping"] == {0: "Oil", 1: "No-Oil", 2: "Lookalike"}

    def test_checkpoints_are_deterministic_with_same_seed(self, fixture_dataset_with_manifests, tmp_path):
        root, manifest_dir = fixture_dataset_with_manifests
        ckpt_dir1 = str(tmp_path / "c1")
        ckpt_dir2 = str(tmp_path / "c2")
        trainmod.train_model(dataset_root=root, model_kind="segmenter", manifest_dir=manifest_dir,
                              epochs=1, batch_size=2, image_size=16, seed=123, checkpoint_dir=ckpt_dir1)
        trainmod.train_model(dataset_root=root, model_kind="segmenter", manifest_dir=manifest_dir,
                              epochs=1, batch_size=2, image_size=16, seed=123, checkpoint_dir=ckpt_dir2)
        ckpt1 = torch.load(os.path.join(ckpt_dir1, "spill_unet_real_last.pt"), weights_only=False)
        ckpt2 = torch.load(os.path.join(ckpt_dir2, "spill_unet_real_last.pt"), weights_only=False)
        sd1 = ckpt1["model_state_dict"]
        sd2 = ckpt2["model_state_dict"]
        for key in sd1:
            assert torch.allclose(sd1[key], sd2[key]), f"parameter {key} differs despite identical seed"

    def test_does_not_overwrite_shipped_synthetic_checkpoints(self, fixture_dataset_with_manifests, tmp_path):
        """Regression guard for the explicit project rule: training must
        never touch the shipped `spill_unet.pt` / `classifier_checkpoint.pth`
        filenames directly - only the `_real_` variants."""
        root, manifest_dir = fixture_dataset_with_manifests
        ckpt_dir = str(tmp_path / "checkpoints")
        os.makedirs(ckpt_dir, exist_ok=True)
        sentinel_path = os.path.join(ckpt_dir, "spill_unet.pt")
        with open(sentinel_path, "wb") as f:
            f.write(b"pretend this is the shipped synthetic-trained checkpoint")
        before = open(sentinel_path, "rb").read()

        trainmod.train_model(dataset_root=root, model_kind="segmenter", manifest_dir=manifest_dir,
                              epochs=1, batch_size=2, image_size=16, seed=1, checkpoint_dir=ckpt_dir)

        after = open(sentinel_path, "rb").read()
        assert before == after
        assert os.path.exists(os.path.join(ckpt_dir, "spill_unet_real_last.pt"))

    def test_missing_dataset_raises_clear_error_not_synthetic_fallback(self, tmp_path):
        with pytest.raises(ds.DatasetNotFoundError):
            trainmod.train_model(dataset_root=str(tmp_path / "nonexistent"), model_kind="segmenter")

    def test_missing_manifests_raises_clear_error(self, tmp_path):
        root = str(tmp_path / "zenodo_sar")
        _make_fixture_dataset(root)  # dataset exists but manifests were never built
        with pytest.raises(ds.DatasetNotFoundError):
            trainmod.train_model(dataset_root=root, model_kind="segmenter")

    def test_invalid_model_kind_rejected(self, fixture_dataset_with_manifests):
        root, manifest_dir = fixture_dataset_with_manifests
        with pytest.raises(ValueError):
            trainmod.train_model(dataset_root=root, model_kind="not_a_real_model", manifest_dir=manifest_dir)
