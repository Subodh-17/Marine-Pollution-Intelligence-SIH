"""
Full validation evaluation of the frozen candidate strategy:
'size_gated_closure_100_25' vs Single-Scale Baseline across all 386 validation scenes.
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, List

# Ensure backend root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import numpy as np
import scipy.ndimage as ndi
import tifffile
import torch
import torch.nn.functional as F

from app.evaluation.metrics import binary_mask_metrics
from app.ml.architectures import SpillUNet
from app.satellite.real_image import normalize_sar_band


def fuse_size_gated_closure(
    p_base: np.ndarray,
    p_coarse_up: np.ndarray,
    min_area: int = 100,
    struct_size: int = 25,
    coarse_weight: float = 0.95,
) -> np.ndarray:
    """Size-Gated Morphological Closure Infill:
    Coarse-scale infill is applied ONLY within the morphological closure
    of confirmed base slick components having area >= min_area.
    Prevents coarse false positives in isolated lookalikes and clean sea,
    while fully recovering interior hollows of large slicks.
    """
    base_mask = p_base > 0.45
    if not np.any(base_mask):
        return p_base
    lbl, n = ndi.label(base_mask)
    if n == 0:
        return p_base
    sizes = ndi.sum(base_mask, lbl, range(1, n + 1))
    valid_lbls = [i + 1 for i, s in enumerate(sizes) if s >= min_area]
    if not valid_lbls:
        return p_base
    large_seed = np.isin(lbl, valid_lbls)
    struct = np.ones((struct_size, struct_size), dtype=bool)
    closed = ndi.binary_closing(large_seed, structure=struct) | ndi.binary_fill_holes(large_seed)
    coarse_eff = np.clip(p_coarse_up * coarse_weight, 0.0, 1.0)
    return np.where(closed, np.maximum(p_base, coarse_eff), p_base).astype(np.float32)


def evaluate_validation_candidate(
    dataset_root: str = r"D:\zenodo_sar",
    manifest_path: str = r"D:\zenodo_sar\manifests\val_manifest.json",
    checkpoint_path: str = "backend/data/ml_checkpoints/spill_unet_real_best.pt",
    image_size: int = 256,
    threshold: float = 0.50,
) -> Dict[str, Any]:
    with open(manifest_path, "r", encoding="utf-8") as f:
        records = json.load(f)["records"]

    if not os.path.exists(checkpoint_path):
        for candidate in [
            os.path.join(os.path.dirname(__file__), "..", "..", "data", "ml_checkpoints", "spill_unet_real_best.pt"),
            os.path.join("data", "ml_checkpoints", "spill_unet_real_best.pt"),
            os.path.join("backend", "data", "ml_checkpoints", "spill_unet_real_best.pt"),
        ]:
            if os.path.exists(candidate):
                checkpoint_path = candidate
                break

    device = torch.device("cpu")
    model = SpillUNet(2, 1)
    model.load_state_dict(torch.load(checkpoint_path, map_location=device)["model_state_dict"])
    model.eval()

    # Metrics collectors
    cats = ["Oil", "Lookalike", "No-Oil"]
    res_base = {c: {"tp": 0, "fp": 0, "fn": 0, "tn": 0, "ious": [], "recalls": [], "precisions": []} for c in cats}
    res_cand = {c: {"tp": 0, "fp": 0, "fn": 0, "tn": 0, "ious": [], "recalls": [], "precisions": []} for c in cats}

    # Quartiles for Oil
    oil_sizes = []
    for r in records:
        if r["category"] == "Oil":
            gt = tifffile.imread(os.path.join(dataset_root, r["mask_path"]))
            oil_sizes.append((r["id"], int(np.sum(gt > 0))))
    oil_sizes.sort(key=lambda x: x[1])
    n_oil = len(oil_sizes)
    q1_ids = set(x[0] for x in oil_sizes[:n_oil // 4])
    q4_ids = set(x[0] for x in oil_sizes[3 * (n_oil // 4):])

    q_stats = {
        "Q1": {"base_rec": [], "cand_rec": [], "base_iou": [], "cand_iou": [], "base_area": [], "cand_area": []},
        "Q4": {"base_rec": [], "cand_rec": [], "base_iou": [], "cand_iou": [], "base_tp": 0, "cand_tp": 0, "gt": 0, "base_area": [], "cand_area": []},
    }

    latencies_base = []
    latencies_cand = []

    print(f"Evaluating 386 validation scenes for candidate 'size_gated_closure_100_25'...")
    t0 = time.time()

    with torch.no_grad():
        for idx, r in enumerate(records):
            cat = r["category"]
            sid = r["id"]
            arr = tifffile.imread(os.path.join(dataset_root, r["image_path"])).astype(np.float64)
            vv = normalize_sar_band(arr[..., 0] if arr.ndim == 3 else arr[0])
            vh = normalize_sar_band(arr[..., 1] if arr.ndim == 3 else arr[1])
            st = torch.from_numpy(np.stack([vv, vh], 0)).unsqueeze(0).float()
            st = F.interpolate(st, size=(image_size, image_size), mode="bilinear", align_corners=False)

            gt_arr = tifffile.imread(os.path.join(dataset_root, r["mask_path"]))
            gt = F.interpolate(torch.from_numpy(gt_arr > 0).float()[None, None, ...], size=(image_size, image_size), mode="nearest")[0, 0].numpy() > 0.5
            gt_px = int(np.sum(gt))

            # Base timing
            tb0 = time.perf_counter()
            p_base = torch.sigmoid(model(st))[0, 0].numpy()
            latencies_base.append((time.perf_counter() - tb0) * 1000.0)

            # Candidate timing
            tc0 = time.perf_counter()
            st_c = F.interpolate(st, size=(image_size // 2, image_size // 2), mode="bilinear", align_corners=False)
            p_c = torch.sigmoid(model(st_c))[0, 0].numpy()
            p_c_ero = ndi.minimum_filter(p_c, size=3)
            p_c_up = F.interpolate(torch.from_numpy(p_c_ero)[None, None, ...], size=(image_size, image_size), mode="bilinear", align_corners=False)[0, 0].numpy()
            p_cand = fuse_size_gated_closure(p_base, p_c_up, min_area=100, struct_size=25, coarse_weight=0.95)
            latencies_cand.append((time.perf_counter() - tc0) * 1000.0)

            pred_base = p_base > threshold
            pred_cand = p_cand > threshold

            # Base metrics
            tp_b = int(np.sum(pred_base & gt))
            fp_b = int(np.sum(pred_base & (~gt)))
            fn_b = int(np.sum((~pred_base) & gt))
            tn_b = int(np.sum((~pred_base) & (~gt)))
            mb = binary_mask_metrics(pred_base, gt)

            res_base[cat]["tp"] += tp_b
            res_base[cat]["fp"] += fp_b
            res_base[cat]["fn"] += fn_b
            res_base[cat]["tn"] += tn_b
            res_base[cat]["ious"].append(mb.iou)
            res_base[cat]["recalls"].append(mb.recall)
            res_base[cat]["precisions"].append(mb.precision)

            # Candidate metrics
            tp_c = int(np.sum(pred_cand & gt))
            fp_c = int(np.sum(pred_cand & (~gt)))
            fn_c = int(np.sum((~pred_cand) & gt))
            tn_c = int(np.sum((~pred_cand) & (~gt)))
            mc = binary_mask_metrics(pred_cand, gt)

            res_cand[cat]["tp"] += tp_c
            res_cand[cat]["fp"] += fp_c
            res_cand[cat]["fn"] += fn_c
            res_cand[cat]["tn"] += tn_c
            res_cand[cat]["ious"].append(mc.iou)
            res_cand[cat]["recalls"].append(mc.recall)
            res_cand[cat]["precisions"].append(mc.precision)

            # Quartile tracking
            if cat == "Oil":
                if sid in q1_ids:
                    q_stats["Q1"]["base_rec"].append(mb.recall)
                    q_stats["Q1"]["cand_rec"].append(mc.recall)
                    q_stats["Q1"]["base_iou"].append(mb.iou)
                    q_stats["Q1"]["cand_iou"].append(mc.iou)
                    q_stats["Q1"]["base_area"].append(float(np.sum(pred_base)) / max(1, gt_px))
                    q_stats["Q1"]["cand_area"].append(float(np.sum(pred_cand)) / max(1, gt_px))

                if sid in q4_ids:
                    q_stats["Q4"]["base_rec"].append(mb.recall)
                    q_stats["Q4"]["cand_rec"].append(mc.recall)
                    q_stats["Q4"]["base_iou"].append(mb.iou)
                    q_stats["Q4"]["cand_iou"].append(mc.iou)
                    q_stats["Q4"]["base_tp"] += tp_b
                    q_stats["Q4"]["cand_tp"] += tp_c
                    q_stats["Q4"]["gt"] += gt_px
                    q_stats["Q4"]["base_area"].append(float(np.sum(pred_base)) / max(1, gt_px))
                    q_stats["Q4"]["cand_area"].append(float(np.sum(pred_cand)) / max(1, gt_px))

            if (idx + 1) % 100 == 0 or (idx + 1) == len(records):
                print(f"  Processed {idx + 1}/{len(records)} ({time.time() - t0:.1f}s)")

    # Totals
    total_gt = sum(res_base[c]["tp"] + res_base[c]["fn"] for c in cats)
    total_tp_base = sum(res_base[c]["tp"] for c in cats)
    total_fp_base = sum(res_base[c]["fp"] for c in cats)
    total_fn_base = sum(res_base[c]["fn"] for c in cats)

    total_tp_cand = sum(res_cand[c]["tp"] for c in cats)
    total_fp_cand = sum(res_cand[c]["fp"] for c in cats)
    total_fn_cand = sum(res_cand[c]["fn"] for c in cats)

    pooled_recall_base = total_tp_base / max(1, total_gt)
    pooled_iou_base = total_tp_base / max(1, total_tp_base + total_fp_base + total_fn_base)
    pooled_precision_base = total_tp_base / max(1, total_tp_base + total_fp_base)

    pooled_recall_cand = total_tp_cand / max(1, total_gt)
    pooled_iou_cand = total_tp_cand / max(1, total_tp_cand + total_fp_cand + total_fn_cand)
    pooled_precision_cand = total_tp_cand / max(1, total_tp_cand + total_fp_cand)

    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "dataset_split": "validation",
        "num_scenes": len(records),
        "strategy_evaluated": "size_gated_closure_100_25",
        "parameters": {"min_area": 100, "struct_size": 25, "coarse_weight": 0.95, "threshold": 0.50},
        "latency_ms": {
            "mean_baseline": round(float(np.mean(latencies_base)), 2),
            "mean_candidate": round(float(np.mean(latencies_cand)), 2),
        },
        "pixel_pooled": {
            "baseline": {
                "recall": round(pooled_recall_base, 4),
                "iou": round(pooled_iou_base, 4),
                "precision": round(pooled_precision_base, 4),
                "tp": total_tp_base,
                "fp": total_fp_base,
                "fn": total_fn_base,
            },
            "candidate": {
                "recall": round(pooled_recall_cand, 4),
                "iou": round(pooled_iou_cand, 4),
                "precision": round(pooled_precision_cand, 4),
                "tp": total_tp_cand,
                "fp": total_fp_cand,
                "fn": total_fn_cand,
            },
            "delta": {
                "recall_gain_abs": round(pooled_recall_cand - pooled_recall_base, 4),
                "recall_gain_pct": round((pooled_recall_cand - pooled_recall_base) / pooled_recall_base * 100, 2),
                "iou_gain_abs": round(pooled_iou_cand - pooled_iou_base, 4),
                "iou_gain_pct": round((pooled_iou_cand - pooled_iou_base) / pooled_iou_base * 100, 2),
                "tp_delta": total_tp_cand - total_tp_base,
                "fp_delta": total_fp_cand - total_fp_base,
            },
        },
        "per_category": {
            "Oil": {
                "n_scenes": len(res_base["Oil"]["ious"]),
                "baseline_macro_iou": round(float(np.mean(res_base["Oil"]["ious"])), 4),
                "candidate_macro_iou": round(float(np.mean(res_cand["Oil"]["ious"])), 4),
                "baseline_macro_recall": round(float(np.mean(res_base["Oil"]["recalls"])), 4),
                "candidate_macro_recall": round(float(np.mean(res_cand["Oil"]["recalls"])), 4),
                "baseline_macro_precision": round(float(np.mean(res_base["Oil"]["precisions"])), 4),
                "candidate_macro_precision": round(float(np.mean(res_cand["Oil"]["precisions"])), 4),
            },
            "Lookalike": {
                "n_scenes": len(res_base["Lookalike"]["ious"]),
                "baseline_fp_px": res_base["Lookalike"]["fp"],
                "candidate_fp_px": res_cand["Lookalike"]["fp"],
                "fp_delta_px": res_cand["Lookalike"]["fp"] - res_base["Lookalike"]["fp"],
                "fp_delta_pct": round((res_cand["Lookalike"]["fp"] - res_base["Lookalike"]["fp"]) / max(1, res_base["Lookalike"]["fp"]) * 100, 2),
            },
            "No-Oil": {
                "n_scenes": len(res_base["No-Oil"]["ious"]),
                "baseline_fp_px": res_base["No-Oil"]["fp"],
                "candidate_fp_px": res_cand["No-Oil"]["fp"],
                "fp_delta_px": res_cand["No-Oil"]["fp"] - res_base["No-Oil"]["fp"],
            },
        },
        "quartiles": {
            "Q1": {
                "baseline_mean_recall": round(float(np.mean(q_stats["Q1"]["base_rec"])), 4),
                "candidate_mean_recall": round(float(np.mean(q_stats["Q1"]["cand_rec"])), 4),
                "baseline_mean_iou": round(float(np.mean(q_stats["Q1"]["base_iou"])), 4),
                "candidate_mean_iou": round(float(np.mean(q_stats["Q1"]["cand_iou"])), 4),
                "baseline_mean_area_ratio": round(float(np.mean(q_stats["Q1"]["base_area"])), 4),
                "candidate_mean_area_ratio": round(float(np.mean(q_stats["Q1"]["cand_area"])), 4),
            },
            "Q4": {
                "baseline_mean_recall": round(float(np.mean(q_stats["Q4"]["base_rec"])), 4),
                "candidate_mean_recall": round(float(np.mean(q_stats["Q4"]["cand_rec"])), 4),
                "baseline_pooled_recall": round(q_stats["Q4"]["base_tp"] / max(1, q_stats["Q4"]["gt"]), 4),
                "candidate_pooled_recall": round(q_stats["Q4"]["cand_tp"] / max(1, q_stats["Q4"]["gt"]), 4),
                "baseline_mean_iou": round(float(np.mean(q_stats["Q4"]["base_iou"])), 4),
                "candidate_mean_iou": round(float(np.mean(q_stats["Q4"]["cand_iou"])), 4),
                "baseline_mean_area_ratio": round(float(np.mean(q_stats["Q4"]["base_area"])), 4),
                "candidate_mean_area_ratio": round(float(np.mean(q_stats["Q4"]["cand_area"])), 4),
            },
        },
    }

    out_json = "backend/data/ml_checkpoints/validation_candidate_evaluation_report.json"
    os.makedirs(os.path.dirname(out_json), exist_ok=True)
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"Report saved to {out_json}")
    return report


if __name__ == "__main__":
    rep = evaluate_validation_candidate()
    print("\n--- VALIDATION EVALUATION: CANDIDATE 'size_gated_closure_100_25' ---")
    pp = rep["pixel_pooled"]
    print(f"Pixel-Pooled Recall: {pp['baseline']['recall']} -> {pp['candidate']['recall']} ({pp['delta']['recall_gain_pct']:+.1f}%)")
    print(f"Pixel-Pooled IoU:    {pp['baseline']['iou']} -> {pp['candidate']['iou']} ({pp['delta']['iou_gain_pct']:+.1f}%)")
    print(f"TP Pixels Recovered: {pp['delta']['tp_delta']:+d}")
    print(f"Lookalike FP Delta:  {rep['per_category']['Lookalike']['fp_delta_px']:+d} ({rep['per_category']['Lookalike']['fp_delta_pct']:+.1f}%)")
    print(f"No-Oil FP Delta:     {rep['per_category']['No-Oil']['fp_delta_px']:+d}")
    print(f"Q4 Mean Recall:      {rep['quartiles']['Q4']['baseline_mean_recall']} -> {rep['quartiles']['Q4']['candidate_mean_recall']}")
    print(f"Q4 Pooled Recall:    {rep['quartiles']['Q4']['baseline_pooled_recall']} -> {rep['quartiles']['Q4']['candidate_pooled_recall']}")
    print(f"Q1 Mean Area Ratio:  {rep['quartiles']['Q1']['baseline_mean_area_ratio']} -> {rep['quartiles']['Q1']['candidate_mean_area_ratio']}")
