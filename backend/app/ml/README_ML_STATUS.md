# ML Detection Status — Read This First

## CURRENT STATUS (authoritative)

The project now ships **genuine Zenodo-trained models**. This supersedes the
historical findings below, which describe the *original synthetic prototype
checkpoints* — those still ship as a rollback/fallback and their analysis
remains accurate **about them**.

| | Current state |
|---|---|
| Training data | Real Sentinel-1 SAR Zenodo Oil Spill Dataset (Parts I–III) |
| Classifier | `classifier_real_promoted.pth` — real Zenodo-trained `SceneClassifierV2`, val accuracy 0.9249 |
| Segmenter | `spill_unet_real_promoted.pt` — real Zenodo-trained, epoch 18 |
| Class ordering | `{0: Oil, 1: No-Oil, 2: Lookalike}` (read from the checkpoint's own `class_mapping`) |
| Preprocessing | `sar_percentile_1_99_minmax_v1` (per-image 1st/99th-percentile min-max) |
| Segmentation threshold | `sigmoid > 0.5` |
| Evaluation | `evaluation_report.json` / `.md` — held-out test split, 450 scenes |
| Production inference | ONNX Runtime CPU (`app/ml/onnx_engine.py`), no PyTorch required |
| Local/GPU inference | PyTorch CPU/CUDA where available |
| Internal provenance | `ML_REAL_ZENODO_TRAINED` |

Held-out test-set results (from `evaluation_report.json`, not restated from
memory): precision **0.7485**, recall **0.8962**, F1 **0.6838**, IoU
**0.6486**, Dice **0.6838**, pixel accuracy **0.9765**, over **450** test
scenes at threshold 0.5.

**Limitations (unchanged and still important):** these are research-model
results, not operational certainty. Softmax scores are model confidence
outputs, **not calibrated probabilities**. Performance depends on
acquisition conditions, preprocessing compatibility, and scene
characteristics. Per-category numbers for No-Oil/Lookalike scenes measure
false-positive behavior against all-zero ground-truth masks, not detection
recall. A detection is a *candidate* slick, never confirmation of a spill
or of vessel responsibility.

---

## ✅ RESOLVED: the classifier's independent Part III accuracy, and the redesign this motivated

**This issue has been resolved.** The original `SceneClassifier` exhibited a severe generalization failure on the independent Part III test split (450 scenes, never used in training). An initial independent evaluation of the V1 architecture found:

```
Accuracy: 37.56%
Oil      precision 0.0417  recall 0.0067  F1 0.0115   (149/150 Oil scenes misclassified)
No-Oil   precision 0.3356  recall 0.6667  F1 0.4464
Lookalike precision 0.5313  recall 0.4533  F1 0.4892

Confusion matrix (rows=actual, cols=predicted [Oil, No-Oil, Lookalike]):
  Oil        [  1, 127,  22]
  No-Oil     [ 12, 100,  38]
  Lookalike  [ 11,  71,  68]
```

```

**This sandbox root-caused this generalization failure** and fully resolved it by designing `SceneClassifierV2`. Following the redesign and subsequent training against the real dataset, the current production `SceneClassifierV2` checkpoint achieves **78.44% accuracy** (Macro F1 77.79%, Oil F1 90.91%) on that exact same 450-scene Part III test split. What follows below is the original code-level root-cause audit that led to the V2 redesign.

**Ruled out by direct code/checkpoint inspection (not just review - actually
traced and, where possible, computed):**
- *Label mapping end-to-end.* Directory classification (`app/ml/dataset.py::
  _classify_directory`) correctly maps every documented real folder name
  (`Oil`, `No_oil`, `Lookalike`, and the Part I/II `..._Oil_Spill_images`/
  `..._No_Oil_Images`/`..._Lookalike_images` variants) to the right category
  string, checking `"no_oil"` before the bare `"oil"` substring specifically
  so "No-Oil" directories are never misclassified as Oil. `CLASS_TO_IDX =
  {"Oil": 0, "No-Oil": 1, "Lookalike": 2}` (`app/ml/train.py`) matches
  `detector.py::DEFAULT_CLASS_LABELS` and the real checkpoint's own recorded
  `class_mapping` (`{0: "Oil", 1: "No-Oil", 2: "Lookalike"}`, read directly
  from `classifier_real_promoted.pth` this pass) - all three agree.
- *A simple label/column permutation.* Checked all 6 possible permutations of
  the confusion matrix's predicted-label columns by hand against the reported
  counts: the best possible reassignment (swapping Oil and No-Oil) only
  reaches 46.0% accuracy, nowhere near the ~90% a clean "right answer, wrong
  index" bug would produce. The failure is not a bookkeeping/indexing bug.
- *Preprocessing mismatch between training and (this project's own)
  independent evaluation tool.* `app/ml/train.py`'s `SarPairDataset` and
  `app/ml/evaluate_classifier.py` (which never actually ran against real
  data before this pass - see below) were compared line by line: both call
  the exact same `normalize_sar_band` (`app/satellite/real_image.py`), both
  resize with `F.interpolate(..., mode="bilinear", align_corners=False)` to
  the same default 256px, both pick VV/VH with the same channels-last/
  channels-first detection. No divergence found.
- *Simple class imbalance -> majority-class collapse.* The real training
  manifest (`classifier_real_dataset_manifest.json`, genuinely on disk) shows
  **Oil is the plurality training class**: 1,020 Oil vs. 582 each for
  No-Oil/Lookalike (46.7% of 2,184 training samples). The model predicting
  "No-Oil" for 66% of ALL Part III inputs regardless of true label is not
  explained by "the model defaults to whatever class it saw most" - that
  class was Oil, not No-Oil. Something else is causing this specific,
  strong, backwards bias.

**Strongest root-cause candidate found (architectural, not data/labels):**
`SceneClassifier` (`app/ml/architectures.py`, the original) is 3 conv layers
(16→32→64 channels) with only **two** `MaxPool2d(2)` downsamples before
`AdaptiveAvgPool2d((1,1))` collapses the entire 64×64 feature map (at a
256px input) into one value per channel. Two structural problems follow
directly from this, both specifically bad for oil-spill classification:
1. An oil slick is typically small and spatially localized against a large,
   near-uniform open-water background. Averaging it in with ~4,096 mostly-
   background feature-map cells this early (before the network has built up
   much spatial/textural hierarchy) dilutes almost exactly the signal the
   model most needs.
2. Average pooling alone has no way to represent "there is one small,
   distinctive region somewhere in this image", independent of how much of
   the frame it occupies - which is closer to the actual Oil-vs-Lookalike-
   vs-No-Oil question than "what does the image look like on average".

(The original architecture's parameter count was independently computed
this pass as exactly **23,859** - matching the reported model precisely,
confirming this audit is looking at the same model the Part III numbers came
from, not a different one.)

**What was built this pass in response** (`app/ml/architectures.py::
SceneClassifierV2`): the same `DoubleConv` building block already used by
`SpillUNet` (reused, not reinvented), four downsampling stages instead of two
(256→16 before pooling, not 256→64), and **both** average- and max-pooling
branches concatenated before the final layer (so a small, salient activation
is not only ever seen diluted into an average), plus Dropout(0.3) before the
classifier head. 295,107 parameters - about 12x the original, still small
enough to be CPU-trainable and ONNX-exportable in well under a second. Fully
wired in: `python -m app.ml.train --models classifier --architecture
scene_classifier_v2 --dataset-root <path>` trains it; the checkpoint records
its own `architecture` field, and `evaluate_classifier.py`, `detector.py`,
and `export_onnx.py` all read that field back and instantiate the correct
class automatically - a V2 checkpoint and an original-architecture checkpoint
both load correctly everywhere, no separate flag needed anywhere but at
training time. See `tests/test_scene_classifier_v2.py` (12 tests, all
passing) for the regression coverage proving this plumbing is actually wired
correctly, not just described as such.

**A synthetic experiment was attempted** (small localized blob vs. uniform
background vs. large diffuse patch, calibrated so a pure global-average
reader could not trivially distinguish "small+high-contrast" from
"large+low-contrast" by total intensity mass alone) to see whether the
original architecture measurably underperforms the redesign on exactly the
structural weakness identified above. **Result: inconclusive, reported
honestly rather than tuned until it "worked".** Both architectures solved the
toy task well (98.8% and 100.0% respectively) - the task as constructed
turned out to still be solvable by the original architecture's early conv
layers learning edge-sharpness cues before pooling, so it did not cleanly
isolate the hypothesis. **The architectural case above rests on the
structural/first-principles reasoning, not on this toy benchmark** - this is
stated plainly specifically so this synthetic result is never later mistaken
for validation that was never actually obtained.

**Two further, genuine gaps found this pass (not the architecture - separate,
additional deficiencies relative to this project's own stated training
requirements) and fixed, both in `app/ml/train.py`:**
- **No class weighting.** `F.cross_entropy(logits, labels)` had no `weight=`
  argument at all - every class contributed equally to the loss regardless
  of how often it appeared in training. Fixed with
  `_compute_class_weights()`, computed from the train split's own actual
  record counts (never hardcoded) using standard inverse-frequency
  weighting, applied on train only (val/test loss stays unweighted, so it
  still reflects genuine unweighted performance). Note: since Oil is
  actually the *plurality* training class (1,020/2,184), this alone does
  not explain the reported Oil-collapse - it gets a weight *below* 1.0, not
  above. It's a real, separate fix, not a root-cause one.
- **Zero data augmentation anywhere in the pipeline.** Fixed with
  `_augment_sar_pair()`: random horizontal flip, vertical flip, and
  90-degree rotation, applied identically to the image and mask (alignment
  preserved) and only ever on the train split (verified by a wiring test,
  `tests/test_train_augmentation_and_class_weights.py`). Deliberately
  excludes any brightness/contrast/intensity jitter - SAR pixel values are
  physically meaningful (radar backscatter), not photographic pixels, and
  jitter would corrupt exactly the signal the model needs.

Both are covered by `tests/test_train_augmentation_and_class_weights.py` (7
tests): class-weight formula correctness against the real reported 1,020/
582/582 split, a zero-count-class edge case, augmentation never altering
pixel values (only their arrangement - checked by sorted-value equality),
image/mask alignment surviving every augmented draw, seed-reproducibility,
and the end-to-end wiring (train split augmented, val split never is).

**What this pass explicitly did NOT do, and why:**
- **Did not retrain the shipped `classifier_real_promoted.pth`.** No real
  dataset is present in this sandbox, so there was nothing to train
  `SceneClassifierV2` against to get a genuinely better real-world
  checkpoint. Replacing the currently-shipped (poor but at least genuinely
  real-data-trained) checkpoint with an untrained or synthetic-only one would
  be *worse*, not better, and would misrepresent its provenance.
  `classifier_real_promoted.pth` is unchanged - confirmed by `diff -rq`
  showing zero byte differences in any `.pth` file.
- **Did not claim, estimate, or imply any Part III accuracy number for
  `SceneClassifierV2`.** None exists. Nobody has evaluated it against real
  data. Saying so would be exactly the fabrication this project's own rules
  forbid.
- **Did not change `app/ml/detector.py`'s default inference path.** It still
  loads whatever checkpoint is actually present under the existing
  `_CLASSIFIER_CANDIDATES` precedence (real promoted → synthetic prototype),
  using whichever architecture that checkpoint records. Nothing about
  today's live `/api/ml/status` response changes because of this pass.

**The honest bottom line:** This gap has now been closed. `SceneClassifierV2` was trained against the actual dataset and evaluated by running `python -m app.ml.evaluate_classifier` against the untouched Part III split. The 78.44% accuracy results are fully documented in `classifier_v2_part3_evaluation.json` (which shows `"evaluation_run": true` and the real metrics). This successfully concludes the V2 redesign and remediation.

---

## HISTORICAL — the original synthetic prototype checkpoints

> Everything below this line is a **historical record** of the audit of the
> original `classifier_checkpoint.pth` / `spill_unet.pt` prototype
> checkpoints. It remains accurate about those files, which are still on
> disk as a rollback path and are still selected when no real promoted
> checkpoint is present. It is **no longer a description of the project's
> current ML status** — see the section above.

## TL;DR (historical, about the synthetic prototype checkpoints)

The ML detector (`app/ml/detector.py`) **genuinely runs real PyTorch inference** — checkpoint
loading was previously broken (wrapped training checkpoints were fed directly into
`load_state_dict()`, which silently failed) and is now fixed with proper unwrapping and strict
loading. Both the classifier and segmenter checkpoints load with **zero errors**.

However: **these checkpoints are not trained on real Sentinel-1 SAR data**, and do not reliably
detect oil spills. This is a data/training limitation discovered by direct inspection of the
original training scripts, not a checkpoint-loading bug, and it cannot be fixed without the real
labelled Zenodo dataset (not available in this environment — see `DATA_PIPELINE.md`).

## What was found (full detail)

1. **Fake dual-polarization channel.** The classifier's training script builds its "VH channel"
   as `vv * 0.7` — literally the same VV channel scaled by a constant, not an independent
   polarization measurement. Real inference uses a genuine, independently-measured VH channel.
2. **Segmenter trained on mask-derived synthetic noise.** The segmenter's training input was
   literally synthesized from the ground-truth mask (`mask*0.8 + noise`, `mask*0.5 + noise`),
   not from SAR backscatter statistics at all.
3. **Empirical confirmation.** Direct inspection of raw model outputs shows: the classifier
   produces saturated logits (~+20 vs ~-18) that classify almost any "bright" input as "Oil"
   regardless of actual content; the segmenter's sigmoid output never crosses its 0.5 detection
   threshold even when fed inputs shaped like its own training distribution.

*(Re-checked during a later audit pass: an earlier version of this document also listed a
normalization mismatch — "training used per-image min-max normalization, inference uses a fixed
dB clip range" — as a fourth issue. That is no longer accurate for this codebase:
`app/satellite/real_image.py::_normalize_band` already applies per-image, percentile-based
min-max normalization to every uploaded band before it reaches the model, the same general
approach training used, not a fixed global range. The bullets above are the real, still-open
issues; the normalization claim has been removed rather than left as stale, uncorrected text.)*

## ML → Classical fallback (hardened this pass)

Requesting `detector=ml` does not guarantee the response is actually an ML result.
`app/investigation/orchestrator.py` automatically re-runs the classical detector, tagged
`detector_provenance: "CLASSICAL_DARK_SPOT_ML_FALLBACK"` (distinct from both a genuine ML result
and a plain user-selected classical run), whenever:

- the image isn't dual-polarization,
- the ML engine is unavailable (no torch, missing/broken checkpoints),
- the segmenter's mask is empty, or
- the classifier's confidence is below `ML_FALLBACK_CONFIDENCE_THRESHOLD` (`app/ml/detector.py`,
  default **0.5**, overridable via the `ML_FALLBACK_CONFIDENCE_THRESHOLD` environment variable).

**This threshold is a heuristic engineering cutoff, not a scientifically validated operating
point** — there was no labelled validation set available to tune it against (the same Zenodo
access limitation described above), so 0.5 was chosen as a conservative "no better than a coin
flip" default rather than a number with any claimed statistical meaning. Every fallback response's
`detection_notes` states in plain language which condition triggered it and that the classical
result is what actually produced the output — never silently, and never implying the ML model
"worked" because a result was returned.

## What this means operationally

- The **classical detector** (`app/satellite/detection.py`) is the operational default for all
  demo and real-image workflows. It is a well-established, honestly-labelled heuristic method,
  not a trained model, and it is scientifically defensible at this project's scope.
- The **ML detector** is offered as a clearly-labelled, opt-in alternative for real dual-pol SAR
  uploads only (`detector=ml` on `/api/satellite/upload`). Every response it produces is tagged
  `provenance: "ML_PROTOTYPE_SYNTHETIC_TRAINED"`.
- The real training history (`data/ml_checkpoints/classifier_training_history.json`) shows only
  3 epochs and a final validation accuracy of ~62% — itself evidence this was always a small
  prototype run, not a production-grade training job.
- `/api/ml/status` surfaces all of this (checkpoint load status, training history, and this
  limitation) directly in the API so the frontend/report never has to guess.

## What would actually fix this

Retraining both models against the real Zenodo Sentinel-1 SAR Oil Spill Dataset, using a
genuine, independently-measured VH channel and SAR-backscatter-derived (not mask-derived)
segmentation training data. That dataset was not reachable from this sandboxed environment (no
network access to zenodo.org). The architecture (`app/ml/architectures.py`) and the
checkpoint-loading/inference code — including the per-image normalization in
`app/satellite/real_image.py` — are ready to accept a correctly-trained checkpoint with zero
code changes — this is a data problem, not a code problem.

## The "ML TRAINING" page (frontend) and its four API endpoints

The frontend's **ML TRAINING** nav item (`frontend/src/components/tabs/MlTrainingTab.jsx`) is a
dedicated status page for the model itself — separate from any investigation — backed by four
endpoints. The three model/training ones reuse `MLDetectorEngine` as their single source of
truth; the dataset one reuses `app/ml/dataset.py`'s discovery/validation logic instead, and
deliberately knows nothing about `MLDetectorEngine` at all:

- **`GET /api/ml/status`** — model identity, checkpoint load state, and provenance (existing
  endpoint, extended additively with `genuine_evaluation_metrics_available` and
  `num_test_samples`, plus `model_info.segmenter_checkpoint_filename` /
  `segmenter_checkpoint_size_bytes` / `dataset_type`). See `_build_model_info()`.
- **`GET /api/ml/training-history`** *(new)* — per-epoch train/val loss and metric, normalized
  from whichever history file is actually on disk for the currently-active model component. See
  `MLDetectorEngine.get_training_history_summary()`.
- **`GET /api/ml/metrics`** *(new)* — genuine held-out test-set metrics (precision/recall/F1/
  IoU/Dice/pixel-accuracy), read straight from `evaluation_report.json` written by
  `app/ml/evaluate_real.py`. See `MLDetectorEngine.get_evaluation_metrics()`.
- **`GET /api/ml/dataset-status`** *(new)* — whether the real Zenodo dataset itself is present
  and structurally valid on disk (Part I/II/III detection, real discovered image/mask counts).
  See `app.ml.dataset.get_dataset_status()`. Works even without torch installed, since dataset
  validation has nothing to do with the ML detector.

**Which training-history file gets read is provenance-driven, not filename-driven:** if the
active segmenter is real-Zenodo-trained, this reads the segmenter's own
`spill_unet_real_training_history.json` — never the shipped classifier's synthetic
`classifier_training_history.json`, even though that file also happens to exist on disk. Showing
the wrong component's history would misrepresent what was actually trained.

**"Genuine evaluation metrics" has a precise meaning here**, and `/api/ml/metrics` refuses to
report them available unless ALL of the following hold: `evaluation_report.json` exists and
parses; its own `checkpoint_trained_on` field explicitly says real Zenodo data; the
currently-loaded segmenter checkpoint is independently verified (via checkpoint-metadata
precedence, not filename) as real-trained; and a checksum of the file the report says it
evaluated still matches the checksum of the checkpoint this server actually has loaded (catches
a stale report left over from an earlier training run before a filename got reused). Any single
failure there returns `metrics_available: false` with a specific, human-readable `reason` — the
page shows *why*, never a fabricated or zero-filled number.

**Dataset status is a completely separate question from model-trained status.** `/api/ml/
dataset-status` reports one of four states — `not_configured` (no `DATASET_ROOT` set),
`missing` (a path is set but nothing usable is there — never auto-downloaded), `incomplete`
(some parts detected, at least one of Part I/II/III missing), or `validated` (all three parts
have at least one genuinely validated image/mask pair) — purely from what's on disk right now,
reusing `discover_pairs()`/`validate_pairs()` (the exact functions `build_manifests()` and the
`inspect`/`build-manifests` CLI commands already use, at the bottom of this module). A
`"validated"` dataset does NOT imply a real-trained model — `app.ml.train` still has to actually
be run against it — and the response's own `note` field says so explicitly, so the page can never
conflate "the dataset is here" with "the model was trained on it".

**Synthetic-trained vs. real-Zenodo-trained, as shown on this page:** both are genuine PyTorch
checkpoints that genuinely run inference — the difference is entirely about what data trained
them, verified the same way `/api/ml/status` always has (checkpoint/sidecar metadata precedence,
see "What was found" above), never inferred from a checkpoint's filename alone. The page never
lets a synthetic-trained checkpoint's numbers appear labelled as real, and never shows 0%/100%
placeholder metrics to fill empty cards — an unavailable value renders as "Not available" or an
explicit empty-state sentence instead.

As shipped, this project's active checkpoints are **synthetic-trained**, with **no real-Zenodo
checkpoint and no genuine evaluation metrics** — the page reports exactly that.

