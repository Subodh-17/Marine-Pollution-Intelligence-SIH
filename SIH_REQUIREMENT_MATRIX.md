# SIH26143 — Requirement Traceability Matrix

Compiled during the release-candidate audit by re-running the actual pipeline end-to-end
(`pytest`, live `uvicorn`, direct API calls) rather than by re-reading prior summaries. Every
"verification method" line below was actually executed during this audit pass.

| # | SIH requirement | Implemented component | Actual verification method (this audit) | Current limitation |
|---|---|---|---|---|
| A | Oil-spill detection | `app/satellite/detection.py` (classical dark-spot + morphology), optional `app/ml/detector.py` (CNN, opt-in) | Ran `POST /api/satellite/upload` against 3 bundled sample GeoTIFFs; classical detector returned a real, plausible 18.7 km² elongated region on the oil scene. `pytest tests/test_ml_and_real_satellite.py` passing. | Classical method is a heuristic, not a trained model, by design (see Requirement C limitation below). ML path exists but is not a validated detector (see row under ML status). |
| B | Spill characterisation (geometry) | `app/satellite/characterization.py` (area, length, width, orientation, perimeter, solidity via NumPy/SciPy — no scikit-image) | Verified computed geometry (area_km2, aspect_ratio, orientation) on both synthetic demo and real-upload paths; values are internally consistent between the JSON API, PDF report, and GeoJSON export for the same investigation. | Geometry is 2D planform only; no sub-slick thickness/volume estimate (not physically derivable from single-pol amplitude SAR alone). |
| C | Age estimation | `app/satellite/age.py` (aspect-ratio + wind heuristic) | Confirmed the "low confidence" label and caveat text render on Dashboard, Satellite tab, and PDF report; confirmed the UI does not crash when age is `unavailable` (no-detection case). | Single-scene age estimation has no ground-truth validation anywhere (none is available); explicitly labelled low-confidence, not a calibrated estimate. |
| D | Oceanographic/meteorological data | `app/environment/` adapters (Real → Cached → Synthetic fallback chain) | Confirmed every environment block in the API response is tagged `SYNTHETIC_DEMO` when no live feed is reachable (this sandbox has no NOAA/Copernicus egress); Real/Cached tiers are implemented but unexercised here. | Only the Synthetic tier is actually reachable/tested in this environment; Real/Cached tiers are implemented but not live-verified against an actual external feed. |
| E | Hindcast | `app/drift/model.py` (current + 0.03×wind leeway advection, backward integration) | Verified hindcast track values in the PDF report and JSON response for the demo investigation are identical (same origin lat/lon/time, same uncertainty-radius growth). | Centroid-only advection; no spreading/diffusion term, no Stokes drift as a separate term (documented in MODEL_CARD.md). |
| F | Origin estimation | Same drift module; backward-propagates to the point of "observed − hindcast horizon" | Verified `probable_origin` + `origin_confidence` ("low", heuristic) appear consistently across Dashboard, map, and PDF for the same investigation ID. | Confidence is qualitative ("low"/"medium"/"high"), explicitly not a calibrated probability; shrinks with hindcast horizon by design, not by fit to data. |
| G | Forecast | Same drift module, forward integration | Verified forward-track points (T+6h/12h/24h/48h) present and internally consistent in JSON/PDF. | Same centroid-only limitation as hindcast (row E). |
| H | AIS reconstruction | `app/ais/adapters.py` (MarineCadastre schema + generic CSV + synthetic fleet), `app/ais/pipeline.py` | Uploaded a hand-built custom AIS CSV via `/api/ais/upload` → `/api/spill/detect?mode=custom_ais`; confirmed parsing, normalization, and trajectory reconstruction against known input coordinates/timestamps. | Only CSV upload and the synthetic fleet are exercised; no live MarineCadastre feed is reachable from this sandbox. |
| I | Irrelevant traffic filtering | `app/ais/pipeline.py` spatial (radius_km) + temporal (window_hours) filters | In the custom-AIS test, a vessel placed >100 km away and a vessel with timestamps 24h+ outside the investigation window were both correctly excluded, each with a specific, calculated reason (distance in km / time delta in hours) rather than a generic message. | Fixed radius/window heuristics (not adaptively tuned per scenario). |
| J | Vessel attribution | `app/scoring/attribution.py` (8-component weighted scoring, weights sum to 100, `assert`-enforced) | Verified for the top demo candidate that all 8 component contributions sum exactly to the reported `final_score`; checked a lower-ranked candidate's breakdown too. Also verified `distance_from_detection_km` (distance to the raw SAR-observed slick location) is computed independently from `min_distance_km` (distance to the hindcast-estimated origin) and differs from it in the demo data, confirming the two are genuinely distinct spatial factors rather than duplicates. | Deterministic rule-based scoring, not a learned classifier; not validated against a labelled "true responsible vessel" dataset (none can exist for a synthetic scenario by construction). The `speed_consistency` factor treats slow/stationary speed near closest approach as a *weak, explicitly non-conclusive* correlate — it is not, and must never be read as, evidence of intent. |
| K | Explainable scoring | `breakdown` field in the API response; rendered in `VesselDetailDrawer.jsx`; plus `distance_from_detection_km`, `data_completeness_pct`, `data_warnings`, and `confidence_level` fields (added this pass, see entry below) | Confirmed clicking a ranked candidate opens the evidence panel with the same 8 named components and their point contributions seen in the raw API response, plus the trajectory-summary table showing distance-from-origin *and* distance-from-detection as separate rows, a data-completeness percentage, and a "data confidence" badge distinct from the numeric score. | `confidence_level` ("low"/"medium"/"high") is a heuristic reflecting how much real AIS data backed the score — explicitly not a probability of vessel involvement; documented as such in the UI caption. |
| L | Visual interface | React/Vite frontend, Leaflet map, 5 investigation tabs | `npm test -- --run` → 12/12 passed; `npm run build` → clean; `npm run lint` (oxlint) → 0 warnings/errors; confirmed basemap-tile-failure fallback banner in `InteractiveMap.jsx` (now backed by public OSM + Wikimedia providers with automatic failover, no API key, no Carto dependency) and confirmed vector overlays (slick, tracks, radius) render independently of raster tiles. | Basemap requires outbound tile-server access to look fully finished; falls back gracefully but without a raster background when offline. |
| M | Exports/reporting | `app/reports/generator.py` (Markdown + PDF via reportlab), CSV/GeoJSON export endpoints | Pulled PDF (`?fmt=pdf` → valid 4-page PDF), CSV, spill GeoJSON, and vessel-tracks GeoJSON for one completed investigation; cross-checked the PDF's top-candidate score/MMSI/distances against the raw JSON — identical. Confirmed report wording is detector-aware (classical = heuristic score, ML = uncalibrated prototype softmax score, fallback = heuristic classical score + explicit fallback reason). | None found; one stale caption bug (AIS `reference_dataset` field) was found and fixed during a prior audit pass (see AUDIT_LOG.md). |
| N | Real GeoTIFF georeferencing | `app/satellite/geotiff_metadata.py` (new this pass) | 10 tests covering a valid geographic-CRS file, missing CRS, missing transform, a corrupt GeoKeyDirectoryTag, and an unsupported projected CRS, plus 3 `real_image.py` integration tests confirming `geolocation_source`/`geolocation_is_assumed` are set correctly end to end. All passing. | Single-tiepoint files only (no rubber-sheeted/multi-tiepoint GeoTIFFs); a real embedded PROJECTED CRS is detected and reported but not reprojected (no pyproj dependency) — falls back to the assumed-coordinate path rather than mis-projecting. |
| O | Database persistence | `app/persistence/db.py` (new this pass, SQLite) | 6 tests, including one that runs an investigation through the real FastAPI `TestClient`, clears the in-memory store, and confirms every read/report/export route still works from the database alone. All passing. | SQLite only; no PostgreSQL client dependency was added, so the "optional PostgreSQL for future deployment" path is undemonstrated (by design — see AUDIT_LOG.md). |
| P | Authentication/authorization | `app/auth.py` (new this pass) | 18 tests, including registering two users and confirming one gets a 404 (not 403) for the other's investigation. All passing. `AUTH_ENABLED=false` by default; verified all 172 other backend tests still pass unchanged with auth disabled. | No frontend login screen was built this pass (API-only); PBKDF2/HMAC scheme, not OAuth/OIDC — appropriate for a local prototype per the request's own framing, not for a production IdP. |
| Q | Background job processing | `app/persistence/jobs.py` (new this pass, in-process `ThreadPoolExecutor`) | 7 tests, including a real end-to-end job submitted via the API and polled to completion, and a restart-simulation test showing a job correctly reports `"interrupted"` (not falsely `"running"`) after the in-memory cache is cleared. All passing. | Cancellation is best-effort (cannot interrupt a thread already executing Python code — documented in the module, not worked around unsafely). |
| R | Live AIS / live environmental data | `app/ais/live_provider.py`, `app/environment/adapters.py::RealWeatherOceanAdapter` (both new/rewritten this pass) | 13 tests against a mocked HTTP layer covering success, missing-field rejection, retry-then-fail, and (for AIS) timestamp normalization/de-duplication/invalid-coordinate filtering/AIS "not available" sentinel filtering. All passing. `mode="live_ais"` verified end-to-end via the API to fall back to the synthetic fleet with an explicit reason when unconfigured. | **Not validated against any real provider** — this sandbox's network egress does not reach any AIS/weather API. Unset (`*_LIVE_PROVIDER_URL` empty) by default; see AUDIT_LOG.md for exactly what wiring a specific real provider would still require. |
| S | Upload security hardening | `app/main.py` (`_check_image_dimensions`, `upload_ais` temp-file-leak fix) | 6 new tests: path-traversal filenames (satellite + AIS), oversized image dimensions rejected, image within limit accepted, previous-upload temp file actually deleted on re-upload, malformed TIFF returns 422 not 500. All passing. | Disk-usage-quota protection (a global cap on total temp-upload bytes across all in-flight uploads) was not implemented this pass — only per-file size/dimension caps. |

**Overall test evidence backing this table (independently re-verified during the "remove
remaining limitations" upgrade pass — see AUDIT_LOG.md's final entry):**
`pytest tests -q` → **175 passed, 1 skipped, 0 failed** (backend; up from 112 at the start
of that pass — the 1 skip was `test_detect_rejects_nan_classifier_output`, requiring
PyTorch, which could not be made to import in this sandbox even after a fresh install
attempt — see AUDIT_LOG.md for the specific `libcublasLt.so` error, not just "no disk
space" this time); `npm test -- --run` → **12 passed, 0 failed** (frontend; up from 7);
`npm run build` clean; `npm run lint` → 0 warnings/errors. All numbers above were
reproduced by directly running the test suites and driving the real API via
`TestClient` during that pass, not carried over from an earlier summary — a "capsule
context" supplied at the start of that pass claimed files and a test count (123 passed)
that did not match the actual uploaded ZIP, and was explicitly not trusted before this
independent re-verification.

**Final packaging pass — torch genuinely installed and exercised (see AUDIT_LOG.md and
MODEL_CARD.md for full detail):** this pass found a way to get a real, working PyTorch install
in the audit sandbox (installing CUDA runtime deps first, then `torch` itself with `--no-deps`
against those already-present libraries) where every earlier pass had failed. With torch
genuinely importable: `pytest tests -q` → **176 passed, 0 skipped, 0 failed** (the previously
torch-gated skip now runs and passes); a live `uvicorn` process's `GET /api/ml/status` confirmed
`torch_available: true` with both checkpoints loaded and real SHA-256 checksums; and a real
`detector=ml` upload against the bundled dual-pol sample genuinely ran both PyTorch forward
passes (classifier: "Oil" at 100% confidence; segmenter: 0.00% mask coverage) and correctly
triggered the classical fallback, matching this document's own prior static-analysis prediction
of the checkpoints' behavior. The classical-only (no-torch) path was also independently
re-verified in this pass: `pytest tests -q` → 175 passed, 1 skipped, 0 failed, confirming both
supported install configurations work. Frontend was independently re-run fresh this pass:
`npm install` (185 packages) → `npm test -- --run` → **12 passed** → `npm run build` clean →
`npm run lint` → 0 warnings/errors. Additional live-server checks performed this pass and not
recorded in earlier entries above: all 5 export formats (PDF, Markdown/JSON, vessel CSV, spill
GeoJSON, vessel-track GeoJSON) pulled for one investigation and cross-checked; a genuine
cross-process restart (server killed and restarted, not just an in-memory-dict-cleared unit
test) confirmed the investigation and its report remain reachable via SQLite; empty/malformed
AIS CSVs correctly return 400 once actually processed via `mode=custom_ais` (200 is expected,
and correct, at the raw `/api/ais/upload` storage step itself); disallowed extensions, corrupt
TIFFs, empty files, and an unknown investigation id all returned the correct 4xx/404, not a 500.

**Explainable-attribution completeness pass (this session):** independently re-inspected the
codebase and ran the full test suites *before* changing anything (baseline: 175 passed/1
skipped backend, 12 passed frontend, clean build/lint — matching the numbers already recorded
above). Compared the SIH requirement text ("distance from spill detection location", "speed
consistency", "data completeness", "confidence level", "missing-data warnings" as explainable
ranking factors) against the actual code and found two genuine gaps: (1) the scorer only
measured distance/time against the hindcast-estimated *origin*, never against the raw
satellite-*detected* location, even though the requirement asks for both as separate factors;
(2) `speed_consistency` and `data_completeness` were not separate, named, weighted factors, and
`confidence_level`/`data_warnings` did not exist at the candidate level. Implemented all five as
additive, backward-compatible changes: `reconstruct_trajectories()` gained optional
`detection_lat`/`detection_lon` parameters (`None` by default — existing 2-arg callers are
unaffected); `DEFAULT_WEIGHTS` grew from 6 to 8 components (rebalanced, still asserted to sum to
100); `VesselCandidate` gained `distance_from_detection_km`, `time_delta_from_detection_hours`,
`data_completeness_pct`, `data_warnings`, and `confidence_level` fields. Wired the new detection
coordinates through `investigation/orchestrator.py` and both `/api/ais/analyze` and
`/api/attribution/analyze` endpoints. Verified end-to-end against a live demo investigation that
`distance_from_detection_km` (22.5 km) is genuinely different from `min_distance_km` (0.52 km)
for the top candidate — confirming origin and detection location are treated as the distinct
points the SIH text calls for, not silently aliased. Added 9 new backend tests (7 in
`test_attribution.py`, 2 in `test_ais.py`) plus one new end-to-end API test covering both
re-scoring endpoints, and one new report-content test; updated one pre-existing test
(`test_attribution_analyze_actually_uses_custom_weights`) whose "100% on one component"
assertion needed the two new components explicitly zeroed once they existed (the underlying
feature it tests — custom weights genuinely changing the score — still passes; only the fixture
input needed extending). Updated `app/reports/generator.py` (Markdown + PDF ranking table) to
surface the new fields, and `frontend/src/components/VesselDetailDrawer.jsx` (the score
breakdown table already renders new weighted components generically with no code change) to add
explicit rows for distance-from-detection, data completeness, and a "data confidence" badge with
a caption clarifying it reflects data quality, not likelihood of guilt. Regenerated
`frontend/src/test/fixtures/investigation.json`'s `candidates` array by feeding its own recorded
AIS trajectory points through the real, updated backend scorer (not hand-edited), so the fixture
stays a genuine captured-shape example rather than synthetic filler; added one new frontend test
asserting the new fields render. Final verification after all changes: `pytest tests -q` →
**186 passed, 1 skipped, 0 failed** (backend); `npm test -- --run` → **13 passed, 0 failed**
(frontend); `npm run build` clean; `npm run lint` → 0 warnings/errors.
