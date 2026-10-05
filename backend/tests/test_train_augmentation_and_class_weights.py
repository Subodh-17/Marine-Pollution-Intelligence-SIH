"""
Regression tests for two pipeline gaps found by this round's loss-function/
augmentation audit (app/ml/train.py) - neither explains the reported
37.56%/0.67%-Oil-recall failure on its own (that's root-caused to the
original SceneClassifier's architecture - see test_scene_classifier_v2.py
and README_ML_STATUS.md), but both are real, fixable deficiencies relative
to this project's own stated requirements ("weighted cross entropy... if
appropriate", "appropriate augmentation for SAR... do not apply
transformations that destroy physical meaning") that were previously
entirely absent from the training pipeline.
"""
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
from app.ml.train import _augment_sar_pair, _compute_class_weights, CLASS_LABELS


class TestClassWeights:
    def test_matches_real_reported_training_distribution(self):
        """Exact counts from classifier_real_dataset_manifest.json (genuinely
        on disk, see MODEL_CARD.md/README_ML_STATUS.md): Oil 1020, No-Oil
        582, Lookalike 582 - Oil is the plurality class, so its weight must
        come out BELOW 1.0 and the other two (equal counts) must be equal
        to each other and both above 1.0."""
        records = [{"category": "Oil"}] * 1020 + [{"category": "No-Oil"}] * 582 + [{"category": "Lookalike"}] * 582
        w = _compute_class_weights(records, torch.device("cpu"))
        oil, no_oil, lookalike = w.tolist()
        assert oil < 1.0 < no_oil
        assert no_oil == pytest.approx(lookalike)
        # Standard n / (k * count) formula, checked exactly, not just "some weight":
        n, k = 2184, 3
        assert oil == pytest.approx(n / (k * 1020), abs=1e-5)
        assert no_oil == pytest.approx(n / (k * 582), abs=1e-5)

    def test_perfectly_balanced_classes_get_weight_one(self):
        records = [{"category": c} for c in CLASS_LABELS for _ in range(10)]
        w = _compute_class_weights(records, torch.device("cpu"))
        assert w.tolist() == pytest.approx([1.0, 1.0, 1.0])

    def test_zero_count_class_gets_zero_weight_not_a_crash(self):
        """A class absent from this particular manifest must not raise
        (division by zero) - it gets weight 0.0 (never selected as a
        target, loss-neutral), not an exception that would crash training."""
        records = [{"category": "Oil"}] * 5 + [{"category": "No-Oil"}] * 5
        w = _compute_class_weights(records, torch.device("cpu"))
        assert w.tolist()[CLASS_LABELS.index("Lookalike")] == 0.0


class TestSarSafeAugmentation:
    def test_only_flips_and_90_degree_rotations_no_intensity_change(self):
        """Every augmented output must be exactly one of the 8 dihedral
        transforms of the original pixel VALUES (flip/rotate rearranges
        pixels; it must never add, scale, or clip any value) - checked by
        confirming the augmented tensor's sorted pixel values are IDENTICAL
        to the original's, for both channels independently."""
        torch.manual_seed(0)
        image = torch.rand(2, 16, 16)
        mask = torch.randint(0, 2, (1, 16, 16)).float()
        for _ in range(20):
            aug_image, aug_mask = _augment_sar_pair(image.clone(), mask.clone())
            for c in range(2):
                assert torch.allclose(
                    torch.sort(aug_image[c].flatten()).values,
                    torch.sort(image[c].flatten()).values,
                ), "augmentation altered pixel VALUES, not just their arrangement"
            assert torch.allclose(torch.sort(aug_mask.flatten()).values, torch.sort(mask.flatten()).values)

    def test_image_and_mask_stay_pixel_aligned(self):
        """A mask pixel's spatial relationship to its image must survive
        augmentation exactly - build an image whose VV channel IS the mask
        (so the "oil region" in the image and in the mask are identical by
        construction) and confirm that identity relationship still holds
        after every augmented draw."""
        torch.manual_seed(1)
        mask = torch.zeros(1, 16, 16)
        mask[0, 3:8, 2:6] = 1.0
        image = torch.stack([mask[0], torch.zeros(16, 16)], dim=0)
        for _ in range(20):
            aug_image, aug_mask = _augment_sar_pair(image.clone(), mask.clone())
            assert torch.equal(aug_image[0], aug_mask[0])

    def test_respects_torch_manual_seed_for_reproducibility(self):
        image = torch.rand(2, 8, 8)
        mask = torch.randint(0, 2, (1, 8, 8)).float()
        torch.manual_seed(42)
        a1, m1 = _augment_sar_pair(image.clone(), mask.clone())
        torch.manual_seed(42)
        a2, m2 = _augment_sar_pair(image.clone(), mask.clone())
        assert torch.equal(a1, a2) and torch.equal(m1, m2)


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


class TestAugmentationWiredOnlyIntoTrainSplit:
    def test_train_dataset_has_augment_true_val_has_augment_false(self, tmp_path):
        """End-to-end wiring check, against the real train_model() call -
        not just the isolated _augment_sar_pair function - confirming the
        val/test split is never augmented, only train."""
        root = str(tmp_path / "zenodo_sar")
        _make_fixture_dataset(root)
        manifest_dir = os.path.join(root, "manifests")
        ds.build_manifests(root, output_dir=manifest_dir, val_fraction=0.5, seed=1)

        import app.ml.train as train_module
        captured = {}
        original_init = train_module.SarPairDataset.__init__

        def spy_init(self, manifest_path, dataset_root, image_size=256, limit=None, augment=False):
            captured.setdefault("augment_values", []).append(augment)
            return original_init(self, manifest_path, dataset_root, image_size=image_size, limit=limit, augment=augment)

        train_module.SarPairDataset.__init__ = spy_init
        try:
            trainmod.train_model(
                dataset_root=root, model_kind="classifier", manifest_dir=manifest_dir,
                epochs=1, batch_size=2, image_size=32, seed=1,
                checkpoint_dir=str(tmp_path / "checkpoints"),
            )
        finally:
            train_module.SarPairDataset.__init__ = original_init

        # Two SarPairDataset constructions per train_model() call: train then val.
        assert captured["augment_values"] == [True, False]
