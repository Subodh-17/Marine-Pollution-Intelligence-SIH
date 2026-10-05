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
def trained_checkpoint(tmp_path):
    root = str(tmp_path / "zenodo_sar")
    _make_fixture_dataset(root)
    manifest_dir = os.path.join(root, "manifests")
    ds.build_manifests(root, output_dir=manifest_dir, val_fraction=0.5, seed=1)
    ckpt_dir = str(tmp_path / "checkpoints")
    trainmod.train_model(
        dataset_root=root, model_kind="segmenter", manifest_dir=manifest_dir,
        epochs=1, batch_size=2, image_size=16, seed=1, checkpoint_dir=ckpt_dir,
    )
    return root, manifest_dir, os.path.join(ckpt_dir, "spill_unet_real_best.pt")


class TestEvaluateReal:
    def test_evaluate_runs_against_held_out_test_split(self, trained_checkpoint):
        root, manifest_dir, ckpt_path = trained_checkpoint
        result = evalmod.evaluate_checkpoint(
            dataset_root=root, checkpoint_path=ckpt_path, manifest_dir=manifest_dir, image_size=16,
        )
        assert result["num_test_scenes"] == 6  # 2 per category
        for key in ("precision", "recall", "f1", "iou", "dice", "pixel_accuracy"):
            assert 0.0 <= result["overall"][key] <= 1.0
        assert set(result["per_category"].keys()) == {ds.CATEGORY_OIL, ds.CATEGORY_LOOKALIKE, ds.CATEGORY_NO_OIL}
        assert result["preprocessing_version"]
        assert result["checkpoint_trained_on"] == "REAL_ZENODO_SENTINEL1_SAR_OIL_SPILL_DATASET"

    def test_evaluate_writes_json_and_markdown(self, trained_checkpoint, tmp_path):
        root, manifest_dir, ckpt_path = trained_checkpoint
        out_dir = str(tmp_path / "eval_out")
        rc = evalmod.main([
            "--dataset-root", root, "--manifest-dir", manifest_dir,
            "--checkpoint", ckpt_path, "--image-size", "16", "--output-dir", out_dir,
        ])
        assert rc == 0
        assert os.path.exists(os.path.join(out_dir, "evaluation_report.json"))
        assert os.path.exists(os.path.join(out_dir, "evaluation_report.md"))
        with open(os.path.join(out_dir, "evaluation_report.json")) as f:
            report = json.load(f)
        assert report["num_test_scenes"] == 6

    def test_missing_checkpoint_fails_clearly(self, tmp_path):
        root = str(tmp_path / "zenodo_sar")
        _make_fixture_dataset(root)
        manifest_dir = os.path.join(root, "manifests")
        ds.build_manifests(root, output_dir=manifest_dir, val_fraction=0.5, seed=1)
        with pytest.raises(ds.DatasetNotFoundError):
            evalmod.evaluate_checkpoint(
                dataset_root=root, checkpoint_path=os.path.join(str(tmp_path), "does_not_exist.pt"),
                manifest_dir=manifest_dir,
            )

    def test_missing_test_manifest_fails_clearly(self, tmp_path, trained_checkpoint):
        root, manifest_dir, ckpt_path = trained_checkpoint
        empty_manifest_dir = str(tmp_path / "no_manifests_here")
        os.makedirs(empty_manifest_dir, exist_ok=True)
        with pytest.raises(ds.DatasetNotFoundError):
            evalmod.evaluate_checkpoint(
                dataset_root=root, checkpoint_path=ckpt_path, manifest_dir=empty_manifest_dir,
            )
