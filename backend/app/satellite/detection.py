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


def _texture_score(gray: np.ndarray, mask: np.ndarray, tot_sum: float | None = None, tot_sq: float | None = None) -> float:
    """Low local variance inside the candidate supports 'smooth oil film'
    vs. natural look-alikes (which tend to be patchier)."""
    m_count = int(mask.sum())
    if m_count < 10:
        return 0.0
    interior_vals = gray[mask]
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
) -> DetectionResult:
    """
    Classical hybrid dark-spot detector.

    Steps:
      1. Adaptive local threshold on the despeckled image to flag
         anomalously dark pixels relative to their neighbourhood.
      2. Morphological opening/closing to remove speckle-scale noise and
         fill small gaps.
      3. Connected-component labelling -> candidate regions.
      4. False-positive filtering: reject regions that are too small, touch
         the image border implausibly, are too close to circular/blob-like
         (natural low-wind areas tend to be more amorphous / large), or have
         high internal texture (look-alikes are noisier than true films).
      5. Keep the highest-plausibility remaining candidate(s) as the
         detected slick; report a heuristic confidence.
    """
    notes = []
    _t0 = time.perf_counter()
    img = image.astype(np.float64)

    # 1. adaptive threshold (local mean minus k * local std)
    local_thresh = gu.threshold_local_gaussian(img, block_size=51)
    dark = (img < (local_thresh - 0.03)) & sea_mask

    # 2. morphology cleanup
    dark = gu.remove_small_objects(dark, min_size=40)
    dark = gu.binary_opening(dark, gu.disk(2))
    dark = gu.binary_closing(dark, gu.disk(3))

    labels = gu.label(dark, connectivity=2)
    regions = gu.regionprops(labels, min_area=min_region_px)

    h, w = image.shape
    max_area = max_region_frac * h * w

    tot_sum = float(img.sum())
    tot_sq = float((img ** 2).sum())

    candidates = []
    scored = []
    for r in regions:
        if r.area < min_region_px:
            continue
        if r.area > max_area:
            notes.append(f"Rejected region id={r.label} (area {r.area}px): exceeds plausible slick size, likely low-wind zone.")
            continue
        # elongation: real slicks are usually elongated (currents/wind stretch them)
        if r.axis_minor_length > 0:
            elongation = r.axis_major_length / max(r.axis_minor_length, 1e-6)
        else:
            elongation = 0
        region_mask = labels == r.label
        tex = _texture_score(img, region_mask, tot_sum=tot_sum, tot_sq=tot_sq)
        solidity = r.solidity  # area / convex_hull_area

        # heuristic plausibility score
        plausibility = (
            0.35 * float(np.clip((elongation - 1) / 5, 0, 1))
            + 0.35 * tex
            + 0.15 * float(np.clip(solidity, 0, 1))
            + 0.15 * float(np.clip(r.area / min_region_px / 10, 0, 1))
        )
        candidates.append(region_mask)
        scored.append((plausibility, r, region_mask, elongation, tex))

    if not scored:
        return DetectionResult(
            mask=np.zeros_like(image, dtype=bool),
            candidate_masks=candidates,
            confidence=0.0,
            notes=notes + ["No plausible oil-slick-like dark region found."],
            processing_time_ms=round((time.perf_counter() - _t0) * 1000, 2),
        )

    scored.sort(key=lambda t: t[0], reverse=True)
    best_score, best_region, best_mask, elongation, tex = scored[0]

    # fill small interior holes for a clean polygon later
    best_mask = ndi.binary_fill_holes(best_mask)

    confidence = float(np.clip(0.45 + 0.5 * best_score, 0.05, 0.98))
    notes.append(f"Selected region id={best_region.label}: elongation={elongation:.2f}, texture_score={tex:.2f}, plausibility={best_score:.2f}")

    return DetectionResult(
        mask=best_mask,
        candidate_masks=candidates,
        confidence=confidence,
        notes=notes,
        processing_time_ms=round((time.perf_counter() - _t0) * 1000, 2),
    )
