"""
Validation experiment script for Phase 4.3B: Task 2
Evaluates candidate multiscale infill strategies on the 386 validation scenes
to eliminate lookalike false alarms while preserving large-slick recovery.

Candidate Strategies Tested:
1. 'baseline': Single-scale baseline (prob_base > 0.5)
2. 'boundary_preserved': Phase 4.3A default (max(prob_base, prob_coarse_ero * 0.95))
3. 'distance_bounded_infill_d24': Coarse infill restricted to within 24px of base detections (prob_base > 0.45)
4. 'distance_bounded_infill_d36': Coarse infill restricted to within 36px of base detections (prob_base > 0.45)
5. 'geodesic_reconstruction': Coarse components retained ONLY if connected to base seed (prob_base > 0.45)
6. 'base_evidence_gated': Coarse probability gated by continuous base evidence (prob_base > 0.15)
7. 'calibrated_distance_bounded': Distance-bounded (32px) with coarse threshold 0.55
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Dict, List

import numpy as np
import scipy.ndimage as ndi
import tifffile
import torch
import torch.nn.functional as F

from app.evaluation.metrics import binary_mask_metrics
from app.ml.architectures import SpillUNet
from app.satellite.real_image import normalize_sar_band


def fuse_distance_bounded(
    prob_base: np.ndarray,
    prob_coarse_up: np.ndarray,
    coarse_weight: float = 0.95,
    seed_thresh: float = 0.45,
    max_distance_px: int = 32,
    coarse_thresh: float = 0.50,
) -> np.ndarray:
    """Coarse infill is permitted ONLY within max_distance_px of base seeds.
    
    Prevents coarse false positives in isolated lookalike regions where base has zero support,
    while permitting full interior infill for large slicks bounded by base perimeter seeds.
    """
    base_seed = prob_base > seed_thresh
    if not np.any(base_seed):
        # No base evidence anywhere in the scene -> zero coarse expansion allowed
        return prob_base

    # Compute distance transform from base seeds
    dist = ndi.distance_transform_edt(~base_seed)
    valid_infill_zone = dist <= max_distance_px

    coarse_eff = np.clip(prob_coarse_up * coarse_weight, 0.0, 1.0)
    # Inside valid infill zone, take max(prob_base, coarse_eff); outside, stay strictly with prob_base
    prob_fused = np.where(valid_infill_zone, np.maximum(prob_base, coarse_eff), prob_base)
    return prob_fused.astype(np.float32)


def fuse_geodesic_reconstruction(
    prob_base: np.ndarray,
    prob_coarse_up: np.ndarray,
    coarse_weight: float = 0.95,
    seed_thresh: float = 0.45,
    coarse_thresh: float = 0.50,
) -> np.ndarray:
    """Coarse positive components are retained ONLY if they topologically overlap a base seed."""
    base_seed = prob_base > seed_thresh
    coarse_cand = (prob_coarse_up * coarse_weight) > coarse_thresh

    if not np.any(base_seed) or not np.any(coarse_cand):
        return prob_base

    # Label connected components of coarse candidates
    lbl, num_features = ndi.label(coarse_cand)
    if num_features == 0:
        return prob_base

    # Find which labels intersect with base_seed
    intersecting_labels = set(lbl[base_seed]) - {0}
    if not intersecting_labels:
        return prob_base

    # Retain only connected coarse components
    mask_retained = np.isin(lbl, list(intersecting_labels))
    coarse_eff = np.clip(prob_coarse_up * coarse_weight, 0.0, 1.0)
    prob_fused = np.where(mask_retained, np.maximum(prob_base, coarse_eff), prob_base)
    return prob_fused.astype(np.float32)


def fuse_base_evidence_gated(
    prob_base: np.ndarray,
    prob_coarse_up: np.ndarray,
    coarse_weight: float = 0.95,
    min_base_evidence: float = 0.15,
) -> np.ndarray:
    """Coarse probability is gated by whether base scale sees at least weak dark feature evidence."""
    # Where base has < min_base_evidence, coarse is attenuated to 0
    gate = np.clip((prob_base - min_base_evidence) / max(1e-4, 0.50 - min_base_evidence), 0.0, 1.0)
    coarse_gated = (prob_coarse_up * coarse_weight) * gate
    return np.maximum(prob_base, coarse_gated).astype(np.float32)


def run_strategy_comparison(
    dataset_root: str = r"D:\zenodo_sar",
    checkpoint_path: str = "backend/data/ml_checkpoints/spill_unet_real_best.pt",
    manifest_path: str = r"D:\zenodo_sar\manifests\val_manifest.json",
    image_size: int = 256,
    device: str = "cpu",
) -> Dict[str, Any]:
    torch_device = torch.device(device)

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    records = manifest.get("records", [])

    if not os.path.exists(checkpoint_path):
        for candidate in [
            os.path.join(os.path.dirname(__file__), "..", "..", "data", "ml_checkpoints", "spill_unet_real_best.pt"),
            os.path.join("data", "ml_checkpoints", "spill_unet_real_best.pt"),
            os.path.join("backend", "data", "ml_checkpoints", "spill_unet_real_best.pt"),
        ]:
            if os.path.exists(candidate):
                checkpoint_path = candidate
                break

    raw_checkpoint = torch.load(checkpoint_path, map_location=torch_device, weights_only=False)
    sd = raw_checkpoint.get("model_state_dict") if isinstance(raw_checkpoint, dict) else None
    model = SpillUNet(in_channels=2, out_channels=1).to(torch_device)
    model.load_state_dict(sd, strict=True)
    model.eval()

    # Define candidate strategies
    strategies: Dict[str, Callable[[np.ndarray, np.ndarray], np.ndarray]] = {
        "baseline": lambda p_base, p_coarse_up: p_base,
        "boundary_preserved": lambda p_base, p_coarse_up: np.maximum(p_base, p_coarse_up * 0.95).astype(np.float32),
        "distance_bounded_d24": lambda p_base, p_coarse_up: fuse_distance_bounded(p_base, p_coarse_up, max_distance_px=24),
        "distance_bounded_d32": lambda p_base, p_coarse_up: fuse_distance_bounded(p_base, p_coarse_up, max_distance_px=32),
        "distance_bounded_d40": lambda p_base, p_coarse_up: fuse_distance_bounded(p_base, p_coarse_up, max_distance_px=40),
        "geodesic_reconstruction": lambda p_base, p_coarse_up: fuse_geodesic_reconstruction(p_base, p_coarse_up),
        "base_evidence_gated": lambda p_base, p_coarse_up: fuse_base_evidence_gated(p_base, p_coarse_up, min_base_evidence=0.15),
        "calibrated_distance_d32": lambda p_base, p_coarse_up: fuse_distance_bounded(p_base, p_coarse_up, coarse_weight=0.90, max_distance_px=32),
    }

    # Initialize stats per strategy
    strat_results = {
        name: {
            "lookalike_fp_px": 0,
            "lookalike_fp_scenes": 0,
            "no_oil_fp_px": 0,
            "no_oil_fp_scenes": 0,
            "oil_overall_tp": 0,
            "oil_overall_fp": 0,
            "oil_overall_fn": 0,
            "oil_overall_gt": 0,
            "oil_scene_ious": [],
            "oil_scene_recalls": [],
            "oil_scene_precisions": [],
            "q1_ious": [],
            "q1_recalls": [],
            "q1_area_ratios": [],
            "q4_ious": [],
            "q4_recalls": [],
            "q4_area_ratios": [],
            "q4_tp": 0,
            "q4_gt": 0,
        }
        for name in strategies
    }

    # Pre-classify Oil scenes by size
    oil_sizes = []
    for rec in records:
        if rec["category"] == "Oil":
            mask_path = os.path.join(dataset_root, rec["mask_path"])
            gt_arr = tifffile.imread(mask_path)
            gt_px = int(np.sum(gt_arr > 0))
            oil_sizes.append((rec["id"], gt_px))
    oil_sizes.sort(key=lambda x: x[1])
    n_oil = len(oil_sizes)
    q_len = n_oil // 4
    q1_ids = set(x[0] for x in oil_sizes[:q_len])
    q4_ids = set(x[0] for x in oil_sizes[3 * q_len:])

    print(f"Loaded {len(records)} validation scenes. Evaluating {len(strategies)} candidate strategies...")
    t_start = time.time()

    with torch.no_grad():
        for idx, rec in enumerate(records):
            img_path = os.path.join(dataset_root, rec["image_path"])
            mask_path = os.path.join(dataset_root, rec["mask_path"])
            cat = rec["category"]
            scene_id = rec["id"]

            arr = tifffile.imread(img_path).astype(np.float64)
            vv_raw, vh_raw = (arr[..., 0], arr[..., 1]) if (arr.ndim == 3 and arr.shape[-1] >= 2) else (arr[0], arr[1])
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

            # 1. Base inference
            logits_base = model(img_t)
            prob_base = torch.sigmoid(logits_base)[0, 0].cpu().numpy()

            # 2. Coarse inference with boundary-preserving erosion
            img_coarse = F.interpolate(img_t, size=(image_size // 2, image_size // 2), mode="bilinear", align_corners=False)
            logits_coarse = model(img_coarse)
            prob_coarse = torch.sigmoid(logits_coarse)[0, 0].cpu().numpy()
            prob_coarse_ero = ndi.minimum_filter(prob_coarse, size=3)
            prob_coarse_t = torch.from_numpy(prob_coarse_ero)[None, None, ...].to(torch_device)
            prob_coarse_up = F.interpolate(prob_coarse_t, size=(image_size, image_size), mode="bilinear", align_corners=False)[0, 0].cpu().numpy()

            # Evaluate each strategy on this scene
            for s_name, s_fn in strategies.items():
                prob_fused = s_fn(prob_base, prob_coarse_up)
                pred_bin = prob_fused > 0.50

                res = strat_results[s_name]

                if cat == "Lookalike":
                    fp_px = int(np.sum(pred_bin))
                    res["lookalike_fp_px"] += fp_px
                    if fp_px > 0:
                        res["lookalike_fp_scenes"] += 1

                elif cat == "No-Oil":
                    fp_px = int(np.sum(pred_bin))
                    res["no_oil_fp_px"] += fp_px
                    if fp_px > 0:
                        res["no_oil_fp_scenes"] += 1

                elif cat == "Oil":
                    tp = int(np.sum(pred_bin & gt_mask))
                    fp = int(np.sum(pred_bin & (~gt_mask)))
                    fn = int(np.sum((~pred_bin) & gt_mask))

                    res["oil_overall_tp"] += tp
                    res["oil_overall_fp"] += fp
                    res["oil_overall_fn"] += fn
                    res["oil_overall_gt"] += gt_px

                    m = binary_mask_metrics(pred_bin, gt_mask)
                    res["oil_scene_ious"].append(m.iou)
                    res["oil_scene_recalls"].append(m.recall)
                    res["oil_scene_precisions"].append(m.precision)

                    area_ratio = float(np.sum(pred_bin)) / max(1, gt_px)

                    if scene_id in q1_ids:
                        res["q1_ious"].append(m.iou)
                        res["q1_recalls"].append(m.recall)
                        res["q1_area_ratios"].append(area_ratio)

                    if scene_id in q4_ids:
                        res["q4_ious"].append(m.iou)
                        res["q4_recalls"].append(m.recall)
                        res["q4_area_ratios"].append(area_ratio)
                        res["q4_tp"] += tp
                        res["q4_gt"] += gt_px

            if (idx + 1) % 50 == 0 or (idx + 1) == len(records):
                print(f"Evaluated {idx + 1}/{len(records)} validation scenes ({time.time() - t_start:.1f}s)")

    # Compile comparative results summary
    summary = {}
    base_lookalike_fp = strat_results["baseline"]["lookalike_fp_px"]

    for s_name, res in strat_results.items():
        lk_fp = res["lookalike_fp_px"]
        lk_delta = lk_fp - base_lookalike_fp
        lk_delta_pct = (lk_delta / max(1, base_lookalike_fp)) * 100.0

        tp_tot = res["oil_overall_tp"]
        fp_tot = res["oil_overall_fp"]
        fn_tot = res["oil_overall_fn"]
        gt_tot = res["oil_overall_gt"]

        pooled_recall = tp_tot / max(1, gt_tot)
        pooled_iou = tp_tot / max(1, tp_tot + fp_tot + fn_tot)
        pooled_precision = tp_tot / max(1, tp_tot + fp_tot)

        q4_pooled_recall = res["q4_tp"] / max(1, res["q4_gt"])

        summary[s_name] = {
            "lookalike_fp_px": lk_fp,
            "lookalike_fp_delta": lk_delta,
            "lookalike_fp_delta_pct": round(lk_delta_pct, 2),
            "lookalike_fp_scenes": res["lookalike_fp_scenes"],
            "no_oil_fp_px": res["no_oil_fp_px"],
            "no_oil_fp_scenes": res["no_oil_fp_scenes"],
            "oil_macro_iou": round(float(np.mean(res["oil_scene_ious"])), 4),
            "oil_macro_recall": round(float(np.mean(res["oil_scene_recalls"])), 4),
            "oil_macro_precision": round(float(np.mean(res["oil_scene_precisions"])), 4),
            "oil_pooled_recall": round(float(pooled_recall), 4),
            "oil_pooled_iou": round(float(pooled_iou), 4),
            "oil_pooled_precision": round(float(pooled_precision), 4),
            "q1_mean_iou": round(float(np.mean(res["q1_ious"])), 4),
            "q1_mean_recall": round(float(np.mean(res["q1_recalls"])), 4),
            "q1_mean_area_ratio": round(float(np.mean(res["q1_area_ratios"])), 4),
            "q4_mean_iou": round(float(np.mean(res["q4_ious"])), 4),
            "q4_mean_recall": round(float(np.mean(res["q4_recalls"])), 4),
            "q4_pooled_recall": round(float(q4_pooled_recall), 4),
            "q4_mean_area_ratio": round(float(np.mean(res["q4_area_ratios"])), 4),
        }

    out_file = "backend/data/ml_checkpoints/validation_strategy_comparison.json"
    os.makedirs(os.path.dirname(out_file), exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print(f"\nSaved strategy comparison report to {out_file}")
    return summary


if __name__ == "__main__":
    res = run_strategy_comparison()
    print("\n" + "=" * 90)
    print("VALIDATION STRATEGY COMPARISON (386 SCENES)")
    print("=" * 90)
    print(f"{'Strategy':<26} | {'Lookalike FP':<14} | {'Delta vs Base':<14} | {'Q4 Recall':<10} | {'Q4 Area':<8} | {'Q1 Area':<8} | {'Oil IoU':<8}")
    print("-" * 90)
    for name, s in res.items():
        print(f"{name:<26} | {s['lookalike_fp_px']:<14} | {s['lookalike_fp_delta']:+d} ({s['lookalike_fp_delta_pct']:+.1f}%) | {s['q4_mean_recall']:.4f}     | {s['q4_mean_area_ratio']:.4f}   | {s['q1_mean_area_ratio']:.4f}   | {s['oil_macro_iou']:.4f}")
    print("=" * 90)
