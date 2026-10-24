import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from app.ml.evaluate_real import _format_markdown
import numpy as np

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
json_path = os.path.join(repo_root, "backend", "data", "ml_checkpoints", "evaluation_report.json")
md_path = os.path.join(repo_root, "backend", "data", "ml_checkpoints", "evaluation_report.md")

with open(json_path, "r", encoding="utf-8") as f:
    data = json.load(f)

per_record = data["per_scene"]
total_tp = sum(r["tp"] for r in per_record)
total_fp = sum(r["fp"] for r in per_record)
total_fn = sum(r["fn"] for r in per_record)
total_tn = sum(r["tn"] for r in per_record)

pooled_p = round(float(total_tp / (total_tp + total_fp)), 4) if (total_tp + total_fp) else 0.0
pooled_r = round(float(total_tp / (total_tp + total_fn)), 4) if (total_tp + total_fn) else 0.0
pooled_f1 = round(float(2 * total_tp / (2 * total_tp + total_fp + total_fn)), 4) if (2 * total_tp + total_fp + total_fn) else 0.0
pooled_iou = round(float(total_tp / (total_tp + total_fp + total_fn)), 4) if (total_tp + total_fp + total_fn) else 0.0
pooled_total = total_tp + total_fp + total_fn + total_tn
pooled_acc = round(float((total_tp + total_tn) / pooled_total), 4) if pooled_total else 1.0

data["pixel_pooled"] = {
    "precision": pooled_p,
    "recall": pooled_r,
    "f1": pooled_f1,
    "iou": pooled_iou,
    "dice": pooled_f1,
    "pixel_accuracy": pooled_acc,
    "tp": total_tp,
    "fp": total_fp,
    "fn": total_fn,
    "tn": total_tn,
}

data["oil_scene_macro"] = data["per_category"].get("Oil", {})

oil_records = [r for r in per_record if r["category"] == "Oil"]
oil_sorted = sorted(oil_records, key=lambda r: (r["tp"] + r["fn"]))
n_oil = len(oil_sorted)
q_slices = [
    ("Q1_smallest", oil_sorted[:n_oil // 4], "< 1.9%"),
    ("Q2_small_medium", oil_sorted[n_oil // 4: 2 * (n_oil // 4)], "1.9% - 4.3%"),
    ("Q3_medium_large", oil_sorted[2 * (n_oil // 4): 3 * (n_oil // 4)], "4.3% - 11.0%"),
    ("Q4_largest", oil_sorted[3 * (n_oil // 4):], "> 11.0%"),
]
tot_oil_pixels = sum(r["tp"] + r["fn"] for r in oil_sorted)
quartiles = {}
for q_name, q_list, cov_label in q_slices:
    q_gt = sum(r["tp"] + r["fn"] for r in q_list)
    q_tp = sum(r["tp"] for r in q_list)
    q_pred = sum(r["tp"] + r["fp"] for r in q_list)
    q_prec = round(float(q_tp / q_pred), 4) if q_pred else 0.0
    q_rec = round(float(np.mean([r["recall"] for r in q_list])), 4)
    q_iou = round(float(np.mean([r["iou"] for r in q_list])), 4)
    q_ratio = round(float(q_pred / q_gt), 4) if q_gt else 0.0
    quartiles[q_name] = {
        "n_scenes": len(q_list),
        "frame_coverage": cov_label,
        "share_of_oil_pixels": round(float(q_gt / tot_oil_pixels), 4) if tot_oil_pixels else 0.0,
        "mean_pixel_recall": q_rec,
        "mean_iou": q_iou,
        "pooled_precision": q_prec,
        "predicted_to_true_area_ratio": q_ratio,
    }

data["slick_size_quartiles"] = quartiles
data["caveats"] = [
    "SCIENTIFIC HONESTY GUARD (Audit Section 1): The headline 'overall' recall of 0.8962 is an unweighted "
    "arithmetic mean across all 450 scenes. The 300 No-Oil/Lookalike scenes have empty masks where "
    "binary_mask_metrics assigned recall=1.0 by convention. True oil detection capability is measured "
    "by pixel_pooled recall (0.3949) or oil_scene_macro recall (0.6886).",
    "LARGE-SLICK FAILURE (Audit Section 2): Large slicks (Q4, >11% frame coverage, containing ~73% of oil pixels) "
    "suffer from severe receptive-field under-segmentation: mean recall is 0.324-0.335 and predicted area "
    "is only ~26% of true spill size. High precision (~99%) confirms the model under-segments rather than hallucinates.",
    "This is the dataset's own published test split (Part III), never mixed into train/val "
    "(see app/ml/dataset.py build_manifests 'LEAKAGE PREVENTION').",
    "Metrics reflect this exact checkpoint at this exact epoch (best epoch 18) and threshold (0.5)."
]

with open(json_path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)

md_content = _format_markdown(data)
with open(md_path, "w", encoding="utf-8") as f:
    f.write(md_content)

print("Successfully updated evaluation_report.json and evaluation_report.md")
