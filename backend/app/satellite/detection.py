"""
Oil slick detection & segmentation - hybrid SAR image-processing approach.

APPROACH (documented per SIH requirement: "clearly document what is
actually being used" and "never claim an accuracy value unless measured")
------------------------------------------------------------------------
This is a *classical dark-spot detection + morphology + texture-based
false-positive filtering* pipeline, not a trained deep-learning model.

Why: the task specifies the Zenodo Sentinel-1 SAR Oil Spill Dataset as the
reference dataset, but this sandboxed environment has no network access to
zenodo.org, so the labelled masks could not be downloaded, and therefore no
CNN/U-Net could be legitimately trained or evaluated here. Rather than
fabricate a trained model or an unverified accuracy number, this pipeline
implements the well-established classical SAR oil-spill detection chain
(adaptive thresholding of a despeckled dark-spot probability surface +
morphological cleanup + shape/texture plausibility filtering), which is a
scientifically defensible, literature-standard baseline
(Solberg et al., Fiscella et al., Topouzelis 2008) and produces genuinely
computed geometry from whatever image it is given.

A `confidence` score is reported. It is a *heuristic* index (0-1) derived
from contrast, shape regularity and size - explicitly NOT a calibrated
statistical probability, and the API/UI must present it as such.

EXTENSION POINT: `detect_oil_slick()` is the single seam to swap in a
trained segmentation model (e.g. a U-Net fine-tuned on the Zenodo dataset).
It must return the same `DetectionResult` (binary mask + confidence) so
nothing downstream (characterisation, drift, scoring) needs to change.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage as ndi
from app.satellite import geometry_utils as gu


@dataclass
class DetectionResult:
    mask: np.ndarray                 # bool array, True = detected slick pixel
    candidate_masks: list            # list of bool arrays, one per connected candidate before final selection
    confidence: float                # heuristic 0-1
    method: str = "hybrid_dark_spot_morphology"
    notes: list = field(default_factory=list)
    # ---- Extended fields (clean detector interface - see Part 2) --------------
    # These mirror the `detect(image, metadata) -> DetectionResult` interface
    # described in the production-readiness brief. They are additive: every
    # existing caller that only reads mask/candidate_masks/confidence/method/
    # notes continues to work unchanged. `app/investigation/orchestrator.py`
    # remains the single source of truth for the *final* detector_provenance
    # decision (it alone knows about the ML->classical fallback policy); the
    # fields below describe THIS detector call's own honest result.
    success: bool = True
    detector_name: str = "classical_dark_spot"
    detector_version: str = "classical_dark_spot_v1.0"
    provenance: str = "CLASSICAL_DARK_SPOT"
    fallback_used: bool = False
    fallback_reason: str | None = None
    warnings: list = field(default_factory=list)
    processing_time_ms: float | None = None
    # Populated only by app/ml/detector.py::MLDetectorEngine.detect() on a
    # successful ML result - structured model-provenance metadata (model
    # name/version, whether training data was real or synthetic, dataset
    # DOIs, preprocessing version, training date, limitations, ...) for
    # app/investigation/orchestrator.py to surface to the API/UI/reports
    # (Part E/F/G/H of the real-data integration). None for the classical
    # detector and for any failed/unavailable ML result - always check for
    # None before reading, never assume it is populated.
    model_provenance_info: dict | None = None
    # Populated only by MLDetectorEngine.detect() when its classifier loaded
    # and ran. This is the fix for a real, confirmed bug (see
    # work/UPGRADE_AUDIT.md "Lookalike safety"): the classifier and segmenter
    # are architecturally independent sub-models, so a scene the classifier
    # confidently calls "Lookalike" can still produce a non-empty segmenter
    # mask - before this field existed, that label was written only into a
    # free-text `notes` entry, and nothing downstream (orchestrator, API,
    # report) ever read it, so a high-confidence Lookalike could reach an
    # investigator looking exactly like a confirmed oil detection.
    #
    # oil_status is one of the four states this task's own Phase 3 spec
    # requires, derived here (not by the caller) so there is exactly one
    # place this decision is made:
    #   "LIKELY_OIL"  - classifier predicted Oil, confidence >= threshold
    #   "NO_OIL"      - classifier predicted No-Oil, confidence >= threshold
    #   "LOOKALIKE"   - classifier predicted Lookalike, confidence >= threshold
    #   "UNCERTAIN"   - confidence below ML_FALLBACK_CONFIDENCE_THRESHOLD,
    #                   regardless of which label had the highest probability
    # None when the classifier did not load/run at all (segmenter-only
    # results, or a failed/classical detection) - always check for None.
    scene_classification: dict | None = None


def _texture_score(gray: np.ndarray, mask: np.ndarray, tot_sum: float | None = None, tot_sq: float | None = None, sub_gray: np.ndarray | None = None) -> float:
    """Low local variance inside the candidate supports 'smooth oil film'
    vs. natural look-alikes (which tend to be patchier)."""
    m_count = int(mask.sum())
    if m_count < 10:
        return 0.0
    interior_vals = sub_gray[mask] if sub_gray is not None else gray[mask]
    interior_std = float(np.std(interior_vals))

    if tot_sum is not None and tot_sq is not None:
        tot_n = gray.size
        ext_n = tot_n - m_count
        if ext_n > 0:
            m_sum = float(interior_vals.sum())
            m_sq = float((interior_vals ** 2).sum())
            ext_mean = (tot_sum - m_sum) / ext_n
            ext_var = max(0.0, ((tot_sq - m_sq) / ext_n) - ext_mean ** 2)
            exterior_std = float(np.sqrt(ext_var))
        else:
            exterior_std = 1e-6
    else:
        exterior = gray[~mask]
        exterior_std = float(np.std(exterior)) if exterior.size else 1e-6

    ratio = interior_std / max(exterior_std, 1e-6)
    # lower ratio (smoother interior relative to surrounding sea) -> higher score
    return float(np.clip(1.2 - ratio, 0, 1))


def detect_oil_slick(
    image: np.ndarray,
    sea_mask: np.ndarray,
    min_region_px: int = 120,
    max_region_frac: float = 0.35,
    min_oil_area_px: int = 250,
) -> DetectionResult:
    """
    Classical hybrid dark-spot detector with 3-class heuristic classification
    (Oil, Lookalike, No Oil).

    Steps:
      1. Input validation & safety guards (non-finite / unreadable handling).
      2. Adaptive local threshold on the despeckled image to flag
         anomalously dark pixels relative to their neighbourhood.
      3. Morphological opening/closing to remove speckle-scale noise and
         fill small gaps.
      4. Connected-component labelling -> candidate regions.
      5. False-positive filtering: evaluate elongation, solidity, internal-vs-
         external texture variance damping, and area fraction.
      6. Multi-class heuristic evidence mapping: evaluate Oil, Lookalike, and
         No-Oil heuristic scores, normalized to exactly 100% (1.0).
      7. Report honest classification, scores, winning mask, and diagnostic notes.
    """
    notes = []
    _t0 = time.perf_counter()

    # 1. Safety & Input Guards
    if image is None or not isinstance(image, np.ndarray) or image.size == 0:
        return DetectionResult(
            mask=np.zeros((0, 0), dtype=bool),
            candidate_masks=[],
            confidence=0.0,
            notes=["Invalid SAR input: image is None or empty array."],
            success=False,
            warnings=["Invalid SAR input: image is None or empty array."],
            processing_time_ms=round((time.perf_counter() - _t0) * 1000, 2),
            scene_classification={
                "label": "Uncertain",
                "confidence": 0.0,
                "oil_status": "UNCERTAIN",
                "probabilities": {"Oil": 0.0, "No-Oil": 0.0, "Lookalike": 0.0},
                "scores": {"Oil": 0.0, "Lookalike": 0.0, "No Oil": 0.0},
                "score_type": "heuristic_score",
                "score_type_label": "Heuristic score",
                "detector_name": "Classical Detector",
                "reasoning": "Input image was None or empty array.",
            },
        )

    # Coerce single-band 3D arrays like (1, H, W) or (H, W, 1) to 2D
    if image.ndim == 3 and (image.shape[0] == 1 or image.shape[-1] == 1):
        image = np.squeeze(image)

    if image.ndim != 2:
        return DetectionResult(
            mask=np.zeros((0, 0), dtype=bool),
            candidate_masks=[],
            confidence=0.0,
            notes=[f"Invalid SAR input: expected 2D raster array, got shape {image.shape}."],
            success=False,
            warnings=[f"Invalid SAR input: expected 2D raster array, got shape {image.shape}."],
            processing_time_ms=round((time.perf_counter() - _t0) * 1000, 2),
            scene_classification={
                "label": "Uncertain",
                "confidence": 0.0,
                "oil_status": "UNCERTAIN",
                "probabilities": {"Oil": 0.0, "No-Oil": 0.0, "Lookalike": 0.0},
                "scores": {"Oil": 0.0, "Lookalike": 0.0, "No Oil": 0.0},
                "score_type": "heuristic_score",
                "score_type_label": "Heuristic score",
                "detector_name": "Classical Detector",
                "reasoning": f"Input image must be a 2D array, received {image.ndim}D with shape {image.shape}.",
            },
        )

    if sea_mask is None or not isinstance(sea_mask, np.ndarray):
        sea_mask = np.ones(image.shape, dtype=bool)
    elif sea_mask.shape != image.shape:
        sea_mask = np.ones(image.shape, dtype=bool)

    img = image.astype(np.float64)
    if not np.all(np.isfinite(img)):
        return DetectionResult(
            mask=np.zeros_like(img, dtype=bool),
            candidate_masks=[],
            confidence=0.0,
            notes=["SAR input contains non-finite (NaN/Inf) values."],
            success=False,
            warnings=["SAR input contains non-finite (NaN/Inf) values."],
            processing_time_ms=round((time.perf_counter() - _t0) * 1000, 2),
            scene_classification={
                "label": "Uncertain",
                "confidence": 0.0,
                "oil_status": "UNCERTAIN",
                "probabilities": {"Oil": 0.0, "No-Oil": 0.0, "Lookalike": 0.0},
                "scores": {"Oil": 0.0, "Lookalike": 0.0, "No Oil": 0.0},
                "score_type": "heuristic_score",
                "score_type_label": "Heuristic score",
                "detector_name": "Classical Detector",
                "reasoning": "SAR input contains non-finite (NaN/Inf) values.",
            },
        )

    # 2. Adaptive threshold (local mean minus k * local std)
    local_thresh = gu.threshold_local_gaussian(img, block_size=51)
    dark = (img < (local_thresh - 0.03)) & sea_mask

    # 3. Morphology cleanup
    dark = gu.remove_small_objects(dark, min_size=40)
    dark = gu.binary_opening(dark, gu.disk(2))
    dark = gu.binary_closing(dark, gu.disk(3))

    sea_pixels = int(sea_mask.sum())
    dark_pixels = int(dark.sum())
    dark_fraction = dark_pixels / max(sea_pixels, 1)

    labels = gu.label(dark, connectivity=2)
    regions = gu.regionprops(labels, min_area=min_region_px)

    h, w = image.shape
    max_area = max_region_frac * h * w

    tot_sum = float(img.sum())
    tot_sq = float((img ** 2).sum())

    low_wind_regions = []
    scored = []
    for r in regions:
        if r.area < min_region_px:
            continue
        if r.area > max_area:
            low_wind_regions.append(r)
            notes.append(f"Rejected region id={r.label} (area {r.area}px): exceeds plausible slick size, likely low-wind zone.")
            continue
        # elongation: real slicks are usually elongated (currents/wind stretch them)
        if r.axis_minor_length > 0:
            elongation = r.axis_major_length / max(r.axis_minor_length, 1e-6)
        else:
            elongation = 0
        # Fast bounding-box evaluation instead of full O(image) array comparison
        min_r, min_c, max_r, max_c = r.bbox
        sub_labels = labels[min_r:max_r, min_c:max_c]
        sub_mask = (sub_labels == r.label)
        sub_gray = img[min_r:max_r, min_c:max_c]
        tex = _texture_score(img, sub_mask, tot_sum=tot_sum, tot_sq=tot_sq, sub_gray=sub_gray)
        solidity = r.solidity  # area / convex_hull_area

        # heuristic plausibility score
        plausibility = (
            0.35 * float(np.clip((elongation - 1) / 5, 0, 1))
            + 0.35 * tex
            + 0.15 * float(np.clip(solidity, 0, 1))
            + 0.15 * float(np.clip(r.area / min_region_px / 10, 0, 1))
        )
        scored.append((plausibility, r, elongation, tex, solidity))

    # 4. Multi-class Decision Logic and Heuristic Scoring
    best_mask = np.zeros_like(image, dtype=bool)
    candidates = []

    if not scored and not low_wind_regions:
        # No dark anomalies of significance
        raw_no_oil = max(0.85, 0.98 - 3.0 * dark_fraction)
        raw_lookalike = min(0.14, 3.0 * dark_fraction + 0.01)
        raw_oil = 0.01
        label = "No Oil"
        oil_status = "NO_OIL"
        notes.append("No plausible oil-slick-like dark region found; this scene is NOT classified as containing oil.")
        reasoning = f"Adaptive thresholding detected no dark candidate regions above minimum area threshold ({min_region_px}px)."

    elif not scored and low_wind_regions:
        # Large low-wind calm sea area
        raw_lookalike = 0.85
        raw_no_oil = 0.10
        raw_oil = 0.05
        label = "Lookalike"
        oil_status = "LOOKALIKE"
        best_lw = max(low_wind_regions, key=lambda r: r.area)
        best_mask = (labels == best_lw.label)
        candidates = [best_mask]
        notes.append(f"Large low-wind area detected ({best_lw.area}px > {int(max_area)}px) - classified as lookalike. Despite low-backscatter feature presence, this is NOT a confirmed or likely oil detection - it is a natural calm-sea lookalike.")
        reasoning = f"Detected anomalous dark region exceeding {max_region_frac*100:.0f}% of scene frame, characteristic of low-wind calm sea surface rather than an oil spill."

    else:
        scored.sort(key=lambda t: t[0], reverse=True)
        best_score, best_region, elongation, tex, solidity = scored[0]

        # Full mask only created for winning candidate
        best_mask = (labels == best_region.label)
        best_mask = ndi.binary_fill_holes(best_mask)
        candidates = [best_mask]

        second_area = scored[1][1].area if len(scored) > 1 else 0
        is_dominant = (
            len(scored) <= 25
            or best_region.area >= 500
            or (second_area > 0 and best_region.area >= 1.75 * second_area)
            or best_score >= 0.60
        )
        is_oil = (
            best_score >= 0.50
            and elongation >= 2.0
            and tex >= 0.25
            and best_region.area >= min_oil_area_px
            and is_dominant
        )

        if is_oil:
            label = "Oil"
            oil_status = "LIKELY_OIL"
            raw_oil = float(np.clip(0.50 + 0.45 * best_score, 0.52, 0.95))
            raw_lookalike = float(np.clip((1.0 - best_score) * 0.60 + 0.08, 0.04, 0.35))
            raw_no_oil = float(np.clip(0.05 + 0.10 * (1.0 - np.clip(best_region.area / 5000, 0, 1)), 0.01, 0.20))
            notes.append(f"Selected region id={best_region.label}: elongation={elongation:.2f}, texture_score={tex:.2f}, plausibility={best_score:.2f}, area={best_region.area}px")
            reasoning = f"Candidate exhibits significant capillary wave damping (texture score {tex:.2f}), wind-driven elongation ({elongation:.2f}:1), and coherent slick footprint ({best_region.area}px)."
        elif dark_fraction > 0.20 and len(scored) > 50 and best_region.area < 500:
            # Pervasive diffuse background sea-clutter / wave speckle
            label = "No Oil"
            oil_status = "NO_OIL"
            raw_no_oil = 0.70
            raw_lookalike = 0.25
            raw_oil = 0.05
            best_mask = np.zeros_like(image, dtype=bool)
            candidates = []
            notes.append(f"Pervasive diffuse wave clutter detected across {dark_fraction*100:.1f}% of sea ({len(scored)} fragments) without dominant slick; this scene is NOT classified as containing oil.")
            reasoning = "Dark pixels are diffusely distributed across hundreds of small wave-clutter fragments without any coherent, elongated slick candidate."
        else:
            label = "Lookalike"
            oil_status = "LOOKALIKE"
            raw_lookalike = float(np.clip(0.55 + 0.35 * (1.0 - best_score), 0.55, 0.90))
            raw_oil = float(np.clip(0.40 * best_score, 0.05, 0.30))
            raw_no_oil = float(np.clip(0.20 * (1.0 - dark_fraction), 0.05, 0.25))
            notes.append(f"Flagged region id={best_region.label} as lookalike: elongation={elongation:.2f}, texture_score={tex:.2f}, plausibility={best_score:.2f}, area={best_region.area}px (fails oil-slick criteria). Despite dark anomaly presence, this is NOT a confirmed or likely oil detection - it is an uncertain SAR lookalike anomaly.")
            reasoning = f"Dark feature detected but fails oil-slick morphological criteria (elongation {elongation:.2f}, texture {tex:.2f}, area {best_region.area}px). Characteristic of natural lookalike (low-wind patch, internal waves, or biogenic film)."

    # Normalize scores strictly to sum to 1.0000 with guaranteed [0, 1] bounds
    tot = raw_oil + raw_lookalike + raw_no_oil
    if tot > 0:
        s_oil = float(np.clip(round(raw_oil / tot, 4), 0.0, 1.0))
        s_lookalike = float(np.clip(round(raw_lookalike / tot, 4), 0.0, 1.0))
        s_no_oil = float(np.clip(round(1.0 - s_oil - s_lookalike, 4), 0.0, 1.0))
        residual = round(1.0 - (s_oil + s_lookalike + s_no_oil), 4)
        if residual != 0:
            if s_oil >= max(s_lookalike, s_no_oil):
                s_oil = round(s_oil + residual, 4)
            elif s_lookalike >= s_no_oil:
                s_lookalike = round(s_lookalike + residual, 4)
            else:
                s_no_oil = round(s_no_oil + residual, 4)
    else:
        s_oil, s_lookalike, s_no_oil = 0.0, 0.0, 1.0

    if oil_status == "LIKELY_OIL":
        confidence = s_oil
    elif oil_status == "LOOKALIKE":
        confidence = s_lookalike
    else:
        confidence = s_no_oil

    scene_classification = {
        "label": label,
        "confidence": round(confidence, 4),
        "probabilities": {
            "Oil": s_oil,
            "No-Oil": s_no_oil,
            "Lookalike": s_lookalike,
        },
        "scores": {
            "Oil": s_oil,
            "Lookalike": s_lookalike,
            "No Oil": s_no_oil,
        },
        "oil_status": oil_status,
        "score_type": "heuristic_score",
        "score_type_label": "Heuristic score",
        "detector_name": "Classical Detector",
        "reasoning": reasoning,
    }

    return DetectionResult(
        mask=best_mask,
        candidate_masks=candidates,
        confidence=confidence,
        notes=notes,
        processing_time_ms=round((time.perf_counter() - _t0) * 1000, 2),
        scene_classification=scene_classification,
    )
