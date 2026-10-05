"""
Genuine evaluation of a real-Zenodo-trained SpillUNet checkpoint against the
held-out TEST split (Part III) of the real Zenodo Sentinel-1 SAR Oil Spill
Dataset - see `app/ml/dataset.py` for how that split is produced and why it
is never mixed with train/val.

This is an OFFLINE, EXPLICIT CLI TOOL, never imported by `app.main`:

    python -m app.ml.evaluate_real --dataset-root ./data/zenodo_sar \\
        --checkpoint ./backend/data/ml_checkpoints/spill_unet_real_best.pt

HONESTY CONTRACT
------------------
- Metrics are computed ONLY against the test manifest (never train/val) -
  see `app/ml/dataset.py::build_manifests` "LEAKAGE PREVENTION" for how that
  split is kept genuinely held-out.
- If the dataset/test manifest/checkpoint is unavailable, this exits
  non-zero with a clear setup message - it never silently substitutes a
  different (e.g. synthetic-trained) checkpoint or a synthetic test set and
  reports numbers as if they were real.
- Per-category (Oil / No-Oil / Lookalike) results are reported separately
  in addition to the overall aggregate, per project requirement - a
  segmentation model that looks good in aggregate but only because it
  correctly predicts "no oil" on the (majority) No-Oil/Lookalike scenes
  would otherwise hide a real weakness on the Oil-scene subset.
"""
from __future__ import annotations

import argparse
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
from app.evaluation.metrics import binary_mask_metrics, confusion_matrix
from app.ml import dataset as ds_mod
from app.ml.dataset import DatasetNotFoundError


def _load_segmenter_checkpoint(checkpoint_path: str, device: "torch.device"):
    from app.ml.architectures import SpillUNet
    if not os.path.exists(checkpoint_path):
        raise DatasetNotFoundError(
            f"Checkpoint not found at {checkpoint_path!r}. Train one first with "
            "`python -m app.ml.train --dataset-root <path>` (see docs/DATASET_SETUP.md)."
        )
    raw = torch.load(checkpoint_path, map_location=device, weights_only=False)
    sd = raw.get("model_state_dict") if isinstance(raw, dict) else None
    if sd is None:
        raise ValueError(f"Checkpoint at {checkpoint_path} is not a recognized {{'model_state_dict': ...}} wrapper.")
    model = SpillUNet(in_channels=2, out_channels=1).to(device)
    model.load_state_dict(sd, strict=True)
    model.eval()
    return model, raw


def evaluate_checkpoint(
    dataset_root: str,
    checkpoint_path: str,
    manifest_dir: Optional[str] = None,
    image_size: int = 256,
    device: str = "cpu",
    threshold: float = 0.5,
) -> dict:
    if not TORCH_AVAILABLE:
        raise RuntimeError("PyTorch is not installed. Install it via `pip install -r requirements-ml.txt`.")

    manifest_dir = manifest_dir or os.path.join(dataset_root, "manifests")
    test_manifest_path = os.path.join(manifest_dir, "test_manifest.json")
    manifest = ds_mod.load_manifest(test_manifest_path)  # raises DatasetNotFoundError if missing
    if manifest["count"] == 0:
        raise DatasetNotFoundError(f"test_manifest.json in {manifest_dir!r} contains zero records.")

    torch_device = torch.device(device)
    model, raw_checkpoint = _load_segmenter_checkpoint(checkpoint_path, torch_device)

    from app.satellite.real_image import normalize_sar_band, PREPROCESSING_VERSION
    import tifffile
    import torch.nn.functional as F

    per_record = []
    by_category: dict = {}

    with torch.no_grad():
        for rec in manifest["records"]:
            img_path = os.path.join(dataset_root, rec["image_path"])
            mask_path = os.path.join(dataset_root, rec["mask_path"])
            arr = tifffile.imread(img_path).astype(np.float64)
            if arr.ndim == 3 and arr.shape[-1] >= 2:
                vv_raw, vh_raw = arr[..., 0], arr[..., 1]
            else:
                vv_raw, vh_raw = arr[0], arr[1]
            vv = normalize_sar_band(vv_raw)
            vh = normalize_sar_band(vh_raw)
            image = np.stack([vv, vh], axis=0).astype(np.float32)
            orig_h, orig_w = vv.shape

            image_t = torch.from_numpy(image).unsqueeze(0)
            if (orig_h, orig_w) != (image_size, image_size):
                image_t = F.interpolate(image_t, size=(image_size, image_size), mode="bilinear", align_corners=False)
            image_t = image_t.to(torch_device)

            logits = model(image_t)
            probs = torch.sigmoid(logits)[0, 0].cpu().numpy()
            pred_mask = probs > threshold

            gt_arr = tifffile.imread(mask_path)
            gt_mask = gt_arr > 0
            if gt_mask.shape != pred_mask.shape:
                gt_t = torch.from_numpy(gt_mask.astype(np.float32))[None, None, ...]
                gt_t = F.interpolate(gt_t, size=pred_mask.shape, mode="nearest")
                gt_mask = gt_t[0, 0].numpy() > 0.5

            m = binary_mask_metrics(pred_mask, gt_mask)
            record_result = {
                "id": rec["id"], "category": rec["category"],
                "precision": m.precision, "recall": m.recall, "f1": m.f1, "iou": m.iou,
                "dice": m.dice, "pixel_accuracy": m.pixel_accuracy,
                "tp": m.tp, "fp": m.fp, "fn": m.fn, "tn": m.tn,
            }
            per_record.append(record_result)
            by_category.setdefault(rec["category"], []).append(m)

    def _aggregate(metrics_list) -> dict:
        if not metrics_list:
            return {"available": False}
        agg = {
            field: round(float(np.mean([getattr(m, field) for m in metrics_list])), 4)
            for field in ("precision", "recall", "f1", "iou", "dice", "pixel_accuracy")
        }
        totals = {
            field: int(sum(getattr(m, field) for m in metrics_list))
            for field in ("tp", "fp", "fn", "tn")
        }
        agg.update(totals)
        agg["n_scenes"] = len(metrics_list)
        return agg

    per_category_results = {cat: _aggregate(ms) for cat, ms in by_category.items()}
    overall_metrics = [m for ms in by_category.values() for m in ms]
    overall = _aggregate(overall_metrics)

    # An aggregate confusion matrix summed across every scored scene (pixel
    # counts, not scene counts) - complements the per-scene numbers above
    # with a single, standard 2x2 table.
    total_tp = sum(r["tp"] for r in per_record)
    total_fp = sum(r["fp"] for r in per_record)
    total_fn = sum(r["fn"] for r in per_record)
    total_tn = sum(r["tn"] for r in per_record)

    from app.ml.paths import portable_path

    result = {
        "evaluated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        "checkpoint_path": portable_path(checkpoint_path),
        "checkpoint_epoch": raw_checkpoint.get("epoch") if isinstance(raw_checkpoint, dict) else None,
        "checkpoint_trained_on": raw_checkpoint.get("trained_on") if isinstance(raw_checkpoint, dict) else None,
        "dataset_root": portable_path(dataset_root),
        "test_manifest": portable_path(test_manifest_path),
        "num_test_scenes": len(per_record),
        "preprocessing_version": PREPROCESSING_VERSION,
        "threshold": threshold,
        "overall": overall,
        "per_category": per_category_results,
        "aggregate_confusion_matrix": {
            "labels": ["predicted_positive", "predicted_negative"],
            "actual_positive": {"predicted_positive": total_tp, "predicted_negative": total_fn},
            "actual_negative": {"predicted_positive": total_fp, "predicted_negative": total_tn},
            "matrix": [[total_tp, total_fn], [total_fp, total_tn]],
        },
        "per_scene": per_record,
        "dataset_dois": ds_mod.DATASET_DOIS,
        "caveats": [
            "This is the dataset's own published test split (Part III), never mixed into train/val "
            "(see app/ml/dataset.py build_manifests 'LEAKAGE PREVENTION').",
            "Metrics are only as good as the mask threshold used (0.5 default, see --threshold) and "
            "reflect this exact checkpoint at this exact epoch - re-evaluate after further training.",
            "Per-category numbers for No-Oil/Lookalike scenes measure false-positive behavior (their "
            "ground truth is an all-zero mask), not detection recall - a low IoU/Dice there without "
            "an accompanying pixel_accuracy check should be read as noisy on an all-negative scene, "
            "not as a missed detection.",
        ],
    }
    return result


def _format_markdown(result: dict) -> str:
    lines = []
    lines.append("# Real-Data Evaluation Report — SpillUNet vs Zenodo Sentinel-1 SAR Oil Spill Dataset (Test Split)")
    lines.append("")
    lines.append(f"**Evaluated at:** {result['evaluated_at']} UTC  ")
    lines.append(f"**Checkpoint:** `{result['checkpoint_path']}`  ")
    lines.append(f"**Checkpoint trained on:** {result.get('checkpoint_trained_on')}  ")
    lines.append(f"**Checkpoint epoch:** {result.get('checkpoint_epoch')}  ")
    lines.append(f"**Preprocessing version:** `{result['preprocessing_version']}`  ")
    lines.append(f"**Test scenes evaluated:** {result['num_test_scenes']}  ")
    lines.append(f"**Mask threshold:** {result['threshold']}")
    lines.append("")
    lines.append("## Overall (all test scenes)")
    lines.append("")
    o = result["overall"]
    if o.get("available", True):
        lines.append("| Metric | Value |")
        lines.append("|---|---|")
        for key in ("precision", "recall", "f1", "iou", "dice", "pixel_accuracy", "n_scenes"):
            lines.append(f"| {key} | {o.get(key)} |")
    else:
        lines.append("_No scored scenes available._")
    lines.append("")
    lines.append("## Per-category breakdown")
    lines.append("")
    lines.append("| Category | n | Precision | Recall | F1 | IoU | Dice | Pixel Acc. |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for cat, m in result["per_category"].items():
        if not m.get("available", True):
            lines.append(f"| {cat} | 0 | - | - | - | - | - | - |")
            continue
        lines.append(
            f"| {cat} | {m['n_scenes']} | {m['precision']} | {m['recall']} | {m['f1']} | "
            f"{m['iou']} | {m['dice']} | {m['pixel_accuracy']} |"
        )
    lines.append("")
    lines.append("## Aggregate confusion matrix (pixels, summed over all scored scenes)")
    lines.append("")
    cm = result["aggregate_confusion_matrix"]["matrix"]
    lines.append("| | Predicted Positive | Predicted Negative |")
    lines.append("|---|---|---|")
    lines.append(f"| Actual Positive | {cm[0][0]} | {cm[0][1]} |")
    lines.append(f"| Actual Negative | {cm[1][0]} | {cm[1][1]} |")
    lines.append("")
    lines.append("## Caveats")
    lines.append("")
    for c in result["caveats"]:
        lines.append(f"- {c}")
    lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate a real-Zenodo-trained SpillUNet checkpoint against the held-out test split.")
    settings = get_settings()
    parser.add_argument("--dataset-root", default=settings.dataset_root)
    parser.add_argument("--manifest-dir", default=None)
    parser.add_argument("--checkpoint", default=os.path.join(settings.ml_checkpoint_dir, "spill_unet_real_best.pt"))
    parser.add_argument("--image-size", type=int, default=settings.ml_train_image_size)
    parser.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--output-dir", default=None, help="Default: alongside the checkpoint.")
    args = parser.parse_args(argv)

    if args.device == "cuda" and not (TORCH_AVAILABLE and torch.cuda.is_available()):
        print("ERROR: --device cuda requested but CUDA is not available in this environment.", file=sys.stderr)
        return 1

    try:
        result = evaluate_checkpoint(
            dataset_root=args.dataset_root, checkpoint_path=args.checkpoint,
            manifest_dir=args.manifest_dir, image_size=args.image_size,
            device=args.device, threshold=args.threshold,
        )
    except (DatasetNotFoundError, ValueError, RuntimeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    output_dir = args.output_dir or os.path.dirname(os.path.abspath(args.checkpoint))
    os.makedirs(output_dir, exist_ok=True)
    json_path = os.path.join(output_dir, "evaluation_report.json")
    md_path = os.path.join(output_dir, "evaluation_report.md")
    with open(json_path, "w") as f:
        json.dump(result, f, indent=2)
    with open(md_path, "w") as f:
        f.write(_format_markdown(result))

    print(f"Evaluated {result['num_test_scenes']} held-out test scenes.")
    print(f"Overall: {result['overall']}")
    print(f"Reports written to:\n  {json_path}\n  {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
