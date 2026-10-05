"""
Verify the exported ONNX models against the original PyTorch checkpoints.

    python -m app.ml.verify_onnx

Two independent halves, because they have different dependencies:

1. ONNX-ONLY checks (need onnxruntime, NOT torch). These are the ones that
   matter for production, and they run anywhere the production backend runs:
   sessions load under CPUExecutionProvider, accept a real dual-pol
   (1, 2, H, W) input, return the expected output shapes, and return finite
   values.

2. PARITY checks (need torch AND onnxruntime). Runs the same inputs through
   the original checkpoint and the exported ONNX and reports max/mean
   absolute difference, classifier argmax agreement, and segmentation mask
   agreement AFTER the exact production threshold (sigmoid > 0.5). If torch
   is unavailable this half is SKIPPED and reported as skipped - never as
   passed.

Exit code is non-zero if any check that actually ran failed.
"""
from __future__ import annotations

import os
import sys

import numpy as np

# Matches detector.py's postprocessing exactly.
SEGMENTATION_THRESHOLD = 0.5
PARITY_TOLERANCE = 1e-4


def _sizes(hw):
    return [(1, 2, h, w) for h, w in hw]


def main(argv=None) -> int:
    from app.ml.detector import CHECKPOINT_DIR
    from app.ml.onnx_engine import ONNX_AVAILABLE, ONNXRUNTIME_VERSION, OnnxBackend

    ckpt_dir = os.path.abspath(CHECKPOINT_DIR)
    failures: list[str] = []
    skipped: list[str] = []

    if not ONNX_AVAILABLE:
        print("FAIL: onnxruntime is not installed - production inference path cannot be verified.")
        return 1

    print(f"onnxruntime version: {ONNXRUNTIME_VERSION}")
    print(f"checkpoint dir:      {ckpt_dir}\n")

    backend = OnnxBackend(
        ckpt_dir,
        classifier_file="classifier_real_best.onnx",
        segmenter_file="spill_unet_real_best.onnx",
    )
    if backend.load_errors:
        for e in backend.load_errors:
            print(f"  load error: {e}")
    if not (backend.classifier_loaded and backend.segmenter_loaded):
        print("FAIL: ONNX models did not both load - nothing further can be verified.")
        return 1

    print("--- ONNX-only checks (production path) ---")
    info = backend.get_onnx_info()
    print(f"  classifier IO: {info['classifier_io']}")
    print(f"  segmenter  IO: {info['segmenter_io']}")
    print(f"  classifier provenance: {backend.classifier_provenance}")
    print(f"  segmenter  provenance: {backend.segmenter_provenance}")

    rng = np.random.default_rng(0)
    shapes = [(64, 64), (128, 128), (256, 256), (128, 192)]
    for h, w in shapes:
        stacked = rng.random((2, h, w)).astype(np.float32)
        probs = backend.run_classifier(stacked)
        seg = backend.run_segmenter(stacked)
        ok_shape = probs.shape == (3,) and seg.shape == (h, w)
        ok_finite = bool(np.all(np.isfinite(probs)) and np.all(np.isfinite(seg)))
        ok_simplex = abs(float(probs.sum()) - 1.0) < 1e-5
        status = "ok" if (ok_shape and ok_finite and ok_simplex) else "FAIL"
        if status == "FAIL":
            failures.append(f"onnx inference {h}x{w}")
        print(f"  {h}x{w}: probs{probs.shape} seg{seg.shape} finite={ok_finite} "
              f"softmax_sums_to_1={ok_simplex} -> {status}")

    print("\n--- PyTorch vs ONNX parity ---")
    try:
        import torch
        from app.ml.architectures import SceneClassifier, SpillUNet
        from app.ml.detector import (
            _CLASSIFIER_CANDIDATES, _SEGMENTER_CANDIDATES, _extract_state_dict,
        )
    except (ImportError, OSError) as e:
        skipped.append(f"parity (torch unavailable: {type(e).__name__})")
        print(f"  SKIPPED: PyTorch not importable ({type(e).__name__}: {e}).")
        print("  The ONNX production path above was still verified; parity was NOT.")
    else:
        from app.ml.architectures import SceneClassifierV2
        _CLASSIFIER_ARCHITECTURE_CLASSES = {"SceneClassifier": SceneClassifier, "SceneClassifierV2": SceneClassifierV2}
        
        def load(candidates, out_name):
            for filename, _prov in candidates:
                p = os.path.join(ckpt_dir, filename)
                if os.path.exists(p):
                    raw = torch.load(p, map_location="cpu", weights_only=False)
                    if out_name == "classifier":
                        arch_name = raw.get("architecture", "SceneClassifier") if isinstance(raw, dict) else "SceneClassifier"
                        model = _CLASSIFIER_ARCHITECTURE_CLASSES.get(arch_name, SceneClassifier)(in_channels=2, num_classes=3)
                    else:
                        model = SpillUNet(in_channels=2, out_channels=1)
                    model.load_state_dict(_extract_state_dict(raw), strict=True)
                    model.eval()
                    return model, filename
            return None, None

        clf, clf_name = load(_CLASSIFIER_CANDIDATES, "classifier")
        seg, seg_name = load(_SEGMENTER_CANDIDATES, "segmenter")
        print(f"  comparing against: {clf_name}, {seg_name}")

        for h, w in shapes:
            stacked = rng.random((2, h, w)).astype(np.float32)
            t = torch.from_numpy(stacked).unsqueeze(0)
            with torch.no_grad():
                t_probs = torch.softmax(clf(t), dim=1).numpy()[0]
                t_seg = torch.sigmoid(seg(t)).numpy()[0, 0]
            o_probs = backend.run_classifier(stacked)
            o_seg = backend.run_segmenter(stacked)

            p_max = float(np.abs(t_probs - o_probs).max())
            p_mean = float(np.abs(t_probs - o_probs).mean())
            s_max = float(np.abs(t_seg - o_seg).max())
            s_mean = float(np.abs(t_seg - o_seg).mean())
            argmax_agree = int(np.argmax(t_probs)) == int(np.argmax(o_probs))
            mask_agree = bool((
                (t_seg > SEGMENTATION_THRESHOLD) == (o_seg > SEGMENTATION_THRESHOLD)
            ).all())
            ok = (p_max < PARITY_TOLERANCE and s_max < PARITY_TOLERANCE
                  and argmax_agree and mask_agree)
            if not ok:
                failures.append(f"parity {h}x{w}")
            print(f"  {h}x{w}: clf max|d|={p_max:.2e} mean|d|={p_mean:.2e} argmax_agree={argmax_agree} | "
                  f"seg max|d|={s_max:.2e} mean|d|={s_mean:.2e} mask_agree={mask_agree} "
                  f"-> {'ok' if ok else 'FAIL'}")

    print("\n--- summary ---")
    print(f"  failures: {len(failures)} {failures if failures else ''}")
    print(f"  skipped:  {len(skipped)} {skipped if skipped else ''}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
