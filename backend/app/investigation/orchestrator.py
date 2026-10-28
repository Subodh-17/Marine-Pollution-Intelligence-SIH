"""
Investigation orchestrator - the seam that wires together every pipeline
stage described in Section 4 of the SIH brief:

  SAR imagery -> preprocessing -> detection -> segmentation ->
  characterisation -> environment conditions -> hindcast -> origin ->
  forecast -> AIS load -> spatial/temporal filter -> trajectory
  reconstruction -> behaviour analysis -> attribution scoring -> ranking

This is intentionally the ONLY module that "knows about" every other
module; each pipeline stage module (satellite/, drift/, ais/, scoring/)
has no dependency on this orchestrator, so they can be tested and reused
independently (see backend/tests/).
"""
from __future__ import annotations

import math
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

import numpy as np

from app.satellite.synthetic import generate_synthetic_sar_scene
from app.satellite.real_image import load_real_scene
from app.satellite.preprocessing import lee_filter, normalize, simple_sea_mask
from app.satellite.detection import detect_oil_slick
from app.satellite.characterization import characterize_slick
from app.satellite.age import estimate_age
from app.satellite.visualization import render_grayscale, render_detection_overlay
from app.environment.adapters import resolve_environment_conditions
from app.drift.model import run_drift
from app.ais.pipeline import clean_records, spatial_filter, temporal_filter, reconstruct_trajectories, summarize_exclusions
from app.ais.adapters import GenericCSVAISAdapter
from app.ais.live_provider import resolve_ais_records
from app.demo.scenario import build_demo_ais_fleet
from app.scoring.attribution import rank_candidates, DEFAULT_WEIGHTS, SEARCH_RADIUS_KM_DEFAULT, TIME_WINDOW_HOURS_DEFAULT
from datetime import timedelta
from app.config import get_settings
from app.logging_config import get_logger, StageTimer
from app.persistence import db as _db

_log = get_logger("orchestrator")

import threading
from collections import OrderedDict

# Fixed observation time for the deterministic demo scenario. Kept as a
# constant (not "now") so the whole pipeline - including the synthetic
# environment field - is exactly reproducible run to run.
DEMO_OBSERVATION_TIME = datetime(2026, 8, 28, 6, 0, 0, tzinfo=timezone.utc).replace(tzinfo=None)


class _BoundedInvestigationStore(OrderedDict):
    """Thread-safe LRU in-memory store for investigations to prevent unbounded memory
    growth on memory-constrained production platforms (e.g. 512MB RAM containers).
    Older entries are safely evicted from memory because they are durably persisted
    in the database and rehydrated on-demand via get_investigation()."""

    def __init__(self, max_entries: int = 100):
        super().__init__()
        self.max_entries = max_entries
        self._lock = threading.Lock()

    def __setitem__(self, key, value):
        with self._lock:
            super().__setitem__(key, value)
            self.move_to_end(key)
            while len(self) > self.max_entries:
                self.popitem(last=False)

    def __getitem__(self, key):
        with self._lock:
            val = super().__getitem__(key)
            self.move_to_end(key)
            return val

    def get(self, key, default=None):
        with self._lock:
            if key in self:
                self.move_to_end(key)
                return super().__getitem__(key)
            return default

    def values(self):
        with self._lock:
            return list(super().values())

    def items(self):
        with self._lock:
            return list(super().items())

    def clear(self):
        with self._lock:
            super().clear()


_STORE: _BoundedInvestigationStore = _BoundedInvestigationStore(max_entries=100)


def _sanitize_for_json(val):
    if isinstance(val, dict):
        return {k: _sanitize_for_json(v) for k, v in val.items()}
    elif isinstance(val, (list, tuple)):
        return [_sanitize_for_json(v) for v in val]
    elif isinstance(val, float):
        if math.isnan(val) or not math.isfinite(val):
            return 0.0
        return val
    return val


@dataclass
class Investigation:
    id: str
    created_at: str
    mode: str  # "demo" | "custom_ais"
    data_sources: dict
    satellite: dict
    environment: dict
    drift: dict
    ais_summary: dict
    candidates: list
    progress_log: list = field(default_factory=list)
    # Reproducibility (see README.md "Reproducibility" section): the exact
    # configuration this run actually used, so a re-run with the same
    # inputs and the same config_used values is expected to produce the
    # same result. Populated by run_investigation(); defaults to an empty
    # dict only for any (test-only) construction path that omits it.
    config_used: dict = field(default_factory=dict)
    # Total wall-clock processing time for the whole pipeline, in
    # milliseconds. Individual stage durations are in each progress_log
    # entry's "duration_ms" field.
    processing_time_ms: float | None = None
    # Section 7 (auth): the username that created this investigation, or
    # "anonymous" when AUTH_ENABLED=false (the default) - see app/auth.py.
    # Not included in to_dict() (ownership is an access-control concern, not
    # investigation content) but used by main.py's _require() to enforce
    # per-owner access when auth is enabled.
    owner: str = "anonymous"
    persisted: bool = False

    def to_dict(self):
        raw = {
            "id": self.id,
            "created_at": self.created_at,
            "mode": self.mode,
            "data_sources": self.data_sources,
            "satellite": self.satellite,
            "environment": self.environment,
            "drift": self.drift,
            "ais_summary": self.ais_summary,
            "candidates": [c.to_dict() for c in self.candidates],
            "progress_log": self.progress_log,
            "config_used": self.config_used,
            "processing_time_ms": self.processing_time_ms,
            "persisted": self.persisted,
        }
        return _sanitize_for_json(raw)


def _drift_result_to_dict(dr) -> dict:
    def pt(p):
        if p is None:
            return None
        return {"t_offset_hours": p.t_offset_hours, "timestamp": p.timestamp, "lat": p.lat, "lon": p.lon,
                "uncertainty_km": p.uncertainty_km, "label": p.label}
    return {
        "observed": pt(dr.observed),
        "backward_track": [pt(p) for p in dr.backward_track],
        "forward_track": [pt(p) for p in dr.forward_track],
        "probable_origin": pt(dr.probable_origin),
        "origin_confidence": dr.origin_confidence,
        "environment_source": dr.environment_source,
        "wind_drift_factor": dr.wind_drift_factor,
        "wind_deflection_deg": dr.wind_deflection_deg,
        "drift_status": getattr(dr, "drift_status", "COMPLETED"),
        "notes": dr.notes,
    }


def run_investigation(
    mode: str = "demo",
    ais_csv_path: str | None = None,
    search_radius_km: float = SEARCH_RADIUS_KM_DEFAULT,
    time_window_hours: float = TIME_WINDOW_HOURS_DEFAULT,
    weights: dict | None = None,
    image_path: str | None = None,
    detector: str = "classical",
    center_lat: float = 19.05,
    center_lon: float = 71.20,
    observed_time_override: datetime | None = None,
    owner: str = "anonymous",
    input_file_sha256: str | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> Investigation:
    """
    mode: "demo" (synthetic offline scenario) | "custom_ais" (demo scene +
    uploaded AIS CSV) | "real_satellite" (uploaded GeoTIFF/PNG SAR image;
    image_path required).

    detector: "classical" (default, always available) | "ml" (only
    meaningful for a dual-polarization real_satellite upload - see
    app/ml/detector.py and app/ml/README_ML_STATUS.md for why this is
    labelled a prototype, synthetic-trained detector, not a validated one).

    input_file_sha256: sha256 of the uploaded SAR image, computed by the
    caller (app/main.py) while streaming it to disk. Recorded verbatim in
    config_used for reproducibility (Phase 21/22) - this function never
    computes or re-derives it, so a caller that omits it (any mode besides
    real_satellite, or a direct/test call) simply gets None here rather
    than a silently wrong hash of the wrong thing.
    """
    settings = get_settings()
    progress = []
    total_timer = StageTimer()
    _stage_timer = StageTimer()

    def log(step, stage: str | None = None, outcome: str = "ok", details: dict | None = None):
        nonlocal _stage_timer
        duration = _stage_timer.elapsed_ms()
        clean_stage = stage or step.lower().replace(" ", "_").replace("-", "_").replace("(", "").replace(")", "").strip("_")
        entry = {
            "step": step,
            "stage": clean_stage,
            "duration_ms": duration,
            "ts": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            "outcome": outcome,
        }
        if details:
            entry["details"] = details
        progress.append(entry)
        if progress_callback:
            try:
                progress_callback(clean_stage)
            except Exception:
                pass
        _log.info(
            "pipeline_stage_completed",
            extra={
                "investigation_id": inv_id,
                "step": step,
                "stage": clean_stage,
                "duration_ms": duration,
                "outcome": outcome,
                **(details or {}),
            },
        )
        _stage_timer = StageTimer()

    inv_id = str(uuid.uuid4())[:8]
    _log.info(
        "investigation_started",
        extra={"investigation_id": inv_id, "mode": mode, "detector": detector},
    )

    # ---- 1. Satellite scene -------------------------------------------------
    geolocation_is_assumed = False
    geolocation_source = "synthetic_demo_scene"
    geotiff_metadata_info = None
    detector_provenance = "CLASSICAL_DARK_SPOT"
    scene_source = "SYNTHETIC_DEMO"
    dataset_reference = "Zenodo Sentinel-1 SAR Oil Spill Dataset (reference) -> SYNTHETIC_DEMO scene used"
    ml_notes: list = []
    ml_model_info = None  # Part E/G/H: structured model-provenance metadata, only populated when
    # the ML path was actually attempted (see app/ml/detector.py::MLDetectorEngine.get_model_info) -
    # stays None for the classical-only / synthetic-demo paths, which have no "model" to report on.

    if mode == "real_satellite":
        if not image_path:
            raise ValueError("real_satellite mode requires image_path")
        observed_time = observed_time_override or datetime.now(timezone.utc).replace(tzinfo=None)
        scene = load_real_scene(image_path, center_lat=center_lat, center_lon=center_lon)
        image, gt = scene.image, scene.geotransform
        geolocation_is_assumed = scene.geolocation_is_assumed
        geolocation_source = scene.geolocation_source
        geotiff_metadata_info = scene.geotiff_metadata
        scene_source = "USER_UPLOADED_REAL_IMAGE"
        if geolocation_is_assumed:
            location_note = (
                "Scene location is an ASSUMED/approximate anchor built from the caller-supplied "
                "center_lat/center_lon (see app/satellite/geotiff_metadata.py for why any embedded "
                "geo tags were not usable, if the file had any)."
            )
        else:
            location_note = (
                "Scene location was read from the file's own embedded GeoTIFF geo-referencing tags "
                "(real, not assumed) - see app/satellite/geotiff_metadata.py."
            )
        dataset_reference = (
            f"User-uploaded SAR-like image ({os.path.basename(image_path)}, "
            f"{'dual-pol VV/VH' if scene.is_dual_pol else 'single-band'}). {location_note}"
        )
        log("Real satellite scene loaded")
    else:
        observed_time = observed_time_override or DEMO_OBSERVATION_TIME
        image, gt = generate_synthetic_sar_scene()
        log("Satellite scene loaded")

    # ---- 2. Preprocessing -------------------------------------------------
    despeckled = lee_filter(image, window=7)
    norm = normalize(despeckled)
    sea_mask = simple_sea_mask(norm)
    log("SAR preprocessing completed")

    # ---- 3. Detection + segmentation --------------------------------------
    #
    # ML -> Classical fallback policy (see MODEL_CARD.md / README_ML_STATUS.md
    # "ML confidence & fallback" section for the full rationale):
    #
    #   detector="classical"            -> classical detector, always.
    #   detector="ml", non-dual-pol     -> classical detector (ML genuinely
    #                                      cannot run without 2 real channels).
    #   detector="ml", dual-pol input   -> attempt ML inference; automatically
    #                                      re-run the classical detector instead
    #                                      if the ML engine is unavailable, its
    #                                      output mask is empty/invalid, or its
    #                                      own confidence is below
    #                                      ML_FALLBACK_CONFIDENCE_THRESHOLD.
    #                                      The response always says which
    #                                      detector actually produced the
    #                                      final result - never silently, and
    #                                      never presented as if the ML model
    #                                      had produced it.
    if mode == "real_satellite" and detector == "ml":
        if not scene.is_dual_pol:
            det = detect_oil_slick(norm, sea_mask)
            det.method = "classical_dark_spot_ml_fallback"
            detector_provenance = "CLASSICAL_DARK_SPOT_ML_FALLBACK"
            ml_notes.append(
                "ML detector was requested but the uploaded image is not dual-polarization "
                "(VV+VH) - the ML models require a genuine 2-channel input. Classical SAR "
                "Detection / Fallback was run instead; this is NOT an ML result."
            )
        else:
            from app.ml.detector import get_ml_engine, ML_FALLBACK_CONFIDENCE_THRESHOLD
            engine = get_ml_engine()
            try:
                # `MLDetectorEngine.detect()` documents a "never raise"
                # contract (see app/ml/detector.py), but this call is still
                # wrapped here as a defense-in-depth safety net - e.g. for
                # test doubles or future engine implementations that don't
                # honor that contract - so a bug in the ML path can never
                # crash the whole investigation; it always degrades to the
                # classical fallback instead.
                ml_det = engine.detect(scene.vv, scene.vh)
            except Exception as e:
                from app.satellite.detection import DetectionResult
                try:
                    fallback_mask = np.zeros_like(scene.vv, dtype=bool)
                except Exception:
                    fallback_mask = np.zeros((0, 0), dtype=bool)
                ml_det = DetectionResult(
                    mask=fallback_mask,
                    candidate_masks=[],
                    confidence=0.0,
                    method="ml_prototype_inference_error",
                    notes=[
                        f"{getattr(engine, 'provenance', 'ML_UNAVAILABLE')}: the ML engine raised an unexpected "
                        f"exception outside its own safety wrapper ({type(e).__name__}: {e}). "
                        "Treated as a failed ML result, never a crash."
                    ],
                )

            ml_model_info = ml_det.model_provenance_info

            fallback_reason = None
            if not engine.available:
                fallback_reason = (
                    "ML engine unavailable in this environment (" + "; ".join(engine.load_errors) + ")."
                )
            elif ml_det.method in ("ml_prototype_inference_error", "ml_prototype_invalid_output"):
                fallback_reason = (
                    "ML inference raised a runtime exception, or produced an invalid/malformed "
                    "output (unsupported input, unexpected tensor shape, NaN/Inf confidence, "
                    "empty/invalid model output) - see detection_notes for the ML engine's own "
                    "error detail."
                )
            elif ml_det.mask is None or ml_det.mask.sum() == 0:
                fallback_reason = "ML segmenter produced an empty/invalid mask (no region above its detection threshold)."
            elif not math.isfinite(ml_det.confidence):
                # Defense in depth: app/ml/detector.py already rejects
                # NaN/Inf confidence internally (never returns it as
                # `ml_prototype_pytorch_cnn_unet`), but a NaN comparison
                # against ML_FALLBACK_CONFIDENCE_THRESHOLD below would
                # silently evaluate to False either way (NaN < x is always
                # False in Python), so this module never trusts a non-finite
                # confidence either, regardless of which engine produced it.
                fallback_reason = (
                    f"ML confidence value was not finite ({ml_det.confidence!r}) - treated as an "
                    "invalid result rather than trusted."
                )
            elif ml_det.confidence < ML_FALLBACK_CONFIDENCE_THRESHOLD:
                fallback_reason = (
                    f"ML confidence ({ml_det.confidence:.2f}) is below the configured, heuristic "
                    f"fallback threshold ({ML_FALLBACK_CONFIDENCE_THRESHOLD:.2f} - see README_ML_STATUS.md)."
                )

            if fallback_reason is not None:
                det = detect_oil_slick(norm, sea_mask)
                det.method = "classical_dark_spot_ml_fallback"
                detector_provenance = "CLASSICAL_DARK_SPOT_ML_FALLBACK"
                ml_notes.append(
                    f"{getattr(engine, 'provenance', 'ML_UNAVAILABLE')}: " + fallback_reason + " Classical SAR "
                    "Detection / Fallback was run instead and is what actually produced this "
                    "result. This fallback does NOT indicate the ML model worked - it indicates "
                    "the ML path did not produce a usable result and the classical baseline was "
                    "used in its place."
                )
                # Keep the ML engine's own notes too (why it didn't qualify),
                # so the "not usable" reasoning is fully auditable, not just
                # asserted.
                ml_notes.extend(ml_det.notes)
            else:
                det = ml_det
                detector_provenance = ml_det.provenance
                # SAFETY FIX (see work/UPGRADE_AUDIT.md "Lookalike safety"):
                # the classifier and segmenter are independent sub-models -
                # a scene the classifier confidently calls Lookalike or
                # No-Oil can still produce a non-empty segmenter mask. That
                # mask is preserved below (real evidence, not discarded),
                # but it must never reach an investigator looking
                # indistinguishable from a confirmed oil detection just
                # because it wasn't low-confidence enough to trigger the
                # existing classical-fallback path above (which only checks
                # confidence, never which label won).
                if ml_det.scene_classification and ml_det.scene_classification.get("oil_status") != "LIKELY_OIL":
                    status = ml_det.scene_classification["oil_status"]
                    label = ml_det.scene_classification["label"]
                    conf_pct = ml_det.scene_classification["confidence"] * 100
                    if status == "NO_OIL":
                        ml_notes.append(
                            f"ML scene classifier predicts No-Oil ({conf_pct:.1f}% confidence). Despite a "
                            "segmentation mask being present below, this scene is NOT classified as containing "
                            "oil - do not present this as a confirmed or likely spill."
                        )
                    elif status == "LOOKALIKE":
                        ml_notes.append(
                            f"ML scene classifier predicts Lookalike ({conf_pct:.1f}% confidence) - a SAR dark "
                            "region resembling oil but attributable to another cause (e.g. low-wind area, ship "
                            "wake, natural slick, rain). Despite a segmentation mask being present below, this "
                            "is NOT a confirmed or likely oil detection - it is an uncertain SAR anomaly."
                        )
                    else:  # UNCERTAIN
                        ml_notes.append(
                            f"ML classifier confidence ({conf_pct:.1f}%, predicted {label}) is below the "
                            "configured fallback threshold for treating any class prediction as reliable - "
                            "insufficient confidence to classify this scene as oil, no-oil, or lookalike."
                        )
    else:
        det = detect_oil_slick(norm, sea_mask)
    log("Oil slick detected" if det.mask.sum() > 0 else "No oil slick detected")
    _log.info(
        "detection_completed",
        extra={
            "investigation_id": inv_id,
            "detector_provenance": detector_provenance,
            "fallback_occurred": bool(ml_notes),
            "detection_confidence": det.confidence,
        },
    )

    # ---- 4. Characterisation ------------------------------------------------
    geometry = characterize_slick(det.mask, det.confidence, gt)
    log("Slick geometry calculated")
    env = None
    env_error_message = None

    if geometry is None:
        # SCIENTIFIC HONESTY GUARD (spec Sections 10/21/37): if no slick was
        # detected, there is no location/time to hindcast from and no origin
        # to anchor an AIS search around. Rather than fabricate a phantom
        # origin (which would produce a meaningless drift track and an AIS
        # candidate list correlated with nothing real), we stop the chain
        # here and return a clearly-labelled "no detection" result. This
        # mirrors what a real investigator would do: no slick, no case to
        # open yet.
        log("No slick detected - hindcast and AIS attribution skipped")
        env = None
        age = None
        drift_dict = {
            "observed": None, "backward_track": [], "forward_track": [],
            "probable_origin": None, "origin_confidence": None,
            "environment_source": None, "wind_drift_factor": None,
            "wind_deflection_deg": None,
            "notes": ["No oil slick was detected in this scene; hindcast/forecast requires a detected slick location and time, so it was not computed."],
        }
        ais_summary = {
            "total_raw_records": 0, "cleaned_records": 0, "within_spatial_filter": 0,
            "within_final_filter": 0, "unique_vessels_in_window": 0,
            "search_radius_km": search_radius_km, "time_window_hours": time_window_hours,
            "window_start": None, "window_end": None, "ais_source": "NOT_RUN",
            "reference_dataset": "AIS analysis was not run because no oil slick was detected to anchor a search location/time around.",
            "excluded_vessels": [],
        }
        candidates = []
        log("Investigation ready (no detection)")
    else:
        # ---- 5. Environment conditions at slick location/time -----------------
        env = None
        env_error_message = None
        try:
            env = resolve_environment_conditions(
                geometry.centroid_lat,
                geometry.centroid_lon,
                observed_time,
                allow_synthetic=(mode != "real_satellite"),
                mode=mode,
            )
            log("Environmental conditions loaded")
        except Exception as e:
            env_error_message = str(e)
            _log.warning("environment_resolution_failed", extra={"error": env_error_message, "mode": mode})
            if mode != "real_satellite":
                from app.environment.adapters import SyntheticEnvironmentAdapter
                env = SyntheticEnvironmentAdapter().fetch(
                    geometry.centroid_lat, geometry.centroid_lon, observed_time, fallback_reason=env_error_message
                )
                log("Environmental conditions loaded (synthetic fallback)")
            else:
                log("Environmental data unavailable (synthetic substitution refused in real_satellite mode)")

        # ---- 6. Age estimate ----------------------------------------------------
        try:
            age = estimate_age(geometry.aspect_ratio, geometry.area_km2, env.wind_speed_ms) if (env and env.wind_speed_ms is not None) else None
        except Exception:
            age = None

        # ---- 7. Hindcast + forecast ----------------------------------------------
        if env is not None:
            try:
                drift = run_drift(
                    geometry.centroid_lat, geometry.centroid_lon, observed_time,
                    backward_hours=settings.drift_backward_hours,
                    forward_hours=settings.drift_forward_hours,
                    allow_synthetic=(mode != "real_satellite"),
                    mode=mode,
                )
                log("Hindcast completed")
                log("Spill origin estimated")
                log("Forecast generated")

                origin_lat, origin_lon = drift.probable_origin.lat, drift.probable_origin.lon
                origin_time = datetime.fromisoformat(drift.probable_origin.timestamp)
                drift_dict = _drift_result_to_dict(drift)
            except Exception as e:
                _log.warning("drift_simulation_failed", extra={"error": str(e)})
                origin_lat, origin_lon = geometry.centroid_lat, geometry.centroid_lon
                origin_time = observed_time
                drift_dict = {
                    "observed": {"t_offset_hours": 0.0, "timestamp": observed_time.isoformat(), "lat": round(origin_lat, 5), "lon": round(origin_lon, 5), "uncertainty_km": 1.0, "label": "Observed"},
                    "backward_track": [],
                    "forward_track": [],
                    "probable_origin": None,
                    "origin_confidence": "none",
                    "environment_source": "UNAVAILABLE",
                    "wind_drift_factor": 0.03,
                    "wind_deflection_deg": 15.0,
                    "drift_status": "UNAVAILABLE",
                    "notes": [f"Drift simulation could not be completed: {e}"],
                }
        else:
            # Operational honesty guard: Real satellite mode refuses silent synthetic drift
            origin_lat, origin_lon = geometry.centroid_lat, geometry.centroid_lon
            origin_time = observed_time
            drift_dict = {
                "observed": {
                    "t_offset_hours": 0.0,
                    "timestamp": observed_time.isoformat(),
                    "lat": round(origin_lat, 5),
                    "lon": round(origin_lon, 5),
                    "uncertainty_km": 1.0,
                    "label": "Observed",
                },
                "backward_track": [],
                "forward_track": [],
                "probable_origin": None,
                "origin_confidence": "none",
                "environment_source": "UNAVAILABLE",
                "wind_drift_factor": 0.03,
                "wind_deflection_deg": 15.0,
                "drift_status": "UNAVAILABLE",
                "notes": [
                    f"Drift reconstruction unavailable: real metocean data (wind/ocean current) could not be retrieved ({env_error_message}). "
                    "Per scientific safety mandate, synthetic demonstration fields are not substituted in real_satellite mode."
                ],
            }
            log("Drift reconstruction skipped (real environmental data unavailable)")

        # ---- 8. AIS load --------------------------------------------------------
        if mode == "custom_ais" and ais_csv_path:
            adapter = GenericCSVAISAdapter()
            raw_records = adapter.read(ais_csv_path)
            ais_source_tag = "USER_UPLOADED_CSV"
            ais_reference_dataset = (
                "User-uploaded AIS CSV (MarineCadastre-compatible or generically-headered "
                "export), normalized via GenericCSVAISAdapter (see DATA_PIPELINE.md)."
            )
        elif mode == "live_ais":
            # Section 3: attempts a configured live AIS provider first,
            # falling back to the synthetic demo fleet (never silently -
            # ais_reference_dataset always states which one actually
            # happened and, on fallback, why) - see app/ais/live_provider.py.
            synthetic_fleet = build_demo_ais_fleet(origin_lat, origin_lon, origin_time)
            window_start_probe = origin_time - timedelta(hours=time_window_hours)
            window_end_probe = origin_time + timedelta(hours=time_window_hours)
            raw_records, ais_source_tag, ais_reference_dataset = resolve_ais_records(
                origin_lat, origin_lon, search_radius_km, window_start_probe, window_end_probe,
                fallback_records=synthetic_fleet,
                fallback_source_tag="SYNTHETIC_DEMO",
                fallback_reference=(
                    "MarineCadastre AIS (https://marinecadastre.gov/accessais/) - canonical field "
                    "schema; live dataset not fetched in this sandboxed build, synthetic demo fleet "
                    "used instead (see DATA_PIPELINE.md)."
                ),
            )
        else:
            raw_records = build_demo_ais_fleet(origin_lat, origin_lon, origin_time)
            ais_source_tag = "SYNTHETIC_DEMO"
            ais_reference_dataset = (
                "MarineCadastre AIS (https://marinecadastre.gov/accessais/) - canonical field "
                "schema; live dataset not fetched in this sandboxed build, synthetic demo fleet "
                "used instead (see DATA_PIPELINE.md)."
            )
        log("AIS traffic reconstructed")

        total_raw = len(raw_records)
        cleaned = clean_records(raw_records)

        # ---- 9. Spatial + temporal filtering -------------------------------------
        window_start = origin_time - timedelta(hours=time_window_hours)
        window_end = origin_time + timedelta(hours=time_window_hours)
        spatial = spatial_filter(cleaned, origin_lat, origin_lon, search_radius_km)
        filtered = temporal_filter(spatial, window_start, window_end)
        log("Irrelevant vessels filtered")

        # ---- 10. Trajectory reconstruction ---------------------------------------
        # detection_lat/lon = the raw satellite-observed slick location
        # (geometry.centroid_*), distinct from origin_lat/lon (the
        # hindcast-estimated origin) - see SIH requirement D.
        trajectories = reconstruct_trajectories(
            filtered, origin_lat, origin_lon,
            detection_lat=geometry.centroid_lat, detection_lon=geometry.centroid_lon,
        )
        log("Vessel behaviour analysed")

        # ---- 10a. Explain nearby-but-excluded vessels -----------------------------
        excluded_vessels = summarize_exclusions(
            cleaned, {t.mmsi for t in trajectories},
            origin_lat, origin_lon, search_radius_km, window_start, window_end,
        )
        # ---- Attribution Gating (Centralized Policy: app/investigation/policy.py) ----
        from app.investigation.policy import evaluate_attribution_eligibility
        policy_eval = evaluate_attribution_eligibility(
            scene_classification=det.scene_classification,
            mask=det.mask,
            observed_time=observed_time,
            min_mask_area_px=settings.min_attribution_mask_px,
            ais_records=raw_records,
        )
        attribution_gated = not policy_eval.eligible
        attribution_status = policy_eval.status
        attribution_decision_reason = policy_eval.reason

        if attribution_gated:
            candidates = []
            log(f"Attribution safety gate applied: {attribution_status}")
            _log.info(
                "attribution_safety_gate_applied",
                extra={
                    "investigation_id": inv_id,
                    "attribution_status": attribution_status,
                    "oil_status": policy_eval.oil_status,
                    "mask_area_px": policy_eval.mask_pixels,
                },
            )
        else:
            # ---- 11. Attribution scoring + ranking -----------------------------------
            candidates = rank_candidates(
                trajectories, origin_lat, origin_lon, origin_time,
                search_radius_km=search_radius_km, time_window_hours=time_window_hours,
                weights=weights or DEFAULT_WEIGHTS,
                detection_time=observed_time,
            )
            log("Candidate scoring completed")
            log("Vessel ranking generated")

        log("Investigation ready")
        _log.info(
            "ais_attribution_completed",
            extra={
                "investigation_id": inv_id,
                "ais_records": total_raw,
                "candidate_vessels": len(candidates),
                "attribution_status": attribution_status,
            },
        )

        ais_summary = {
            "total_raw_records": total_raw,
            "cleaned_records": len(cleaned),
            "within_spatial_filter": len(spatial),
            "within_final_filter": len(filtered),
            "unique_vessels_in_window": len(trajectories),
            "search_radius_km": search_radius_km,
            "time_window_hours": time_window_hours,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "ais_source": ais_source_tag,
            "reference_dataset": ais_reference_dataset,
            "excluded_vessels": excluded_vessels,
            "attribution_status": attribution_status,
            "attribution_gated": attribution_gated,
            "attribution_decision_reason": attribution_decision_reason,
            "evidence_provenance": policy_eval.evidence_provenance,
        }

    h, w = image.shape
    top_left = gt.pixel_to_latlon(0, 0)
    bottom_right = gt.pixel_to_latlon(h, w)
    scene_bounds = {
        "north": top_left[0], "west": top_left[1],
        "south": bottom_right[0], "east": bottom_right[1],
    }

    satellite_payload = {
        "geometry": geometry.to_dict() if geometry else None,
        "scene_bounds": scene_bounds,
        "confidence": det.confidence,
        "age_estimate": age.__dict__ if age else None,
        "detection_notes": det.notes + ml_notes,
        "original_image": render_grayscale(image),
        "processed_image": render_grayscale(norm),
        "detection_overlay": render_detection_overlay(norm, det.mask),
        "method": det.method,
        "source": scene_source,
        "detector_provenance": detector_provenance,
        "detector_version": det.detector_version,
        "geolocation_is_assumed": geolocation_is_assumed,
        "geolocation_source": geolocation_source,
        "geotiff_metadata": geotiff_metadata_info,
        "dataset_reference": dataset_reference,
        "model_provenance_info": ml_model_info,
        "scene_classification": det.scene_classification,
        "attribution_status": ais_summary.get("attribution_status", "NOT_RUN"),
        "attribution_gating": {
            "gated": ais_summary.get("attribution_gated", False),
            "status": ais_summary.get("attribution_status", "NOT_RUN"),
            "reason": ais_summary.get("attribution_decision_reason", ""),
            "oil_status": det.scene_classification.get("oil_status") if det.scene_classification else None,
            "mask_area_px": geometry.area_px if geometry else 0,
            "evidence_provenance": ais_summary.get("evidence_provenance"),
        },
    }

    if geometry is None:
        environment_payload = None
    elif env:
        environment_payload = {
            "wind_speed_ms": env.wind_speed_ms,
            "wind_dir_deg": env.wind_dir_deg,
            "current_speed_ms": env.current_speed_ms,
            "current_dir_deg": env.current_dir_deg,
            "wave_height_m": env.wave_height_m,
            "source": env.source,
            "retrieved_at": env.retrieved_at,
            "fallback_reason": env.fallback_reason,
            "wind_provider": getattr(env, "wind_provider", None),
            "current_provider": getattr(env, "current_provider", None),
            "data_valid_time": getattr(env, "data_valid_time", None),
            "data_age_hours": getattr(env, "data_age_hours", None),
            "is_fallback": getattr(env, "is_fallback", False),
            "coverage_status": getattr(env, "coverage_status", "FULL_METOCEAN"),
            "notes": getattr(env, "notes", []),
        }
    else:
        environment_payload = {
            "wind_speed_ms": None,
            "wind_dir_deg": None,
            "current_speed_ms": None,
            "current_dir_deg": None,
            "wave_height_m": None,
            "source": "UNAVAILABLE",
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "fallback_reason": env_error_message or "Environmental data unavailable",
            "wind_provider": "UNAVAILABLE",
            "current_provider": "UNAVAILABLE",
            "data_valid_time": None,
            "data_age_hours": None,
            "is_fallback": False,
            "coverage_status": "UNAVAILABLE",
            "notes": [
                f"Operational real_satellite mode refused synthetic environmental data: {env_error_message}"
            ],
        }

    data_sources = {
        "satellite": dataset_reference,
        "ais": f"MarineCadastre AIS (reference schema) -> {ais_summary['ais_source']}",
        "environment": (
            "NOT_RUN" if geometry is None
            else (
                (env.source + (f" - {env.fallback_reason}" if env.fallback_reason else ""))
                if env else f"UNAVAILABLE - {env_error_message or 'Live metocean feed unreachable'}"
            )
        ),
        # Explicit, exhaustive map of every `detector_provenance` value this
        # orchestrator can produce (see the three/four-way assignment above:
        # "CLASSICAL_DARK_SPOT" default, "CLASSICAL_DARK_SPOT_ML_FALLBACK",
        # or whatever app/ml/detector.py's engine reports on success -
        # "ML_PROTOTYPE_SYNTHETIC_TRAINED" or "ML_REAL_ZENODO_TRAINED").
        # Deliberately no `.get(..., <ambiguous default>)` fallback: an
        # unrecognized provenance value is a bug in this module, not
        # something to silently paper over with a generic label, so it
        # raises immediately instead of shipping a mislabelled result.
        "detection_method": {
            "CLASSICAL_DARK_SPOT": (
                "Classical SAR Detection (hybrid dark-spot thresholding + morphology + "
                "texture-based false-positive filtering; heuristic confidence, not a "
                "calibrated statistical probability)"
            ),
            "ML_PROTOTYPE_SYNTHETIC_TRAINED": (
                "ML prototype (PyTorch CNN + U-Net, synthetic-trained - see app/ml/README_ML_STATUS.md; "
                "uncalibrated softmax confidence, not a validated detector)"
            ),
            "ML_REAL_ZENODO_TRAINED": (
                "ML model (PyTorch SpillUNet, trained on the real Zenodo Sentinel-1 SAR Oil Spill "
                "Dataset - see docs/DATASET_SETUP.md and the evaluation report for held-out "
                "test-set metrics before treating this as validated for a new deployment context)"
            ),
            "CLASSICAL_DARK_SPOT_ML_FALLBACK": (
                "Classical SAR Detection / Fallback (ML was requested but did not produce a usable "
                "result - see detection_notes for why)"
            ),
        }[detector_provenance],
        "drift_method": (
            (
                "Physics-based simplified advection (wind leeway only; ocean current unavailable)"
                if drift_dict.get("drift_status") == "DEGRADED_PARTIAL_WIND"
                else "Physics-based simplified advection (current + wind leeway)"
            )
            if (drift_dict.get("drift_status") not in (None, "UNAVAILABLE") and drift_dict.get("probable_origin"))
            else "NOT_RUN (environmental data unavailable)"
        ),
        "attribution_method": "Weighted spatio-temporal explainable scoring",
    }

    total_ms = total_timer.elapsed_ms()

    # ---- Reproducibility: the exact configuration this run used ---------------
    # A re-run of the same mode/inputs with these same values is expected to
    # reproduce the same result (the synthetic demo is fully deterministic -
    # see DEMO_OBSERVATION_TIME above and README.md's "Reproducibility"
    # section for what "same" means for the real-upload/custom-AIS modes,
    # which depend on the caller's own input file).
    config_used = {
        "mode": mode,
        "detector_requested": detector,
        "search_radius_km": search_radius_km,
        "time_window_hours": time_window_hours,
        "attribution_weights": weights or DEFAULT_WEIGHTS,
        "ml_confidence_threshold": settings.ml_confidence_threshold,
        "drift_backward_hours": list(settings.drift_backward_hours),
        "drift_forward_hours": list(settings.drift_forward_hours),
        "app_version": settings.app_version,
        "detector_version": satellite_payload["detector_version"],
        "input_file_sha256": input_file_sha256,
    }

    inv = Investigation(
        id=inv_id,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        mode=mode,
        data_sources=data_sources,
        satellite=satellite_payload,
        environment=environment_payload,
        drift=drift_dict,
        ais_summary=ais_summary,
        candidates=candidates,
        progress_log=progress,
        config_used=config_used,
        processing_time_ms=total_ms,
        owner=owner,
    )
    saved = _db.save_investigation(inv)  # Section 6: survives a backend restart; never raises (see db.py)
    inv.persisted = bool(saved)
    if get_settings().database_enabled and not saved:
        _log.error("investigation_persistence_failed", extra={"investigation_id": inv_id})
        _STORE.pop(inv_id, None)
        if get_settings().database_mandatory:
            raise _db.PersistenceError(f"Mandatory database persistence failed for investigation {inv_id}")
    else:
        _STORE[inv_id] = inv
    _log.info(
        "investigation_completed",
        extra={
            "investigation_id": inv_id,
            "detector_provenance": detector_provenance,
            "processing_time_ms": total_ms,
            "candidate_vessels": len(candidates),
        },
    )
    return inv


class _CandidateView:
    """Read-only wrapper so a candidate rehydrated from the database (a
    plain JSON dict) supports the same two access patterns the codebase
    uses for a live in-memory `RankedCandidate` - attribute access
    (`c.mmsi`, used by main.py::get_vessel) and `.to_dict()` (used
    everywhere else) - without needing a second candidate class or changing
    any existing route."""

    __slots__ = ("_d",)

    def __init__(self, d: dict):
        object.__setattr__(self, "_d", d)

    def __getattr__(self, name):
        try:
            return self._d[name]
        except KeyError:
            raise AttributeError(name)

    def to_dict(self) -> dict:
        return self._d


@dataclass
class RehydratedInvestigation:
    """An Investigation reconstructed from the database after a restart
    (see get_investigation() below). Deliberately duck-type-compatible with
    `Investigation` for every attribute/method main.py's routes actually
    use (`.satellite`, `.drift`, `.ais_summary`, `.candidates`, `.mode`,
    `.to_dict()`) so no route needs to special-case "was this investigation
    ever in the in-memory store"."""

    id: str
    created_at: str
    mode: str
    data_sources: dict
    satellite: dict
    environment: dict
    drift: dict
    ais_summary: dict
    candidates: list
    progress_log: list
    config_used: dict
    processing_time_ms: float | None
    owner: str = "anonymous"
    persisted: bool = True

    def to_dict(self) -> dict:
        raw = {
            "id": self.id, "created_at": self.created_at, "mode": self.mode,
            "data_sources": self.data_sources, "satellite": self.satellite,
            "environment": self.environment, "drift": self.drift,
            "ais_summary": self.ais_summary,
            "candidates": [c.to_dict() for c in self.candidates],
            "progress_log": self.progress_log, "config_used": self.config_used,
            "processing_time_ms": self.processing_time_ms,
            "persisted": True,
        }
        return _sanitize_for_json(raw)


def get_investigation(inv_id: str):
    """Looks up an investigation by id. Checks the fast in-memory store
    first (unchanged behavior/performance for the common same-process
    case), then falls back to the SQLite-backed store - Section 6's actual
    "survives a backend restart" requirement - and, if found there,
    write-through-caches it back into `_STORE` so a repeated lookup for the
    same id doesn't re-hit the database every time."""
    inv = _STORE.get(inv_id)
    if inv is not None:
        return inv
    data = _db.load_investigation(inv_id)
    if data is None:
        return None
    rehydrated = RehydratedInvestigation(
        id=data["id"], created_at=data["created_at"], mode=data["mode"],
        data_sources=data["data_sources"], satellite=data["satellite"],
        environment=data["environment"], drift=data["drift"],
        ais_summary=data["ais_summary"],
        candidates=[_CandidateView(c) for c in (data.get("candidates") or [])],
        progress_log=data.get("progress_log") or [],
        config_used=data.get("config_used") or {},
        processing_time_ms=data.get("processing_time_ms"),
        owner=data.get("_owner") or "anonymous",
    )
    _STORE[inv_id] = rehydrated
    return rehydrated
