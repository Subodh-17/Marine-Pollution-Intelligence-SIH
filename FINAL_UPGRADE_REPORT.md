# FINAL_UPGRADE_REPORT.md

Scope note, stated up front as in every prior round of this thread: this
request specified 16 phases; several (uncertainty-aware drift ensemble,
GIS sensitive zones, timeline/replay, human-in-the-loop workflow) are
substantial standalone features. This round traced the ML pipeline
end-to-end as instructed (Phase 1), found and fixed two real, confirmed
bugs it surfaced (Phase 3, Phase 5), and verified both by execution against
real checkpoint weights and a real bundled sample scene — not by code
reading alone. Phases 6-14 were not attempted this round; see J.

## A. Files changed

- `backend/app/ml/detector.py` — `scene_classification`/`oil_status`
  computation added to `detect()`
- `backend/app/satellite/detection.py` — `scene_classification` field added
  to `DetectionResult`
- `backend/app/investigation/orchestrator.py` — safety notes + payload
  wiring for non-`LIKELY_OIL` verdicts
- `backend/app/reports/generator.py` — verdict surfaced in Incident
  Overview + Section 3
- `backend/app/main.py` — root-cause fix for the `/api/ml/status`
  provenance bug (Phase 5)

## B. Features added

`scene_classification` (`label`, `confidence`, `probabilities`,
`oil_status`) — a structured, un-missable field distinguishing the four
states Phase 3 requires: `LIKELY_OIL`, `NO_OIL`, `LOOKALIKE`, `UNCERTAIN`.
Surfaced in the API response (`satellite.scene_classification`), in
`detection_notes` as an explicit warning, and in the Markdown report's
Incident Overview and Section 3.

## C. Bugs fixed

**1. (Phase 1/3) Lookalike/No-Oil could silently present as a confirmed
oil detection.** Traced the full pipeline as instructed and confirmed: the
classifier and segmenter are architecturally independent sub-models. A
scene the classifier confidently calls "Lookalike" (even at 99%+
confidence) can still produce a non-empty segmenter mask, because the
segmenter never sees the classifier's output. Before this fix, nothing
downstream — not the orchestrator, not the API response, not the report —
ever read the classifier's label; only `mask`/`confidence`/`provenance`
drove what looked like a "detection." The existing fallback-vs-ML decision
in `orchestrator.py` only checks *how confident* the classifier was, never
*which label* it picked, so a confident Lookalike sailed straight through.
**Verified fixed** by forcing a controlled 99.8%-confidence Lookalike
prediction with a non-empty mask through the real orchestrator against a
real bundled sample scene end-to-end: the API response now carries
`oil_status: "LOOKALIKE"`, and the generated report leads with
`> **Scene classification verdict: SAR LOOK-ALIKE / UNCERTAIN TARGET**`.

**2. (Phase 5) `/api/ml/status` reported the wrong provenance when no
model was loaded at all.** Confirmed this was implementation error, not a
stale test: `MLDetectorEngine.provenance` (the engine's own authoritative
attribute) was already correctly `"ML_UNAVAILABLE"` in this situation — the
endpoint just ignored it and hardcoded `PROVENANCE_ML_SYNTHETIC`
(`"ML_PROTOTYPE_SYNTHETIC_TRAINED"`) instead, falsely implying a synthetic
model was active when *nothing* was loaded. Fixed by using `engine.provenance`
directly. **Verified fixed** by replicating the endpoint's exact branch
logic against a live engine in this sandbox (torch unavailable, no ONNX
artifacts — precisely the condition the original bug report describes) and
confirming it now matches the test's own independently-derived expectation.
The actual FastAPI test (`test_ml_status_returns_200_and_never_claims_validated`)
could not be *run* here — no `fastapi`/`pytest` installable, no network —
so this is stated as logically verified, not as a passed test.

## D. Tests before/after

Before this round: 49 tests across `test_onnx_inference.py` (34) +
`test_tiling.py` (15). After: **55 passed, 0 failed, 2 skipped** — added
`tests/test_lookalike_safety.py` (6 new tests, including the core
regression case: confident Lookalike + forced non-empty mask →
`oil_status="LOOKALIKE"`, never `"LIKELY_OIL"`). Run via the pytest-shim
used throughout this thread; real `pytest -q tests` / `npm test -- --run`
/ `npm run build` / `npm run lint` were **not run** — no network, no
`pytest`/`node_modules` installable in this sandbox.

## E. Fresh ML metrics

**Not produced. Phase 2 is BLOCKED** — `backend/data/zenodo_sar` does not
exist in this checkout (your real dataset is on your own machine with the
RTX 5070, not uploaded here). No metric in this report is fabricated or
estimated in its place.

## F. 3-class confusion matrix

**Not produced, same reason as E.**

## G. Drift validation

Not attempted this round (Phase 6/7 deferred — see J).

## H. Performance impact

Not benchmarked this round — no change made here was performance-sensitive
(the fix is a few dict lookups and string comparisons per detection, not a
new pass over the image).

## I. Security findings

Not audited this round (Phase 12 deferred — see J).

## J. Remaining limitations / not attempted this round

- **Phase 2** (real 3-class evaluation) and **Phase 16** (final real-data
  validation JSON/MD) — blocked, no dataset in this sandbox.
- **Phase 4** (confidence calibration) — the `UNCERTAIN` bucket added this
  round reuses the existing `ML_FALLBACK_CONFIDENCE_THRESHOLD` rather than
  a calibration study; no reliability diagram/ECE has been computed, and
  none is claimed.
- **Phase 6/7** (drift ensemble, attribution propagation), **Phase 8**
  (multi-temporal — requires inspecting your real dataset for valid
  temporal sequences, not possible here), **Phase 9** (GIS sensitive
  zones), **Phase 10** (timeline/replay), **Phase 11**
  (human-in-the-loop), **Phase 12** (security audit), **Phase 13**
  (frontend) — not attempted, each a standalone feature; same triage
  reasoning as every prior round of this thread.
- One real, minor **model-quality observation** worth flagging honestly,
  *not* a code bug and explicitly not something I attempted to fix: when
  the sandbox-built ONNX graphs (exported from your real checkpoint bytes
  via a torch-free reader, since `torch` isn't installable here — see this
  thread's earlier rounds) were run against the four bundled real sample
  images, the segmenter produced an empty mask on all four, and the
  classifier's predicted label didn't match what the filenames suggest
  (e.g. predicted "Oil" on `demo_lookalike_scene_01.tif`). **This is n=4
  samples through a hand-built, non-canonical ONNX export — not a
  statement about real model accuracy**, and retraining/tuning based on it
  is explicitly out of scope. It's noted only because Phase 1 asked me not
  to assume correctness without tracing actual behavior; a real evaluation
  needs Phase 2's proper held-out run on your machine.

## K. Exact commands to run the final system

```bash
cd backend
pip install -r requirements.txt -r requirements-ml.txt   # -ml only for local/dev, never Render
python -m app.ml.export_onnx      # requires your real checkpoints in place
python -m app.ml.verify_onnx
uvicorn app.main:app --host 127.0.0.1 --port 8000

cd ../frontend
npm install
npm run dev
```

## L. Exact commands to create the final release ZIP

```bash
find . -name "__pycache__" -o -name ".pytest_cache" | xargs rm -rf
find . -name "app.db" -delete
zip -r -X Marine_Pollution_Intelligence_SIH_FINAL.zip Marine_Pollution_Intelligence_SIH_FINAL \
    -x "*.pyc" "*__pycache__*"
unzip -t Marine_Pollution_Intelligence_SIH_FINAL.zip   # integrity check
```
