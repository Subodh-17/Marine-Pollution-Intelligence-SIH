"""
Genuine evaluation of a real-Zenodo-trained SceneClassifier checkpoint
against the held-out TEST split (Part III) of the real Zenodo Sentinel-1 SAR
Oil Spill Dataset - the classifier-side counterpart of `evaluate_real.py`
(which evaluates the SpillUNet segmenter). Before this script existed, the
project's real-data evaluation evidence covered the segmenter only; the
3-class scene classifier (Oil / No-Oil / Lookalike) had a training-time
validation accuracy (see classifier_real_model_metadata.json) but no
held-out TEST-split evaluation with per-class precision/recall/F1 or a
confusion matrix - exactly what this script produces.

This is an OFFLINE, EXPLICIT CLI TOOL, never imported by `app.main`:

    python -m app.ml.evaluate_classifier --dataset-root ./data/zenodo_sar \\
        --checkpoint ./backend/data/ml_checkpoints/classifier_real_promoted.pth

HONESTY CONTRACT
------------------
- Metrics are computed ONLY against the test manifest (never train/val) -
  see `app/ml/dataset.py::build_manifests` "LEAKAGE PREVENTION" for how that
  split is kept genuinely held-out.
- Preprocessing (per-image 1st/99th-percentile min-max normalization, then
  resize to the training image size) is imported directly from
  `app/satellite/real_image.py`, the exact same function inference and
  training both use - never a reimplementation that could silently drift.
- If the dataset/test manifest/checkpoint is unavailable, this NEVER
  fabricates a report. `main()` still writes `classifier_v2_part3_evaluation.
  json`/`.md`, but with `evaluation_run: false`, an explicit `reason`, and
  every metric field `null` - the same "honestly unavailable, not silently
  substituted" pattern `/api/ml/metrics` already uses for the segmenter (see
  app/ml/detector.py::get_evaluation_metrics). This is deliberately
  different from evaluate_real.py (which just exits non-zero and writes
  nothing on failure): Part 8 of this round's request asks for a clearly
  named evaluation *artifact* to exist and be inspectable even when the raw
  Zenodo images are not present in the current environment - so the absence
  itself is machine-readable, not just a stderr message.
- The confusion matrix and six directional confusion counts distinguish
  Oil / No-Oil / Lookalike explicitly - see MODEL_CARD.md / README_ML_STATUS
  .md "Lookalike safety" for why a Lookalike-predicted-as-Oil error matters
  more than the aggregate accuracy number alone would show.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from typing import Optional

import numpy as np

try:
    import torch
    TORCH_AVAILABLE = True
except ImportError:  # pragma: no cover
    TORCH_AVAILABLE = False

from app.config import get_settings
from app.evaluation.metrics import multiclass_classification_metrics
from app.ml import dataset as ds_mod
from app.ml.dataset import DatasetNotFoundError

DEFAULT_CLASS_LABELS = ["Oil", "No-Oil", "Lookalike"]

# The six cross-class error directions Part 5/8 of this round's request asks
# to be called out explicitly, in addition to the full confusion matrix -
# an Oil<->Lookalike confusion is the one that matters most for the
# Lookalike-safety guarantee (see orchestrator.py), so it must never be left
# implicit inside a 3x3 table a reader has to cross-reference by hand.
_DIRECTIONAL_PAIRS = [
    ("Oil", "No-Oil"), ("Oil", "Lookalike"),
    ("No-Oil", "Oil"), ("No-Oil", "Lookalike"),
    ("Lookalike", "Oil"), ("Lookalike", "No-Oil"),
]


def _sha256_of_file(path: str, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def _load_classifier_checkpoint(checkpoint_path: str, device: "torch.device"):
    from app.ml.architectures import SceneClassifier, SceneClassifierV2
    if not os.path.exists(checkpoint_path):
        raise DatasetNotFoundError(
            f"Checkpoint not found at {checkpoint_path!r}. Train one first with "
            "`python -m app.ml.train --models classifier --dataset-root <path>` "
            "(see docs/DATASET_SETUP.md)."
        )
    raw = torch.load(checkpoint_path, map_location=device, weights_only=False)
    sd = raw.get("model_state_dict") if isinstance(raw, dict) else None
    if sd is None:
        raise ValueError(f"Checkpoint at {checkpoint_path} is not a recognized {{'model_state_dict': ...}} wrapper.")
    # The checkpoint records its own architecture name (app/ml/train.py,
    # `"architecture": model.__class__.__name__`) - read it back rather than
    # assuming the original `SceneClassifier`, so a checkpoint trained with
    # `--architecture scene_classifier_v2` (see architectures.py::
    # SceneClassifierV2) evaluates correctly too, with no separate flag
    # needed on this script's own CLI. Older checkpoints predating this
    # field default to the original architecture, preserving compatibility.
    architecture_name = raw.get("architecture", "SceneClassifier") if isinstance(raw, dict) else "SceneClassifier"
    model_cls = {"SceneClassifier": SceneClassifier, "SceneClassifierV2": SceneClassifierV2}.get(architecture_name)
    if model_cls is None:
        raise ValueError(f"Checkpoint at {checkpoint_path} records unknown architecture "
                          f"{architecture_name!r} - expected 'SceneClassifier' or 'SceneClassifierV2'.")
    model = model_cls(in_channels=2, num_classes=len(DEFAULT_CLASS_LABELS)).to(device)
    model.load_state_dict(sd, strict=True)
    model.eval()

    # Class ordering comes from the checkpoint's OWN class_mapping (written
    # by app/ml/train.py), exactly like app/ml/detector.py does - never
    # hardcoded here independently, so this script can never silently
    # mislabel a class if a future checkpoint's ordering ever changed.
    class_labels = list(DEFAULT_CLASS_LABELS)
    mapping = raw.get("class_mapping") if isinstance(raw, dict) else None
    if isinstance(mapping, dict) and len(mapping) == len(DEFAULT_CLASS_LABELS):
        try:
            class_labels = [mapping[i] for i in range(len(DEFAULT_CLASS_LABELS))]
        except Exception:
            pass
    return model, raw, class_labels


def evaluate_checkpoint(
    dataset_root: str,
    checkpoint_path: str,
    manifest_dir: Optional[str] = None,
    image_size: int = 256,
    device: str = "cpu",
) -> dict:
    if not TORCH_AVAILABLE:
        raise RuntimeError("PyTorch is not installed. Install it via `pip install -r requirements-ml.txt`.")

    manifest_dir = manifest_dir or os.path.join(dataset_root, "manifests")
    test_manifest_path = os.path.join(manifest_dir, "test_manifest.json")
    manifest = ds_mod.load_manifest(test_manifest_path)  # raises DatasetNotFoundError if missing
    if manifest["count"] == 0:
        raise DatasetNotFoundError(f"test_manifest.json in {manifest_dir!r} contains zero records.")

    torch_device = torch.device(device)
    model, raw_checkpoint, class_labels = _load_classifier_checkpoint(checkpoint_path, torch_device)

    from app.satellite.real_image import normalize_sar_band, PREPROCESSING_VERSION
    import tifffile
    import torch.nn.functional as F

    per_record = []
    y_true, y_pred, confidences = [], [], []

    with torch.no_grad():
        for rec in manifest["records"]:
            img_path = os.path.join(dataset_root, rec["image_path"])
            arr = tifffile.imread(img_path).astype(np.float64)
            if arr.ndim == 3 and arr.shape[-1] >= 2:
                vv_raw, vh_raw = arr[..., 0], arr[..., 1]
            else:
                vv_raw, vh_raw = arr[0], arr[1]
            vv = normalize_sar_band(vv_raw)
            vh = normalize_sar_band(vh_raw)
            image = np.stack([vv, vh], axis=0).astype(np.float32)

            image_t = torch.from_numpy(image).unsqueeze(0)
            if image_t.shape[-2:] != (image_size, image_size):
                image_t = F.interpolate(image_t, size=(image_size, image_size), mode="bilinear", align_corners=False)
            image_t = image_t.to(torch_device)

            logits = model(image_t)
            probs = torch.softmax(logits, dim=1)[0].cpu().numpy()
            idx = int(np.argmax(probs))
            predicted_label = class_labels[idx]
            confidence = float(probs[idx])
            true_label = rec["category"]

            per_record.append({
                "id": rec["id"], "true_label": true_label, "predicted_label": predicted_label,
                "confidence": round(confidence, 4), "correct": predicted_label == true_label,
                "probabilities": {class_labels[i]: round(float(probs[i]), 4) for i in range(len(class_labels))},
            })
            y_true.append(true_label)
            y_pred.append(predicted_label)
            confidences.append(confidence)

    m = multiclass_classification_metrics(y_true, y_pred, labels=class_labels)

    label_to_idx = {label: i for i, label in enumerate(class_labels)}
    directional_confusion = {
        f"{a}_to_{b}".replace("-", "_"): m.confusion[label_to_idx[a]][label_to_idx[b]]
        for a, b in _DIRECTIONAL_PAIRS
    }

    confidences_arr = np.array(confidences, dtype=np.float64)
    correct_mask = np.array([r["correct"] for r in per_record], dtype=bool)
    confidence_summary = {
        "mean": round(float(confidences_arr.mean()), 4) if confidences_arr.size else None,
        "median": round(float(np.median(confidences_arr)), 4) if confidences_arr.size else None,
        "min": round(float(confidences_arr.min()), 4) if confidences_arr.size else None,
        "max": round(float(confidences_arr.max()), 4) if confidences_arr.size else None,
        "mean_when_correct": round(float(confidences_arr[correct_mask].mean()), 4) if correct_mask.any() else None,
        "mean_when_incorrect": round(float(confidences_arr[~correct_mask].mean()), 4) if (~correct_mask).any() else None,
    }

    from app.ml.paths import portable_path

    result = {
        "evaluation_run": True,
        "evaluated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        "checkpoint_path": portable_path(checkpoint_path),
        "checkpoint_sha256": _sha256_of_file(checkpoint_path),
        "architecture": raw_checkpoint.get("architecture", "SceneClassifier") if isinstance(raw_checkpoint, dict) else "SceneClassifier",
        "checkpoint_epoch": raw_checkpoint.get("epoch") if isinstance(raw_checkpoint, dict) else None,
        "checkpoint_trained_on": raw_checkpoint.get("trained_on") if isinstance(raw_checkpoint, dict) else None,
        "dataset_root": portable_path(dataset_root),
        "test_manifest": portable_path(test_manifest_path),
        "num_test_scenes": len(per_record),
        "class_mapping": {i: label for i, label in enumerate(class_labels)},
        "preprocessing_version": PREPROCESSING_VERSION,
        "image_size": image_size,
        "overall": {
            "accuracy": m.accuracy,
            "macro_precision": m.macro_precision, "macro_recall": m.macro_recall, "macro_f1": m.macro_f1,
            "weighted_precision": m.weighted_precision, "weighted_recall": m.weighted_recall,
            "weighted_f1": m.weighted_f1,
        },
        "per_class": m.per_class,
        "confusion_matrix": {
            "labels": class_labels,
            "rows_are": "actual", "columns_are": "predicted",
            "matrix": m.confusion,
        },
        "directional_confusion": directional_confusion,
        "confidence_summary": confidence_summary,
        "per_scene": per_record,
        "dataset_dois": ds_mod.DATASET_DOIS,
        "dataset_citation": ds_mod.DATASET_CITATION,
        "limitations": [
            "This is the dataset's own published test split (Part III), never mixed into train/val "
            "(see app/ml/dataset.py build_manifests 'LEAKAGE PREVENTION').",
            "Softmax confidence is the model's own raw output, not a calibrated statistical "
            "probability - no calibration (e.g. temperature scaling, Platt scaling) is implemented "
            "anywhere in this codebase. Treat 'confidence' as a relative ranking signal only.",
            "This evaluation reports the classifier's OWN predicted label vs ground truth - it does "
            "not re-derive or validate ML_FALLBACK_CONFIDENCE_THRESHOLD (app/ml/detector.py), which "
            "governs a separate, orthogonal decision (whether to trust ANY ML result enough to skip "
            "the classical-detector fallback). See MODEL_CARD.md / README_ML_STATUS.md.",
            "A Lookalike/No-Oil scene correctly classified as such can still carry a non-empty "
            "segmenter mask (the two sub-models are architecturally independent) - this evaluation "
            "covers ONLY the classifier's own scene-level label, not the segmenter's mask quality "
            "(see app/ml/evaluate_real.py for that) or the orchestrator's Lookalike-safety gating "
            "(app/investigation/orchestrator.py, guarded by tests/test_lookalike_safety.py).",
            "Re-evaluate after any further training - these numbers describe this exact checkpoint "
            "file (see checkpoint_sha256) at this exact epoch only.",
        ],
    }
    return result


def _unavailable_report(checkpoint_path: str, reason: str) -> dict:
    """Honest 'could not evaluate' artifact - never a fabricated result.
    Fills in whatever IS genuinely knowable without the dataset (checkpoint
    identity/hash if the checkpoint file itself is present, the dataset's
    own recorded manifest counts if that sidecar file is present) and
    leaves every actual metric field null with this `reason` attached -
    mirrors the same 'available: false' + null-fields shape
    `/api/ml/metrics` already uses for the segmenter's evaluation status
    (app/ml/detector.py::get_evaluation_metrics) rather than inventing a
    different unavailability shape here.
    """
    checkpoint_sha256 = None
    checkpoint_exists = os.path.exists(checkpoint_path)
    if checkpoint_exists:
        try:
            checkpoint_sha256 = _sha256_of_file(checkpoint_path)
        except OSError:
            pass

    # The dataset's own manifest/count metadata (written by app/ml/train.py
    # at training time, see classifier_real_dataset_manifest.json) is
    # genuinely on disk even when the raw Zenodo TIFFs are not - reporting
    # it here is honest (it is real, recorded data) and gives a reader
    # something concrete about the held-out test split's size/composition
    # while being explicit that no inference was actually run against it.
    manifest_snapshot = None
    checkpoint_dir = os.path.dirname(os.path.abspath(checkpoint_path)) if checkpoint_path else None
    if checkpoint_dir:
        manifest_sidecar = os.path.join(checkpoint_dir, "classifier_real_dataset_manifest.json")
        if os.path.exists(manifest_sidecar):
            try:
                with open(manifest_sidecar) as f:
                    manifest_snapshot = json.load(f)
            except Exception:
                pass

    from app.ml.paths import portable_path

    return {
        "evaluation_run": False,
        "reason": reason,
        "evaluated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        "checkpoint_path": portable_path(checkpoint_path) if checkpoint_path else None,
        "checkpoint_present_on_disk": checkpoint_exists,
        "checkpoint_sha256": checkpoint_sha256,
        "class_mapping": {i: label for i, label in enumerate(DEFAULT_CLASS_LABELS)},
        "dataset_manifest_snapshot": manifest_snapshot,
        "dataset_dois": ds_mod.DATASET_DOIS,
        "dataset_citation": ds_mod.DATASET_CITATION,
        "num_test_scenes": None,
        "overall": None,
        "per_class": None,
        "confusion_matrix": None,
        "directional_confusion": None,
        "confidence_summary": None,
        "per_scene": None,
        "limitations": [
            "No genuine 3-class classifier evaluation has been run in this environment - the reason "
            "field above explains exactly why. Every metric field in this file is null; none were "
            "fabricated or estimated.",
            "To produce real numbers, run this exact command against a machine with the extracted "
            "real Zenodo Sentinel-1 SAR Oil Spill Dataset (see docs/DATASET_SETUP.md): "
            "`python -m app.ml.evaluate_classifier --dataset-root <path-to-zenodo_sar>`.",
        ],
    }


def _format_markdown(result: dict) -> str:
    lines = []
    lines.append("# 3-Class Classifier Evaluation — SceneClassifier vs Zenodo Sentinel-1 SAR Oil Spill Dataset (Test Split)")
    lines.append("")
    lines.append(f"**Evaluated at:** {result['evaluated_at']} UTC  ")
    lines.append(f"**Checkpoint:** `{result.get('checkpoint_path')}`  ")
    if result.get("checkpoint_sha256"):
        lines.append(f"**Checkpoint SHA-256:** `{result['checkpoint_sha256']}`  ")

    if not result.get("evaluation_run"):
        lines.append("")
        lines.append(f"## Evaluation NOT run — {result.get('reason')}")
        lines.append("")
        lines.append("Every metric below is genuinely unavailable in this environment - none were fabricated.")
        lines.append("")
        if result.get("dataset_manifest_snapshot"):
            counts = result["dataset_manifest_snapshot"].get("counts", {})
            lines.append("### Recorded dataset split (from the training-time manifest snapshot, not re-verified here)")
            lines.append("")
            lines.append(f"- Train: {counts.get('train')} · Val: {counts.get('val')} · Test: {counts.get('test')}")
            if counts.get("test_by_category"):
                lines.append(f"- Test by category: {counts['test_by_category']}")
            lines.append("")
        lines.append("### Limitations")
        lines.append("")
        for c in result["limitations"]:
            lines.append(f"- {c}")
        lines.append("")
        return "\n".join(lines)

    lines.append(f"**Checkpoint trained on:** {result.get('checkpoint_trained_on')}  ")
    lines.append(f"**Architecture:** {result.get('architecture')}  ")
    lines.append(f"**Checkpoint epoch:** {result.get('checkpoint_epoch')}  ")
    lines.append(f"**Preprocessing version:** `{result['preprocessing_version']}`  ")
    lines.append(f"**Class mapping:** {result['class_mapping']}  ")
    lines.append(f"**Test scenes evaluated:** {result['num_test_scenes']}")
    lines.append("")
    lines.append("## Overall")
    lines.append("")
    o = result["overall"]
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    for key in ("accuracy", "macro_precision", "macro_recall", "macro_f1",
                "weighted_precision", "weighted_recall", "weighted_f1"):
        lines.append(f"| {key} | {o[key]} |")
    lines.append("")
    lines.append("## Per-class")
    lines.append("")
    lines.append("| Class | Precision | Recall | F1 | Support |")
    lines.append("|---|---|---|---|---|")
    for label in result["confusion_matrix"]["labels"]:
        pc = result["per_class"][label]
        lines.append(f"| {label} | {pc['precision']} | {pc['recall']} | {pc['f1']} | {pc['support']} |")
    lines.append("")
    lines.append("## Confusion matrix (rows = actual, columns = predicted)")
    lines.append("")
    labels = result["confusion_matrix"]["labels"]
    matrix = result["confusion_matrix"]["matrix"]
    lines.append("| Actual \\ Predicted | " + " | ".join(labels) + " |")
    lines.append("|---" * (len(labels) + 1) + "|")
    for i, label in enumerate(labels):
        lines.append(f"| **{label}** | " + " | ".join(str(v) for v in matrix[i]) + " |")
    lines.append("")
    lines.append("## Directional confusion counts")
    lines.append("")
    for k, v in result["directional_confusion"].items():
        pretty = k.replace("_to_", " → ").replace("_", "-")
        lines.append(f"- {pretty}: {v}")
    lines.append("")
    lines.append("## Confidence summary")
    lines.append("")
    cs = result["confidence_summary"]
    lines.append(f"- Mean: {cs['mean']} · Median: {cs['median']} · Min: {cs['min']} · Max: {cs['max']}")
    lines.append(f"- Mean when correct: {cs['mean_when_correct']} · Mean when incorrect: {cs['mean_when_incorrect']}")
    lines.append("")
    lines.append("## Limitations")
    lines.append("")
    for c in result["limitations"]:
        lines.append(f"- {c}")
    lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate a real-Zenodo-trained SceneClassifier checkpoint against the held-out test split."
    )
    settings = get_settings()
    parser.add_argument("--dataset-root", default=settings.dataset_root)
    parser.add_argument("--manifest-dir", default=None)
    parser.add_argument("--checkpoint", default=os.path.join(settings.ml_checkpoint_dir, "classifier_real_promoted.pth"))
    parser.add_argument("--image-size", type=int, default=settings.ml_train_image_size)
    parser.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
    parser.add_argument("--output-dir", default=None, help="Default: alongside the checkpoint.")
    args = parser.parse_args(argv)

    if args.device == "cuda" and not (TORCH_AVAILABLE and torch.cuda.is_available()):
        print("ERROR: --device cuda requested but CUDA is not available in this environment.", file=sys.stderr)
        return 1

    try:
        result = evaluate_checkpoint(
            dataset_root=args.dataset_root, checkpoint_path=args.checkpoint,
            manifest_dir=args.manifest_dir, image_size=args.image_size, device=args.device,
        )
        exit_code = 0
    except DatasetNotFoundError as e:
        print(f"NOTICE: {e} Writing an honest 'evaluation not run' artifact instead of fabricating results.", file=sys.stderr)
        result = _unavailable_report(args.checkpoint, str(e))
        exit_code = 0  # the artifact itself is the honest, successful output of this tool
    except (ValueError, RuntimeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        result = _unavailable_report(args.checkpoint, str(e))
        exit_code = 1

    output_dir = args.output_dir or os.path.dirname(os.path.abspath(args.checkpoint))
    os.makedirs(output_dir, exist_ok=True)
    json_path = os.path.join(output_dir, "classifier_v2_part3_evaluation.json")
    md_path = os.path.join(output_dir, "classifier_v2_part3_evaluation.md")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(_format_markdown(result))

    if result.get("evaluation_run"):
        print(f"Evaluated {result['num_test_scenes']} held-out test scenes.")
        print(f"Overall: {result['overall']}")
    else:
        print(f"Evaluation NOT run: {result['reason']}")
    print(f"Reports written to:\n  {json_path}\n  {md_path}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
