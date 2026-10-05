import json
import os
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
import tifffile

from app.ml.architectures import SceneClassifierV2
from app.satellite.real_image import normalize_sar_band


# ============================================================
# CONFIG
# ============================================================

DATASET_ROOT = Path(r"D:\zenodo_sar")

CHECKPOINT = Path(
    r"backend\data\ml_checkpoints\classifier_v2_lr3e4_best.pth"
)

OUTPUT_JSON = Path(
    r"backend\data\ml_checkpoints\classifier_v2_part3_evaluation.json"
)

CLASS_LABELS = ["Oil", "No-Oil", "Lookalike"]
CLASS_TO_IDX = {name: i for i, name in enumerate(CLASS_LABELS)}


# ============================================================
# HELPERS
# ============================================================

def load_vv_vh(image_path):
    """
    Load a Sentinel-1 dual-pol TIFF and return normalized
    VV and VH arrays using the project's production
    1-99 percentile normalization.
    """

    arr = tifffile.imread(str(image_path))

    arr = np.asarray(arr)

    if arr.ndim != 3:
        raise ValueError(
            f"Expected 3D dual-pol TIFF, got shape={arr.shape}"
        )

    # Handle either:
    #   (H, W, 2)
    # or
    #   (2, H, W)
    if arr.shape[-1] == 2:
        vv = arr[..., 0]
        vh = arr[..., 1]
    elif arr.shape[0] == 2:
        vv = arr[0]
        vh = arr[1]
    else:
        raise ValueError(
            f"Could not identify VV/VH channels in shape={arr.shape}"
        )

    vv = normalize_sar_band(vv)
    vh = normalize_sar_band(vh)

    return vv.astype(np.float32), vh.astype(np.float32)


def resize_input(vv, vh, image_size=256):
    """
    Match the training preprocessing:
    resize VV/VH to 256x256 using bilinear interpolation.
    """

    x = np.stack([vv, vh], axis=0).astype(np.float32)

    tensor = torch.from_numpy(x).unsqueeze(0)

    if tensor.shape[-2:] != (image_size, image_size):
        tensor = F.interpolate(
            tensor,
            size=(image_size, image_size),
            mode="bilinear",
            align_corners=False,
        )

    return tensor


def discover_test_images():
    """
    Discover exactly the Part III test images.

    Expected structure contains:
        Oil       -> 150
        No-Oil    -> 150
        Lookalike -> 150
    """

    # Search only inside the known Part III/test area.
    candidates = []

    for category in CLASS_LABELS:
        category_dirs = []

        for p in DATASET_ROOT.rglob("*"):
            if not p.is_dir():
                continue

            name = p.name.lower()

            if category == "Oil":
                category_match = (
                    name == "oil"
                    or "oil spill" in name
                    or "oil_spill" in name
                )
            elif category == "No-Oil":
                category_match = (
                    name == "no oil"
                    or name == "no_oil"
                    or name == "no-oil"
                    or "no oil" in name
                )
            else:
                category_match = (
                    name == "lookalike"
                    or "lookalike" in name
                )

            if category_match:
                # Require the path to look like Part III/test.
                path_text = str(p).lower().replace("\\", "/")

                if "part3" in path_text or "test" in path_text:
                    category_dirs.append(p)

        # Deduplicate
        category_dirs = sorted(set(category_dirs))

        for directory in category_dirs:
            for image_path in directory.glob("*.tif"):
                # Ignore segmentation masks.
                if image_path.stem.lower().endswith("_segmentation"):
                    continue

                candidates.append(
                    (category, image_path)
                )

            for image_path in directory.glob("*.tiff"):
                if image_path.stem.lower().endswith("_segmentation"):
                    continue

                candidates.append(
                    (category, image_path)
                )

    # Deduplicate by absolute path.
    unique = {}
    for category, path in candidates:
        unique[str(path.resolve())] = (category, path)

    return list(unique.values())


# ============================================================
# LOAD MODEL
# ============================================================

def load_model():
    print("Loading checkpoint:")
    print(CHECKPOINT)

    if not CHECKPOINT.exists():
        raise FileNotFoundError(
            f"Checkpoint not found: {CHECKPOINT}"
        )

    checkpoint = torch.load(
        CHECKPOINT,
        map_location="cpu",
        weights_only=False,
    )

    print("Checkpoint architecture:",
          checkpoint.get("architecture"))

    print("Checkpoint epoch:",
          checkpoint.get("epoch"))

    print("Checkpoint validation metrics:",
          checkpoint.get("val_metrics"))

    model = SceneClassifierV2(
        in_channels=2,
        num_classes=3,
    )

    model.load_state_dict(
        checkpoint["model_state_dict"]
    )

    model.eval()

    return model, checkpoint


# ============================================================
# MAIN EVALUATION
# ============================================================

def main():

    print("=" * 70)
    print("REAL ZENODO PART III — FINAL CLASSIFIER EVALUATION")
    print("=" * 70)

    print()
    print("Dataset:", DATASET_ROOT)
    print("Checkpoint:", CHECKPOINT)
    print()

    model, checkpoint = load_model()

    samples = discover_test_images()

    print()
    print("Discovered test images:", len(samples))

    counts = {
        label: 0
        for label in CLASS_LABELS
    }

    for label, _ in samples:
        counts[label] += 1

    print("Class counts:")
    for label in CLASS_LABELS:
        print(f"  {label}: {counts[label]}")

    expected_total = 450

    if len(samples) != expected_total:
        raise RuntimeError(
            f"SAFETY CHECK FAILED: expected exactly "
            f"{expected_total} Part III images, found "
            f"{len(samples)}."
        )

    for label in CLASS_LABELS:
        if counts[label] != 150:
            raise RuntimeError(
                f"SAFETY CHECK FAILED: expected 150 {label} "
                f"images, found {counts[label]}."
            )

    print()
    print("✓ Exactly 450 Part III images found.")
    print("✓ 150 images per class.")
    print("✓ Test set is untouched.")
    print()

    confusion = np.zeros(
        (3, 3),
        dtype=np.int64
    )

    per_class = {
        label: {
            "support": 0,
            "correct": 0,
        }
        for label in CLASS_LABELS
    }

    predictions = []

    with torch.no_grad():

        for i, (true_label, image_path) in enumerate(
            samples,
            start=1
        ):

            vv, vh = load_vv_vh(image_path)

            x = resize_input(
                vv,
                vh,
                image_size=256
            )

            logits = model(x)

            probabilities = torch.softmax(
                logits,
                dim=1
            )[0].cpu().numpy()

            predicted_idx = int(
                np.argmax(probabilities)
            )

            predicted_label = CLASS_LABELS[
                predicted_idx
            ]

            true_idx = CLASS_TO_IDX[
                true_label
            ]

            confusion[
                true_idx,
                predicted_idx
            ] += 1

            per_class[true_label]["support"] += 1

            if predicted_label == true_label:
                per_class[true_label]["correct"] += 1

            predictions.append({
                "image": str(image_path),
                "true": true_label,
                "predicted": predicted_label,
                "probabilities": {
                    CLASS_LABELS[j]:
                    round(float(probabilities[j]), 6)
                    for j in range(3)
                }
            })

            if i % 25 == 0:
                print(
                    f"Evaluated {i}/450"
                )

    # ========================================================
    # METRICS
    # ========================================================

    total = int(confusion.sum())
    correct = int(np.trace(confusion))

    accuracy = (
        correct / total
        if total
        else 0.0
    )

    metrics = {}

    precision_values = []
    recall_values = []
    f1_values = []

    for i, label in enumerate(CLASS_LABELS):

        tp = int(confusion[i, i])

        fp = int(
            confusion[:, i].sum() - tp
        )

        fn = int(
            confusion[i, :].sum() - tp
        )

        support = int(
            confusion[i, :].sum()
        )

        precision = (
            tp / (tp + fp)
            if (tp + fp)
            else 0.0
        )

        recall = (
            tp / (tp + fn)
            if (tp + fn)
            else 0.0
        )

        f1 = (
            2 * precision * recall /
            (precision + recall)
            if (precision + recall)
            else 0.0
        )

        metrics[label] = {
            "support": support,
            "true_positive": tp,
            "false_positive": fp,
            "false_negative": fn,
            "precision": precision,
            "recall": recall,
            "f1": f1,
        }

        precision_values.append(precision)
        recall_values.append(recall)
        f1_values.append(f1)

    macro_precision = float(
        np.mean(precision_values)
    )

    macro_recall = float(
        np.mean(recall_values)
    )

    macro_f1 = float(
        np.mean(f1_values)
    )

    result = {
        "evaluation": {
            "dataset": "Zenodo Sentinel-1 SAR Oil Spill Dataset Part III",
            "test_images": total,
            "classes": CLASS_LABELS,
            "preprocessing": "sar_percentile_1_99_minmax_v1",
            "image_size": 256,
            "checkpoint": str(CHECKPOINT),
            "checkpoint_epoch": checkpoint.get("epoch"),
            "checkpoint_val_metrics": checkpoint.get("val_metrics"),
            "architecture": checkpoint.get(
                "architecture",
                "SceneClassifierV2"
            ),
        },

        "accuracy": accuracy,

        "macro_precision": macro_precision,
        "macro_recall": macro_recall,
        "macro_f1": macro_f1,

        "confusion_matrix": confusion.tolist(),

        "metrics": metrics,

        "predictions": predictions,
    }

    OUTPUT_JSON.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with open(
        OUTPUT_JSON,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            result,
            f,
            indent=2
        )

    # ========================================================
    # PRINT FINAL REPORT
    # ========================================================

    print()
    print("=" * 70)
    print("FINAL PART III RESULTS")
    print("=" * 70)

    print(
        f"Accuracy:          {accuracy * 100:.2f}%"
    )

    print(
        f"Macro Precision:   {macro_precision * 100:.2f}%"
    )

    print(
        f"Macro Recall:      {macro_recall * 100:.2f}%"
    )

    print(
        f"Macro F1:          {macro_f1 * 100:.2f}%"
    )

    print()
    print("Confusion Matrix")
    print(
        "                 Predicted"
    )
    print(
        "              Oil  No-Oil  Lookalike"
    )

    for i, label in enumerate(CLASS_LABELS):
        print(
            f"{label:10s} "
            f"{confusion[i,0]:4d} "
            f"{confusion[i,1]:7d} "
            f"{confusion[i,2]:10d}"
        )

    print()

    for label in CLASS_LABELS:

        m = metrics[label]

        print(
            f"{label}: "
            f"Precision={m['precision'] * 100:.2f}%  "
            f"Recall={m['recall'] * 100:.2f}%  "
            f"F1={m['f1'] * 100:.2f}%  "
            f"Support={m['support']}"
        )

    print()
    print("Oil recall is the most important diagnostic:")
    print(
        f"  Oil Recall = "
        f"{metrics['Oil']['recall'] * 100:.2f}%"
    )

    print()
    print("Full report saved to:")
    print(OUTPUT_JSON)

    print()
    print("=" * 70)


if __name__ == "__main__":
    main()
