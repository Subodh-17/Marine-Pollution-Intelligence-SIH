"""
Diagnostic script for Phase 4.3B: Task 1
Reproduces and dissects lookalike false alarms on the validation split (386 scenes).
Analyzes:
1. Baseline single-scale predictions
2. Coarse-scale predictions alone
3. Coarse-scale eroded predictions
4. Current boundary_preserved fusion
5. Where extra false positives arise (new components vs enlargement, area distribution)
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List

import numpy as np
import scipy.ndimage as ndi
import tifffile
import torch
import torch.nn.functional as F

from app.evaluation.metrics import binary_mask_metrics
from app.ml.architectures import SpillUNet
from app.satellite.real_image import normalize_sar_band


def run_diagnosis(
    dataset_root: str = r"D:\zenodo_sar",
    checkpoint_path: str = "backend/data/ml_checkpoints/spill_unet_real_best.pt",
    manifest_path: str = r"D:\zenodo_sar\manifests\val_manifest.json",
    image_size: int = 256,
    threshold: float = 0.5,
    coarse_weight: float = 0.95,
    device: str = "cpu",
) -> Dict[str, Any]:
    torch_device = torch.device(device)

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    records = manifest.get("records", [])
    print(f"Loaded {len(records)} validation records from {manifest_path}")

    if not os.path.exists(checkpoint_path):
        for candidate in [
            os.path.join(os.path.dirname(__file__), "..", "..", "data", "ml_checkpoints", "spill_unet_real_best.pt"),
            os.path.join("data", "ml_checkpoints", "spill_unet_real_best.pt"),
            os.path.join("backend", "data", "ml_checkpoints", "spill_unet_real_best.pt"),
        ]:
            if os.path.exists(candidate):
                checkpoint_path = candidate
                break

    # Load SpillUNet
    raw_checkpoint = torch.load(checkpoint_path, map_location=torch_device, weights_only=False)
    sd = raw_checkpoint.get("model_state_dict") if isinstance(raw_checkpoint, dict) else None
    model = SpillUNet(in_channels=2, out_channels=1).to(torch_device)
    model.load_state_dict(sd, strict=True)
    model.eval()

    # Trackers for the categories
    categories = ["Lookalike", "No-Oil", "Oil"]
    metrics_by_cat = {
        cat: {
            "n_scenes": 0,
            "base_fp_px": 0,
            "coarse_raw_fp_px": 0,
            "coarse_ero_fp_px": 0,
            "fused_fp_px": 0,
            "base_fp_scenes": 0,
            "fused_fp_scenes": 0,
            "base_tp_px": 0,
            "fused_tp_px": 0,
            "gt_px": 0,
            "base_components_fp": 0,
            "fused_components_fp": 0,
            "new_components_scenes": 0,
            "enlarged_components_scenes": 0,
        }
        for cat in categories
    }

    oil_scene_quartile_analysis = []

    print("Running diagnostic forward passes across validation scenes...")
    t_start = time.time()

    with torch.no_grad():
        for idx, rec in enumerate(records):
            img_path = os.path.join(dataset_root, rec["image_path"])
            mask_path = os.path.join(dataset_root, rec["mask_path"])
            cat = rec["category"]

            arr = tifffile.imread(img_path).astype(np.float64)
            if arr.ndim == 3 and arr.shape[-1] >= 2:
                vv_raw, vh_raw = arr[..., 0], arr[..., 1]
            else:
                vv_raw, vh_raw = arr[0], arr[1]

            vv = normalize_sar_band(vv_raw)
            vh = normalize_sar_band(vh_raw)
            stacked = np.stack([vv, vh], axis=0).astype(np.float32)

            img_t = torch.from_numpy(stacked).unsqueeze(0)
            orig_h, orig_w = vv.shape
            if (orig_h, orig_w) != (image_size, image_size):
                img_t = F.interpolate(img_t, size=(image_size, image_size), mode="bilinear", align_corners=False)
            img_t = img_t.to(torch_device)

            # Ground truth
            gt_arr = tifffile.imread(mask_path)
            gt_bin = gt_arr > 0
            if gt_bin.shape != (image_size, image_size):
                gt_t = torch.from_numpy(gt_bin.astype(np.float32))[None, None, ...]
                gt_t = F.interpolate(gt_t, size=(image_size, image_size), mode="nearest")
                gt_mask = gt_t[0, 0].numpy() > 0.5
            else:
                gt_mask = gt_bin
            gt_px = int(np.sum(gt_mask))

            # 1. Base scale
            logits_base = model(img_t)
            prob_base = torch.sigmoid(logits_base)[0, 0].cpu().numpy()
            pred_base = prob_base > threshold

            # 2. Coarse scale alone (0.5x, upsampled back to base without erosion)
            img_coarse = F.interpolate(img_t, size=(image_size // 2, image_size // 2), mode="bilinear", align_corners=False)
            logits_coarse = model(img_coarse)
            prob_coarse = torch.sigmoid(logits_coarse)[0, 0].cpu().numpy()

            prob_coarse_raw_t = torch.from_numpy(prob_coarse)[None, None, ...].to(torch_device)
            prob_coarse_raw_up = F.interpolate(prob_coarse_raw_t, size=(image_size, image_size), mode="bilinear", align_corners=False)[0, 0].cpu().numpy()
            pred_coarse_raw = (prob_coarse_raw_up * coarse_weight) > threshold

            # 3. Coarse scale eroded
            prob_coarse_ero = ndi.minimum_filter(prob_coarse, size=3)
            prob_coarse_ero_t = torch.from_numpy(prob_coarse_ero)[None, None, ...].to(torch_device)
            prob_coarse_ero_up = F.interpolate(prob_coarse_ero_t, size=(image_size, image_size), mode="bilinear", align_corners=False)[0, 0].cpu().numpy()
            pred_coarse_ero = (prob_coarse_ero_up * coarse_weight) > threshold

            # 4. Fused (boundary_preserved)
            prob_fused = np.maximum(prob_base, np.clip(prob_coarse_ero_up * coarse_weight, 0.0, 1.0)).astype(np.float32)
            pred_fused = prob_fused > threshold

            # False positives vs ground truth
            fp_base_mask = pred_base & (~gt_mask)
            fp_coarse_raw_mask = pred_coarse_raw & (~gt_mask)
            fp_coarse_ero_mask = pred_coarse_ero & (~gt_mask)
            fp_fused_mask = pred_fused & (~gt_mask)

            fp_base_px = int(np.sum(fp_base_mask))
            fp_coarse_raw_px = int(np.sum(fp_coarse_raw_mask))
            fp_coarse_ero_px = int(np.sum(fp_coarse_ero_mask))
            fp_fused_px = int(np.sum(fp_fused_mask))

            tp_base_px = int(np.sum(pred_base & gt_mask))
            tp_fused_px = int(np.sum(pred_fused & gt_mask))

            # Connected components of FP
            lbl_base, num_cc_base = ndi.label(fp_base_mask)
            lbl_fused, num_cc_fused = ndi.label(fp_fused_mask)

            cat_stats = metrics_by_cat[cat]
            cat_stats["n_scenes"] += 1
            cat_stats["base_fp_px"] += fp_base_px
            cat_stats["coarse_raw_fp_px"] += fp_coarse_raw_px
            cat_stats["coarse_ero_fp_px"] += fp_coarse_ero_px
            cat_stats["fused_fp_px"] += fp_fused_px
            if fp_base_px > 0:
                cat_stats["base_fp_scenes"] += 1
            if fp_fused_px > 0:
                cat_stats["fused_fp_scenes"] += 1
            cat_stats["base_tp_px"] += tp_base_px
            cat_stats["fused_tp_px"] += tp_fused_px
            cat_stats["gt_px"] += gt_px
            cat_stats["base_components_fp"] += num_cc_base
            cat_stats["fused_components_fp"] += num_cc_fused

            if fp_fused_px > fp_base_px:
                if fp_base_px == 0:
                    cat_stats["new_components_scenes"] += 1
                else:
                    cat_stats["enlarged_components_scenes"] += 1

            if cat == "Oil":
                oil_scene_quartile_analysis.append({
                    "id": rec["id"],
                    "gt_px": gt_px,
                    "base_tp": tp_base_px,
                    "fused_tp": tp_fused_px,
                    "base_fp": fp_base_px,
                    "fused_fp": fp_fused_px,
                    "base_recall": round(tp_base_px / max(1, gt_px), 4),
                    "fused_recall": round(tp_fused_px / max(1, gt_px), 4),
                    "base_iou": round(tp_base_px / max(1, tp_base_px + fp_base_px + (gt_px - tp_base_px)), 4),
                    "fused_iou": round(tp_fused_px / max(1, tp_fused_px + fp_fused_px + (gt_px - tp_fused_px)), 4),
                    "base_area_ratio": round(np.sum(pred_base) / max(1, gt_px), 4),
                    "fused_area_ratio": round(np.sum(pred_fused) / max(1, gt_px), 4),
                })

            if (idx + 1) % 50 == 0 or (idx + 1) == len(records):
                print(f"Processed {idx + 1}/{len(records)} scenes ({time.time() - t_start:.1f}s)")

    # Analyze quartiles for Oil scenes
    oil_scene_quartile_analysis.sort(key=lambda x: x["gt_px"])
    n_oil = len(oil_scene_quartile_analysis)
    q_size = n_oil // 4
    quartiles = {
        "Q1_smallest": oil_scene_quartile_analysis[:q_size],
        "Q2_medium_small": oil_scene_quartile_analysis[q_size: 2 * q_size],
        "Q3_medium_large": oil_scene_quartile_analysis[2 * q_size: 3 * q_size],
        "Q4_largest": oil_scene_quartile_analysis[3 * q_size:],
    }

    quartile_summary = {}
    for q_name, q_list in quartiles.items():
        total_gt = sum(x["gt_px"] for x in q_list)
        total_base_tp = sum(x["base_tp"] for x in q_list)
        total_fused_tp = sum(x["fused_tp"] for x in q_list)
        total_base_fp = sum(x["base_fp"] for x in q_list)
        total_fused_fp = sum(x["fused_fp"] for x in q_list)
        quartile_summary[q_name] = {
            "n_scenes": len(q_list),
            "min_gt_px": q_list[0]["gt_px"] if q_list else 0,
            "max_gt_px": q_list[-1]["gt_px"] if q_list else 0,
            "total_gt_px": total_gt,
            "mean_base_recall": round(float(np.mean([x["base_recall"] for x in q_list])), 4),
            "mean_fused_recall": round(float(np.mean([x["fused_recall"] for x in q_list])), 4),
            "mean_base_iou": round(float(np.mean([x["base_iou"] for x in q_list])), 4),
            "mean_fused_iou": round(float(np.mean([x["fused_iou"] for x in q_list])), 4),
            "pooled_base_recall": round(total_base_tp / max(1, total_gt), 4),
            "pooled_fused_recall": round(total_fused_tp / max(1, total_gt), 4),
            "mean_base_area_ratio": round(float(np.mean([x["base_area_ratio"] for x in q_list])), 4),
            "mean_fused_area_ratio": round(float(np.mean([x["fused_area_ratio"] for x in q_list])), 4),
        }

    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "dataset_root": dataset_root,
        "manifest": manifest_path,
        "num_scenes": len(records),
        "categories": metrics_by_cat,
        "oil_quartiles": quartile_summary,
    }

    out_path = "backend/data/ml_checkpoints/lookalike_false_alarm_diagnosis.json"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"Saved diagnosis report to {out_path}")
    return report


if __name__ == "__main__":
    rep = run_diagnosis()
    print("\n--- DIAGNOSIS SUMMARY ON VALIDATION SPLIT (386 SCENES) ---")
    for cat, s in rep["categories"].items():
        print(f"\n[{cat.upper()}] ({s['n_scenes']} scenes):")
        print(f"  Base FP px:        {s['base_fp_px']} (in {s['base_fp_scenes']} scenes, {s['base_components_fp']} CCs)")
        print(f"  Coarse Raw FP px:   {s['coarse_raw_fp_px']}")
        print(f"  Coarse Eroded FP:   {s['coarse_ero_fp_px']}")
        print(f"  Fused FP px:        {s['fused_fp_px']} (in {s['fused_fp_scenes']} scenes, {s['fused_components_fp']} CCs)")
        print(f"  FP Pixel Delta:     {s['fused_fp_px'] - s['base_fp_px']:+d} ({((s['fused_fp_px'] - s['base_fp_px'])/max(1, s['base_fp_px'])*100):+.1f}%)")
        print(f"  New FP Scenes:      {s['new_components_scenes']}")
        print(f"  Enlarged FP Scenes: {s['enlarged_components_scenes']}")

    print("\n[OIL SLICK QUARTILE RECOVERY (VALIDATION)]:")
    for q_name, qs in rep["oil_quartiles"].items():
        print(f"  {q_name} (gt: {qs['min_gt_px']}-{qs['max_gt_px']} px):")
        print(f"    Recall: Base {qs['mean_base_recall']:.4f} -> Fused {qs['mean_fused_recall']:.4f} (Pooled: {qs['pooled_base_recall']:.4f} -> {qs['pooled_fused_recall']:.4f})")
        print(f"    IoU:    Base {qs['mean_base_iou']:.4f} -> Fused {qs['mean_fused_iou']:.4f}")
        print(f"    Area:   Base {qs['mean_base_area_ratio']:.4f} -> Fused {qs['mean_fused_area_ratio']:.4f}")
