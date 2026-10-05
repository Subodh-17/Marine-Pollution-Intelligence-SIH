"""
Evaluation of the classical detector against the bundled sample SAR scenes'
real ground-truth masks (backend/data/sample_sar/*_mask.png).

WHY THIS EXISTS
----------------
Earlier drafts of this project's documentation stated that "no labelled
ground truth was reachable in this environment" (justifying why no
precision/recall/F1/IoU number was ever reported anywhere). That statement
was stale: `backend/data/sample_sar/` ships four scenes each with a genuine
binary `_mask.png` ground-truth companion. This module computes real,
non-fabricated metrics against them - MODEL_CARD.md and README.md have been
updated to report the numbers this module actually produces, not an assumed
"can't be done" placeholder.

WHAT THIS EVALUATION IS AND IS NOT
------------------------------------
- It IS a real, reproducible sanity check of the classical detector
  (`app/satellite/detection.py`) against 3 procedurally-generated sample
  scenes with unambiguous ground truth: two "oil" scenes (scored on
  precision/recall/F1/IoU) and one "no-oil" reference scene (scored
  separately as a false-positive-rate control, since precision/recall/F1/IoU
  degenerate to an uninformative step function when ground truth has zero
  positive pixels).
- It is NOT a statistically meaningful benchmark. n=2 scored oil scenes is far
  too small to generalize from, and the images are procedurally-generated
  sample data carried over from an earlier project, not real Sentinel-1
  acquisitions (see DATA_PIPELINE.md). Retraining/evaluating against the
  real Zenodo Sentinel-1 SAR Oil Spill Dataset remains the actual next step.
- The fourth bundled scene, `demo_lookalike_scene_01`, is DELIBERATELY
  EXCLUDED from the scored metrics. Its `_mask.png` was carried over from an
  earlier project with no accompanying documentation establishing whether it
  labels "true oil pixels" or "the look-alike feature's own footprint" (a
  look-alike is, by definition, a region that should NOT be classified as
  oil). Scoring it as an "is-oil" ground truth mask without knowing which of
  those two things it means would risk fabricating a misleading number - a
  detector that correctly reports "not oil" for a look-alike would score a
  false 0% recall if the mask actually means "look-alike footprint", while
  it would score a true 0% recall (a real miss) if it actually means "there
  is unlabelled real oil here too." Rather than guess, this scene's raw
  detector output is reported separately as a qualitative, unscored data
  point only (see `excluded` in the returned dict).
"""
from __future__ import annotations

import os

import numpy as np
from PIL import Image

from app.satellite.real_image import load_real_scene
from app.satellite.preprocessing import lee_filter, normalize, simple_sea_mask
from app.satellite.detection import detect_oil_slick
from app.evaluation.metrics import binary_mask_metrics, confusion_matrix

SAMPLE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "sample_sar")

# Scenes with an actual positive (oil) class - precision/recall/F1/IoU are
# meaningful here and are averaged together into the aggregate below.
_OIL_SCENES = ["demo_oil_scene_01", "demo_oil_scene_02"]
# Scored, but kept OUT of the precision/recall/F1/IoU aggregate: with zero
# ground-truth positive pixels, "precision" degenerates to a not-very-meaningful
# 0.0/1.0 step function rather than a graded score, so blending it into the
# same average as the two lines above would understate detector quality on
# oil scenes without actually telling you anything about false-positive
# behavior either. Reported on its own as a false-positive-rate control
# instead (see `false_positive_control` in the returned dict).
_NO_OIL_SCENE = "demo_no_oil_scene_01"
_EXCLUDED_SCENES = ["demo_lookalike_scene_01"]


def _run_classical_detector(image_path: str) -> np.ndarray:
    scene = load_real_scene(image_path)
    despeckled = lee_filter(scene.image, window=7)
    norm = normalize(despeckled)
    sea_mask = simple_sea_mask(norm)
    det = detect_oil_slick(norm, sea_mask)
    return det.mask


def run_classical_detector_evaluation(sample_dir: str | None = None) -> dict:
    """
    Runs the classical detector on the bundled sample SAR scenes and scores
    it against their real ground-truth masks. Returns a dict with per-scene
    metrics, an aggregate (mean over the scored scenes only), the excluded
    scene's raw (unscored) detection footprint, and the caveats above as
    plain strings so any caller/report surfaces them alongside the numbers.

    Returns {"available": False, "reason": ...} if the sample files aren't
    present (e.g. a stripped-down install) rather than raising, since this is
    a diagnostic utility, not a required part of the running application.
    """
    sample_dir = sample_dir or SAMPLE_DIR
    if not os.path.isdir(sample_dir):
        return {"available": False, "reason": f"Sample SAR directory not found at {sample_dir}."}

    def _score_scene(name: str) -> dict:
        img_path = os.path.join(sample_dir, f"{name}.tif")
        mask_path = os.path.join(sample_dir, f"{name}_mask.png")
        if not (os.path.exists(img_path) and os.path.exists(mask_path)):
            return {"scene": name, "available": False}
        pred_mask = _run_classical_detector(img_path)
        gt_mask = np.array(Image.open(mask_path)) > 0
        if pred_mask.shape != gt_mask.shape:
            return {
                "scene": name, "available": False,
                "reason": f"shape mismatch: predicted {pred_mask.shape} vs ground truth {gt_mask.shape}",
            }
        m = binary_mask_metrics(pred_mask, gt_mask)
        return {
            "scene": name, "available": True,
            "precision": m.precision, "recall": m.recall, "f1": m.f1, "iou": m.iou,
            "tp": m.tp, "fp": m.fp, "fn": m.fn, "tn": m.tn,
            "confusion_matrix": confusion_matrix(m),
            "predicted_positive_px": int(pred_mask.sum()), "ground_truth_positive_px": int(gt_mask.sum()),
        }

    scored = [_score_scene(name) for name in _OIL_SCENES]
    valid_scored = [s for s in scored if s.get("available")]
    if valid_scored:
        aggregate = {
            metric: round(float(np.mean([s[metric] for s in valid_scored])), 4)
            for metric in ("precision", "recall", "f1", "iou")
        }
    else:
        aggregate = None

    no_oil_result = _score_scene(_NO_OIL_SCENE)
    if no_oil_result.get("available"):
        total_px = no_oil_result["tp"] + no_oil_result["fp"] + no_oil_result["fn"] + no_oil_result["tn"]
        false_positive_control = {
            "scene": _NO_OIL_SCENE,
            "available": True,
            "false_positive_pixels": no_oil_result["fp"],
            "false_positive_rate": round(no_oil_result["fp"] / total_px, 6) if total_px else None,
            "note": (
                "Ground truth for this scene is an all-zero (no-oil) mask, so precision/recall/F1/"
                "IoU degenerate to an uninformative 0.0/1.0 step function rather than a graded "
                "score - reported here as a direct false-positive pixel count/rate instead, and "
                "kept out of the oil-scene aggregate above."
            ),
        }
    else:
        false_positive_control = no_oil_result

    excluded = []
    for name in _EXCLUDED_SCENES:
        img_path = os.path.join(sample_dir, f"{name}.tif")
        if not os.path.exists(img_path):
            excluded.append({"scene": name, "available": False})
            continue
        pred_mask = _run_classical_detector(img_path)
        excluded.append({
            "scene": name,
            "available": True,
            "predicted_positive_px": int(pred_mask.sum()),
            "predicted_positive_frac": round(float(pred_mask.mean()), 6),
            "reason_excluded": (
                "Ground-truth mask semantics for this scene are not documented in this build "
                "(could mean 'true oil pixels' or 'look-alike feature footprint' - these imply "
                "opposite correct detector behavior). Scoring it would risk a fabricated/"
                "misleading metric, so only the raw, unscored detector output is reported."
            ),
        })

    return {
        "available": True,
        "oil_scenes": scored,
        "aggregate_over_oil_scenes": aggregate,
        "false_positive_control": false_positive_control,
        "excluded_scenes": excluded,
        "caveats": [
            "n=2 scored oil scenes (+1 no-oil false-positive control) - not a statistically "
            "meaningful benchmark, only a reproducible sanity check.",
            "These are procedurally-generated sample images carried over from an earlier "
            "project, not real Sentinel-1 acquisitions (see DATA_PIPELINE.md).",
            "These sample images are procedurally-generated scenes with a clean, well-separated "
            "low-backscatter region against speckle noise, by construction. Real Sentinel-1 "
            "scenes carry confounds these samples don't (wind streaks, current shear, coastal "
            "effects, other vessels), so any score here is likely an optimistic upper bound on "
            "real-world performance, not a representative estimate of it.",
            "This does not substitute for evaluation against the real Zenodo Sentinel-1 SAR "
            "Oil Spill Dataset, which remains unreachable from this sandboxed environment.",
        ],
    }


if __name__ == "__main__":
    import json
    print(json.dumps(run_classical_detector_evaluation(), indent=2))
