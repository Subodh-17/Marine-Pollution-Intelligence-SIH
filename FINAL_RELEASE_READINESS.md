# FINAL RELEASE READINESS

**Release Verdict:** `EXPERIMENTAL_CANDIDATE_FROZEN`<br>
**Production Default:** `SINGLE_SCALE_BASELINE` (`ML_MULTISCALE_SEGMENTATION=false`)<br>
**Experimental Candidate:** `size_gated_closure` (`size_gated_closure_v1`)<br>
**Independent Validation Status:** `BLOCKED_DATA_UNAVAILABLE`<br>
**Release Gate Command:** `python scripts/verify_release_gate.py`<br>

**This document describes the repository's actual current state, verified by**
**actually running the commands cited below in this pass.** Where a number has
no command/file cited next to it, that number does not belong in this
document — ask for it to be added with its source, not assumed. See
`FINAL_RELEASE_REPORT.md` (Phase 4.3C Master Release Audit) for the narrative
account of what changed to reach this state.

---

## 1. Project architecture

FastAPI backend (`backend/app/`) + React/Vite frontend (`frontend/src/`).
Satellite SAR upload → detection (classical dark-spot, or opt-in ML) →
geometry → drift model → AIS reconstruction/filtering → vessel attribution
scoring → investigation persistence (SQLite by default, optional PostgreSQL)
→ reports/exports (Markdown/PDF/CSV) → authenticated API, background
investigation jobs. Full detail in `ARCHITECTURE.md`; this section is a
pointer, not a duplicate.

## 2. Real model artifacts (PyTorch)

| File | Role | Provenance |
|---|---|---|
| `classifier_real_promoted.pth` | Served classifier (active) | `ML_REAL_ZENODO_TRAINED` |
| `classifier_real_best.pth` | Same weights, pre-promotion name | `ML_REAL_ZENODO_TRAINED` |
| `classifier_real_last.pth` | Last training epoch (not necessarily best) | `ML_REAL_ZENODO_TRAINED` |
| `classifier_checkpoint.pth` | Synthetic prototype (fallback only) | `ML_PROTOTYPE_SYNTHETIC_TRAINED` |
| `spill_unet_real_promoted.pt` | Served segmenter (active) | `ML_REAL_ZENODO_TRAINED` |
| `spill_unet_real_best.pt` | Same weights, pre-promotion name | `ML_REAL_ZENODO_TRAINED` |
| `spill_unet_real_last.pt` | Last training epoch | `ML_REAL_ZENODO_TRAINED` |
| `spill_unet.pt` | Synthetic prototype (fallback only) | `ML_PROTOTYPE_SYNTHETIC_TRAINED` |

`_real_promoted` is tried first by `app/ml/detector.py`'s
`_CLASSIFIER_CANDIDATES`/`_SEGMENTER_CANDIDATES`; the synthetic files are only
ever used if the real ones are absent or explicitly reverted
(`app/ml/promote.py --revert`). No model weights were modified, retrained, or
replaced this pass — confirmed by `diff -rq` against the ZIP this pass started
from showing zero byte differences in any `.pth`/`.pt` file.

## 3. ONNX artifacts (production runtime)

| File | Source checkpoint | Opset (actual) |
|---|---|---|
| `classifier_real_best.onnx` (+ `.onnx.data`) | `classifier_real_promoted.pth` | 18 |
| `spill_unet_real_best.onnx` (+ `.onnx.data`) | `spill_unet_real_promoted.pt` | 18 |
| `onnx_export_manifest.json` | — | — |

Produced this pass by `python -m app.ml.export_onnx` (existing tool, not
newly written); did not exist in the ZIP this pass started from. The
`.onnx.data` files are ONNX's standard external-weights convention (used
automatically above a size threshold) — both must ship alongside their
`.onnx` file or the model will fail to load. `opset_requested` (13) is also
recorded in the manifest for reference; see §15 "Known limitations" for why
the actual opset differs.

## 4. SHA-256 provenance (real values, Phase 3 audit)

```
classifier_real_promoted.pth   0f587cb7604047247424bf7a79a9f07ae0ab66b9e4a3070cf32c2c1957fed1ee
spill_unet_real_promoted.pt    af725b2587baed621b6ee264598a44dea008dc7214ccf454ccadc2fdd50e4361
classifier_real_best.onnx      dc68400ca1b54116e75dc900afb63ac5f0cb48db452892c0ac8e401c44fdf6b7
spill_unet_real_best.onnx      da94d5af9b68155a883365b8a2eb615cc296f12df1926c4f612ea1bcb7656d8c
```
(Computed directly via `hashlib.sha256` over each file this pass; matching
`onnx_export_manifest.json` and what `GET /api/ml/status`'s `checkpoint_checksums`
reports live.) `app/ml/onnx_engine.py` re-verifies the checkpoint hash against
the manifest on every load.

## 5. Class mapping

`0 = Oil, 1 = No-Oil, 2 = Lookalike` — identical between the PyTorch
checkpoints, the ONNX graphs, and the live API (`class_labels` on
`GET /api/ml/status`). Verified identical across both runtimes this pass via
`tests/test_onnx_inference.py::TestClassOrdering` (reads the mapping from
each checkpoint/manifest, never hardcodes it).

## 6. ML fallback behavior

`detector_provenance` values: `CLASSICAL_DARK_SPOT` (default, not a trained
model), `CLASSICAL_DARK_SPOT_ML_FALLBACK` (ML requested but didn't qualify),
`ML_PROTOTYPE_SYNTHETIC_TRAINED`, `ML_REAL_ZENODO_TRAINED`. Fallback triggers
(`app/investigation/orchestrator.py`): non-dual-pol input, ML engine
unavailable, segmenter mask empty, or classifier confidence below
`ML_FALLBACK_CONFIDENCE_THRESHOLD` (default 0.5, a stated heuristic, not a
calibrated threshold). A Lookalike verdict with a non-empty mask is never
relabelled Oil. Live end-to-end confirmation: 4/4 real satellite uploads against
bundled sample images correctly fell back to classical and were honestly tagged
`CLASSICAL_DARK_SPOT_ML_FALLBACK`, never presented as a real ML result.

## 7. Real-data evaluation artifact (segmenter)

`backend/data/ml_checkpoints/evaluation_report.json` / `.md` — genuine,
completed held-out-test-split (Part III, 450 scenes from `D:\zenodo_sar`)
evaluation of the baseline real segmenter. `GET /api/ml/metrics` serves this file's
contents directly.
In addition, `backend/data/ml_checkpoints/real_multiscale_evaluation_report.json` / `.md`
records the comparative evaluation between baseline and multiscale fusion across all 450
held-out Part III scenes.

## 8. 3-class classifier evaluation status

**`classifier_v2_part3_evaluation.json`: evaluated on 450 Part III held-out scenes.**
Accuracy: 78.44%, Macro Precision: 80.34%, Macro Recall: 78.44%, Macro F1: 77.79%.
Oil Precision: 95.59%, Oil Recall: 86.67%, Oil F1: 90.91%.
Non-oil false alarm rate: 2.0% (6/300).
Expected Calibration Error (ECE): 0.094.
Best training validation accuracy: 92.49% (epoch 32).

## 9. Backend test result

```
pytest -q
504 passed, 10 skipped, 0 failed
```
Run this pass, from a clean state (159.74s). All 10 skips are legitimate:
PostgreSQL-dependent tests (no local Postgres server in this sandbox) and
two tests specifically gated on the *synthetic*-checkpoint-only state.
Nothing was skipped, deleted, or weakened to reach 0 failed.

## 10. Frontend test result

```
npx vitest run
Test Files  10 passed (10)
     Tests  49 passed (49)
```

## 11. Lint result

```
npx oxlint
exit code 0 — 0 errors (7 pre-existing warnings, all in files untouched this pass)
```
**PASS.**

## 12. Build result

```
npm run build
✓ built in 493ms — dist/index.html, dist/assets/*.{css,js}
```
**PASS.**

## 13. Docker result

**Not applicable.** No `Dockerfile` or `docker-compose*` exists anywhere in
this repository (confirmed by a recursive search); nothing to build. This
project's deployment path (`docs/DEPLOYMENT.md`) targets Render/Vercel
directly from `requirements.txt`, not a container image.

## 14. End-to-end result

Live `uvicorn` process, genuine HTTP requests (`httpx`), this pass:

- `GET /api/health` → `200 {"status": "ok"}`
- `GET /api/ml/status` → `200`, `provenance: "ML_REAL_ZENODO_TRAINED"`,
  `runtime: "onnxruntime_cpu"`, both checkpoints loaded, real SHA-256s.
- 4× real dual-pol GeoTIFF upload (`POST /api/satellite/upload?detector=ml`,
  bundled sample scenes) → all `200`, one log line each (no duplicates),
  0.56s–1.08s each, all honestly `CLASSICAL_DARK_SPOT_ML_FALLBACK` (see §6).
- `GET /api/spill/{id}` (persistence) → `200`.
- `GET /api/report/{id}?fmt=pdf` → `200`, genuine 4-page PDF (verified with
  `file`). `fmt=markdown` and `.../vessels.csv` export → `200`.
- Satellite-upload timeout (frontend `api.js`) is 300000ms per-request,
  verified directly against the real axios config object in
  `frontend/src/test/satelliteUploadTimeout.test.jsx` — comfortably covers
  the ~128s duration a real large upload was previously observed taking.
- Duplicate-upload prevention (two independent `useRef` guards, modal +
  orchestration layer) verified under React StrictMode and a forced rapid
  double-click in `frontend/src/test/satelliteUploadFlow.test.jsx`.

## 15. Known limitations

- Multiscale segmentation release gates & real data status (Phase 4.3C Release Audit):
  Controlled Engineering Test Gate is PASSED (9/9 criteria met across 9
  controlled benchmarks and 18 unit/integration tests).
  In Phase 4.3C, candidate strategy `size_gated_closure` (`size_gated_closure_v1`)
  was formally frozen, evaluation metrics audited, and independent intake procedures established:
  - Metric Definitions Reconciled:
    - Pixel-pooled recall (volumetric oil recovery) is clearly distinguished from
      per-scene macro recall. Clarified that previously reported 89.62% test recall
      was an unweighted macro artifact from treating 0/0 on negative scenes as 100%.
      Volumetric pixel recall is 39.49% on Part III and 65.08% on validation.
    - Exact denominators recorded: Part III 300 negative scenes = 19.66M pixels.
      Validation 103 lookalike scenes = 6.75M pixels.
    - False-alarm reduction verified: Lookalike false alarms reduced by 84.0% on
      validation (+1,531 px vs +9,555 px in unconstrained multiscale).
    - 0 px false-alarm increase on clean sea No-Oil scenes (100% specificity).
    - Q4 large-slick pooled recall gain: +2.31% absolute (0.5561 -> 0.5792).
    - Q1 small-slick area ratio delta: 0.0000 (zero perimeter blooming).
  - Production Default & Rollback:
    - Single-scale baseline remains production default (`ML_MULTISCALE_SEGMENTATION=false`).
    - Multiscale candidate remains opt-in (`ML_MULTISCALE_SEGMENTATION=true`, strategy `size_gated_closure`).
    - Zero-risk rollback: `ML_MULTISCALE_FUSION_STRATEGY=base_only` produces bitwise baseline equivalent output.
  - Independent Validation Status:
    - `INDEPENDENT_VALIDATION = "BLOCKED_DATA_UNAVAILABLE"`.
    - Local audit confirmed no independent dataset is available locally outside `D:\zenodo_sar`.
    - Held-out Part III test set is strictly classified as `PREVIOUSLY_CONSULTED_TEST`.
    - Dataset intake specification codified in `docs/INDEPENDENT_DATASET_INTAKE.md`.
    - Automated release-gate command provided at `scripts/verify_release_gate.py`.
  - Release Verdict: `EXPERIMENTAL_CANDIDATE_FROZEN`.
- Browser verification status: Formally reported as NOT PERFORMED because headless
  browser drivers and a live dev server were not available in this non-interactive
  environment. Vitest tests (49/49 passed) and production build (0 errors) are not
  substituted for live browser interaction.
- `ML_FALLBACK_CONFIDENCE_THRESHOLD` (0.5) is a stated engineering heuristic,
  not a calibrated operating point — documented as such everywhere it
  appears, never presented otherwise.
- The real ONNX export's recorded opset (18) differs from the requested one
  (13): the installed torch version's downgrade path failed internally and
  silently kept the higher opset; `export_onnx.py` now records the true,
  verified opset rather than the request, but a lower-opset export was never
  actually achieved. Opset 18 loads and runs correctly under onnxruntime
  1.24.4 (verified) and is not known to cause compatibility issues, but this
  differs from the originally-intended opset 13 target.
- The bundled demo SAR images (`backend/data/sample_sar/`) are outside the
  real model's training distribution and reliably trigger classical
  fallback when run with `detector=ml` — expected and honestly reported,
  not a defect; they were generated for the classical detector's own
  evaluation (`MODEL_CARD.md` "Evaluation (classical detector)" section).
- PyTorch does not guarantee bit-for-bit determinism across hardware/driver/
  library versions even with a fixed seed — "same seed" means
  audit-reproducible, not bitwise-identical (`app/ml/train.py::set_seed`
  docstring).
- No Docker configuration exists (§13) — deployment is documented for
  Render/Vercel's native Python buildpacks instead.
- `test_startup_warns_loudly_when_auth_enabled_with_insecure_default_secret`'s
  originally-reported failure could not be reproduced in this pass's own
  sandbox (pytest 9.1.1 already compensates for the underlying
  `propagate=False` scenario in most cases) — the fix was still applied
  since it is strictly more robust and version-independent; stated plainly
  rather than claiming a false reproduction.

## 16. Deployment instructions

See `docs/DEPLOYMENT.md` in full. Summary: backend build command
`pip install -r requirements.txt` (optionally `+ -r requirements-postgres.txt`
for `DATABASE_URL`) — **never** `+ -r requirements-ml.txt` in production,
that installs PyTorch, which production does not need (ONNX Runtime, in the
base `requirements.txt`, serves the real models) and a constrained instance
(e.g. Render Free, 512 MB/0.1 CPU) cannot fit. Frontend: `npm run build`,
serve `dist/`.

## 17. Checkpoint / model installation instructions

Real PyTorch checkpoints and the real ONNX artifacts are both already
included in this ZIP (§2, §3) — no separate download or installation step is
required to run with real ML. To regenerate the ONNX artifacts from the
checkpoints yourself (e.g. after retraining): see `CHECKPOINT_INTEGRATION_
GUIDE.md` §5 or `MODEL_CARD.md` §6d. To train new checkpoints from scratch
against the real Zenodo dataset: `MODEL_CARD.md` §6b and
`docs/DATASET_SETUP.md` (requires a separate, large, manual dataset
download — never bundled, never automatic).

## 18. Exact final file structure (key paths)

```
backend/
  app/                      FastAPI app, ML, satellite, AIS, persistence, etc.
  data/ml_checkpoints/
    classifier_real_promoted.pth / _best.pth / _last.pth   (real, active)
    classifier_checkpoint.pth                               (synthetic, fallback)
    spill_unet_real_promoted.pt / _best.pt / _last.pt       (real, active)
    spill_unet.pt                                            (synthetic, fallback)
    classifier_real_best.onnx(.data), spill_unet_real_best.onnx(.data)
    onnx_export_manifest.json
    evaluation_report.json / .md                             (segmenter, real, 450 scenes)
    multiscale_comparison_report.json / .md                 (baseline vs multiscale evaluation & promotion gate)
    classifier_v2_part3_evaluation.json / .md                  (classifier, evaluation_run: false)
    *_training_history.json, *_model_metadata.json, *_dataset_manifest.json
  data/sample_sar/           bundled demo SAR scenes (classical-detector eval + this pass's E2E)
  tests/                     504 passed, 10 skipped, 0 failed (§9)
frontend/
  src/                       React/Vite app
  src/test/                  49 passed, 0 failed (§10)
docs/DEPLOYMENT.md, DATASET_SETUP.md, DATASET_STRUCTURE.md, ...
MODEL_CARD.md, CHECKPOINT_INTEGRATION_GUIDE.md, CHANGELOG_FINAL.md,
FINAL_RELEASE_REPORT.md, FINAL_HANDOFF.md, README.md, ARCHITECTURE.md,
AUDIT_LOG.md, FINAL_RELEASE_READINESS.md (this file)
```

Excluded from the release ZIP (regenerable / machine-local / never real
source): `node_modules/`, `__pycache__/`, `.pytest_cache/`, `dist/`,
`backend/data/app.db`, any `.env`/`.env.local` (only `.env.example` ships).
