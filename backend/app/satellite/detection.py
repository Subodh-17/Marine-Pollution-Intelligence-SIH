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
    is_flat_scene: bool = False,
    raw_dynamic_range: float | None = None,
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

    if is_flat_scene:
        empty_mask = np.zeros_like(image, dtype=bool) if (image is not None and isinstance(image, np.ndarray)) else np.zeros((0, 0), dtype=bool)

        # Physically derived No-Oil confidence based on backscatter uniformity:
        # Open calm ocean without wave perturbance or oil slick features exhibits low dynamic range.
        # As raw dynamic range decreases from 20 DN down towards 0 DN, backscatter homogeneity
        # increases, producing higher physical confidence in clean sea.
        if raw_dynamic_range is not None and np.isfinite(raw_dynamic_range):
            dr = float(np.clip(raw_dynamic_range, 0.0, 20.0))
            dr_ratio = dr / 20.0
            homogeneity = 1.0 - dr_ratio
            raw_no_oil = float(np.clip(0.88 + 0.09 * homogeneity, 0.88, 0.98))
            raw_lookalike = float(np.clip(0.01 + 0.08 * dr_ratio, 0.01, 0.10))
            raw_oil = float(np.clip(0.01 + 0.01 * dr_ratio, 0.01, 0.02))
        else:
            var = float(np.var(image)) if image is not None and image.size > 0 else 0.0
            raw_no_oil = float(np.clip(0.92 - 0.5 * var, 0.85, 0.95))
            raw_lookalike = float(np.clip(0.05 + 0.3 * var, 0.03, 0.10))
            raw_oil = 0.01

        tot = raw_oil + raw_lookalike + raw_no_oil
        s_oil = float(np.clip(round(raw_oil / tot, 4), 0.0, 1.0))
        s_lookalike = float(np.clip(round(raw_lookalike / tot, 4), 0.0, 1.0))
        s_no_oil = float(np.clip(round(1.0 - s_oil - s_lookalike, 4), 0.0, 1.0))
        residual = round(1.0 - (s_oil + s_lookalike + s_no_oil), 4)
        if residual != 0:
            s_no_oil = round(s_no_oil + residual, 4)

        conf = s_no_oil
        dr_text = f" ({raw_dynamic_range:.1f} DN < 20 DN)" if raw_dynamic_range is not None else " (<20 DN)"

        return DetectionResult(
            mask=empty_mask,
            candidate_masks=[],
            confidence=conf,
            notes=[f"Scene exhibits uniform open sea surface backscatter with low dynamic range{dr_text}; no anomalous dark features present."],
            processing_time_ms=round((time.perf_counter() - _t0) * 1000, 2),
            scene_classification={
                "label": "No Oil",
                "confidence": conf,
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
                "oil_status": "NO_OIL",
                "score_type": "heuristic_score",
                "score_type_label": "Heuristic score",
                "detector_name": "Classical Detector",
                "reasoning": f"Scene exhibits uniform open sea surface backscatter with negligible variance{dr_text}; no anomalous dark features present.",
            },
        )

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
        # Local bounding-box & neighborhood background contrast evaluation
        min_r, min_c, max_r, max_c = r.bbox
        pad = 12
        b_min_r = max(0, min_r - pad)
        b_max_r = min(h, max_r + pad)
        b_min_c = max(0, min_c - pad)
        b_max_c = min(w, max_c + pad)

        patch_labels = labels[b_min_r:b_max_r, b_min_c:b_max_c]
        patch_mask = (patch_labels == r.label)
        patch_gray = img[b_min_r:b_max_r, b_min_c:b_max_c]

        patch_dil = ndi.binary_dilation(patch_mask, iterations=8)
        patch_bg = patch_dil & (~patch_mask)

        if patch_mask.sum() > 0 and patch_bg.sum() > 0:
            reg_mean = float(patch_gray[patch_mask].mean())
            bg_mean = float(patch_gray[patch_bg].mean())
            contrast = max(0.0, bg_mean - reg_mean)
        else:
            contrast = 0.0

        tex = _texture_score(img, patch_mask, tot_sum=tot_sum, tot_sq=tot_sq, sub_gray=patch_gray)
        solidity = r.solidity  # area / convex_hull_area

        # heuristic plausibility score incorporating capillary damping contrast
        plausibility = (
            0.35 * float(np.clip((elongation - 1) / 5, 0, 1))
            + 0.30 * tex
            + 0.15 * float(np.clip(solidity, 0, 1))
            + 0.10 * float(np.clip(r.area / min_region_px / 10, 0, 1))
            + 0.10 * float(np.clip(contrast / 0.20, 0, 1))
        )
        scored.append((plausibility, r, elongation, tex, solidity, contrast))

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
        best_lw = max(low_wind_regions, key=lambda r: r.area)
        best_mask = (labels == best_lw.label)
        candidates = [best_mask]
        lw_fraction = float(np.clip(best_lw.area / max(1, h * w), 0.0, 1.0))
        raw_lookalike = float(np.clip(0.70 + 0.25 * lw_fraction, 0.70, 0.95))
        raw_no_oil = float(np.clip(0.20 * (1.0 - lw_fraction), 0.04, 0.20))
        raw_oil = 0.02
        label = "Lookalike"
        oil_status = "LOOKALIKE"
        notes.append(f"Large low-wind area detected ({best_lw.area}px > {int(max_area)}px) - classified as lookalike. Despite low-backscatter feature presence, this is NOT a confirmed or likely oil detection - it is a natural calm-sea lookalike.")
        reasoning = f"Detected anomalous dark region exceeding {max_region_frac*100:.0f}% of scene frame ({lw_fraction*100:.1f}%), characteristic of low-wind calm sea surface rather than an oil spill."

    else:
        scored.sort(key=lambda t: t[0], reverse=True)
        best_score, best_region, elongation, tex, solidity, contrast = scored[0]

        # Full mask only created for winning candidate
        best_mask = (labels == best_region.label)
        best_mask = ndi.binary_fill_holes(best_mask)
        candidates = [best_mask]

        all_areas = sorted([r.area for r in regions], reverse=True)
        max_scene_area = all_areas[0] if all_areas else 0
        second_scene_area = all_areas[1] if len(all_areas) > 1 else 0
        second_area = second_scene_area
        total_candidates = len(scored)
        large_patches = sum(1 for r in regions if r.area >= 400)

        # Inspect largest region in the scene for macro-slick damping and morphology
        max_r = max(regions, key=lambda r: r.area) if regions else None
        max_r_contrast = contrast
        max_r_tex = tex
        max_r_elongation = elongation
        if max_r and max_r.label != best_region.label:
            m_min_r, m_min_c, m_max_r, m_max_c = max_r.bbox
            mb_min_r = max(0, m_min_r - 12)
            mb_max_r = min(h, m_max_r + 12)
            mb_min_c = max(0, m_min_c - 12)
            mb_max_c = min(w, m_max_c + 12)
            m_labels = labels[mb_min_r:mb_max_r, mb_min_c:mb_max_c]
            m_mask = (m_labels == max_r.label)
            m_gray = img[mb_min_r:mb_max_r, mb_min_c:mb_max_c]
            m_dil = ndi.binary_dilation(m_mask, iterations=8)
            m_bg = m_dil & (~m_mask)
            if m_mask.sum() > 0 and m_bg.sum() > 0:
                max_r_contrast = max(0.0, float(m_gray[m_bg].mean()) - float(m_gray[m_mask].mean()))
            max_r_tex = _texture_score(img, m_mask, tot_sum=tot_sum, tot_sq=tot_sq, sub_gray=m_gray)
            max_r_elongation = max_r.axis_major_length / max(max_r.axis_minor_length, 1e-6) if max_r.axis_minor_length > 0 else 0

        # Physical dominance & lookalike criteria:
        # A true oil spill has a coherent slick footprint that exhibits high capillary wave damping,
        # smooth internal texture, and wind/current-driven elongation.
        # Natural lookalike fields (such as oceanic internal wave trains or biogenic films) exhibit
        # multiple competing parallel bands or fragments of comparable size without linear streamline dominance.
        has_competing_major_patch = (
            second_scene_area > 0
            and max_scene_area < 2.5 * second_scene_area
            and len(regions) >= 5
            and (large_patches >= 4 or best_region.area < 1000)
        )
        is_genuine_oil_morphology = (
            (max_r_elongation >= 6.0 and max_scene_area >= 5000)
            or (contrast >= 0.40 and elongation >= 5.0)
            or (max_scene_area >= 10000 and max_r_contrast >= 0.25 and (max_r_tex >= 0.60 or elongation >= 10.0))
            or (max_scene_area >= 50000)
        )
        is_wave_train = has_competing_major_patch and not is_genuine_oil_morphology

        # Calm sprawl lookalike (large sprawling patch but low contrast or no elongation):
        is_calm_sprawl = (
            best_region.area < 0.50 * max_scene_area
            and max_scene_area >= 1500
            and max_r_contrast < 0.12
            and elongation < 2.0
            and max_r_elongation < 3.0
        )

        # Rough interior texture lookalike:
        is_rough_interior = (tex < 0.22 and max_r_tex < 0.22)

        is_lookalike_anomaly = is_wave_train or is_calm_sprawl or is_rough_interior

        # Physical Oil Spill Criteria:
        has_significant_damping = (contrast >= 0.045 or max_r_contrast >= 0.08)
        has_smooth_texture = (tex >= 0.22 or max_r_tex >= 0.22)
        has_oil_shape = (elongation >= 1.45) or (best_region.area >= 500 and elongation >= 1.20) or (contrast >= 0.10 and max_scene_area >= 1000 and elongation >= 1.15)
        has_sufficient_area = (best_region.area >= min_oil_area_px or max_scene_area >= min_oil_area_px)
        has_oil_plausibility = (best_score >= 0.38) or (contrast >= 0.08 and best_region.area >= 400 and best_score >= 0.32) or (max_scene_area >= 3000 and max_r_contrast >= 0.08 and best_score >= 0.35)

        is_oil = (
            has_oil_plausibility
            and has_oil_shape
            and has_smooth_texture
            and has_sufficient_area
            and has_significant_damping
            and not is_lookalike_anomaly
        )

        # Clean sea backscatter checks (reject sub-slick noise, negligible damping, faint background, diffuse clutter):
        is_sub_slick_noise = (max_scene_area < min_oil_area_px and contrast < 0.08)
        has_negligible_damping = (contrast < 0.040 and max_scene_area < 500)
        is_unremarkable_background = (best_score < 0.32 and max_scene_area < 500)
        is_diffuse_sea_clutter = (
            (dark_fraction > 0.02 and total_candidates > 25 and max_scene_area < 500 and contrast < 0.065)
            or (dark_fraction > 0.20 and total_candidates > 40 and best_region.area < 500)
        )

        is_clean_sea = not is_oil and (
            is_sub_slick_noise
            or has_negligible_damping
            or is_unremarkable_background
            or is_diffuse_sea_clutter
        )

        if is_oil:
            label = "Oil"
            oil_status = "LIKELY_OIL"
            raw_oil = float(np.clip(0.55 + 0.40 * best_score, 0.55, 0.95))
            raw_lookalike = float(np.clip((1.0 - best_score) * 0.40 + 0.05, 0.03, 0.30))
            raw_no_oil = float(np.clip(0.05 + 0.10 * (1.0 - np.clip(best_region.area / 5000, 0, 1)), 0.01, 0.15))
            notes.append(f"Selected region id={best_region.label}: elongation={elongation:.2f}, texture_score={tex:.2f}, plausibility={best_score:.2f}, area={best_region.area}px, contrast={contrast:.3f}")
            reasoning = f"Candidate exhibits significant capillary wave damping (contrast {contrast:.2f}, texture score {tex:.2f}), wind-driven elongation ({elongation:.2f}:1), and coherent slick footprint ({best_region.area}px)."
        elif is_clean_sea:
            label = "No Oil"
            oil_status = "NO_OIL"
            raw_no_oil = float(np.clip(0.70 + 0.22 * (1.0 - np.clip(contrast / 0.08, 0, 1)), 0.70, 0.94))
            raw_lookalike = float(np.clip(0.08 + 0.15 * np.clip(contrast / 0.08, 0, 1), 0.05, 0.24))
            raw_oil = float(np.clip(0.02 + 0.03 * (best_score / 0.50), 0.01, 0.05))
            best_mask = np.zeros_like(image, dtype=bool)
            candidates = []
            notes.append(f"Clean sea surface backscatter detected: candidate features are sub-slick speckle noise (area {best_region.area}px < {min_oil_area_px}px or contrast {contrast:.3f} < 0.080); this scene is NOT classified as containing oil.")
            reasoning = f"Detected features correspond to natural sea surface backscatter and minor wave texture (area {best_region.area}px, contrast {contrast:.3f}, score {best_score:.2f}) without coherent slick damping anomaly."
        elif (best_region.area >= min_oil_area_px or max_scene_area >= min_oil_area_px) and contrast >= 0.040:
            label = "Lookalike"
            oil_status = "LOOKALIKE"
            raw_lookalike = float(np.clip(0.60 + 0.30 * (1.0 - best_score) + (0.10 if is_lookalike_anomaly else 0.0), 0.60, 0.90))
            raw_oil = float(np.clip(0.25 * best_score, 0.04, 0.22))
            raw_no_oil = float(np.clip(0.20 * (1.0 - dark_fraction), 0.05, 0.25))
            notes.append(f"Flagged region id={best_region.label} as lookalike: elongation={elongation:.2f}, texture_score={tex:.2f}, plausibility={best_score:.2f}, area={best_region.area}px (fails oil-slick criteria). Despite dark anomaly presence, this is NOT a confirmed or likely oil detection - it is an uncertain SAR lookalike anomaly.")
            if is_wave_train:
                reasoning = f"Dark feature is part of a widespread oceanic internal wave train ({len(regions)} crests across {dark_fraction*100:.1f}% sea), characteristic of natural periodic lookalikes."
            elif is_calm_sprawl:
                reasoning = f"Dark feature is part of an amorphous calm sea / low-wind sprawl (area {max_scene_area}px, contrast {max_r_contrast:.3f}), lacking the capillary damping and elongation of crude mineral oil."
            elif elongation < 1.40 and best_score < 0.42:
                reasoning = f"Dark feature detected but lacks characteristic wind/current elongation (elongation {elongation:.2f}:1), consistent with natural calm-sea lookalike."
            elif not has_significant_damping:
                reasoning = f"Dark feature exhibits weak capillary wave damping ({contrast:.3f} < 0.045), characteristic of natural surface tension lookalikes rather than crude mineral oil."
            else:
                reasoning = f"Dark feature detected (area {best_region.area}px, contrast {contrast:.2f}) but fails oil-slick morphological dominance criteria (elongation {elongation:.2f}, texture {tex:.2f})."
        else:
            label = "Uncertain"
            oil_status = "UNCERTAIN"
            raw_lookalike = 0.45
            raw_no_oil = 0.45
            raw_oil = 0.10
            notes.append("Dark anomaly exhibits ambiguous SAR characteristics; classified as uncertain.")
            reasoning = "Dark anomaly exhibits ambiguous SAR characteristics between sea clutter and natural lookalike."

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
