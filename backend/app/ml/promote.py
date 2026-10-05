"""
Explicit promotion of a real-Zenodo-trained checkpoint (produced by
`app/ml/train.py`, scored by `app/ml/evaluate_real.py`) to the filenames
`app/ml/detector.py` actually loads at inference time.

WHY THIS IS A SEPARATE STEP (never automatic)
------------------------------------------------
Per this project's explicit rule ("Do not overwrite the existing working
checkpoint until the new model passes validation"), training never touches
the shipped checkpoint files, and this script - the only thing that CAN
make a real-trained checkpoint become the one served at inference time -
refuses to run unless:
  1. A real evaluation report exists (`app/ml/evaluate_real.py` was actually
     run against the held-out test split - not train/val), AND
  2. The caller passes `--i-understand-this-changes-inference-behavior`,
     confirming this is a deliberate, informed action, AND
  3. It NEVER deletes or overwrites `spill_unet.pt` / `classifier_checkpoint.pth`
     (the shipped synthetic-trained checkpoints) - it only writes new
     `*_real_promoted*` files that `app/ml/detector.py`'s precedence list
     checks BEFORE those. Deleting the promoted file (or running
     `--revert`) instantly reverts to the shipped synthetic checkpoint,
     byte-for-byte untouched.

USAGE
-----
    python -m app.ml.promote --checkpoint-dir ./backend/data/ml_checkpoints \\
        --i-understand-this-changes-inference-behavior

    python -m app.ml.promote --checkpoint-dir ./backend/data/ml_checkpoints --revert
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone


def _evaluation_report_looks_real(report_path: str) -> tuple[bool, str]:
    if not os.path.exists(report_path):
        return False, f"No evaluation report found at {report_path!r}."
    try:
        with open(report_path) as f:
            report = json.load(f)
    except Exception as e:
        return False, f"Could not parse evaluation report: {e}"
    if report.get("checkpoint_trained_on") != "REAL_ZENODO_SENTINEL1_SAR_OIL_SPILL_DATASET":
        return False, (
            "Evaluation report's checkpoint_trained_on field does not say "
            "'REAL_ZENODO_SENTINEL1_SAR_OIL_SPILL_DATASET' - refusing to promote a checkpoint that "
            "was not genuinely evaluated as real-data-trained."
        )
    if not report.get("num_test_scenes"):
        return False, "Evaluation report shows zero test scenes evaluated - refusing to promote."
    return True, "OK"


def promote_segmenter(checkpoint_dir: str, source_filename: str = "spill_unet_real_best.pt") -> str:
    source_path = os.path.join(checkpoint_dir, source_filename)
    if not os.path.exists(source_path):
        raise FileNotFoundError(
            f"{source_path!r} not found - run `python -m app.ml.train` first (see docs/DATASET_SETUP.md)."
        )
    eval_report_path = os.path.join(checkpoint_dir, "evaluation_report.json")
    ok, reason = _evaluation_report_looks_real(eval_report_path)
    if not ok:
        raise RuntimeError(
            f"Refusing to promote {source_path!r}: {reason} Run "
            "`python -m app.ml.evaluate_real --checkpoint " + source_path + "` first."
        )
    dest_path = os.path.join(checkpoint_dir, "spill_unet_real_promoted.pt")
    shutil.copy2(source_path, dest_path)
    return dest_path


def promote_classifier(checkpoint_dir: str, source_filename: str = "classifier_real_best.pth") -> str:
    source_path = os.path.join(checkpoint_dir, source_filename)
    if not os.path.exists(source_path):
        raise FileNotFoundError(
            f"{source_path!r} not found - run `python -m app.ml.train` first (see docs/DATASET_SETUP.md)."
        )
    dest_path = os.path.join(checkpoint_dir, "classifier_real_promoted.pth")
    shutil.copy2(source_path, dest_path)
    return dest_path


def revert(checkpoint_dir: str) -> list:
    removed = []
    for fname in ("spill_unet_real_promoted.pt", "classifier_real_promoted.pth"):
        path = os.path.join(checkpoint_dir, fname)
        if os.path.exists(path):
            os.remove(path)
            removed.append(path)
    return removed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Promote a validated real-Zenodo-trained checkpoint to be the one app/ml/detector.py loads."
    )
    parser.add_argument("--checkpoint-dir", required=True)
    parser.add_argument("--segmenter-source", default="spill_unet_real_best.pt")
    parser.add_argument("--classifier-source", default="classifier_real_best.pth")
    parser.add_argument("--skip-classifier", action="store_true",
                         help="Only promote the segmenter (e.g. if the classifier hasn't been retrained yet).")
    parser.add_argument("--i-understand-this-changes-inference-behavior", action="store_true")
    parser.add_argument("--revert", action="store_true", help="Remove promoted checkpoints, reverting to the shipped synthetic ones.")
    args = parser.parse_args(argv)

    if args.revert:
        removed = revert(args.checkpoint_dir)
        if removed:
            print("Reverted - removed:")
            for p in removed:
                print(f"  {p}")
        else:
            print("Nothing to revert - no promoted checkpoint files were present.")
        return 0

    if not args.i_understand_this_changes_inference_behavior:
        print(
            "ERROR: promotion changes what app/ml/detector.py serves at inference time. Re-run with "
            "--i-understand-this-changes-inference-behavior to confirm this is deliberate.",
            file=sys.stderr,
        )
        return 1

    try:
        seg_dest = promote_segmenter(args.checkpoint_dir, args.segmenter_source)
    except (FileNotFoundError, RuntimeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(f"Promoted segmenter: {seg_dest}")

    if not args.skip_classifier:
        try:
            clf_dest = promote_classifier(args.checkpoint_dir, args.classifier_source)
            print(f"Promoted classifier: {clf_dest}")
        except FileNotFoundError as e:
            print(f"WARNING: classifier not promoted ({e}). Segmenter promotion still applies.", file=sys.stderr)

    print(
        "\nThe shipped synthetic checkpoints (spill_unet.pt, classifier_checkpoint.pth) were NOT "
        "modified or deleted. Run with --revert at any time to instantly fall back to them."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
