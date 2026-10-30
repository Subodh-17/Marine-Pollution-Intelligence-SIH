"""
Fit and evaluate temperature scaling for SceneClassifier on the Zenodo SAR dataset.

LEAKAGE SAFETY:
- Temperature T is fitted strictly on the 386 validation scenes (val_manifest.json).
- The 450 held-out test scenes (test_manifest.json) are evaluated solely for verification.
- Model weights are NEVER modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone

# Ensure backend directory is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import numpy as np
import onnxruntime as ort
import tifffile
from scipy.ndimage import zoom

from app.ml.calibration import (
    evaluate_calibration,
    fit_temperature,
    temperature_scale,
)
from app.satellite.real_image import normalize_sar_band
from app.ml.paths import portable_path


def _sha256_of_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def run_inference_on_split(
    session: ort.InferenceSession,
    dataset_root: str,
    manifest_path: str,
    class_labels: list[str],
    image_size: int = 256,
) -> tuple[np.ndarray, np.ndarray, list[dict]]:
    input_name = session.get_inputs()[0].name
    with open(manifest_path, "r", encoding="utf-8") as f:
        records = json.load(f)["records"]

    label_to_idx = {lbl: i for i, lbl in enumerate(class_labels)}
    logits_list = []
    y_true_list = []
    metadata_list = []

    t0 = time.time()
    for idx, rec in enumerate(records):
        img_path = os.path.join(dataset_root, rec["image_path"])
        true_label = rec["category"]
        y_true_list.append(label_to_idx[true_label])

        arr = tifffile.imread(img_path).astype(np.float64)
        if arr.ndim == 3 and arr.shape[-1] >= 2:
            vv_raw, vh_raw = arr[..., 0], arr[..., 1]
        else:
            vv_raw, vh_raw = arr[0], arr[1]

        vv = normalize_sar_band(vv_raw)
        vh = normalize_sar_band(vh_raw)

        # Resize to (image_size, image_size) if needed
        h, w = vv.shape
        if (h, w) != (image_size, image_size):
            zoom_y = image_size / h
            zoom_x = image_size / w
            vv_res = zoom(vv, (zoom_y, zoom_x), order=1).astype(np.float32)
            vh_res = zoom(vh, (zoom_y, zoom_x), order=1).astype(np.float32)
        else:
            vv_res = vv.astype(np.float32)
            vh_res = vh.astype(np.float32)

        stacked = np.stack([vv_res, vh_res], axis=0)[np.newaxis, ...]  # (1, 2, H, W)
        logits = session.run(None, {input_name: stacked})[0][0].astype(np.float64)
        logits_list.append(logits)

        metadata_list.append({
            "id": rec["id"],
            "true_label": true_label,
            "image_path": rec["image_path"],
        })

        if (idx + 1) % 100 == 0 or (idx + 1) == len(records):
            elapsed = time.time() - t0
            print(f"  Processed {idx + 1}/{len(records)} scenes ({elapsed:.1f}s)")

    return np.array(logits_list, dtype=np.float64), np.array(y_true_list, dtype=np.int64), metadata_list


def main():
    parser = argparse.ArgumentParser(description="Fit and evaluate classifier confidence calibration.")
    parser.add_argument("--dataset-root", default=r"D:\zenodo_sar")
    parser.add_argument("--onnx-path", default="backend/data/ml_checkpoints/classifier_real_best.onnx")
    parser.add_argument("--output-json", default="backend/data/ml_checkpoints/classifier_calibration.json")
    args = parser.parse_args()

    val_manifest = os.path.join(args.dataset_root, "manifests", "val_manifest.json")
    test_manifest = os.path.join(args.dataset_root, "manifests", "test_manifest.json")

    print(f"Loading ONNX classifier from {args.onnx_path}...")
    model_sha256 = _sha256_of_file(args.onnx_path)
    session = ort.InferenceSession(args.onnx_path, providers=["CPUExecutionProvider"])
    class_labels = ["Oil", "No-Oil", "Lookalike"]

    cache_file = "backend/data/ml_checkpoints/classifier_logits_cache.npz"
    if os.path.exists(cache_file):
        print(f"Loading cached logits from {cache_file}...")
        cached = np.load(cache_file)
        val_logits = cached["val_logits"]
        val_y_true = cached["val_y_true"]
        test_logits = cached["test_logits"]
        test_y_true = cached["test_y_true"]
    else:
        print(f"\n1. Running inference on VALIDATION split ({val_manifest})...")
        val_logits, val_y_true, _ = run_inference_on_split(session, args.dataset_root, val_manifest, class_labels)
        print(f"\n2. Running inference on HELD-OUT TEST split ({test_manifest})...")
        test_logits, test_y_true, _ = run_inference_on_split(session, args.dataset_root, test_manifest, class_labels)
        np.savez_compressed(cache_file, val_logits=val_logits, val_y_true=val_y_true, test_logits=test_logits, test_y_true=test_y_true)
        print(f"Saved logits cache to {cache_file}.")

    print("\nFitting Temperature Scaling on Validation Split...")
    opt_t, val_uncal, val_cal, val_bins = fit_temperature(val_logits, val_y_true)
    print(f"   Optimal Temperature (T*): {opt_t:.4f}")
    print(f"   Validation NLL:   {val_uncal.nll:.4f} -> {val_cal.nll:.4f} (delta: {val_cal.nll - val_uncal.nll:.4f})")
    print(f"   Validation Brier: {val_uncal.brier_score:.4f} -> {val_cal.brier_score:.4f}")
    print(f"   Validation ECE:   {val_uncal.ece:.4f} -> {val_cal.ece:.4f} (delta: {val_cal.ece - val_uncal.ece:.4f})")
    print(f"   Validation MCE:   {val_uncal.mce:.4f} -> {val_cal.mce:.4f}")
    print(f"   Validation Acc:   {val_uncal.accuracy:.4f} (strictly invariant)")

    print("\nEvaluating on HELD-OUT TEST split to test generalization...")
    test_uncal, _ = evaluate_calibration(test_logits, test_y_true, temperature=1.0)
    test_cal, test_bins = evaluate_calibration(test_logits, test_y_true, temperature=opt_t)
    print(f"   Held-out Test NLL:   {test_uncal.nll:.4f} -> {test_cal.nll:.4f}")
    print(f"   Held-out Test Brier: {test_uncal.brier_score:.4f} -> {test_cal.brier_score:.4f}")
    print(f"   Held-out Test ECE:   {test_uncal.ece:.4f} -> {test_cal.ece:.4f}")
    print(f"   Held-out Test MCE:   {test_uncal.mce:.4f} -> {test_cal.mce:.4f}")
    print(f"   Held-out Test Acc:   {test_uncal.accuracy:.4f} (strictly invariant)")

    artifact = {
        "calibrated_at": datetime.now(timezone.utc).isoformat(),
        "method": "temperature_scaling",
        "temperature": round(opt_t, 4),
        "model_sha256": model_sha256,
        "onnx_filename": os.path.basename(args.onnx_path),
        "class_labels": class_labels,
        "training_split_protection": "Fitted strictly on validation split; test split kept held-out.",
        "validation_dataset": {
            "manifest": portable_path(val_manifest),
            "num_scenes": len(val_logits),
            "uncalibrated_metrics": {
                "nll": val_uncal.nll,
                "brier_score": val_uncal.brier_score,
                "ece": val_uncal.ece,
                "mce": val_uncal.mce,
                "mean_confidence": val_uncal.mean_confidence,
                "accuracy": val_uncal.accuracy,
            },
            "calibrated_metrics": {
                "nll": val_cal.nll,
                "brier_score": val_cal.brier_score,
                "ece": val_cal.ece,
                "mce": val_cal.mce,
                "mean_confidence": val_cal.mean_confidence,
                "accuracy": val_cal.accuracy,
            },
            "reliability_bins": val_bins,
        },
        "held_out_test_evaluation": {
            "manifest": portable_path(test_manifest),
            "num_scenes": len(test_logits),
            "uncalibrated_metrics": {
                "nll": test_uncal.nll,
                "brier_score": test_uncal.brier_score,
                "ece": test_uncal.ece,
                "mce": test_uncal.mce,
                "mean_confidence": test_uncal.mean_confidence,
                "accuracy": test_uncal.accuracy,
            },
            "calibrated_metrics": {
                "nll": test_cal.nll,
                "brier_score": test_cal.brier_score,
                "ece": test_cal.ece,
                "mce": test_cal.mce,
                "mean_confidence": test_cal.mean_confidence,
                "accuracy": test_cal.accuracy,
            },
            "reliability_bins": test_bins,
        },
        "provenance": "TEMPERATURE_SCALED_VALIDATION_SPLIT",
    }

    os.makedirs(os.path.dirname(args.output_json), exist_ok=True)
    with open(args.output_json, "w", encoding="utf-8") as f:
        json.dump(artifact, f, indent=2)
    print(f"\nCalibration artifact successfully saved to: {args.output_json}")


if __name__ == "__main__":
    main()
