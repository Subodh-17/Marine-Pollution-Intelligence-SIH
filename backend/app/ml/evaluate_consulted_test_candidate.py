"""
Exploratory Generalization Check on Previously Consulted Test Set (Part III - 450 scenes).
Evaluates the frozen candidate 'size_gated_closure_100_25' vs Single-Scale Baseline.
NOTE: Per Phase 4.3B instructions, Part III was previously consulted for the initial gate,
so this is an exploratory check, NOT an unbiased independent evaluation.
"""
from __future__ import annotations

import json
import os
import sys
import time

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


def run_consulted_test():
    dataset_root = r"D:\zenodo_sar"
    manifest_path = r"D:\zenodo_sar\manifests\test_manifest.json"
    ckpt_path = "backend/data/ml_checkpoints/spill_unet_real_best.pt"
    if not os.path.exists(ckpt_path):
        ckpt_path = "data/ml_checkpoints/spill_unet_real_best.pt"

    with open(manifest_path) as f:
        records = json.load(f)["records"]

    device = torch.device("cpu")
    model = SpillUNet(2, 1)
    model.load_state_dict(torch.load(ckpt_path, map_location=device)["model_state_dict"])
    model.eval()

    cats = ["Oil", "Lookalike", "No-Oil"]
    res_base = {c: {"tp": 0, "fp": 0, "fn": 0, "ious": [], "recalls": []} for c in cats}
    res_cand = {c: {"tp": 0, "fp": 0, "fn": 0, "ious": [], "recalls": []} for c in cats}
    q4_stats = {"base_tp": 0, "cand_tp": 0, "gt": 0, "base_rec": [], "cand_rec": [], "base_iou": [], "cand_iou": []}
    q1_stats = {"base_area": [], "cand_area": []}

    # Find Q1 and Q4 oil scenes
    oil_sizes = []
    for r in records:
        if r["category"] == "Oil":
            gt = tifffile.imread(os.path.join(dataset_root, r["mask_path"]))
            oil_sizes.append((r["id"], int(np.sum(gt > 0))))
    oil_sizes.sort(key=lambda x: x[1])
    n_oil = len(oil_sizes)
    q1_ids = set(x[0] for x in oil_sizes[:n_oil // 4])
    q4_ids = set(x[0] for x in oil_sizes[3 * (n_oil // 4):])

    print(f"Evaluating {len(records)} previously consulted test scenes...")
    t0 = time.time()

    with torch.no_grad():
        for idx, r in enumerate(records):
            cat = r["category"]
            sid = r["id"]
            arr = tifffile.imread(os.path.join(dataset_root, r["image_path"])).astype(np.float64)
            vv = normalize_sar_band(arr[..., 0] if arr.ndim == 3 else arr[0])
            vh = normalize_sar_band(arr[..., 1] if arr.ndim == 3 else arr[1])
            st = torch.from_numpy(np.stack([vv, vh], 0)).unsqueeze(0).float()
            st = F.interpolate(st, size=(256, 256), mode="bilinear", align_corners=False)

            gt_arr = tifffile.imread(os.path.join(dataset_root, r["mask_path"]))
            gt = F.interpolate(torch.from_numpy(gt_arr > 0).float()[None, None, ...], size=(256, 256), mode="nearest")[0, 0].numpy() > 0.5
            gt_px = int(np.sum(gt))

            p_base = torch.sigmoid(model(st))[0, 0].numpy()
            st_c = F.interpolate(st, size=(128, 128), mode="bilinear", align_corners=False)
            p_c = torch.sigmoid(model(st_c))[0, 0].numpy()
            p_c_ero = ndi.minimum_filter(p_c, size=3)
            p_c_up = F.interpolate(torch.from_numpy(p_c_ero)[None, None, ...], size=(256, 256), mode="bilinear", align_corners=False)[0, 0].numpy()
            p_cand = fuse_size_gated_closure(p_base, p_c_up, min_area=100, struct_size=25, coarse_weight=0.95)

            pred_base = p_base > 0.50
            pred_cand = p_cand > 0.50

            # Base metrics
            tp_b = int(np.sum(pred_base & gt))
            fp_b = int(np.sum(pred_base & (~gt)))
            fn_b = int(np.sum((~pred_base) & gt))
            mb = binary_mask_metrics(pred_base, gt)
            res_base[cat]["tp"] += tp_b
            res_base[cat]["fp"] += fp_b
            res_base[cat]["fn"] += fn_b
            res_base[cat]["ious"].append(mb.iou)
            res_base[cat]["recalls"].append(mb.recall)

            # Candidate metrics
            tp_c = int(np.sum(pred_cand & gt))
            fp_c = int(np.sum(pred_cand & (~gt)))
            fn_c = int(np.sum((~pred_cand) & gt))
            mc = binary_mask_metrics(pred_cand, gt)
            res_cand[cat]["tp"] += tp_c
            res_cand[cat]["fp"] += fp_c
            res_cand[cat]["fn"] += fn_c
            res_cand[cat]["ious"].append(mc.iou)
            res_cand[cat]["recalls"].append(mc.recall)

            if cat == "Oil":
                if sid in q4_ids:
                    q4_stats["base_tp"] += tp_b
                    q4_stats["cand_tp"] += tp_c
                    q4_stats["gt"] += gt_px
                    q4_stats["base_rec"].append(mb.recall)
                    q4_stats["cand_rec"].append(mc.recall)
                    q4_stats["base_iou"].append(mb.iou)
                    q4_stats["cand_iou"].append(mc.iou)
                if sid in q1_ids:
                    q1_stats["base_area"].append(float(np.sum(pred_base)) / max(1, gt_px))
                    q1_stats["cand_area"].append(float(np.sum(pred_cand)) / max(1, gt_px))

            if (idx + 1) % 100 == 0 or (idx + 1) == len(records):
                print(f"  Processed {idx + 1}/{len(records)} ({time.time() - t0:.1f}s)")

    tot_tp_b = sum(res_base[c]["tp"] for c in cats)
    tot_fp_b = sum(res_base[c]["fp"] for c in cats)
    tot_fn_b = sum(res_base[c]["fn"] for c in cats)
    tot_gt = tot_tp_b + tot_fn_b

    tot_tp_c = sum(res_cand[c]["tp"] for c in cats)
    tot_fp_c = sum(res_cand[c]["fp"] for c in cats)
    tot_fn_c = sum(res_cand[c]["fn"] for c in cats)

    rep = {
        "dataset_split": "previously_consulted_test_split",
        "num_scenes": len(records),
        "pixel_pooled": {
            "baseline": {
                "recall": round(tot_tp_b / max(1, tot_gt), 4),
                "iou": round(tot_tp_b / max(1, tot_tp_b + tot_fp_b + tot_fn_b), 4),
                "precision": round(tot_tp_b / max(1, tot_tp_b + tot_fp_b), 4),
                "tp": tot_tp_b,
                "fp": tot_fp_b,
            },
            "candidate": {
                "recall": round(tot_tp_c / max(1, tot_gt), 4),
                "iou": round(tot_tp_c / max(1, tot_tp_c + tot_fp_c + tot_fn_c), 4),
                "precision": round(tot_tp_c / max(1, tot_tp_c + tot_fp_c), 4),
                "tp": tot_tp_c,
                "fp": tot_fp_c,
            },
        },
        "lookalike": {
            "baseline_fp": res_base["Lookalike"]["fp"],
            "candidate_fp": res_cand["Lookalike"]["fp"],
            "delta_fp": res_cand["Lookalike"]["fp"] - res_base["Lookalike"]["fp"],
            "delta_pct": round((res_cand["Lookalike"]["fp"] - res_base["Lookalike"]["fp"]) / max(1, res_base["Lookalike"]["fp"]) * 100, 2),
        },
        "no_oil": {
            "baseline_fp": res_base["No-Oil"]["fp"],
            "candidate_fp": res_cand["No-Oil"]["fp"],
            "delta_fp": res_cand["No-Oil"]["fp"] - res_base["No-Oil"]["fp"],
        },
        "q4": {
            "baseline_mean_recall": round(float(np.mean(q4_stats["base_rec"])), 4),
            "candidate_mean_recall": round(float(np.mean(q4_stats["cand_rec"])), 4),
            "baseline_pooled_recall": round(q4_stats["base_tp"] / max(1, q4_stats["gt"]), 4),
            "candidate_pooled_recall": round(q4_stats["cand_tp"] / max(1, q4_stats["gt"]), 4),
            "baseline_mean_iou": round(float(np.mean(q4_stats["base_iou"])), 4),
            "candidate_mean_iou": round(float(np.mean(q4_stats["cand_iou"])), 4),
        },
        "q1": {
            "baseline_area_ratio": round(float(np.mean(q1_stats["base_area"])), 4),
            "candidate_area_ratio": round(float(np.mean(q1_stats["cand_area"])), 4),
        },
    }

    out_json = "backend/data/ml_checkpoints/consulted_test_candidate_report.json"
    with open(out_json, "w") as f:
        json.dump(rep, f, indent=2)
    print(f"Saved consulted test check to {out_json}")
    return rep


if __name__ == "__main__":
    rep = run_consulted_test()
    print("\n--- CONSULTED TEST SET CHECK: CANDIDATE 'size_gated_closure_100_25' ---")
    pp = rep["pixel_pooled"]
    print(f"Pixel Recall:   {pp['baseline']['recall']} -> {pp['candidate']['recall']}")
    print(f"Pixel IoU:      {pp['baseline']['iou']} -> {pp['candidate']['iou']}")
    print(f"Lookalike FP:   {rep['lookalike']['baseline_fp']} -> {rep['lookalike']['candidate_fp']} (Delta: {rep['lookalike']['delta_fp']:+d}, {rep['lookalike']['delta_pct']:+.1f}%)")
    print(f"No-Oil FP:      {rep['no_oil']['baseline_fp']} -> {rep['no_oil']['candidate_fp']} (Delta: {rep['no_oil']['delta_fp']:+d})")
    print(f"Q4 Mean Recall: {rep['q4']['baseline_mean_recall']} -> {rep['q4']['candidate_mean_recall']}")
    print(f"Q4 Pool Recall: {rep['q4']['baseline_pooled_recall']} -> {rep['q4']['candidate_pooled_recall']}")
    print(f"Q1 Area Ratio:  {rep['q1']['baseline_area_ratio']} -> {rep['q1']['candidate_area_ratio']}")
