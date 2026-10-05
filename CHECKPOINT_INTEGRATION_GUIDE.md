# CHECKPOINT_INTEGRATION_GUIDE.md

For when you paste your final real Zenodo-trained checkpoints into this
checkout.

## 1. Where to copy them

```
backend/data/ml_checkpoints/classifier_real_best.pth
backend/data/ml_checkpoints/spill_unet_real_best.pt
```

These exact filenames are `promote.py`'s defaults and the export script's
source files (see §4/§5) — using them means every command below works with
no extra flags. If you'd rather use different filenames, set
`ML_CLASSIFIER_CHECKPOINT`/`ML_SEGMENTER_CHECKPOINT` (see
`docs/DEPLOYMENT.md` §5) instead of renaming your files.

## 2. Promote them for inference

Dropping the files in `ml_checkpoints/` is not enough by itself —
`app/ml/detector.py`'s precedence list loads `*_real_promoted*`, not
`*_real_best*` (see its own docstring for why: promotion is a deliberate,
one-command step, not automatic). Run:

```bash
cd backend
python -m app.ml.promote --checkpoint-dir data/ml_checkpoints \
    --i-understand-this-changes-inference-behavior
```

This copies `classifier_real_best.pth` → `classifier_real_promoted.pth` and
`spill_unet_real_best.pt` → `spill_unet_real_promoted.pt`. It does not
modify your source files. To roll back to the shipped synthetic prototype
at any time: `python -m app.ml.promote --checkpoint-dir data/ml_checkpoints --revert`.

## 3. Verify the checksums

```bash
cd backend/data/ml_checkpoints
sha256sum classifier_real_best.pth classifier_real_promoted.pth
sha256sum spill_unet_real_best.pt spill_unet_real_promoted.pt
```

The `_best` and `_promoted` hash of each pair should be identical (promotion
is a copy, not a transform). Record these hashes somewhere durable — this
is what `app/ml/onnx_engine.py` will re-check against before it ever reports
`ML_REAL_ZENODO_TRAINED` for the ONNX path (§5).

## 4. Confirm PyTorch loads them correctly

```bash
cd backend
pip install -r requirements.txt -r requirements-ml.txt
python -c "
import torch
from app.ml.architectures import SceneClassifier, SpillUNet
from app.ml.detector import _extract_state_dict

for path, Model, n in [
    ('data/ml_checkpoints/classifier_real_promoted.pth', SceneClassifier(2, 3), 'classifier'),
    ('data/ml_checkpoints/spill_unet_real_promoted.pt', SpillUNet(2, 1), 'segmenter'),
]:
    raw = torch.load(path, map_location='cpu', weights_only=False)
    Model.load_state_dict(_extract_state_dict(raw), strict=True)
    print(n, 'OK - trained_on:', raw.get('trained_on'), '| epoch:', raw.get('epoch'))
"
```

If this raises a `strict=True` mismatch, your checkpoint's architecture
does not match `app/ml/architectures.py` — **stop and diagnose**, per the
project's own rule; don't loosen `strict=True` to make it load.

## 5. Export to ONNX and verify parity

```bash
python -m app.ml.export_onnx
python -m app.ml.verify_onnx
```

`export_onnx.py` reads the same precedence list the runtime uses (so it
always exports whatever `detector.py` would actually serve), writes
`classifier_real_best.onnx` / `spill_unet_real_best.onnx` plus
`onnx_export_manifest.json` (recording the source checkpoint, its sha256 at
export time, and its `trained_on` marker — this is what
`app/ml/onnx_engine.py` checks before it will report
`ML_REAL_ZENODO_TRAINED`), and does not modify the `.pth`/`.pt` files.

`verify_onnx.py` runs ONNX-only checks (load, shape, finite outputs) and,
if PyTorch is available, the PyTorch-vs-ONNX parity comparison — reports max
absolute difference, mean absolute difference, argmax/mask agreement, and
exits non-zero on failure. **Read its output before trusting the export** —
don't assume success.

## 6. Run the final evaluation

```bash
python -m app.ml.evaluate_real \
    --dataset-root data/zenodo_sar \
    --checkpoint data/ml_checkpoints/spill_unet_real_promoted.pt
```

This overwrites `evaluation_report.json`/`.md` using the exact held-out TEST
split (`app/ml/dataset.py`'s split logic — never mixed with train/val).
Paths written into the report are now repo-relative, not your local machine
path (see `work/FINAL_AUDIT.md` item 1 / `app/ml/paths.py`) — no
sanitization step is needed after this.

**Never use this command to tune the segmentation threshold against the
test set** — `ML_SEGMENTATION_THRESHOLD` calibration, if you do it, belongs
against a validation split, not this one.

## 7. Start the backend

```bash
cd backend
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Check `curl http://127.0.0.1:8000/api/ml/status` — expect
`"available": true`, `"provenance": "ML_REAL_ZENODO_TRAINED"`,
`"classifier_loaded": true`, `"segmenter_loaded": true`,
`"runtime": "pytorch"` (or `"onnxruntime_cpu"` if you also completed §5 and
set `ML_RUNTIME=onnx`/left it on `auto` with no PyTorch installed).

## 8. Start the frontend

```bash
cd frontend
npm install
npm run dev
```

## 9. Run the complete demo

1. Open the frontend dev URL, go to the satellite-analysis tab.
2. Upload a dual-pol (VV+VH) Sentinel-1 scene, `detector=ml`.
3. Confirm the result shows "AI Model — Zenodo Trained" and a segmentation
   mask; check the browser network tab or backend logs for
   `inference_runtime=` in the result notes to see which runtime actually
   ran.
4. Try a single-band image — confirm it falls back to the classical
   detector with an explicit reason, not a fabricated dual-pol result.
5. Generate an investigation report and confirm the ML provenance section
   matches what `/api/ml/status` reported.

## 10. Production (Render Free)

Do **not** `pip install -r requirements-ml.txt` on Render — that installs
PyTorch, which the 512 MB/0.1 CPU tier can't fit and doesn't need. Ship the
`.onnx` files from §5 (commit them) and Render's build stays
`pip install -r requirements.txt`, which includes `onnxruntime`. See
`docs/DEPLOYMENT.md` §5 for the full production path, memory budget, and
the tiling env vars if your real scenes are larger than 512×512
(`docs/DEPLOYMENT.md`, "Large-scene inference (tiling)").
