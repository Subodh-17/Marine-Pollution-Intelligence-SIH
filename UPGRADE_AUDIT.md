# UPGRADE_AUDIT.md

Scope note, up front: this request specified 31 phases describing what
would, done fully, be a substantially larger platform (evidence fusion
engine, uncertainty-aware drift ensembles, multi-temporal SAR, GIS
sensitive-zone layers, investigation timeline/graph, alert engine,
human-in-the-loop audit trail, multimodal satellite support...). Per the
request's own Phase 30 ("do not attempt a huge blind rewrite... work
systematically... continue only after phase is stable"), this round audits
honestly and implements one well-scoped, verified phase rather than
producing shallow stubs across 31. See the bottom of this file for the
recommended order for the rest.

## Current architecture (as found)

- **Backend:** FastAPI (`backend/app/main.py`), investigation orchestrator
  (`app/investigation/orchestrator.py`) coordinating satellite detection
  (classical dark-spot + ML), AIS attribution, drift/hindcast, and report
  generation (Markdown/PDF/CSV/GeoJSON).
- **ML:** `SceneClassifier` + `SpillUNet` (`app/ml/architectures.py`), real
  Zenodo-trained checkpoints with a synthetic-prototype fallback, dual
  runtime (PyTorch local/dev, ONNX Runtime CPU production — added in prior
  rounds this thread), tiled inference for large scenes (added last round).
- **Persistence:** `app/persistence/db.py` — investigations survive a
  restart.
- **Frontend:** React/Vite (not audited in depth this round — no
  network/`node_modules` to actually run it against; see Blocked below).

## Existing capabilities confirmed by direct inspection

Real-vs-synthetic ML provenance with filename-precedence + checksum
verification; classical fallback with an explicit reason string; PDF/MD/CSV
reports with a `model_provenance_info` section already covering most of
Phase 21's list; `config_used` reproducibility dict already covering
`app_version`, `detector_version`, attribution weights, drift windows;
owner-scoped auth; upload size/MIME validation with streaming (no
whole-file memory buffer); tiled inference for oversized scenes.

## Real gaps found this round (Phase 21 specifically)

Phase 21 asks for model name/version/checkpoint/sha256/dataset/preprocessing
version/threshold/runtime/software version/timestamp/input-file-hash on
every investigation. Auditing what actually existed against that list:

| Field | Found | Action |
|---|---|---|
| model name, checkpoint filename+sha256, dataset used | present | none needed |
| app/software version, timestamp | present (`config_used["app_version"]`, `Investigation.created_at`) | none needed |
| inference runtime (onnx/pytorch) | **missing** from the per-investigation dict (tracked internally, never surfaced) | added |
| segmentation threshold | **hardcoded** `0.5`, not configurable or reported | made configurable (`ML_SEGMENTATION_THRESHOLD`) + reported |
| input file hash | **missing entirely** | added (streamed while writing the upload, zero extra I/O) |
| model_version, preprocessing_version, dataset_dois | present on PyTorch runtime, **silently `None` on the ONNX runtime path** | root-caused: ONNX path never reads the raw checkpoint dict these came from; fixed by falling back to the sidecar JSON both paths already ship with |

The last row is the most consequential finding: production (ONNX runtime)
was under-reporting its own provenance compared to local/dev (PyTorch
runtime) for exactly the fields an investigator would check. Not a
fabrication risk (missing fields render as `None`/absent, never a wrong
value), but a real completeness gap, now fixed and covered by 6 new tests.

## Bugs found and fixed

- The ONNX-path provenance gap above.
- One AIS-upload comment line accidentally dropped mid-edit while changing
  `_save_upload_with_size_limit`'s return signature — caught by re-reading
  the diff and restored before packaging.

## Technical debt / dead code / stale docs

Not re-audited from scratch this round — see prior rounds'
`work/FINAL_AUDIT.md` (path portability) and `FINAL_IMPROVEMENT_REPORT.md`,
both still accurate and not superseded by anything here.

## Blocked (same constraints as every prior round, unchanged)

- No network access in this sandbox → `torch`, `onnx`, `pytest`, `npm`
  cannot be installed.
- No `backend/data/zenodo_sar` in this checkout → Phase 1's held-out
  re-evaluation against real data cannot run here; your real dataset is on
  your own machine.
- Frontend untouched and untested this round (no `node_modules`, no way to
  run `npm test`/`build`/`lint`).

## What was actually implemented and verified this round

1. `ML_SEGMENTATION_THRESHOLD` — configurable, documented as not
   test-set-derived.
2. `inference_runtime` + `segmentation_threshold` added to
   `model_provenance_info`.
3. `input_file_sha256` — computed in the existing streaming upload writer
   (no extra file read), threaded through `run_investigation()` into
   `Investigation.config_used`.
4. ONNX-path provenance gap (model_version/preprocessing_version/
   dataset_dois) fixed via sidecar-JSON fallback.
5. **Verified by execution, not just import:** a full `mode="demo"`
   investigation end-to-end (classical detector, 3 AIS candidates, drift,
   1029ms), Markdown+CSV report generation against the same investigation,
   and the ONNX runtime path's `model_info` fields directly — all real runs
   against real checkpoint bytes, not assertions from code reading.
6. Test suite: **43 passed, 0 failed, 2 skipped** (the 2 skips are stated
   PyTorch-parity skips, not silent passes) — run via the pytest-compatible
   shim used throughout this thread, since real `pytest` cannot be
   installed here.

## Recommended implementation order for the remaining phases

Grouped by what's genuinely independent vs. what depends on something else
being built first, since several of the requested phases (10 Evidence
Fusion Engine, 17 Investigation Graph) are consumers of others (7 Drift
uncertainty, 8/9 AIS behavior/temporal correlation, 12 Vessel attribution
explainability) rather than standalone:

1. **Phase 11** (calibrated confidence buckets) — small, additive, no
   dependency on real data being present here; natural next step after
   this round's threshold work.
2. **Phase 12** (vessel attribution explainability output format) — mostly
   a presentation change on top of the existing scoring, moderate size.
3. **Phase 8/9** (AIS behavior + temporal correlation) — meaningful new
   computation, but self-contained; needed before Phase 10 can fuse it.
4. **Phase 7** (drift uncertainty ensemble) — self-contained, needed before
   Phase 10 can fuse it.
5. **Phase 10** (Evidence Fusion Engine) — do this only after 7/8/9/12
   exist for real, or it has nothing genuine to fuse and risks becoming
   exactly the "arbitrary weighted score without documented methodology"
   the request itself warns against.
6. Phases 3 (lookalike categories), 5 (spill characterization), 6
   (environmental context), 13 (sensitive zones), 14 (multimodal),
   15/16/17 (timeline/map/graph UI), 18 (alerts), 19 (human-in-the-loop) —
   each independently sizable; sequence by your priority, not a technical
   dependency.
7. Phases 25/26 (API robustness/security) and 28 (testing) apply
   continuously, not as a single end-of-project pass — best done alongside
   each phase above, not deferred to the end.

Phase 1 (real-data evaluation) and Phase 4 (multi-temporal SAR, which
needs multiple real scenes) can only genuinely run wherever your dataset
actually is.
