"""
Offline ONNX export: real trained checkpoints -> ONNX for CPU production.

    python -m app.ml.export_onnx

WHAT THIS DOES
--------------
Loads the checkpoints that `app/ml/detector.py` would actually serve (the
same `_CLASSIFIER_CANDIDATES` / `_SEGMENTER_CANDIDATES` precedence list -
this script never hardcodes its own filename preference), rebuilds the exact
architectures from `app/ml/architectures.py`, and exports each to ONNX.

WHAT THIS DOES NOT DO
---------------------
- It does NOT train, fine-tune, quantize, prune or otherwise alter weights.
- It does NOT modify or overwrite the source `.pth` / `.pt` files.
- It does NOT invent a checkpoint: if the real promoted checkpoints are
  absent, it exports whatever IS present and records that honestly in the
  manifest (so the ONNX inherits `ML_PROTOTYPE_SYNTHETIC_TRAINED`, never a
  borrowed real label).

REQUIRES PYTORCH - deliberately. This is an offline developer/CI step, not
something production runs. Production only ever reads the `.onnx` output
via `app/ml/onnx_engine.py`, which does not import torch at all.

DYNAMIC AXES - WHY
------------------
Height and width are exported as dynamic axes rather than fixed. This is not
a stylistic choice: `MLDetectorEngine.detect()` requires the segmenter's
output to match the input scene's shape exactly
(`if seg_prob.shape != vv.shape: <invalid result>`), and scenes arrive at
whatever resolution the uploaded GeoTIFF/PNG happens to be. Pinning a fixed
input size would force a resize step that does not exist in the current
pipeline, changing preprocessing and therefore changing model behavior -
explicitly out of scope. Batch is fixed at 1 in practice (detect() sends one
scene) but left dynamic as it costs nothing.

Note the architecture's own constraint is unchanged by export: SpillUNet
downsamples twice and concatenates skip connections, so H and W must each be
divisible by 4. That was already true of the PyTorch path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

REAL_ZENODO_MARKER = "REAL_ZENODO_SENTINEL1_SAR_OIL_SPILL_DATASET"
OPSET = 13


def _sha256_of_file(path: str, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Export real trained checkpoints to ONNX.")
    parser.add_argument(
        "--checkpoint-dir", default=None,
        help="Defaults to app/ml/detector.py's CHECKPOINT_DIR.",
    )
    parser.add_argument(
        "--sample-size", type=int, default=256,
        help="Spatial size of the tracing sample (must be divisible by 4). "
             "Does not fix the exported input size - H/W are dynamic axes.",
    )
    args = parser.parse_args(argv)

    try:
        import torch
    except (ImportError, OSError) as e:
        print(
            "ERROR: PyTorch is required to EXPORT (not to serve) these models.\n"
            f"       Import failed: {type(e).__name__}: {e}\n"
            "       Install the offline/training extras:  pip install -r requirements-ml.txt\n"
            "       Production inference itself never needs torch - see app/ml/onnx_engine.py.",
            file=sys.stderr,
        )
        return 2

    from app.ml.architectures import SceneClassifier, SpillUNet
    from app.ml.detector import (
        CHECKPOINT_DIR, _CLASSIFIER_CANDIDATES, _SEGMENTER_CANDIDATES,
        _extract_state_dict, PROVENANCE_ML_REAL,
    )
    from app.ml.onnx_engine import MANIFEST_FILENAME

    ckpt_dir = os.path.abspath(args.checkpoint_dir or CHECKPOINT_DIR)
    size = args.sample_size
    if size % 4 != 0:
        print(f"ERROR: --sample-size must be divisible by 4 (got {size}).", file=sys.stderr)
        return 2

    def first_existing(candidates):
        for filename, provenance in candidates:
            p = os.path.join(ckpt_dir, filename)
            if os.path.exists(p):
                return filename, p, provenance
        return None, None, None

    manifest_models: dict = {}
    exported_any = False

    from app.ml.architectures import SceneClassifierV2
    _CLASSIFIER_ARCHITECTURE_CLASSES = {"SceneClassifier": SceneClassifier, "SceneClassifierV2": SceneClassifierV2}

    # The classifier's "model factory" is a callable, not a pre-built
    # instance - unlike the segmenter (one architecture, SpillUNet), the
    # classifier checkpoint records its own `architecture` (app/ml/train.py:
    # `"architecture": model.__class__.__name__`), which must be read
    # BEFORE the right shape can be instantiated for load_state_dict(strict
    # =True) to succeed - see architectures.py::SceneClassifierV2 docstring
    # for why a second classifier architecture exists at all.
    jobs = [
        ("classifier", _CLASSIFIER_CANDIDATES,
         lambda arch_name: _CLASSIFIER_ARCHITECTURE_CLASSES.get(arch_name, SceneClassifier)(in_channels=2, num_classes=3),
         "classifier_real_best.onnx", ["logits"], {0: "batch"}),
        ("segmenter", _SEGMENTER_CANDIDATES, lambda arch_name: SpillUNet(in_channels=2, out_channels=1),
         "spill_unet_real_best.onnx", ["logits"], {0: "batch", 2: "height", 3: "width"}),
    ]

    for role, candidates, model_factory, out_name, output_names, out_axes in jobs:
        src_name, src_path, provenance = first_existing(candidates)
        if src_path is None:
            print(f"SKIP {role}: no checkpoint found among "
                  f"{[f for f, _ in candidates]} in {ckpt_dir}")
            continue

        raw = torch.load(src_path, map_location="cpu", weights_only=False)
        state_dict = _extract_state_dict(raw)
        if state_dict is None:
            print(f"ERROR {role}: {src_name} is not a recognizable state_dict.", file=sys.stderr)
            return 1
        architecture_name = raw.get("architecture", "SceneClassifier") if isinstance(raw, dict) else "SceneClassifier"
        model = model_factory(architecture_name)
        model.load_state_dict(state_dict, strict=True)
        model.eval()

        trained_on = raw.get("trained_on") if isinstance(raw, dict) else None
        class_mapping = raw.get("class_mapping") if isinstance(raw, dict) else None
        class_labels = None
        if isinstance(class_mapping, dict) and len(class_mapping) == 3:
            try:
                class_labels = [class_mapping[i] for i in range(3)]
            except Exception:
                class_labels = None

        # Cross-check: the precedence list said this file is the real one, so
        # the checkpoint's own self-reported marker had better agree. If it
        # does not, stop rather than export something that would later be
        # labelled real on the strength of its filename.
        if provenance == PROVENANCE_ML_REAL and trained_on != REAL_ZENODO_MARKER:
            print(
                f"ERROR {role}: {src_name} is in the real-checkpoint precedence slot but its "
                f"own 'trained_on' field is {trained_on!r}, not {REAL_ZENODO_MARKER!r}. "
                "Refusing to export a model whose provenance is ambiguous.",
                file=sys.stderr,
            )
            return 1

        out_path = os.path.join(ckpt_dir, out_name)
        sample = torch.randn(1, 2, size, size)
        try:
            torch.onnx.export(
                model,
                sample,
                out_path,
                export_params=True,
                opset_version=OPSET,
                do_constant_folding=True,
                input_names=["input"],
                output_names=output_names,
                dynamic_axes={"input": {0: "batch", 2: "height", 3: "width"},
                              output_names[0]: out_axes},
            )
        except Exception as e:
            print(
                f"ERROR {role}: ONNX export failed ({type(e).__name__}: {e}).\n"
                "       This is reported as-is rather than worked around - do not substitute a "
                "different model or alter the architecture to make export succeed.",
                file=sys.stderr,
            )
            return 1

        # `opset_version=OPSET` above is a REQUEST, not a guarantee: newer
        # torch releases trace through `torch.export`'s dynamo-based path by
        # default, which emits a higher native opset and then tries to
        # downgrade to the one requested via onnx's own version converter -
        # a conversion that can itself fail (seen in practice: torch 2.14 +
        # onnxscript failing to downgrade an Identity/axes-as-attribute node
        # from opset 18 down to 13) and silently falls back to keeping the
        # higher opset instead of raising. The export still succeeds and the
        # graph is still valid, but recording the REQUESTED opset in the
        # manifest in that case would be a false provenance claim - exactly
        # the kind of thing this pipeline exists to refuse to do elsewhere.
        # So the manifest always records the opset actually embedded in the
        # produced file, read back from the file itself, never the request.
        import onnx as onnx_pkg
        actual_opset = OPSET
        try:
            exported_model = onnx_pkg.load(out_path)
            domain_opsets = {imp.domain: imp.version for imp in exported_model.opset_import}
            actual_opset = domain_opsets.get("", OPSET)
            if actual_opset != OPSET:
                print(
                    f"NOTE {role}: requested opset {OPSET} but the exported graph is opset "
                    f"{actual_opset} (the requested-opset downgrade was not achievable with "
                    f"the installed torch/onnx toolchain - torch {torch.__version__}). "
                    "Recording the actual embedded opset in the manifest, not the request."
                )
        except Exception as e:
            print(f"WARNING {role}: could not read back opset from {out_name} ({e}); "
                  f"recording the requested opset {OPSET} as a best effort.", file=sys.stderr)

        manifest_models[out_name] = {
            "role": role,
            "source_checkpoint": src_name,
            "source_sha256": _sha256_of_file(src_path),
            "source_trained_on": trained_on,
            "source_provenance_slot": provenance,
            "architecture": type(model).__name__,
            "opset": actual_opset,
            "opset_requested": OPSET,
            "input_name": "input",
            "input_layout": "NCHW (N, 2=[VV,VH], H, W); H and W dynamic, each divisible by 4",
            "output_names": output_names,
            "output_is_logits": True,
            "class_labels": class_labels,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "torch_version": torch.__version__,
        }
        exported_any = True
        size_kb = os.path.getsize(out_path) / 1024.0
        print(f"OK {role}: {src_name} -> {out_name} ({size_kb:.1f} KB, opset {actual_opset}, "
              f"trained_on={trained_on})")

    if not exported_any:
        print("Nothing exported - no checkpoints found.", file=sys.stderr)
        return 1

    manifest_path = os.path.join(ckpt_dir, MANIFEST_FILENAME)
    existing = {}
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path) as f:
                prev = json.load(f)
            existing = prev.get("models") or {}
        except Exception:
            existing = {}
    existing.update(manifest_models)

    with open(manifest_path, "w") as f:
        json.dump(
            {
                "generated_by": "app/ml/export_onnx.py",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "note": (
                    "Records which real checkpoint each .onnx was exported from, and that "
                    "checkpoint's sha256 at export time. app/ml/onnx_engine.py re-verifies "
                    "this before it will report ML_REAL_ZENODO_TRAINED."
                ),
                "models": existing,
            },
            f,
            indent=2,
        )
    print(f"OK manifest: {MANIFEST_FILENAME}")
    print("\nNext: verify the export actually runs -> python -m app.ml.verify_onnx")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
