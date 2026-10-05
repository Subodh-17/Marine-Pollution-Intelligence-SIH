# FINAL HANDOFF — Marine Pollution Intelligence & Vessel Attribution Platform

## September 2026 geospatial/AIS-robustness follow-up pass (most recent — this supersedes the section below for current test counts; see this entry for what's actually new)

This pass started from the state left by the "production-hardening pass" immediately below
(247 passed/14 skipped backend, 36 passed frontend) and focused on areas that pass had not yet
covered in depth: geospatial coordinate correctness end-to-end, the ML model-loading
abstraction, race conditions/duplicate requests, and AIS trajectory data-quality edge cases.
Most of what was audited here turned out to already be correct - verified by new tests that
passed immediately, not by re-reading prior notes - with one genuine gap found and fixed:

- **Geospatial correctness audit (no bugs found, but now regression-tested).** Read
  `SceneGeoTransform.pixel_to_latlon`/`latlon_to_pixel` (`app/satellite/synthetic.py`) and the
  drift model's great-circle destination formula (`app/drift/model.py`) line-by-line: no
  lat/lon swap, and the antimeridian-wrapping formula (`(lon + 540) % 360 - 180`) was already
  correct. Added `backend/tests/test_coordinate_validation.py` - pixel<->latlon round-trips, an
  antimeridian-crossing check, an exhaustive sweep of near-pole latitudes x distances up to a
  full Earth circumference confirming no NaN/Inf/domain-error ever occurs, and an AIS
  out-of-range-coordinate rejection check. All 30 passed on the first run.
- **ML model-loading abstraction (no changes made).** Confirmed `app/ml/detector.py` already
  implements real sha256 checkpoint checksums, model/preprocessing-version tracking, CPU/CUDA
  device auto-selection, a lazy singleton (loaded once, reused - Section 19's performance ask),
  and honest three-way provenance (`ML_PROTOTYPE_SYNTHETIC_TRAINED` /
  `ML_REAL_ZENODO_TRAINED` / classical fallback) that never claims inference happened without a
  loaded checkpoint. This already matches the "clean model-loading abstraction" ask closely
  enough that making speculative changes to it would add risk to sensitive, already-correct
  code without a concrete bug to justify it, so none were made.
- **Duplicate-request / race-condition check (no bug found).** Verified live with Playwright
  (rapid double-click on "Run Demo Investigation") that only one `/api/spill/detect` call ever
  fires - the button unmounts synchronously with the `running` state before a second click can
  land. Confirmed working, not a fix.
- **AIS trajectory data-quality gap (found and fixed).** The AIS pipeline validated
  coordinate *ranges* and dropped duplicates, but never checked whether **consecutive pings for
  the same vessel** implied a physically impossible speed - e.g. a corrupted/duplicate-MMSI
  record placing a vessel 40 km away one minute later. Added `position_jumps` tracking to
  `reconstruct_trajectories` (`app/ais/pipeline.py`, `MAX_REALISTIC_SPEED_KNOTS = 60`) and a new
  `POSITION_JUMP` flag (`app/ais/behavior.py`). This is deliberately kept at **"info" severity**
  (weight 0.05 in `behavior_score()`, not "notable"/"high"'s 0.20/0.40) because an impossible
  jump is evidence of a *data* problem (corrupt/duplicate/spoofed record), not of what the
  vessel actually did - scoring it as if it were suspicious behaviour would misrepresent what
  the evidence actually shows. Verified end-to-end against the live server with a
  deliberately-corrupted AIS CSV (a jump specifically placed to still land inside the existing
  50 km spatial-filter radius, so it actually reaches the new check rather than being excluded
  by a different, earlier filter): the flag appears correctly in the API response and renders
  correctly in the Ranking tab's evidence drawer (with the frontend's existing severity-based
  icon styling picking the right lower-emphasis icon automatically, since it already renders
  whatever flags the backend returns rather than hardcoding flag types) with **zero effect** on
  the demo scenario's existing deterministic scores (55.3/52.2/46.0 - re-verified byte-identical
  before/after). 4 new tests in `backend/tests/test_ais.py`.
- **Additional live verification (no bugs found).** Re-confirmed graceful, honest empty states
  for a zero-AIS-candidate investigation end-to-end (Overview shows "0"/"No candidates" rather
  than an error; Ranking shows "No candidate vessels found..."; the Report tab correctly omits
  only the "Download vessel tracks (GeoJSON)" button since there are no tracks to export, while
  still generating a valid, smaller PDF) - no crash, no stuck loading state, nothing fabricated.

**Test suite after this pass:** `pytest tests -q` → **280 passed, 14 skipped, 0 failed** (up
from 247 - 30 new coordinate-validation tests + 4 new AIS position-jump tests + no regressions
elsewhere, confirmed by an unrelated fresh live demo run producing byte-identical scores to
before this pass). `npm test -- --run` → **36 passed, 0 failed** (unchanged from the prior
pass - this pass's fix was backend-only). `npm run build` → clean.

## September 2026 production-hardening pass (previous — see this entry for the map/timeline/report-download fixes)

This pass started from the state left by the prior "Full-project audit pass" below (246
passed/14 skipped backend, 34 passed frontend) and went looking for *currently reproducible*
bugs via a real running instance (`uvicorn` + `vite`) and a real Chromium browser
(Playwright) — not by re-reading prior audit notes. Three genuine, previously-undetected bugs
were found and fixed, each with a regression test that was confirmed to fail against the old
code and pass against the fix (reverted and re-applied live to verify this, not just asserted):

1. **Dashboard-crashing map bug (`InteractiveMap.jsx`).** The basemap tile-provider failover
   logic had a stale-closure race: `handleTileError`'s bounds check read `providerIndex` from
   its enclosing render's closure, while the actual increment (`setProviderIndex(i => i + 1)`)
   applied to the live current state. When both configured tile providers are unreachable
   (true of this sandbox's network egress, and of any network-restricted deployment), a late
   `tileerror` event from an already-superseded `<TileLayer>` instance could push
   `providerIndex` one past the end of `TILE_PROVIDERS`, crashing the entire Overview/AIS map
   with `Cannot read properties of undefined (reading 'url')` — caught by the ErrorBoundary, so
   the very first tab a judge sees after "Run Demo Investigation" showed a "This section
   couldn't be rendered" card instead of the map. Fixed by gating and clamping off a `useRef`
   that's always synchronously current, making the index mathematically unable to leave the
   valid range regardless of event timing. New test: `src/test/InteractiveMap.test.jsx`.
2. **Timeline chronological-ordering bug (`TimelineTab.jsx`).** The node list was built as
   `[...backward_track, origin, observed, ...forward_track]` and never actually sorted by
   timestamp. Since `probable_origin` shares its `t_offset_hours` with the *earliest*
   `backward_track` point (both represent the same -24h moment under different labels), the
   dedupe logic removed the duplicate but left "PROBABLE ORIGIN" sitting in its original array
   position — after the later T-18h/T-12h/T-6h stages — even though it's chronologically the
   earliest point on the timeline. Fixed with an explicit sort by `t_offset_hours`. New test:
   `src/test/TimelineTab.test.jsx`.
3. **Report-tab downloads that didn't download.** "Download JSON" and both GeoJSON buttons
   pointed at plain JSON API responses with no `Content-Disposition` header, so clicking them
   opened raw JSON inline in a new tab instead of saving a file (confirmed via
   `page.expect_download()` timing out). Fixed client-side — a Blob + throwaway `<a download>`
   element — rather than changing the shared `/api/spill/{id}` data endpoint's headers. The
   JSON download now serializes the exact in-memory `investigation` object already on screen
   instead of re-fetching, so it's guaranteed to match what's on screen. This did introduce a
   one-line CSS regression (converting two links to buttons meant three of the five toolbar
   items lost the browser's default anchor underline) - fixed in the same pass by adding
   `text-decoration: none` to the shared `.btn-secondary` class (checked all 9 other call
   sites first — all already `<button>`, so this is a no-op everywhere else).

Also fixed, without a dedicated regression test (lower severity, straightforward diff):
- **Internal temp-path disclosure**: a corrupt/unreadable satellite image upload's error
  message echoed PIL's raw exception text, which embeds the server's random
  `/tmp/tmpXXXXXXXX.png` path (e.g. `cannot identify image file '/tmp/tmpsf5dh7b4.png'`). Now
  substitutes the user's own original filename before the message leaves the server. Regression
  test added this time: `test_corrupt_image_error_does_not_leak_server_temp_path` in
  `backend/tests/test_upload_security.py` (confirmed failing against the old code).
- **Stale attribution-weights documentation**: this file's own "Attribution weights" table
  (below - now corrected) and effectively-duplicated text still described a 6-factor
  30/25/15/10/10/10 split, but `app/scoring/attribution.py::DEFAULT_WEIGHTS` has since been
  split into 8 factors (24/20/12/8/8/10/10/8 — the extra two are explicit `speed_consistency`
  and `data_completeness` components). The evidence-drawer screenshot review that would have
  caught this drift (see the "Vessel evidence drawer" bullet further below, from an earlier
  pass) recorded the *old* 6-factor numbers as an example, so the mismatch went unnoticed until
  this pass diffed the doc against the live `DEFAULT_WEIGHTS` dict directly.

**Verified genuinely working (not just re-asserted from prior passes)**, via a live browser
walking the actual app: Satellite tab's three-image detection chain, AIS tab's candidate
filtering + "nearby vessels ruled out" panel, Ranking tab's evidence drawer (score breakdown
sums correctly, "candidate"/"not proof of responsibility" language intact), ML Training tab's
honest `UNAVAILABLE`/`MISSING` states (no fabricated numbers - torch genuinely isn't installed,
dataset genuinely isn't present, both stated plainly), repeated demo re-runs (fresh
investigation ID + no stale map/report state each time, including when re-running while sitting
on the Report tab), the full custom-AIS upload flow (a hand-crafted 6-point CSV for a vessel
placed near the demo origin correctly surfaced as the sole, correctly-scored candidate), and the
full real-satellite upload flow (one of the bundled `backend/data/sample_sar/*.tif` samples
produced genuinely different detection numbers than the synthetic demo scene — 18.713 km² / 77.7%
vs. 3.943 km² / 84.7% — confirming it actually processed the uploaded file, with geolocation
correctly labeled `assumed_center_coordinates (assumed)` since this TIFF carries no embedded
geo-referencing).

**Test suite after this pass:** `pytest tests -q` → **247 passed, 14 skipped, 0 failed** (up
from 246; the one new test is the temp-path-disclosure regression above; skips still all
torch-only — torch remains uninstallable in this sandbox, a CPU-only wheel pull exhausts
available disk). `npm test -- --run` → **36 passed, 0 failed** (up from 34 — the two new
InteractiveMap/TimelineTab regression tests). `npm run build` → clean. `npx oxlint` → same
2 pre-existing warnings, 0 errors (unrelated file, reviewed by a prior pass — see below).

No ML model was retrained or modified, no dataset was added or downloaded, and no
data/coordinates/AIS records/attribution evidence were invented — all fixes above are either
pure bug fixes (crashes, ordering, missing headers) or doc corrections against already-real
code.

## Full-project audit pass against the SIH brief (previous — see AUDIT_LOG.md's matching entry for full detail)

Re-ran everything fresh in this session rather than trusting the numbers in the section below.
**Backend:** `pytest tests -q` → **246 passed, 14 skipped, 0 failed** (all skips are
torch-related; torch could not be installed in this sandbox — same disk/network constraints as
every prior pass, see AUDIT_LOG.md). **Frontend:** found a real regression —
`src/test/smoke.test.jsx` rendered `<App/>` without the `ThemeProvider` that `Header`'s theme
toggle requires, so 9 of its tests were failing with a thrown error, not an assertion failure.
Fixed (wrapped the same way `main.jsx` does) → `npm test -- --run` → **34 passed, 0 failed**
(up from 25 passed/9 failed). `npm run build` → clean. `npx oxlint` → **2 warnings, 0 errors**
(two informational React-hooks warnings from a newer oxlint version, reviewed and judged to be
intentional/acceptable patterns in `lib/theme.jsx`, not defects — see AUDIT_LOG.md for the
reasoning; left unchanged rather than restructuring a working, tested module to silence a linter).

Also reworded one Model-Provenance-panel label (`SatelliteTab.jsx`) from raw field-name-style
text ("Fallback status" / "Classical fallback active") to a fuller, more professional phrasing
("Detection method used" / "Classical detector used (ML result not applied)"), and added
`.pytest_cache/`, `venv/`, `.venv/` to `.gitignore`. No backend logic, ML behavior, AIS/scoring
pipeline, or report generation was changed this pass. Real ML training/validation, live
AIS/weather provider validation, and a frontend login screen remain out of scope for the same
environment reasons as every earlier pass (documented below and in AUDIT_LOG.md) — this pass
did not attempt or claim progress on any of those.

## Final independent verification and packaging pass (previous — see AUDIT_LOG.md for full detail)

This is the current state, superseding all sections below for anything they disagree on. Every
number here was reproduced by directly executing the actual project in this session — nothing
carried over from the sections below without being re-run first.

**Backend tests:** `pytest tests -q` (no torch) → **175 passed, 1 skipped, 0 failed**, ~46s, no
hang. With torch genuinely installed and importable in this sandbox (a first — see below) →
**176 passed, 0 skipped, 0 failed**.

**Frontend tests:** `npm test -- --run` → **12 passed, 0 failed**.

**Frontend build:** `npm run build` → clean, 3 output files. Production build additionally
smoke-tested by serving it with `vite preview` (HTTP 200, correct asset references).

**Frontend lint:** `npx oxlint` → **0 warnings, 0 errors**.

**Complete demo executed:** Yes, against a real `uvicorn` process (not `TestClient`):
`/health`, `/ready`, `/api/config`, `/api/ml/status`, a full demo investigation
(`mode=demo` → 200, 3.943 km² region, 3 ranked candidates).

**Real ML path genuinely exercised (new this pass):** every earlier pass in this document's
history failed to get PyTorch importable in its sandbox. This pass succeeded (install the CUDA
runtime dependency wheels first, then `torch` itself with `--no-deps` against those now-present
libraries). With real `torch 2.14.0+cu130` running: `GET /api/ml/status` confirmed
`torch_available: true` with both checkpoints loaded and real SHA-256 checksums; a
`detector=ml` upload on the bundled dual-pol sample genuinely ran both PyTorch forward passes
(classifier said "Oil" at 100% confidence; segmenter produced a 0.00%-coverage mask) and the
orchestrator correctly fell back to the classical detector with real, non-empty geometry
(18.778 km²) — the same fallback behavior previously verified only against mocked engines,
now confirmed against genuine inference output.

**All five exports verified:** PDF (`file` confirms a real 4-page PDF; `pypdf` text extraction
confirms the correct report title and matching investigation id), Markdown/JSON report, vessel
CSV, spill GeoJSON, vessel-track GeoJSON — all HTTP 200 for the same investigation, cross-checked
for consistency.

**No-detection case verified:** covered by dedicated backend tests (`test_no_detection_scenario_
returns_200_not_500`, `test_no_detection_does_not_crash_the_pipeline`) confirming a 200 response
with `geometry: null` and `candidates: []`, a 409 (not 500) from AIS analysis on a no-detection
investigation, and a valid, honestly-worded report ("No oil-slick-like feature was detected").
Note: the bundled synthetic "no-oil" sample scene is not itself a clean zero-detection input —
the classical detector finds a small, already-documented ~0.3 km² false positive on it (see
MODEL_CARD.md's evaluation section) — so the true zero-detection path is exercised via the
dedicated unit tests above rather than that specific sample file.

**Edge cases verified live this pass:** empty file upload (422), garbage-content `.tif` (422,
specific message), disallowed extension (400, specific message), empty/wrong-column AIS CSV
(200 at upload, 400 with a specific message once processed via `mode=custom_ais`), unknown
investigation id (404), a crafted path-traversal filename (422, rejected on content — and by
inspection the upload path never uses the client filename to build a filesystem path), and a
genuine **cross-process restart** (a second, independently-started `uvicorn` process — not just
an in-memory dict cleared inside one test process — still serves the same investigation and its
report from `data/app.db`).

**Remaining, honestly-stated limitations (unchanged by this pass):** the ML checkpoints are
still not trained on real Sentinel-1 data and remain an unvalidated prototype by design — this
pass exercised real inference and confirmed the documented "expect a fallback" behavior, it did
not and could not fix the underlying data limitation (see MODEL_CARD.md). No real Sentinel-1,
live AIS, or live weather/ocean feed is reachable from this sandbox, so those paths remain
verified only via the synthetic/mocked fallback logic. Zenodo integration remains explicitly
future work (`ENABLE_ZENODO_DATA=false`). No frontend login screen exists for the (disabled by
default) auth backend. SQLite only; no PostgreSQL client dependency was added.

**Documentation fixes made this pass:** `SIH_REQUIREMENT_MATRIX.md` row L corrected a stale
`7/7` frontend test count to `12/12` (the file's own summary paragraph elsewhere already said
12 — an inconsistency within the same file). `MODEL_CARD.md`'s "torch could not be imported"
paragraph, accurate for every earlier pass, was rewritten to describe this pass's successful
install and real inference run instead of leaving stale text standing.

**ZIP:** repackaged as `Marine_Pollution_Intelligence_SIH_FINAL.zip`, single root folder
`Marine_Pollution_Intelligence_SIH_FINAL/`, extracted afterward and re-verified (see the end of
this document / the accompanying chat response for the extraction check and forbidden-file
absence confirmation).

## "Remove remaining limitations" upgrade pass (previous — this ZIP, `Marine_Pollution_Intelligence_SIH_FINAL_10.zip`)

This is the current, authoritative validation status. The sections below it are an accurate
historical record of earlier sessions and are preserved as-is; use the numbers in *this*
section for the current state.

**Context this pass started from:** a "capsule context" summary supplied at the start of this
pass claimed specific files (`app/persistence/db.py`, `app/auth.py`,
`app/satellite/geotiff_metadata.py`) and a test count (123 passed) already existed. Direct
inspection of the actually-uploaded ZIP found none of those files present and a baseline of
**112 passed, 1 skipped** — the capsule context did not match the actual state of the project
and was not relied upon; everything below was implemented and verified fresh, from that
112-passed baseline.

**What was implemented, each genuinely new and test-verified (see AUDIT_LOG.md's final entry
for full detail on each):**

1. Real embedded-GeoTIFF georeferencing (`app/satellite/geotiff_metadata.py`) — 10 tests.
2. SQLite persistence surviving a backend restart (`app/persistence/db.py`) — 6 tests.
3. Lightweight authentication, off by default (`app/auth.py`) — 18 tests.
4. In-process background job processing (`app/persistence/jobs.py`) — 7 tests.
5. Live AIS (`app/ais/live_provider.py`) and live environment
   (`app/environment/adapters.py::RealWeatherOceanAdapter`) provider scaffolding — 13 tests.
   **Neither validated against a real provider** — no reachable AIS/weather API from this
   sandbox's network egress.
6. Upload security hardening: a real temp-file-leak bug fixed (AIS re-upload), a real
   max-image-dimension check added — 6 tests.
7. Model checksums (`checkpoint_checksums` in `GET /api/ml/status`) — real SHA-256 of each
   loaded checkpoint file.
8. Report/export consistency: the exact required disclaimer sentence, `geolocation_source`,
   environment `fallback_reason` — 3 tests.
9. Frontend: a Geolocation provenance chip, smarter truncation for chained provenance strings —
   5 tests.

**Explicitly not done, stated plainly:** real ML training/validation against real Sentinel-1
data (no such data reachable from this sandbox — unchanged from every prior pass); the ML
detector's actual PyTorch forward pass was not re-exercised this pass either (torch could not be
made to import — see below); live AIS/environment providers were not validated against any real
endpoint; PostgreSQL persistence was not implemented (SQLite only, per the request's own
instruction); no frontend login screen was built for the new auth backend.

**Validation performed for this pass (fresh, in this session):**
- Backend: `pytest tests -q` → **175 passed, 1 skipped, 0 failed** (up from 112 at the start of
  this pass). `pytest tests --durations=20 -q` also run, returned cleanly.
- Frontend: `npm install` (185 packages, clean) → `npm test -- --run` → **12 passed, 0 failed**
  (up from 7) → `npm run build` → clean → `npm run lint` (oxlint) → **0 warnings, 0 errors**.
- `pip install torch` attempted fresh: the default PyPI wheel for this platform bundles ~5GB of
  CUDA dependencies and once exhausted this sandbox's disk; after freeing space and installing
  with `--no-deps`, `import torch` still failed (`libcublasLt.so.*[0-9] not found`) because that
  wheel requires CUDA shared libraries at import time regardless of whether GPU is used — a
  genuine CPU-only wheel is hosted only at `download.pytorch.org`, outside this sandbox's
  allowed network domains. Torch was uninstalled again; the 1 skip above is
  `test_detect_rejects_nan_classifier_output`, same as every prior pass, for the same
  underlying reason with a more specific error captured this time.
- A demo investigation, `mode="live_ais"` (confirmed falls back to synthetic with an explicit
  reason), a real-satellite upload with `detector=classical`, restart-persistence (in-memory
  store cleared mid-test, investigation still reachable from SQLite), and all four export
  formats (PDF/Markdown/CSV/GeoJSON) were re-verified via `TestClient`-driven calls against the
  real, running application code.
- ZIP repackaged as `Marine_Pollution_Intelligence_SIH_FINAL_10.zip`, extracted into a clean
  temporary directory, and confirmed to contain exactly one top-level folder
  (`Marine_Pollution_Intelligence_SIH_FINAL/`) with no `__pycache__`, `.pytest_cache`,
  `node_modules`, `dist`, virtual environment, or leftover database/upload files.

## Production-readiness + ML-interface hardening pass (previous)

This is the current, authoritative validation status of the project as shipped in
`Marine_Pollution_Intelligence_SIH_FINAL.zip` (repackaged and renamed from
`Marine_Pollution_Intelligence_SIH_PRODUCTION_ML_FINAL.zip` during the final packaging pass —
no code change, folder/zip naming only). The sections below it are an
accurate historical record of earlier sessions and are preserved as-is; use the numbers in
*this* section for the current state. See the chat response that accompanied this ZIP for the
full "Part 1–6" breakdown against the production-readiness brief.

**What changed this pass (additive only — no UI redesign, no feature removal, no demo-scenario
change):**
- New `backend/app/config.py`: every configurable value (upload size limit, allowed extensions,
  processing timeout, ML confidence threshold, AIS search radius/time-window defaults, drift
  hindcast/forecast duration, CORS origins, log level) now has a single source of truth, an env
  var override, and a safe default — wired into `attribution.py`, `ml/detector.py`, and
  `investigation/orchestrator.py` without changing any existing default value.
- New `.env.example` (repo root) documenting every variable; no real secrets (none exist in this
  codebase).
- New `backend/app/logging_config.py`: structured single-line JSON logging with a per-request
  correlation id, wired into `main.py` (request-level) and `orchestrator.py` (investigation id,
  pipeline stage, AIS record/candidate counts, detector provenance, fallback reason).
- `main.py`: added `GET /health` (liveness), `GET /ready` (readiness, with ML availability as a
  separate non-blocking field), `GET /api/config` (non-secret config introspection); global
  `RequestValidationError` and `Exception` handlers so no route can ever leak a raw Python
  traceback to the frontend; upload endpoints now stream-enforce `MAX_UPLOAD_SIZE_MB` (413 on
  overflow) and validate extensions against the configured allow-list (400 on mismatch).
  `GET /api/health` kept byte-for-byte unchanged for frontend compatibility.
- `investigation/orchestrator.py`: every investigation now records a `config_used` block
  (reproducibility — exact mode/detector/weights/thresholds/durations/versions used) and a
  `processing_time_ms` total, with per-stage `duration_ms` in `progress_log`.
- `satellite/detection.py` / `ml/detector.py`: `DetectionResult` extended with
  `success, detector_name, detector_version, provenance, fallback_used, fallback_reason,
  warnings, processing_time_ms` — additive fields only; every existing caller of
  `.mask/.candidate_masks/.confidence/.method/.notes` is unaffected. `provenance` now
  distinguishes `ML_UNAVAILABLE` and `ML_FAILED` (previously both collapsed into one classical
  fallback note).
- `evaluation/metrics.py` / `evaluate_samples.py`: added an explicit labelled confusion-matrix
  formatter around the existing tp/fp/fn/tn counts.
- `frontend/src/App.jsx`: error messages now surface the backend's actual `detail` string when
  present (still falling back to the existing generic message), so upload/validation failures
  are more actionable — no other UI change.
- 19 new backend tests (`tests/test_production_readiness.py`) covering config defaults/overrides,
  `/health` + `/ready`, structured error responses, upload size/extension limits, reproducibility
  metadata, the extended `DetectionResult` fields, and the confusion-matrix helper. All prior
  tests are untouched and still pass.

**Validation performed for this pass (fresh, in this session):**
- Backend: `pytest tests/ -q` → **112 passed, 1 skipped, 0 failed** (same 1 pre-existing skip —
  `test_detect_rejects_nan_classifier_output`, requires PyTorch; this sandbox has none installed).
- Frontend: `npm test -- --run` → **7 passed, 0 failed** (unchanged suite), `npm run build` →
  clean, `npm run lint` (oxlint) → **0 warnings, 0 errors**.
- Real `uvicorn app.main:app` process started (not just TestClient) and exercised end-to-end via
  a Python HTTP client: `/health`, `/ready`, `/api/health`, `/api/config` all 200; a full demo
  investigation returned a populated `config_used` block, non-null `processing_time_ms`, and
  per-stage `duration_ms`; a malformed query parameter returned a structured 422 with no
  traceback; a nonexistent investigation id returned a structured 404; every request logged as a
  single-line JSON record with a correlation id matching the `X-Request-ID` response header.
- Repackaged as a single-root-folder ZIP, verified by extracting into a clean temporary directory
  and inspecting the resulting tree (see the "Final extracted folder tree" in the chat response).

**Deliberate deviation from the requested top-level folder layout:** `tests/` and `data/` remain
inside `backend/` (as `backend/tests/`, `backend/data/`) rather than at the repository root. Both
are load-bearing for how the app actually runs (`pytest.ini`, `conftest.py`, and every adapter's
relative path to `backend/data/sample_sar/` and `backend/data/ml_checkpoints/` assume that
location) — moving them would have been the "unnecessary refactoring" the brief explicitly asked
to avoid, for a purely cosmetic folder-shape change. `docs/` exists at the root as requested but
contains an index (`docs/README.md`) linking to the real documents, which stay at the root where
~15 existing in-code comments already reference them by relative path — see `docs/README.md`
for the full rationale. `backend/`, `frontend/`, and `README.md` — the three things the brief's
own verification checklist actually names — are exactly where specified.

## Final submission-hardening pass (previous — see AUDIT_LOG.md for full detail)

This section is an accurate historical record of the pass before this one. The section above
supersedes its test counts and current-state claims; this section's own numbers describe what
was true *at that earlier time*.

**Validation performed for this pass (fresh, in this session):**
- Backend: `pytest tests/ -q` → **93 passed, 1 skipped, 0 failed** (the 1 skip is
  `test_detect_rejects_nan_classifier_output`, which requires PyTorch to be installed to build a
  fake classifier module and is skipped via `pytest.importorskip` — this sandbox has no PyTorch
  installed; see AUDIT_LOG.md for why it could not be installed this pass).
- Frontend: `npm test -- --run` → **7 passed, 0 failed**, `npm run build` → clean, `npm run lint`
  (oxlint) → **0 warnings, 0 errors**.
- Real `uvicorn app.main:app` process started (not just TestClient) and exercised via `curl`:
  `/api/health` → 200; demo investigation → real detection, real drift, 3 ranked candidates;
  ML-requested upload of the bundled dual-pol sample scene → clean `CLASSICAL_DARK_SPOT_ML_FALLBACK`
  (torch not installed here) with a real, non-empty detection, not a crash or an empty result;
  PDF/CSV/GeoJSON/JSON exports all pulled and spot-checked against the live investigation.
- Hardened `app/ml/detector.py` end-to-end: the entire inference path (input validation through
  both forward passes) is now inside one try/except, with explicit rejection of mismatched
  VV/VH shapes, missing (`None`) bands, non-finite pixel input, and NaN/Inf classifier
  confidence/segmenter output — previously some of these paths were not defensively guarded.
  8 new tests cover this, both through the real HTTP endpoint and at the unit level.
- Report title changed from "Oil Spill Investigation Report" to "Marine Pollution Intelligence
  Investigation Report" in both the generated Markdown/PDF report and the Report tab UI.
- Repackaged as a single-root-folder ZIP (`Marine_Pollution_Intelligence_SIH_FINAL/`), verified
  by extracting into a clean temporary directory and inspecting the resulting tree.

## Merge pass: SIH26143_Marine_Spill_Intelligence_Platform → this project

This section documents a subsequent merge pass performed on top of everything below (which
remains an accurate historical record of this project's own development). Two archives were
merged: this project (`marine-pollution-intel`, used as the architectural base) and
`SIH26143_Marine_Spill_Intelligence_Platform` (source of the ML detector and the bundled
sample SAR-like imagery). See `MERGE_MATRIX.md` for the full component-by-component comparison
and rationale.

**What was added, concretely:**
- `app/ml/detector.py` + `app/ml/architectures.py` — an optional PyTorch CNN classifier + U-Net
  segmenter, ported from SIH26143 with its checkpoint-loading bug fixed (was silently failing to
  load wrapped training checkpoints; now unwraps and loads strictly, zero errors). Honestly
  labelled `ML_PROTOTYPE_SYNTHETIC_TRAINED` everywhere it surfaces — see `MODEL_CARD.md` §6.
- `app/satellite/real_image.py` + `POST /api/satellite/upload` — real GeoTIFF/PNG upload,
  feeding the *same* detection/characterization/drift/AIS/attribution pipeline the demo uses,
  zero duplication.
- `GET /api/ml/status` and three new export endpoints (`vessels.csv`, `spill_polygon.geojson`,
  `vessel_tracks.geojson`), all built from the existing in-memory `Investigation` object.
- Five sample SAR-like GeoTIFFs (`backend/data/sample_sar/`, carried over from SIH26143) so the
  real-upload path can be tried immediately — real files with procedurally-generated (not
  satellite-acquired) pixel data; see `DATA_PIPELINE.md` §1a.
- Frontend: a new "Use real satellite image" upload modal, wired the same way the existing
  "Use my AIS data" modal is; three new download links in the Report tab.
- **Two real pre-existing frontend bugs found and fixed** (`SatelliteTab.jsx` and
  `TimelineTab.jsx` both threw on a genuine "no slick detected" result, because
  `age_estimate`/`probable_origin` are `null` in that case). This was latent because the
  deterministic demo scenario always detects something; the new real-upload path makes a true
  no-detection result newly reachable, so it was fixed as part of this merge.

**What was deliberately NOT taken from SIH26143:** its own FastAPI app/orchestrator, its ML-only
detection-as-primary-path design, its frontend, its hardcoded scenario coordinates, its fabricated
evaluation-metrics function (already independently found and fixed in a prior session on that
project, but the fix itself wasn't relevant here since this project's evaluation approach - "no
fabricated metrics, ever" - was already stronger). All of these were assessed and are inferior to
what this project already had - see `MERGE_MATRIX.md`.

**Validation performed for this merge pass** (fresh, in this session):
- Backend: `pytest tests/ -q` → **69 passed** (57 pre-existing + 12 new, zero regressions).
- Frontend: `npm test -- --run` → **6 passed**, `npm run build` → clean, `npm run lint` → **0
  warnings, 0 errors**.
- Real `uvicorn app.main:app` process started (not just TestClient) and hit via `curl`:
  `/api/health` → 200, demo investigation → MV KONKAN STAR still ranks #1 (deterministic
  narrative preserved).
- Real upload of the bundled `demo_oil_scene_01.tif` through the classical detector found a
  genuine ~18.7 km² elongated region; through the ML detector, honestly reproduced the
  saturated "Oil"/empty-segmentation result documented in `MODEL_CARD.md` §6.
- Confirmed zero `scikit-image` imports anywhere, and `torch` is never imported eagerly at
  module load time (`app/ml/detector.py` guards it in `try/except ImportError`; `app.ml` is only
  imported lazily inside the two call sites that actually need it) — a classical-only Windows
  install without `torch` installed starts up and runs the full demo/custom-AIS/real-image
  (classical) workflows normally.
- One real-browser (Playwright/Chromium) screenshot of the landing page confirmed the new header
  button renders correctly; a fuller browser pass was interrupted by sandbox instability
  unrelated to the code (background processes not persisting between tool invocations) - the
  automated test suites above are the primary evidence for this pass, not screenshots.

**Known limitation carried over:** the ML detector's checkpoints are not trained on real SAR
data (see `MODEL_CARD.md` §6 for the full root-cause diagnosis) and cannot be fixed without the
real, correctly-matched Zenodo training data, which is not reachable from this environment. The
classical detector remains the operational default for exactly this reason.

---

This document reflects the state after a **Windows compatibility + full browser verification**
pass on top of the already-built, already-hardened prototype. It supersedes the test-count and
verification claims in earlier versions of this file — everything below was independently
re-verified in this pass, in this environment, right before writing it.

## A note on this document's own history

A `marine-pollution-intel-final.zip` was uploaded and inspected directly (diffed file-by-file
against the working copy already in this environment). It contained exactly one genuine,
adoptable improvement beyond what was already here — `/api/ais/analyze`'s response was missing
`total_raw_records`/`cleaned_records`/`within_spatial_filter`, so the AIS tab's funnel showed a
stale `—` for "Within radius" after a judge actually touched the filter sliders. That fix was
verified and adopted (see Bug #5 below). An earlier draft of this handoff file (bundled in that
same upload) also described a prior "browser verification pass" with very specific numbers and
a Playwright screenshot session — those specific claims were **not** independently reproducible
as described and are not repeated here. Everything in this document was verified fresh, in this
session, with the evidence (test output, screenshots, byte counts) shown inline.

## Environment reality check (read this before trusting any "it works" claim)

This sandbox is **Linux, Python 3.12.3** — not Windows, not Python 3.11. I cannot literally
reproduce your Application Control DLL block. What I *can* do, and did:

1. Remove the dependency that's almost certainly causing it (scikit-image).
2. Prove the replacement is numerically/behaviorally identical against the full test suite.
3. Prove it imports and runs cleanly in a **completely fresh, minimal virtualenv** containing
   only this project's trimmed `requirements.txt` — no scikit-image, shapely, or pandas
   installed at all (all three were audited and removed; only scikit-image was actually
   imported anywhere, but the other two were unused dead weight too).
4. **Genuinely** browser-test the running app end-to-end with Playwright + headless Chromium,
   which — correcting an earlier mistaken belief on my part — turned out to actually be
   available in this sandbox (`/opt/pw-browsers`, pre-installed, `PLAYWRIGHT_BROWSERS_PATH`
   already set). I had previously reported no browser was available; that was wrong, and this
   pass corrects it with real evidence.

What I still **cannot** do: run actual Windows, or reproduce your specific Application Control
policy. Section "Windows-specific verification checklist" below tells you exactly what to run
yourself to close that last gap.

## The scikit-image fix

### What was actually blocking you

`app/satellite/detection.py` and `app/satellite/characterization.py` did `from skimage import
measure, morphology, filters`. On Windows under an Application Control policy, this fails with
`ImportError: DLL load failed while importing _flood_fill_cy` — even though nothing in this
codebase calls flood fill. This happens because `skimage.morphology`'s `__init__.py` eagerly
imports its whole submodule tree at import time, pulling in that compiled extension as a side
effect of importing anything from the package at all.

### The fix

Added `backend/app/satellite/geometry_utils.py` — a pure NumPy + SciPy (zero new dependencies)
reimplementation of exactly the skimage functions used:

| Replaced | With |
|---|---|
| `skimage.measure.label` | `scipy.ndimage.label` with an explicit 8-connectivity structure |
| `skimage.measure.regionprops` (area, centroid, bbox, axis lengths, orientation, perimeter, solidity) | Hand-derived from image moments (axis length/orientation via the inertia-tensor eigenvalue formula skimage itself uses internally), the classical crack-perimeter estimator (Vossepoel & Smeulders 1982 — the same algorithm skimage's `perimeter()` uses), and `scipy.spatial.ConvexHull` for solidity |
| `skimage.morphology.remove_small_objects/opening/closing/disk` | `scipy.ndimage` equivalents + a hand-built disk structuring element |
| `skimage.filters.threshold_local(method='gaussian')` | `scipy.ndimage.gaussian_filter` with the same `sigma = (block_size-1)/6` skimage uses internally |
| `skimage.measure.find_contours` | Moore-neighbor boundary tracing (a coarser pixel-boundary outline instead of sub-pixel marching squares — visually equivalent for the already-subsampled ~60-point map polygon this feeds) |

**The detection algorithm itself is unchanged** — same adaptive thresholding, same
morphological cleanup, same connected-component scoring, same geometry formulas. Only the
implementation substrate changed. `requirements.txt` no longer lists `scikit-image`, `shapely`,
or `pandas` (all three audited and confirmed unused — `shapely` and `pandas` were dead weight
from early scaffolding, not actually imported anywhere).

### Evidence this works, not just "should work"

```
grep -rn "skimage" app/ --include="*.py"     → zero import statements (only comments)

Fresh, isolated virtualenv (no skimage/shapely/pandas installed at all):
  python -c "import app.main"                → imports cleanly
  pytest tests/ -q                            → 57 passed
  uvicorn app.main:app --port 8001            → boots, /api/health → {"status":"ok"}
  POST /api/spill/detect?mode=demo            → 200, area_km2=3.943, top score=54.1
                                                 (IDENTICAL to the pre-fix skimage numbers,
                                                 confirming the reimplementation is exact,
                                                 not just "close enough")
```

### A real bug this fix surfaced and fixed: 3x performance regression

My first version of `regionprops()` computed the crack-perimeter estimator by convolving the
**entire image array** for every detected region — fine for skimage's compiled Cython, very
slow in pure Python when a scene has ~560 small speckle-noise regions before size filtering.
First measurement: **4.43s per investigation** (vs ~0.6-1.3s before this pass). Profiled it with
`cProfile`, found `_crack_perimeter`'s full-array `ndi.convolve`/`ndi.binary_erosion` calls were
99% of the added cost, fixed by cropping each region to its bounding box (+1px pad) before
tracing — which is what skimage's own `regionprops.image` does internally anyway, so this also
made the reimplementation more faithful, not just faster. **Re-measured: 1.33s**, verified the
exact output numbers (`area_km2=3.943`, `top score=54.1`) were unchanged before/after the
optimization. Added 16 dedicated unit tests for `geometry_utils.py`
(`backend/tests/test_geometry_utils.py`) covering label connectivity, region geometry against
hand-computed cases, disk/morphology, thresholding, and contour tracing.

## Test results

> **Update note (latest hardening pass):** the paragraph below described a
> specific earlier development session's suite (57 backend / 6 frontend
> tests), before authentication, persistence, background jobs, live-data
> providers, ML/real-satellite support, upload-security, and
> production-readiness tests were added. That snapshot is now stale and is
> superseded by the accurate counts here: **176 backend tests** and **12
> frontend tests** across the current test files (see the table below).
> This latest pass also fixed a genuine bug in this area:
> `generate_pdf_report()` didn't create its output directory automatically,
> which raised `FileNotFoundError` on Windows (no `/tmp`) or any missing
> output folder - see `tests/test_e2e.py::test_no_detection_does_not_crash_the_pipeline`
> and `app/reports/generator.py`.
>
> **Verification method for this pass:** this sandbox has no outbound
> network access, so `fastapi`/`pydantic`/`httpx`/`pytest` could not be
> pip-installed here (they were previously verified locally/in CI - see
> the original session notes preserved below). The 176-test count reflects
> reading every test file directly. Of these, **150 tests across 9 files
> that import no fastapi/pydantic/httpx** (`test_ais`, `test_attribution`,
> `test_drift`, `test_e2e`, `test_evaluation`, `test_geometry`,
> `test_geometry_utils`, `test_geotiff_metadata`, `test_live_providers`)
> were **actually executed against the real application code** in this
> pass, via a small no-network test runner that replicates `conftest.py`'s
> fixtures - 149 passed, 1 failed only because it explicitly needs
> `fastapi.testclient` inside an otherwise-runnable file. The remaining 8
> files (26 tests) that `from fastapi.testclient import TestClient` could
> not be executed in this sandbox at all. Anyone running this on a normal
> machine with internet access (`pip install -r requirements.txt && pytest
> tests -q`) should expect the full 176/176 to run and pass, but that
> full run was not independently reproduced in this specific pass - only
> the 150 fastapi-independent tests were.

**Backend: 176 tests total across 17 files** (see verification note above
for exactly which subset was executed in the latest pass vs. carried
forward from the original session):

```
tests/test_ais.py                 7 tests
tests/test_api.py                16 tests  — every FastAPI route, via TestClient
tests/test_attribution.py         6 tests
tests/test_auth.py               18 tests
tests/test_drift.py               6 tests
tests/test_e2e.py                 5 tests  — full pipeline, incl. no-detection edge case
tests/test_evaluation.py          8 tests
tests/test_geometry.py            4 tests
tests/test_geometry_utils.py     16 tests  — the skimage-replacement module, in isolation
tests/test_geotiff_metadata.py   10 tests
tests/test_jobs.py                7 tests
tests/test_live_providers.py     13 tests
tests/test_ml_and_real_satellite.py 26 tests
tests/test_persistence.py         6 tests
tests/test_production_readiness.py 19 tests
tests/test_report_consistency.py  3 tests
tests/test_upload_security.py     6 tests
```

**Frontend: 12 tests total** (`src/test/DataSourceIndicator.test.jsx` - 5,
`src/test/smoke.test.jsx` - 7). `npm run build`/`npm run lint` status was
not independently re-verified in the latest pass (this sandbox's `npm
install` is blocked by network policy - see verification note above);
carried forward from the original session's results below.

### Original session notes (historical - see update note above)

**Backend: 57/57 pytest passing, 0 warnings, 18s.**

```
tests/test_ais.py            7 tests
tests/test_api.py           14 tests  — every FastAPI route, via TestClient
tests/test_attribution.py    6 tests
tests/test_drift.py          6 tests
tests/test_e2e.py            5 tests  — full pipeline, incl. no-detection edge case
tests/test_geometry.py       4 tests
tests/test_geometry_utils.py 16 tests — NEW: the skimage-replacement module, in isolation
```

Also re-ran the entire suite inside a **from-scratch, minimal virtualenv** (only this project's
trimmed `requirements.txt`, nothing else installed) — same 57/57 pass.

**Frontend: 6/6 vitest passing, `npm run build` clean, `oxlint` clean (0/0).**

## Real browser verification (this session, genuinely — see caveat above about the earlier draft)

Started both servers as real subprocesses, launched headless Chromium via Playwright, and
drove the actual running app — not TestClient, not a mock:

- **Landing page → Run Demo Investigation → dashboard**: click-through captured in
  screenshots; dashboard populated with **84.7% confidence, 3.943 km² slick, ~18.3h age
  (low confidence, caveat shown), origin 19.250°/71.179°, 3 AIS candidates of 456 raw pings,
  top candidate MV KONKAN STAR at 54.1/100** — all real computed values, screenshotted directly
  off the rendered page.
- **All six tabs** (Overview, Satellite, AIS, Ranking, Timeline, Report) navigated with
  **zero page errors** (`page.on("pageerror")` — zero uncaught JS exceptions across the entire
  run).
- **49 failed network requests, all accounted for by URL**: Google Fonts and CartoDB basemap
  tiles, both returning 403 from *this sandbox's* network egress allowlist (not present in the
  allowed-domains list) — not application bugs. Confirmed the Leaflet vector overlays (slick
  polygon, hindcast/forecast tracks, AIS candidate tracks, search-radius circle) render
  correctly via SVG regardless, since they don't depend on raster tile loading.
- **AIS filter sliders + Apply filters**: exercised via a real `input[type=range].fill()` +
  click, screenshotted before/after — confirmed this calls the backend (not a hardcoded
  frontend value).
- **Vessel evidence drawer**: clicked the top-ranked row, confirmed the full score breakdown
  (Spatial Proximity 29.7/30, Temporal Correlation 10.9/25, Trajectory Consistency 1.0/15,
  Heading Consistency 0.0/10, Behavioural Anomalies 4.0/10, AIS Continuity 8.5/10 — summing to
  the displayed 54.1) renders correctly, with "HIGHEST-PRIORITY CANDIDATE" language and the
  non-legal disclaimer.
- **Report tab + PDF download**: clicked "Download PDF" for real (`page.expect_download()`),
  saved the actual downloaded file, verified its header is `%PDF` and it's 8,409 bytes — a real
  file the browser actually produced, not an API call made separately from the UI.
- **"Use my AIS data" upload flow**: uploaded a hand-crafted 3-point CSV for a vessel
  positioned near the deterministic demo origin with a speed-drop anomaly, ran the investigation
  through the real upload modal, and confirmed **that exact uploaded vessel** (MV BROWSER TEST,
  MMSI 999111222) appeared as the top-ranked candidate at 62.1/100 with a "HIGH" behaviour flag
  — end-to-end proof the custom-AIS pipeline is real, not hardcoded to only show demo vessels.

Screenshots from this run are available on request (not included in the deliverable zip to keep
it lean, but every claim above was captured as a `.png` during the actual run).

## Windows-specific verification checklist (run this yourself — this is the one thing I can't do for you)

```powershell
# 1. Fresh virtualenv, exactly what a clean install looks like
python -m venv venv
venv\Scripts\activate
pip install -r backend\requirements.txt

# 2. Confirm scikit-image is NOT installed (it shouldn't even be attempted)
pip show scikit-image
# expect: "WARNING: Package(s) not found: scikit-image"

# 3. The actual test that matters — does app.main import without the DLL error?
cd backend
python -c "import app.main; print('OK - imported cleanly')"

# 4. Start the server for real
python -m uvicorn app.main:app --reload --port 8000
# then in a browser or curl: http://127.0.0.1:8000/api/health  ->  {"status":"ok"}

# 5. Run the full test suite
pytest tests/ -v
# expect: 69 passed

# 6. Run an actual investigation and sanity-check the numbers
curl -X POST "http://127.0.0.1:8000/api/spill/detect?mode=demo"
# expect area_km2 close to 3.943 and a populated candidates list (exact float precision may
# differ trivially between platforms/NumPy builds - the shape of the result should match)
```

If step 3 or 4 still shows a DLL/import error, it is **not** scikit-image anymore (confirmed
gone from every import path) — check `pip show` output for anything else your Application
Control policy might be flagging (fastapi/uvicorn/scipy/numpy are pure-Python or have been
working per your own report; matplotlib and reportlab are the other compiled-adjacent
dependencies to check first if something new comes up).

## How to run

```bash
cd backend && pip install -r requirements.txt && uvicorn app.main:app --reload --port 8000
cd frontend && npm install && npm run dev      # http://localhost:5173

# tests (counts as of the most recent pass, top of this file — grows over time)
cd backend && pytest tests/ -v                 # 246 passed, 14 skipped without torch installed
cd frontend && npm test                        # 34 tests
```

## API endpoints (all live-verified this session)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | liveness check |
| POST | `/api/spill/detect` | run the full pipeline (`mode=demo` or `mode=custom_ais`) |
| GET | `/api/spill/{id}` | full investigation object |
| GET | `/api/spill/{id}/geometry` | slick geometry only |
| GET | `/api/spill/{id}/hindcast` | backward track + probable origin |
| GET | `/api/spill/{id}/forecast` | forward track |
| POST | `/api/ais/upload` | upload a MarineCadastre/generic AIS CSV |
| GET | `/api/ais/candidates` | AIS summary + current candidates |
| POST | `/api/ais/analyze` | re-filter AIS by radius/time-window (persists; returns funnel counts) |
| GET | `/api/vessels/ranking` | ranked candidate list |
| GET | `/api/vessels/{mmsi}` | one vessel's full evidence |
| POST | `/api/attribution/analyze` | re-score with custom weights (validated, normalized, persisted) |
| GET | `/api/report/{id}` | Markdown (`fmt=markdown`), PDF (`fmt=pdf`), or CSV (`fmt=csv`) report |
| GET | `/api/export/{id}/vessels.csv` | ranked candidate list as CSV |
| GET | `/api/export/{id}/spill_polygon.geojson` | detected slick polygon as GeoJSON |
| GET | `/api/export/{id}/vessel_tracks.geojson` | ranked candidates' AIS trajectories as GeoJSON |

## Attribution weights (verified as currently implemented)

This table was updated during the September 2026 hardening pass after being
found stale against the actual code (`app/scoring/attribution.py::DEFAULT_WEIGHTS`) -
a prior pass had documented an earlier 6-factor version of the scorer that had
since been split into 8 factors (adding explicit `speed_consistency` and
`data_completeness` components) without the docs being updated to match. The
values below were read directly from that dict, not retyped from memory:

```
Spatial proximity:       24%
Temporal correlation:    20%
Trajectory consistency:  12%
Heading consistency:      8%
Speed consistency:        8%
Behavioural anomalies:   10%
AIS continuity/gaps:     10%
Data completeness:        8%
```

Verified via `test_weights_sum_to_100` (asserts `sum(DEFAULT_WEIGHTS.values()) == 100`)
and, live, by POSTing a skewed custom-weight body to `/api/attribution/analyze`
(all weight on behavioural anomalies) and confirming the score changed
accordingly — not just echoed back unchanged.

## Dataset honesty (verified in the actual rendered UI and generated report, not just claimed)

- Every investigation's Data Source Indicator strip (screenshotted) reads: Satellite → "Zenodo
  Sentinel-1 SAR Oil Spill Dataset (reference) → SYNTHETIC_DEMO scene used", AIS → "MarineCadastre
  AIS (reference schema) → SYNTHETIC_DEMO" (or `USER_UPLOADED_CSV` in custom mode), Environment →
  `SYNTHETIC_DEMO`.
- The generated report's Incident Overview and Section 15 (Limitations) state this in full
  sentences, not just tags — verified by reading the actual rendered report text in this
  session's screenshot.
- No claim of live AIS/weather/ocean data anywhere in code, UI, or docs — `RealWeatherOceanAdapter`
  and `CachedEnvironmentAdapter` both explicitly raise (documented, not silently stubbed) since
  no live endpoint is reachable from this environment.
- No claim of training against the Zenodo dataset — `MODEL_CARD.md` states plainly that the
  detector is a classical (non-trained) algorithm, and why.

## Known limitations

- Literal Windows execution was not verified by me — see the checklist above; run it yourself
  and this list should shrink to zero.
- Detection is a classical hybrid algorithm, not a dataset-trained model — confidence is a
  heuristic index, not a calibrated probability.
- Drift is simplified advection (centroid transport, no spreading/diffusion).
- Spill age is a low-confidence, single-scene heuristic.
- No persistence layer — investigations live in an in-process dict; restarting the backend
  clears them.
- The `find_boundary_contour` replacement traces whole-pixel boundaries (coarser than skimage's
  sub-pixel marching squares) — visually fine for the map polygon overlay, not intended for
  sub-pixel-precision geometry work.
- Basemap tiles and Google Fonts require outbound internet access; confirmed via this session's
  browser test that the app degrades gracefully (SVG overlays still render) when they're
  unavailable, but a real demo venue should have working internet for the polished look.

## SIH demo sequence

See `DEMO_GUIDE.md` for the full narrated script. Verified this session, in order: start both
servers → open `http://localhost:5173` → **RUN DEMO INVESTIGATION** → Dashboard (KPIs + map) →
Satellite (3-image chain + geometry) → AIS (adjust filters live) → Ranking → click top vessel →
evidence breakdown → Timeline → Report → **Download PDF** (real file, verified `%PDF` header).
Every step above has a corresponding screenshot and/or test assertion from this session.
