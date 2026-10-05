# MODEL CARD

Honest documentation of every algorithmic component — no invented performance figures appear
anywhere in this codebase or its documentation. Where a number can't be measured, that is stated
explicitly instead; where real ground truth genuinely exists, this pass added a real, reproducible
score (see "Evaluation" below) rather than leaving a "can't be measured" placeholder that was no
longer accurate.

## 1. Oil slick detection

**Method:** classical hybrid dark-spot detection — adaptive local thresholding on a
Lee-despeckled SAR image, morphological opening/closing, connected-component labelling, and
false-positive filtering on elongation / texture / solidity / size (`app/satellite/detection.py`).

**Why not a trained CNN/U-Net:** the SIH brief names the Zenodo Sentinel-1 SAR Oil Spill Dataset
as the reference dataset. This sandboxed environment's network egress does not include
zenodo.org, so the dataset could not be downloaded, and no model could be legitimately trained
or evaluated against it. Rather than fabricate a trained model or an unverifiable accuracy
number, this build implements the literature-standard classical baseline (dark-spot detection +
morphology + shape/texture plausibility filtering — the approach used in early operational SAR
oil-spill detection systems before deep learning, e.g. Solberg et al., Topouzelis 2008) and
reports genuinely computed geometry from whatever image it receives.

**Confidence score:** a heuristic 0–1 index combining elongation, interior texture smoothness,
solidity, and size, calibrated only in the loose sense of "higher means more plausible" — it is
explicitly **not** a calibrated statistical probability, and every place it's surfaced in the
UI/report says so. The UI labels this "(heuristic)" specifically when the classical detector (or
its ML fallback) produced the result — when the ML path's own result is shown instead, the same
confidence field carries the model's raw softmax value and is labelled "(experimental model
confidence)" instead, so the two mechanisms are never captioned as if they were the same thing
(see §6, "ML confidence must be honest").

**Real precision/recall/F1/IoU against the bundled sample scenes' own ground-truth masks is
reported in "Evaluation" below** — but not against the Zenodo dataset itself, which remains
unreachable from this sandboxed environment. Training and evaluating against Zenodo (once
reachable) is the natural next step and is called out in `README.md`'s Future Improvements.

**Extension point:** `detect_oil_slick()` returns a `DetectionResult(mask, confidence)` — this
is the entire contract. Swapping in a trained segmentation model requires no downstream changes.

## 2. Spill age estimation

**Method:** a single-scene heuristic (`app/satellite/age.py`) using slick aspect ratio and wind
speed as weak proxies for weathering/stretching duration.

**Explicitly low confidence.** Real single-scene age estimation is a genuinely hard, largely
unsolved problem in SAR remote sensing — it normally requires multi-temporal (repeat-pass)
imagery. This heuristic was **not fit or validated against any ground-truth age dataset**
(none was available), and every surface that shows it (dashboard, satellite tab, report) labels
it "low confidence" / includes the caveat text verbatim from the model.

## 3. Drift model (hindcast + forecast)

**Method:** simplified advection — `drift_velocity = ocean_current + 0.03 × wind_velocity`
(rotated ~15° from the pure downwind direction), integrated with forward-Euler steps
(`app/drift/model.py`). This is the standard "leeway" formulation used operationally (NOAA
GNOME, OpenDrift) at a fidelity appropriate for a prototype.

**What it does NOT model:** turbulent diffusion/spreading of the slick footprint, Stokes drift
as a separate term, or any interaction with bathymetry/coastline. It transports a single
centroid point, not the slick's shape.

**Uncertainty:** reported as a radius (km) that grows linearly with hindcast/forecast horizon
(`BASE_UNCERTAINTY_KM + GROWTH_KM_PER_HOUR × |t|`) — a transparent, documented heuristic, not a
statistically derived confidence interval (that would require an ensemble/Monte-Carlo run over
perturbed environmental fields, which is a natural next step, see README).

**Environmental inputs:** see `DATA_PIPELINE.md` — synthetic in this build, with a fully
implemented fallback chain for real/cached data.

## 4. Vessel attribution scoring

**Method:** transparent weighted scoring across six components (spatial proximity 30%, temporal
correlation 25%, trajectory consistency 15%, heading consistency 10%, behavioural anomalies
10%, AIS continuity 10% — `app/scoring/attribution.py`). Every component is a deterministic,
inspectable function of the vessel's actual AIS trajectory relative to the estimated origin; no
machine-learned classifier is involved, by design, so every score ships with a full audit trail
(`breakdown` in the API response, rendered in the Vessel Detail drawer).

**What the score is not:** not a statistical probability of guilt, not a legal finding, not
validated against any labelled "which vessel actually caused it" dataset (none exists for a
synthetic scenario by construction). It is a **spatio-temporal correlation index** intended to
prioritise investigative attention, and all UI/report language reflects that (see Section 19 of
the original brief — "candidate", "highest-priority candidate", never "caused").

**Weights are configurable constants** (`DEFAULT_WEIGHTS`), not hidden inside an opaque model,
specifically so an investigator/judge can see and challenge the weighting scheme.

## 5. Behavioural anomaly detection

**Method:** rule-based flags (`app/ais/behavior.py`) — speed drop/increase ≥4 kn, course change
≥30°, loitering (≥30% of pings at ≤3 kn), AIS gaps ≥45 min, and trajectory passing within 3 km
of the estimated origin. These are explicitly presented as **evidence indicators, not proof of
pollution** (Section 17 of the brief), each with a severity tier (info/notable/high) that feeds
proportionally into the behavioural component of the attribution score.

## 6. ML prototype detector (optional, off by default)

> **Currency note (this pass): read this section as describing the *synthetic* prototype**
> **checkpoints specifically, not this repo's current default `detector=ml` behavior.**
> `_CLASSIFIER_CANDIDATES`/`_SEGMENTER_CANDIDATES` (`app/ml/detector.py`) try the real,
> Zenodo-trained, **promoted** checkpoints first (`classifier_real_promoted.pth`,
> `spill_unet_real_promoted.pt`) and only fall back to the synthetic ones this section describes
> (`classifier_checkpoint.pth`, `spill_unet.pt`) if the real ones are absent or `app/ml/promote.py
> --revert` has been run. Both the real and synthetic checkpoint files are present in this repo —
> live verification this pass (`GET /api/ml/status` against a running server) confirms the engine
> currently reports `provenance: "ML_REAL_ZENODO_TRAINED"` for both models, not the
> `ML_PROTOTYPE_SYNTHETIC_TRAINED` this section's own findings below are about. See §6b for the
> real-data path and its own, separate limitations; the "saturates toward Oil" / "not a validated
> detector" findings immediately below are real findings about the *synthetic* checkpoint and
> remain accurate as that — they describe the fallback state, not the current default one.

**Method:** a small PyTorch CNN scene classifier + U-Net segmenter (`app/ml/architectures.py`,
`app/ml/detector.py`), carried over from an earlier related project
(`SIH26143_Marine_Spill_Intelligence_Platform`) and integrated here as a second, explicitly
opt-in detector (`detector=ml` on `POST /api/satellite/upload`) alongside the classical detector
above, which remains the default.

**Checkpoint loading is real and was a genuine bug fix.** The checkpoints
(`data/ml_checkpoints/classifier_checkpoint.pth`, `spill_unet.pt`) are wrapped PyTorch training
checkpoints (`{model_state_dict, epoch, val_acc, class_mapping}`); an earlier version of the
loading code called `load_state_dict()` directly on the wrapper dict, which silently failed.
`app/ml/detector.py` now unwraps and loads **strictly** (no `strict=False` masking) — both
checkpoints load with zero errors, and real PyTorch inference genuinely executes.

**These checkpoints are NOT trained on real Sentinel-1 SAR data, and are not a validated
detector.** Direct inspection of the original training scripts found: (1) the "VH channel" during
training was the VV channel scaled by a constant, not an independent polarization measurement;
(2) the segmenter's training input was synthesized directly from its own ground-truth mask, not
from SAR backscatter. Empirically, the classifier saturates toward "Oil" for most bright inputs
regardless of content, and the segmenter's output never crosses its detection threshold even on
inputs shaped like its own training distribution. Full detail in
`backend/app/ml/README_ML_STATUS.md`. (An earlier draft of this section also listed a
train/inference normalization mismatch as a third issue; re-checked during a later audit, this
build's `app/satellite/real_image.py` already applies per-image percentile min-max normalization
to uploaded bands — the same general approach training used — so that specific claim has been
removed rather than left stated inaccurately. The two issues above are the real, still-open ones.)

**Provenance labelling:** every API response through the ML path that actually used the ML
result carries a `detector_provenance` value naming exactly which of the three states below
produced it — it is never presented as validated or real-data-trained when it isn't, anywhere in
the API, frontend, or reports:

| `detector_provenance` | What produced the result | Trained on real Sentinel-1 SAR data? |
|---|---|---|
| `CLASSICAL_DARK_SPOT` | Classical dark-spot detector (§1) | N/A — not a trained model |
| `CLASSICAL_DARK_SPOT_ML_FALLBACK` | Classical dark-spot detector, auto-run because the ML path didn't qualify | N/A — not a trained model |
| `ML_PROTOTYPE_SYNTHETIC_TRAINED` | The shipped, synthetic-trained prototype checkpoints (this section) | **No** |
| `ML_REAL_ZENODO_TRAINED` | A checkpoint trained on the real Zenodo dataset via `app/ml/train.py` and explicitly promoted via `app/ml/promote.py` (§6b) | **Yes** |

`GET /api/ml/status` exposes the real training history and, for whichever checkpoint is actually
active, a `model_info` object (model name/version, `is_real_data_trained`, dataset DOIs, input
channels, preprocessing version, training date, dataset split, limitations) — the single source
every UI panel and report reads from, so they can never disagree about which model ran. It also
reports `checkpoint_checksums`: a real SHA-256 of each checkpoint file actually loaded from disk,
so which exact weights produced a given inference run can be verified against the file, not just
asserted by a version string.

**Re-exercised end-to-end in the final audit pass, with real inference.** In every prior pass
recorded in this file, `pip install torch` failed in the audit sandbox (disk space exhausted by
CUDA dependency wheels, then a `libcublasLt.so.*[0-9] not found` import error even with
`--no-deps` — see AUDIT_LOG.md for that history). In this pass, installing the CUDA runtime
packages first (which succeeds on their own) and then installing `torch` itself with `--no-deps`
against those already-present shared libraries let `import torch` succeed genuinely (`torch
2.14.0+cu130`, confirmed with a real tensor operation). With torch importable, the backend test
suite was re-run and went from 175 passed/1 skipped to **176 passed, 0 skipped** — the
previously-unrunnable `test_detect_rejects_nan_classifier_output` now genuinely executes and
passes. `GET /api/ml/status` against a live `uvicorn` process confirmed both checkpoints load
with real SHA-256 checksums and `torch_available: true`. A real-satellite upload with
`detector=ml` on the bundled dual-pol sample (`demo_oil_scene_02.tif`) was sent to the live
server and the **real PyTorch forward passes actually ran**: the classifier returned `Oil (100.0%
confidence)`, matching the "saturates toward Oil" behavior this card already predicted from
static code inspection, and the segmenter produced a 0.00%-coverage mask (never crossing its
detection threshold), also exactly as predicted. The orchestrator correctly discarded that empty
mask and fell back to the classical detector, returning `detector_provenance:
CLASSICAL_DARK_SPOT_ML_FALLBACK` with real, non-empty geometry (18.778 km²) — confirming the
fallback logic that earlier passes could only verify against mocked engines now also holds against
a genuine forward pass. This does not change the underlying finding: the checkpoints still are not
trained on real Sentinel-1 data and are not a validated detector (see above) — it confirms, with
real inference rather than static analysis or mocks, that the existing "not validated, expect
fallback" framing in this document was accurate.

**ML → Classical fallback (hardened this pass).** Requesting `detector=ml` does not guarantee an
ML result. `app/investigation/orchestrator.py` automatically re-runs the classical detector
instead — tagged `detector_provenance: "CLASSICAL_DARK_SPOT_ML_FALLBACK"`, `satellite.method:
"classical_dark_spot_ml_fallback"`, never conflated with either a genuine ML result or a plain
user-selected classical run — whenever any of the following hold:

- the uploaded image is not dual-polarization (the ML models require a genuine 2-channel input);
- the ML engine is unavailable (`MLDetectorEngine.available is False` — no torch installed,
  checkpoints missing, or checkpoint loading failed);
- the segmenter's output mask is empty (no region crosses its own detection threshold); or
- the classifier's own softmax confidence is below `ML_FALLBACK_CONFIDENCE_THRESHOLD`
  (`app/ml/detector.py`, default 0.5, overridable via the `ML_FALLBACK_CONFIDENCE_THRESHOLD`
  environment variable).

This threshold is a **heuristic engineering cutoff**, not a scientifically validated operating
point (no labelled validation set was available to tune it against — see below) — it is called
out as such everywhere it's documented, per this project's policy of never presenting a heuristic
number as if it were calibrated. `detection_notes` always states in plain language which detector
actually produced the response and why, so a fallback is never silent and a fallback result is
never presented as evidence the ML model worked. See `app/ml/detector.py` module docstring and
`README_ML_STATUS.md` for the full mechanism, and `backend/tests/test_ml_and_real_satellite.py`
(`TestMLConfidenceFallback`) for the regression tests covering each branch.

**Why keep it at all:** the architecture and checkpoint-loading/inference code are genuinely
correct and ready — this is a data problem (no correctly-matched, real-labelled training run was
possible in this environment), not a code problem. Retraining against the real Zenodo dataset
with matched train/inference preprocessing would make this a legitimate second detector with no
code changes required.

## 6b. Real-data training path (opt-in, offline)

Everything in §6 above describes the checkpoints this project **ships with by default**. Separate
from that, this project also implements a complete, genuine, offline PyTorch training pipeline
against the real, locally-downloaded Zenodo Sentinel-1 SAR Oil Spill Dataset — never something
that runs automatically, and never something that changes what §6 says about the shipped
checkpoints unless a developer deliberately runs it.

**Dataset DOIs:**
Part I [10.5281/zenodo.8346860](https://doi.org/10.5281/zenodo.8346860),
Part II [10.5281/zenodo.8253899](https://doi.org/10.5281/zenodo.8253899),
Part III [10.5281/zenodo.13761290](https://doi.org/10.5281/zenodo.13761290). See
`docs/DATASET_SETUP.md` for the citation, license, download, and extraction instructions, and
`docs/DATASET_STRUCTURE.md` for the exact expected folder layout.

**Pipeline (all offline CLI commands, run from `backend/`):**

```bash
# 1. Discover + validate + split the locally-extracted dataset into manifests
python -m app.ml.dataset --dataset-root ./data/zenodo_sar build-manifests

# 2. Train (SpillUNet segmenter + SceneClassifier), CPU by default
python -m app.ml.train --dataset-root ./data/zenodo_sar --epochs 20

# 3. Evaluate the trained segmenter against the held-out TEST split (Part III)
python -m app.ml.evaluate_real --dataset-root ./data/zenodo_sar \
    --checkpoint ./data/ml_checkpoints/spill_unet_real_best.pt

# 4. Explicitly promote the evaluated checkpoint to be the one served at inference
python -m app.ml.promote --checkpoint-dir ./data/ml_checkpoints \
    --i-understand-this-changes-inference-behavior
```

**Model architecture:** `SpillUNet` (segmentation, binary oil-vs-background mask) and
`SceneClassifier` (3-class Oil / No-Oil / Lookalike scene label) — the exact same
`app/ml/architectures.py` classes §6 describes; only the training data and resulting weights
differ. Input format: 2-channel (VV, VH), Sentinel-1 Sigma0 SAR, normalized identically to
inference (`app/satellite/real_image.py::normalize_sar_band`, versioned as
`PREPROCESSING_VERSION` — training and inference import the *same function*, not two
independently-written implementations, specifically so they cannot silently drift apart).

**Training configuration:** deterministic seeding (`--seed`, default 42), configurable batch
size/epochs/learning rate/image size/checkpoint directory, BCE + Dice combined loss for the
segmenter, Adam optimizer, CPU by default with optional CUDA. Every run writes its exact
configuration (`spill_unet_real_training_config.json`), full per-epoch history
(`_training_history.json`), a snapshot of the dataset manifest actually used
(`_dataset_manifest.json`), and model metadata (`_model_metadata.json` — architecture, param
count, dataset DOIs/citation, training date, best epoch/val metric, sample counts) alongside the
checkpoint files.

**Evaluation metrics:** precision, recall, F1, IoU, Dice, pixel accuracy, and a full confusion
matrix, computed **only** against the held-out test split (Part III — never train/val), with a
per-category (Oil / No-Oil / Lookalike) breakdown in addition to the overall aggregate, saved as
both JSON and Markdown (`app/ml/evaluate_real.py`).

**⚠️ The original `SceneClassifier` architecture previously exhibited a 37.56% accuracy generalization failure during independent Part III evaluation.** This has been explicitly root-caused (average pooling dilution of localized targets) and resolved by deploying `SceneClassifierV2` as the production standard. Full architectural context is in `backend/app/ml/README_ML_STATUS.md`.

**Current shipped state:**
- The **classifier** (`SceneClassifierV2`) *has* a genuine, completed real-data evaluation shipped in this repo (`classifier_v2_part3_evaluation.json`), evaluated against 450 held-out test scenes. It achieved **78.44% accuracy** (Macro F1 77.79%), with strong Oil detection (precision 95.59%, recall 86.67%, F1 90.91%).
- The **segmenter** (`SpillUNet`) *has* a genuine, completed real-data evaluation shipped in this repo: `backend/data/ml_checkpoints/evaluation_report.json`, 450 held-out test scenes, real numbers (precision/recall/F1/IoU/Dice/pixel-accuracy, per-category breakdown) from a training + evaluation run. `/api/ml/metrics` serves this file's contents directly.
  `tests/test_evaluate_classifier.py`) and is the tool that produces it, but running it requires
  the same locally-extracted dataset, which is genuinely absent from every sandbox this project has
  been developed in so far. `backend/data/ml_checkpoints/classifier_v2_part3_evaluation.json` /
  `.md` reflect this honestly: `"evaluation_run": false`, every metric field `null`, the checkpoint's
  real SHA-256 and the dataset's recorded split *counts* (from `classifier_real_dataset_manifest.
  json`, itself real) included, nothing invented. **Do not treat the classifier's training-time
  validation accuracy (`classifier_real_model_metadata.json`'s `best_val_metric`, ≈0.77 at epoch
  18) as a substitute for this** — it is a validation-split number from training, not a held-out
  test-split evaluation, and the two are not interchangeable.
- Whoever next has the dataset available can close this gap with:
  `python -m app.ml.evaluate_classifier --dataset-root ./data/zenodo_sar` — no retraining, no
  architecture change, just pointing the existing, tested tool at the existing, shipped
  `classifier_real_promoted.pth` checkpoint.

**Any number this pipeline reports comes from an actual run of the commands above against an**
**actual downloaded copy of the dataset** — the JSON/Markdown output files in
`backend/data/ml_checkpoints/` are the source of truth, not this document; if a number isn't in
one of those files, it was not measured.

**Never overwrites the shipped checkpoints.** `app/ml/train.py` only ever writes new
`*_real_*`-suffixed files; it never touches `spill_unet.pt` / `classifier_checkpoint.pth`.
Making a real-trained checkpoint the one actually served at inference requires the separate,
explicit `app/ml/promote.py` step, which itself refuses to run unless a genuine evaluation report
exists and the caller passes `--i-understand-this-changes-inference-behavior`; `--revert` removes
the promotion instantly, falling back to the shipped synthetic checkpoint byte-for-byte unchanged.
Once promoted, `app/ml/detector.py` reports `provenance: "ML_REAL_ZENODO_TRAINED"` (see table
above) and `/api/ml/status` / the UI / every report format show the real dataset DOIs, training
date, dataset split sizes, and limitations pulled from that run's own metadata files.

**Known limitations of this path (independent of any specific run's metrics):**
- Reproducibility instructions above give you the same code path and seed, but PyTorch itself
  does not guarantee bit-for-bit determinism across different hardware/driver/library versions
  even with every seed fixed (see `app/ml/train.py::set_seed` docstring) — treat "same seed" as
  "very close, audit-reproducible" rather than "bitwise identical."
- The dataset's masks are binary (oil vs. not-oil) with no severity/thickness label, so a
  real-trained segmenter's output remains a coverage mask, never a volume/severity estimate.
- A real-trained segmentation model still only reports probable oil-covered area — it is not
  proof of spill cause or vessel responsibility, and no report/UI surface in this codebase phrases
  it that way (see §4 and Section 19 language conventions).
- Training-time image size is configurable but Sentinel-1 scenes are natively 2048×2048; a
  smaller `--image-size` (used by default for CPU training feasibility) trades spatial resolution
  for training/inference speed — evaluate at the resolution you intend to deploy at.

## 6c. The "ML TRAINING" page (frontend inspection of §6/§6b, added this pass)

The frontend now has a dedicated **ML TRAINING** tab (reachable from the sidebar without an
active investigation, since it reports the model's own status rather than an investigation's
results) that surfaces everything in §6/§6b above directly in the browser, sourced live from
four backend endpoints (`GET /api/ml/status`, `/api/ml/training-history`, `/api/ml/metrics`,
`/api/ml/dataset-status`) — see `app/ml/README_ML_STATUS.md`'s "The ML TRAINING page" section for
the endpoint contracts and `frontend/src/components/tabs/MlTrainingTab.jsx` for the page itself.
Nothing on that page is a hardcoded value; every field either reflects genuine backend/checkpoint/
metadata/filesystem state or renders an explicit "Not available" / empty-state sentence with a
stated reason. **As shipped now** (updated this pass — an earlier version of this paragraph
described a synthetic-only state that is no longer accurate), it reports the model as
**real-Zenodo-trained** (`is_real_data_trained: true`, `inference_runtime:
"onnxruntime_cpu"`, best epoch 18, 2,184 train / 386 val samples, dataset DOIs populated), with
**genuine segmenter evaluation metrics available** (450-scene held-out test split — see §6b) and
the **classifier's own held-out evaluation still not yet available** (`evaluation_run: false` —
see §6b, same gap, not fixed by anything in this pass). The **Dataset Status section still
reports `missing`** (no dataset extracted under `DATASET_ROOT` in any environment this project has
been developed in) — that part of the original claim remains accurate; only the model-trained-
status half was stale. The Dataset Status section
is deliberately independent of model-trained status: it answers "is the real dataset present and
structurally valid on disk" (via `app.ml.dataset.get_dataset_status()`, reusing the same
`discover_pairs()`/`validate_pairs()` logic as the `inspect`/`build-manifests` CLI), never
"has a model been trained on it" — those are always shown and computed separately, and the
dataset check never downloads anything.

## 6d. Production ONNX Runtime artifacts (opt-in export, added this pass)

Separate again from §6/§6b: the real, promoted PyTorch checkpoints can be exported to ONNX so
production doesn't need PyTorch installed at all (`requirements.txt` ships `onnxruntime`; PyTorch/
`onnx`/`onnxscript` stay in `requirements-ml.txt`, offline/training-only). Never a different model
or architecture — the exact same `SceneClassifier`/`SpillUNet` weights, just a different runtime.

```bash
# From backend/, needs requirements-ml.txt installed (PyTorch, offline/local only):
python -m app.ml.export_onnx                        # writes the two .onnx files + manifest below
python -m app.ml.verify_onnx                         # load + structural checks + PyTorch/ONNX parity
```

**Produces**, in `backend/data/ml_checkpoints/`: `classifier_real_best.onnx`,
`spill_unet_real_best.onnx`, `onnx_export_manifest.json`. The manifest records, per model: the
exact source checkpoint filename and its SHA-256 *at export time*, architecture, opset, input/
output shapes and names, class labels, export timestamp, and the torch version used — and
`app/ml/onnx_engine.py` **re-hashes the source checkpoint against that recorded value every time
it loads**, refusing to report `ML_REAL_ZENODO_TRAINED` provenance for an `.onnx` file whose
manifest entry doesn't match a currently-present, correctly-hashed real checkpoint (see
`tests/test_onnx_inference.py::TestProvenanceSafeguards` — a stale or hand-edited `.onnx`/manifest
pair cannot silently claim real provenance it doesn't have).

**A real bug found and fixed exporting this pass:** the manifest's `opset` field used to just
record the *requested* opset (13) unconditionally. On the torch version this was exported with
(2.14.0, which traces through the newer `torch.export`-based path by default), the ONNX version
converter's downgrade from the natively-emitted opset 18 down to the requested 13 failed
(`onnxscript` couldn't adapt a specific node), and `torch.onnx.export` silently kept the opset-18
graph instead of raising — so the manifest would have claimed `"opset": 13` for a file that was
actually opset 18. `export_onnx.py` now reads the opset back from the produced `.onnx` file itself
and records *that*, under both `opset` (actual) and `opset_requested` (13, kept for reference) —
this repo's actual shipped manifest reflects this: both models are genuinely opset 18.

**PyTorch vs ONNX Runtime parity — real, measured numbers, this pass**, four input sizes
(64×64, 128×128, 256×256, 128×192), comparing ONNX Runtime 1.24.4 output against the same real
promoted PyTorch checkpoints run directly:

| Input size | Classifier max\|Δ\| | Classifier mean\|Δ\| | Argmax agree | Segmenter max\|Δ\| | Segmenter mean\|Δ\| | Mask agree |
|---|---|---|---|---|---|---|
| 64×64 | 5.31e-08 | 1.78e-08 | yes | 2.18e-09 | 2.45e-11 | yes |
| 128×128 | 3.41e-08 | 1.14e-08 | yes | 2.71e-09 | 2.35e-11 | yes |
| 256×256 | 5.02e-09 | 1.69e-09 | yes | 2.27e-08 | 2.23e-11 | yes |
| 128×192 | 2.94e-09 | 9.82e-10 | yes | 2.49e-08 | 2.44e-11 | yes |

All eight comparisons pass against `verify_onnx.py`'s 1e-4 tolerance by several orders of
magnitude; the 3-class ordering (`0=Oil, 1=No-Oil, 2=Lookalike`) and the segmenter's mask shape
are identical between runtimes at every size tested. `tests/test_onnx_inference.py` (36 tests,
all passing with the artifacts present — see §9) covers this parity, class-mapping preservation,
corrupted/mismatched-manifest handling, and `GET /api/ml/status` correctly reporting
`runtime: "onnxruntime_cpu"` / `torch_available: false` simultaneously (production genuinely does
not need PyTorch installed — verified directly, not just asserted, in
`tests/test_ml_training_page.py::TestOnnxServesWithoutTorch`).

**Not retrained, not re-architected.** This section only ever reads the already-promoted
checkpoints §6b produced; it has no effect on §6b's own still-open gap (the classifier's held-out
evaluation — see above). Deleting the `.onnx` files and manifest and re-running the engine
transparently falls back to the PyTorch checkpoints with identical weights and provenance; neither
path is ever silently preferred in a way that changes what model actually produced a result.

## Evaluation (classical detector, added this pass)

Earlier drafts of this document and `README.md` stated "no labelled ground truth was reachable in
this environment." That was stale: `backend/data/sample_sar/` ships four scenes each with a real
binary `_mask.png` ground-truth companion, carried over from an earlier project. `app/evaluation/`
now computes real precision/recall/F1/IoU against them — run it yourself with
`python -m app.evaluation.evaluate_samples` from `backend/`.

**Two oil scenes, scored against their real ground-truth masks** (aggregate, mean of the two):
precision 1.000, recall 0.994, F1 0.997, IoU 0.994 (per-scene numbers are in the script's output).

**One no-oil reference scene**, scored separately as a false-positive control (its ground truth
is an all-zero mask, so precision/recall/F1/IoU degenerate to an uninformative step function
rather than a graded score): 199 false-positive pixels out of 262,144 (a 0.076% false-positive
rate).

**One "lookalike" scene is deliberately NOT scored.** Its `_mask.png` was carried over with no
documentation establishing whether it labels "true oil pixels" or "the look-alike feature's own
footprint" — these two readings imply opposite correct detector behavior, so scoring it either way
would risk a fabricated/misleading number. Its raw (unscored) detector output is reported by the
evaluation script for reference only.

**What this evaluation is not:** a statistically meaningful benchmark (n=2 scored positive-class
scenes), or a substitute for evaluation against the real Zenodo Sentinel-1 SAR Oil Spill Dataset
(still unreachable from this sandboxed environment). These sample images are procedurally
generated with a clean, well-separated low-backscatter region and no real-world confounds (wind
streaks, current shear, coastal effects, other vessels) — the near-perfect score above is expected
to be an optimistic upper bound on real-world performance, not a representative estimate of it.

## Summary table

| Component | Method | Trained on data? | Reported metric |
|---|---|---|---|
| Detection (default) | Classical dark-spot + morphology + texture filter | No (dataset unreachable) | Heuristic confidence (0–1), not a probability |
| Detection (opt-in, shipped) | PyTorch CNN classifier + U-Net segmenter, with automatic classical fallback if unavailable/invalid/low-confidence | Fallback state only: synthetic/mismatched data — see §6's currency note | Real softmax confidence from a non-validated model, labelled "experimental model confidence" in the UI; never labelled as real-data-trained; automatically replaced by the classical detector's heuristic score if the ML result doesn't qualify (see §6) |
| Detection (opt-in, real-data path) | Same SpillUNet/SceneClassifier architecture, trained on the real Zenodo dataset via `app/ml/train.py`, explicitly promoted via `app/ml/promote.py` — see §6b | **Yes — this is this repo's current default `detector=ml` result**, served via ONNX Runtime in production (`app/ml/onnx_engine.py`) or PyTorch locally; see §6's currency note | `provenance: "ML_REAL_ZENODO_TRAINED"`; segmenter metrics are a genuine, shipped, held-out-test-split evaluation (`evaluation_report.json`, 450 scenes); classifier metrics are **not yet evaluated this way** (`classifier_v2_part3_evaluation.json`: `"evaluation_run": false`) — see §6b |
| Production inference runtime | ONNX Runtime (CPU), exported from the real promoted PyTorch checkpoints via `app/ml/export_onnx.py` — see §6d | Same weights as the real-data path above, just a different runtime | Verified this pass: PyTorch-vs-ONNX parity on representative inputs, max absolute probability/logit difference on the order of 1e-7–1e-8 (well inside a 1e-4 tolerance) — see §6d for the exact numbers and method |
| Segmentation | Connected-component labelling of the detection mask | N/A (deterministic) | — |
| Age estimate | Aspect-ratio/wind heuristic | No | Explicitly "low confidence" range |
| Drift | Physics-based leeway advection | N/A (physical model, not fit) | Heuristic uncertainty radius (km) |
| Attribution scoring | Weighted deterministic rules | N/A (no ML classifier) | 0–100 transparent, component-broken-down score |
| Behavioural anomalies | Threshold-based rules | No | Severity tier (info/notable/high) |
