"""
Validation experiment: Spatial infill strategies targeting lookalike false alarm suppression.
Tests size-gating, distance-bounding, and morphological closure on 386 validation scenes.
"""
from __future__ import annotations

import json
import os
import time
from typing import Dict

import numpy as np
import scipy.ndimage as ndi
import tifffile
import torch
import torch.nn.functional as F

from app.ml.architectures import SpillUNet
from app.satellite.real_image import normalize_sar_band


def fuse_size_gated(p_base: np.ndarray, p_coarse_up: np.ndarray, min_area: int = 150, max_dist: int = 32) -> np.ndarray:
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
    dist = ndi.distance_transform_edt(~large_seed)
    valid_zone = dist <= max_dist
    coarse_eff = np.clip(p_coarse_up * 0.95, 0.0, 1.0)
    return np.where(valid_zone, np.maximum(p_base, coarse_eff), p_base).astype(np.float32)


def fuse_closure_infill(p_base: np.ndarray, p_coarse_up: np.ndarray, struct_size: int = 25) -> np.ndarray:
    base_mask = p_base > 0.45
    if not np.any(base_mask):
        return p_base
    struct = np.ones((struct_size, struct_size), dtype=bool)
    closed = ndi.binary_closing(base_mask, structure=struct)
    infill_zone = closed
    coarse_eff = np.clip(p_coarse_up * 0.95, 0.0, 1.0)
    return np.where(infill_zone, np.maximum(p_base, coarse_eff), p_base).astype(np.float32)


def fuse_size_gated_closure(p_base: np.ndarray, p_coarse_up: np.ndarray, min_area: int = 150, struct_size: int = 31) -> np.ndarray:
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
    closed = ndi.binary_closing(large_seed, structure=struct)
    coarse_eff = np.clip(p_coarse_up * 0.95, 0.0, 1.0)
    return np.where(closed, np.maximum(p_base, coarse_eff), p_base).astype(np.float32)


def main():
    manifest_path = r"D:\zenodo_sar\manifests\val_manifest.json"
    dataset_root = r"D:\zenodo_sar"

    with open(manifest_path) as f:
        records = json.load(f)["records"]

    device = torch.device("cpu")
    model = SpillUNet(2, 1)
    ckpt_path = "data/ml_checkpoints/spill_unet_real_best.pt"
    if not os.path.exists(ckpt_path):
        ckpt_path = "backend/data/ml_checkpoints/spill_unet_real_best.pt"
    model.load_state_dict(torch.load(ckpt_path, map_location=device)["model_state_dict"])
    model.eval()

    results: Dict[str, Dict] = {
        "baseline": {"lk_fp": 0, "no_oil_fp": 0, "q4_tp": 0, "q4_gt": 0, "q4_rec": [], "q1_rec": [], "q1_area": []},
        "boundary_preserved": {"lk_fp": 0, "no_oil_fp": 0, "q4_tp": 0, "q4_gt": 0, "q4_rec": [], "q1_rec": [], "q1_area": []},
        "size_gated_150_dist32": {"lk_fp": 0, "no_oil_fp": 0, "q4_tp": 0, "q4_gt": 0, "q4_rec": [], "q1_rec": [], "q1_area": []},
        "closure_infill_25": {"lk_fp": 0, "no_oil_fp": 0, "q4_tp": 0, "q4_gt": 0, "q4_rec": [], "q1_rec": [], "q1_area": []},
        "size_gated_closure_100_25": {"lk_fp": 0, "no_oil_fp": 0, "q4_tp": 0, "q4_gt": 0, "q4_rec": [], "q1_rec": [], "q1_area": []},
        "size_gated_closure_150_31": {"lk_fp": 0, "no_oil_fp": 0, "q4_tp": 0, "q4_gt": 0, "q4_rec": [], "q1_rec": [], "q1_area": []},
    }

    oil_sizes = []
    for r in records:
        if r["category"] == "Oil":
            gt = tifffile.imread(os.path.join(dataset_root, r["mask_path"]))
            oil_sizes.append((r["id"], int(np.sum(gt > 0))))
    oil_sizes.sort(key=lambda x: x[1])
    n_oil = len(oil_sizes)
    q1_ids = set(x[0] for x in oil_sizes[:n_oil // 4])
    q4_ids = set(x[0] for x in oil_sizes[3 * (n_oil // 4):])

    print(f"Evaluating {len(records)} validation scenes across {len(results)} strategies...")
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
            p_base = torch.sigmoid(model(st))[0, 0].numpy()

            st_c = F.interpolate(st, size=(128, 128), mode="bilinear", align_corners=False)
            p_c = torch.sigmoid(model(st_c))[0, 0].numpy()
            p_c_ero = ndi.minimum_filter(p_c, size=3)
            p_c_up = F.interpolate(torch.from_numpy(p_c_ero)[None, None, ...], size=(256, 256), mode="bilinear", align_corners=False)[0, 0].numpy()

            preds = {
                "baseline": p_base > 0.50,
                "boundary_preserved": np.maximum(p_base, p_c_up * 0.95) > 0.50,
                "size_gated_150_dist32": fuse_size_gated(p_base, p_c_up, min_area=150, max_dist=32) > 0.50,
                "closure_infill_25": fuse_closure_infill(p_base, p_c_up, struct_size=25) > 0.50,
                "size_gated_closure_100_25": fuse_size_gated_closure(p_base, p_c_up, min_area=100, struct_size=25) > 0.50,
                "size_gated_closure_150_31": fuse_size_gated_closure(p_base, p_c_up, min_area=150, struct_size=31) > 0.50,
            }

            if cat == "Lookalike":
                for k, p in preds.items():
                    results[k]["lk_fp"] += int(np.sum(p))
            elif cat == "No-Oil":
                for k, p in preds.items():
                    results[k]["no_oil_fp"] += int(np.sum(p))
            elif cat == "Oil":
                gt_arr = tifffile.imread(os.path.join(dataset_root, r["mask_path"]))
                gt = F.interpolate(torch.from_numpy(gt_arr > 0).float()[None, None, ...], size=(256, 256), mode="nearest")[0, 0].numpy() > 0.5
                gt_px = int(np.sum(gt))

                if sid in q4_ids:
                    for k, p in preds.items():
                        tp = int(np.sum(p & gt))
                        results[k]["q4_tp"] += tp
                        results[k]["q4_gt"] += gt_px
                        results[k]["q4_rec"].append(tp / max(1, gt_px))

                if sid in q1_ids:
                    for k, p in preds.items():
                        tp = int(np.sum(p & gt))
                        results[k]["q1_rec"].append(tp / max(1, gt_px))
                        results[k]["q1_area"].append(float(np.sum(p)) / max(1, gt_px))

            if (idx + 1) % 100 == 0 or (idx + 1) == len(records):
                print(f"  Processed {idx + 1}/{len(records)} scenes ({time.time() - t0:.1f}s)")

    print("\n" + "=" * 105)
    print("TASK 2: SPATIAL INFILL STRATEGY VALIDATION RESULTS (386 SCENES)")
    print("=" * 105)
    print(f"{'Strategy':<20} | {'Lookalike FP':<14} | {'Delta vs Base':<14} | {'No-Oil FP':<10} | {'Q4 Mean Rec':<11} | {'Q4 Pool Rec':<11} | {'Q1 Area':<8}")
    print("-" * 105)
    base_lk = results["baseline"]["lk_fp"]
    for k, v in results.items():
        delta = v["lk_fp"] - base_lk
        pct = (delta / max(1, base_lk)) * 100.0
        q4_m_rec = np.mean(v["q4_rec"]) if v["q4_rec"] else 0.0
        q4_p_rec = v["q4_tp"] / max(1, v["q4_gt"]) if v["q4_gt"] else 0.0
        q1_area = np.mean(v["q1_area"]) if v["q1_area"] else 0.0
        print(f"{k:<20} | {v['lk_fp']:<14} | {delta:+d} ({pct:+.1f}%) | {v['no_oil_fp']:<10} | {q4_m_rec:<11.4f} | {q4_p_rec:<11.4f} | {q1_area:<8.4f}")
    print("=" * 105)


if __name__ == "__main__":
    main()
