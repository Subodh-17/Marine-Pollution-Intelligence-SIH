"""
Tests for app/ml/evaluate_classifier.py - the 3-class classifier evaluation
CLI (the classifier-side counterpart of test_evaluate_real.py, which covers
the segmenter). Mirrors that file's fixture-dataset/train-then-evaluate
pattern exactly, so both evaluators are exercised the same way, against a
genuinely trained (not mocked) tiny fixture checkpoint - never against a
tester-supplied confusion matrix - so a real bug in image loading,
preprocessing, or metric assembly would actually surface here.
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
from app.ml import evaluate_classifier as evalmod
from app.ml.dataset import DatasetNotFoundError


def _write_tiff(path, arr):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tifffile.imwrite(path, arr)


def _make_fixture_dataset(root: str, size: int = 16):
    """Same layout/category balance as test_evaluate_real.py's fixture -
    3 categories x 2 test scenes each, so both evaluators are exercised
    against an equivalent held-out test split."""
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
def trained_classifier_checkpoint(tmp_path):
    root = str(tmp_path / "zenodo_sar")
    _make_fixture_dataset(root)
    manifest_dir = os.path.join(root, "manifests")
    ds.build_manifests(root, output_dir=manifest_dir, val_fraction=0.5, seed=1)
    ckpt_dir = str(tmp_path / "checkpoints")
    trainmod.train_model(
        dataset_root=root, model_kind="classifier", manifest_dir=manifest_dir,
        epochs=1, batch_size=2, image_size=16, seed=1, checkpoint_dir=ckpt_dir,
    )
    return root, manifest_dir, os.path.join(ckpt_dir, "classifier_real_best.pth")


class TestEvaluateClassifier:
    def test_evaluate_runs_against_held_out_test_split(self, trained_classifier_checkpoint):
        root, manifest_dir, ckpt_path = trained_classifier_checkpoint
        result = evalmod.evaluate_checkpoint(
            dataset_root=root, checkpoint_path=ckpt_path, manifest_dir=manifest_dir, image_size=16,
        )
        assert result["evaluation_run"] is True
        assert result["num_test_scenes"] == 6  # 2 per category, 3 categories
        assert result["checkpoint_trained_on"] == "REAL_ZENODO_SENTINEL1_SAR_OIL_SPILL_DATASET"
        assert result["class_mapping"] == {0: "Oil", 1: "No-Oil", 2: "Lookalike"}
        assert result["preprocessing_version"]

        # Overall + per-class metrics are well-formed probabilities/counts,
        # regardless of what a 1-epoch fixture model's actual accuracy is.
        for key in ("accuracy", "macro_precision", "macro_recall", "macro_f1",
                    "weighted_precision", "weighted_recall", "weighted_f1"):
            assert 0.0 <= result["overall"][key] <= 1.0
        assert set(result["per_class"].keys()) == {"Oil", "No-Oil", "Lookalike"}
        for cls, m in result["per_class"].items():
            assert m["support"] == 2
            assert 0.0 <= m["precision"] <= 1.0
            assert 0.0 <= m["recall"] <= 1.0
            assert 0.0 <= m["f1"] <= 1.0

        # Confusion matrix is 3x3, rows sum to each class's support (every
        # actual instance is predicted as exactly one of the three labels).
        cm = result["confusion_matrix"]
        assert cm["labels"] == ["Oil", "No-Oil", "Lookalike"]
        assert len(cm["matrix"]) == 3 and all(len(row) == 3 for row in cm["matrix"])
        for row in cm["matrix"]:
            assert sum(row) == 2

        # All six directional off-diagonal counts are explicitly present and
        # consistent with the confusion matrix they were derived from.
        expected_keys = {
            "Oil_to_No_Oil", "Oil_to_Lookalike", "No_Oil_to_Oil",
            "No_Oil_to_Lookalike", "Lookalike_to_Oil", "Lookalike_to_No_Oil",
        }
        assert set(result["directional_confusion"].keys()) == expected_keys
        label_idx = {label: i for i, label in enumerate(cm["labels"])}
        assert result["directional_confusion"]["Oil_to_No_Oil"] == cm["matrix"][label_idx["Oil"]][label_idx["No-Oil"]]
        assert result["directional_confusion"]["Lookalike_to_Oil"] == cm["matrix"][label_idx["Lookalike"]][label_idx["Oil"]]

        assert len(result["per_scene"]) == 6
        for rec in result["per_scene"]:
            assert rec["predicted_label"] in ("Oil", "No-Oil", "Lookalike")
            assert 0.0 <= rec["confidence"] <= 1.0
            assert set(rec["probabilities"].keys()) == {"Oil", "No-Oil", "Lookalike"}
            assert rec["correct"] == (rec["predicted_label"] == rec["true_label"])

        cs = result["confidence_summary"]
        assert 0.0 <= cs["mean"] <= 1.0
        assert cs["min"] <= cs["mean"] <= cs["max"]

    def test_evaluate_writes_json_and_markdown(self, trained_classifier_checkpoint, tmp_path):
        root, manifest_dir, ckpt_path = trained_classifier_checkpoint
        out_dir = str(tmp_path / "eval_out")
        rc = evalmod.main([
            "--dataset-root", root, "--manifest-dir", manifest_dir,
            "--checkpoint", ckpt_path, "--image-size", "16", "--output-dir", out_dir,
        ])
        assert rc == 0
        json_path = os.path.join(out_dir, "classifier_v2_part3_evaluation.json")
        md_path = os.path.join(out_dir, "classifier_v2_part3_evaluation.md")
        assert os.path.exists(json_path)
        assert os.path.exists(md_path)
        with open(json_path) as f:
            report = json.load(f)
        assert report["evaluation_run"] is True
        assert report["num_test_scenes"] == 6
        md_text = open(md_path).read()
        assert "Confusion matrix" in md_text
        assert "Directional confusion counts" in md_text

    def test_missing_checkpoint_raises_clearly(self, tmp_path):
        root = str(tmp_path / "zenodo_sar")
        _make_fixture_dataset(root)
        manifest_dir = os.path.join(root, "manifests")
        ds.build_manifests(root, output_dir=manifest_dir, val_fraction=0.5, seed=1)
        with pytest.raises(DatasetNotFoundError):
            evalmod.evaluate_checkpoint(
                dataset_root=root, checkpoint_path=os.path.join(str(tmp_path), "does_not_exist.pth"),
                manifest_dir=manifest_dir,
            )

    def test_missing_test_manifest_raises_clearly(self, tmp_path, trained_classifier_checkpoint):
        root, manifest_dir, ckpt_path = trained_classifier_checkpoint
        empty_manifest_dir = str(tmp_path / "no_manifests_here")
        os.makedirs(empty_manifest_dir, exist_ok=True)
        with pytest.raises(DatasetNotFoundError):
            evalmod.evaluate_checkpoint(
                dataset_root=root, checkpoint_path=ckpt_path, manifest_dir=empty_manifest_dir,
            )

    def test_missing_dataset_writes_honest_unavailable_artifact_via_cli(self, tmp_path):
        """`main()` (the CLI entry point) must never fabricate a report when
        the dataset/manifest genuinely isn't there - Part 8's honesty
        contract - it writes a clearly-labelled unavailable artifact
        instead, with every metric field null."""
        missing_root = str(tmp_path / "no_such_dataset")
        out_dir = str(tmp_path / "eval_out")
        # No real checkpoint needed for this path either - use a path that
        # doesn't exist, to also exercise checkpoint_present_on_disk=False.
        missing_ckpt = str(tmp_path / "no_such_checkpoint.pth")
        rc = evalmod.main([
            "--dataset-root", missing_root, "--checkpoint", missing_ckpt, "--output-dir", out_dir,
        ])
        assert rc == 0  # an honest "not run" artifact is this tool's successful output
        json_path = os.path.join(out_dir, "classifier_v2_part3_evaluation.json")
        assert os.path.exists(json_path)
        with open(json_path) as f:
            report = json.load(f)
        assert report["evaluation_run"] is False
        assert report["reason"]
        assert report["checkpoint_present_on_disk"] is False
        assert report["checkpoint_sha256"] is None
        for key in ("num_test_scenes", "overall", "per_class", "confusion_matrix",
                    "directional_confusion", "confidence_summary", "per_scene"):
            assert report[key] is None
