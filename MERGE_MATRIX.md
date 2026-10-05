# MERGE MATRIX — SIH26143_Marine_Spill_Intelligence_Platform ⨯ marine-pollution-intel

Both archives were fully extracted and inspected file-by-file before any code was written.
This is the resulting component-by-component decision, based on reading and testing the actual
implementations, not filenames or dates.

| Component | Best source | Reason (verified, not assumed) |
|---|---|---|
| Backend orchestrator/architecture | **marine-pollution-intel** | Clean modular pipeline matching the spec's stage diagram exactly (satellite → detection → geometry → age → drift → AIS → attribution → report); graceful "no detection" handling throughout; in-memory investigation store with re-analysis endpoints. SIH26143's `main.py` mixed all stages together with hardcoded coordinates. |
| Classical SAR detection | **marine-pollution-intel** | Literature-grounded dark-spot detector (adaptive threshold + morphology + shape/texture plausibility filter). SIH26143 had no classical fallback at all — only a crude single-value VV threshold buried inside its ML module. |
| Geometry utilities | **marine-pollution-intel** | Pure NumPy/SciPy, explicitly engineered to avoid the scikit-image Windows Application-Control DLL issue (documented in-file); 17 passing geometry tests. SIH26143 used OpenCV directly with no Windows-compatibility consideration. |
| SAR preprocessing (Lee filter) | **marine-pollution-intel** | Textbook coefficient-of-variation adaptive Lee filter (Lee 1980). SIH26143's was a simpler box mean/variance approximation. |
| Real geotransform | **marine-pollution-intel** | Proper per-pixel affine `SceneGeoTransform`. SIH26143 used a fixed-anchor approximation. |
| Age estimation | **marine-pollution-intel** | Does not exist in SIH26143 at all. |
| Drift/hindcast physics | **marine-pollution-intel** | True multi-step Euler integration with environment resampled at each step, growing uncertainty radius over time. SIH26143 used a single-step calculation with static environmental values regardless of position/time. |
| Environment data fallback | **marine-pollution-intel** | Genuine `REAL → CACHED → SYNTHETIC` adapter chain, ready for real endpoints. SIH26143 had hardcoded default values only. |
| AIS ingestion (real CSV) | **marine-pollution-intel** | `MarineCadastreAISAdapter` + generic-CSV adapter against the real, documented MarineCadastre schema. SIH26143 had zero real AIS ingestion — purely synthetic, no upload path. |
| AIS demo scenario | **marine-pollution-intel** | The MV KONKAN STAR narrative, with 12 background vessels independently verified (by test) to be filtered out by real spatial/temporal logic, not hand-picked. |
| Behavioural analysis | **marine-pollution-intel** | More rule types (loitering, speed increase, AIS gaps, trajectory-through-origin), each with a severity tier. |
| Attribution/vessel scoring | **marine-pollution-intel** | Six independently-computed, real fractional components with a full audit-trail breakdown. SIH26143 used a coarse two-bucket distance heuristic for its "temporal" and "trajectory" scores. |
| Report generation | **marine-pollution-intel** | Real PDF (reportlab) + Markdown, both built from the live investigation object. SIH26143 only had client-side JSON export and browser print. |
| Image visualization | **marine-pollution-intel** | Real base64 PNG rendering of the actual scene and detection overlay. SIH26143's frontend used static gradient placeholders captioned "schematic, not real imagery" because no real rendering existed. |
| ML classifier/segmenter | **SIH26143, relabeled** | The only project with a genuine (if ultimately synthetic-trained) PyTorch model. Its checkpoint-loading bug was fixed as part of this merge; every output is now honestly tagged `ML_PROTOTYPE_SYNTHETIC_TRAINED` and offered only as an explicit opt-in, never the default. See `MODEL_CARD.md` §6. |
| Bundled sample SAR imagery | **SIH26143** | Real files (not synthesized at request time) with procedurally-generated, not satellite-acquired, dual-polarization pixel data and ground-truth masks — see `DATA_PIPELINE.md` §1a for the exact provenance wording. marine-pollution-intel only synthesizes a scene at request time, with no bundled example files at all. |
| Frontend | **marine-pollution-intel** | Already fully wired and tested (6 vitest smoke tests, oxlint clean) against this exact backend contract. Rebuilding SIH26143's frontend against a different API contract would have violated "don't rebuild the UI unnecessarily" and discarded working, tested code for no benefit. |
| Tests | **Merged** | marine-pollution-intel's 57 tests kept as the base; 12 new tests added for the ML module and real-image upload endpoints (checkpoint loading, 404/400 validation, provenance labelling, fallback behaviour, CSV/GeoJSON export correctness). |
| Documentation | **marine-pollution-intel base + new sections** | Its `MODEL_CARD.md`/`DATA_PIPELINE.md`/`ARCHITECTURE.md`/`FINAL_HANDOFF.md` were already extremely thorough and honest; extended with new sections for the ML detector and real-image path rather than rewritten. |
| Windows compatibility | **marine-pollution-intel** | Explicitly engineered and documented for it (see its own `FINAL_HANDOFF.md` history). SIH26143 never addressed Windows compatibility; this merge preserves that work untouched (zero scikit-image, no eager torch import). |

## What was explicitly rejected from SIH26143

- Its FastAPI app/orchestrator (superseded entirely).
- Its ML-first architecture (the fact that its ONLY detector was a synthetic-trained ML model,
  with no classical fallback, was itself a design weakness this merge corrects).
- Its frontend (superseded entirely; see rationale above).
- Its hardcoded per-scenario coordinates and the resulting bug where every scenario returned
  near-identical analysis — this exact bug class does not exist in marine-pollution-intel's
  design (the demo scenario is deliberately deterministic *by design*, not by accident, and is
  independently tested to differ from custom-AIS and real-image runs).
- Its `run_evaluation_on_part3()` fabricated-metrics function — not merged in any form.
  marine-pollution-intel's policy of reporting no metric it cannot honestly compute is the
  stronger, adopted standard.
