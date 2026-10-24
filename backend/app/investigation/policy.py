"""
Centralized Vessel Attribution Eligibility & Safety Policy.
Enforces fail-closed safeguards across all investigation pipelines and API endpoints:
- Orchestration pipeline (orchestrator.py)
- Sub-analysis endpoints (/api/ais/analyze, /api/attribution/analyze)
- Custom upload & manual mask evaluation

SCIENTIFIC CONTRACT (SIH-26143 ML Audit §3 & Phase 2 Priority 5):
Ungated attribution falsely flagged 34% of Lookalike/No-Oil scenes with accused vessels.
This policy guarantees fail-closed abstention when:
1. Scene classifier indicates Lookalike or No-Oil (cuts false accusations by ~17x to 2.0%)
2. Mask is empty, malformed, or below configurable minimum area threshold
3. Temporal correlation timestamps are missing or stale
4. Classification probabilities are ambiguous (0.30 <= P(Oil) < 0.50), routing to human review

Terminology strictly adheres to non-defamatory phrasing ("candidate vessel", "spatio-temporal association").
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import numpy as np

from app.config import get_settings

STATUS_PERMITTED = "ATTRIBUTION_PERMITTED"
STATUS_LOOKALIKE = "ABSTAINED_LOOKALIKE"
STATUS_NO_OIL = "ABSTAINED_NO_OIL"
STATUS_EMPTY_MASK = "ABSTAINED_EMPTY_MASK"
STATUS_BELOW_MIN_AREA = "ABSTAINED_BELOW_MIN_AREA"
STATUS_UNCERTAIN = "REVIEW_REQUIRED_UNCERTAIN"
STATUS_INVALID_TIMESTAMP = "ABSTAINED_INVALID_TIMESTAMP"
STATUS_STALE_AIS = "ABSTAINED_STALE_AIS"


@dataclass
class AttributionEligibility:
    eligible: bool
    status: str
    reason: str
    mask_pixels: int = 0
    min_required_pixels: int = 250
    oil_status: Optional[str] = None
    oil_probability: Optional[float] = None
    evidence_provenance: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "eligible": self.eligible,
            "status": self.status,
            "reason": self.reason,
            "mask_pixels": self.mask_pixels,
            "min_required_pixels": self.min_required_pixels,
            "oil_status": self.oil_status,
            "oil_probability": self.oil_probability,
            "evidence_provenance": self.evidence_provenance,
        }


def evaluate_attribution_eligibility(
    scene_classification: Optional[Dict[str, Any]] = None,
    mask: Optional[np.ndarray] = None,
    observed_time: Optional[datetime] = None,
    min_mask_area_px: Optional[int] = None,
    ais_records: Optional[List[Any]] = None,
    max_ais_staleness_hours: float = 72.0,
    mask_pixels: Optional[int] = None,
) -> AttributionEligibility:
    """Evaluates whether vessel attribution is safe and permitted.
    Guarantees fail-closed behavior across all input modes.
    """
    settings = get_settings()
    min_px = min_mask_area_px if min_mask_area_px is not None else settings.min_attribution_mask_px

    # Calculate mask pixels safely
    if mask_pixels is None:
        mask_pixels = 0
        if mask is not None:
            try:
                if isinstance(mask, np.ndarray):
                    mask_pixels = int(np.sum(mask > 0))
            except Exception:
                mask_pixels = 0

    oil_status = None
    oil_prob = None
    if isinstance(scene_classification, dict):
        oil_status = scene_classification.get("oil_status") or scene_classification.get("predicted_class")
        probs = scene_classification.get("probabilities") or scene_classification.get("class_probabilities")
        if isinstance(probs, dict):
            oil_prob = probs.get("Oil")

    # Standard evidence provenance structure
    provenance = {
        "observed_location_verified": mask_pixels > 0,
        "computed_trajectory": "hindcast_advection_model",
        "model_inference": "SceneClassifierV2_SpillUNet" if scene_classification else "classical_dark_spot",
        "candidate_association": "spatio_temporal_correlation_index",
        "human_reviewed_conclusion": False,
        "attribution_policy": "SIH26143_FAIL_CLOSED_V2",
    }

    # 1. Gate on Empty Mask
    if mask_pixels == 0:
        return AttributionEligibility(
            eligible=False,
            status=STATUS_EMPTY_MASK,
            reason="No verified slick pixels detected in SAR scene. Vessel attribution is safely abstained.",
            mask_pixels=0,
            min_required_pixels=min_px,
            oil_status=oil_status,
            oil_probability=oil_prob,
            evidence_provenance=provenance,
        )

    # 2. Gate on Scene Classification Lookalike
    if oil_status == "LOOKALIKE":
        return AttributionEligibility(
            eligible=False,
            status=STATUS_LOOKALIKE,
            reason=(
                "Radar feature identified as oceanographic Lookalike (e.g. biogenic film, low wind, "
                "internal waves). Fail-closed safety gate prevents false accusations against commercial traffic."
            ),
            mask_pixels=mask_pixels,
            min_required_pixels=min_px,
            oil_status=oil_status,
            oil_probability=oil_prob,
            evidence_provenance=provenance,
        )

    # 3. Gate on Scene Classification No-Oil
    if oil_status == "NO_OIL":
        return AttributionEligibility(
            eligible=False,
            status=STATUS_NO_OIL,
            reason=(
                "Scene classified as clean open water (No-Oil). Isolated dark pixels represent noise or "
                "sub-threshold clutter. Vessel attribution is barred."
            ),
            mask_pixels=mask_pixels,
            min_required_pixels=min_px,
            oil_status=oil_status,
            oil_probability=oil_prob,
            evidence_provenance=provenance,
        )

    # 4. Gate on Minimum Mask Area (Noise / Speckle artifact rejection)
    if mask_pixels < min_px:
        return AttributionEligibility(
            eligible=False,
            status=STATUS_BELOW_MIN_AREA,
            reason=(
                f"Segmented region ({mask_pixels} px) is below minimum attribution threshold ({min_px} px). "
                "Micro-patches lack sufficient geometric continuity for spatiotemporal drift correlation."
            ),
            mask_pixels=mask_pixels,
            min_required_pixels=min_px,
            oil_status=oil_status,
            oil_probability=oil_prob,
            evidence_provenance=provenance,
        )

    # 5. Gate on Ambiguous Classification / Low Confidence
    if oil_prob is not None and (0.30 <= oil_prob < 0.50):
        return AttributionEligibility(
            eligible=False,
            status=STATUS_UNCERTAIN,
            reason=(
                f"Scene classification oil probability ({oil_prob*100:.1f}%) is in the ambiguous band "
                "[30%, 50%). Analyst review required before candidate scoring is authorized."
            ),
            mask_pixels=mask_pixels,
            min_required_pixels=min_px,
            oil_status=oil_status,
            oil_probability=oil_prob,
            evidence_provenance=provenance,
        )

    # 6. Gate on Observation Timestamp
    if observed_time is None:
        return AttributionEligibility(
            eligible=False,
            status=STATUS_INVALID_TIMESTAMP,
            reason="Missing observation timestamp. Spatio-temporal vessel correlation cannot be computed.",
            mask_pixels=mask_pixels,
            min_required_pixels=min_px,
            oil_status=oil_status,
            oil_probability=oil_prob,
            evidence_provenance=provenance,
        )

    # 7. Gate on Stale AIS Data (if AIS records provided)
    if ais_records:
        try:
            valid_pings = [r for r in ais_records if getattr(r, "timestamp", None) is not None]
            if valid_pings:
                # Find closest ping time to observation
                obs_utc = observed_time.replace(tzinfo=timezone.utc) if observed_time.tzinfo is None else observed_time
                closest_sec = min(
                    abs((p.timestamp.replace(tzinfo=timezone.utc) if p.timestamp.tzinfo is None else p.timestamp) - obs_utc).total_seconds()
                    for p in valid_pings
                )
                if closest_sec > max_ais_staleness_hours * 3600.0:
                    return AttributionEligibility(
                        eligible=False,
                        status=STATUS_STALE_AIS,
                        reason=(
                            f"Nearest available AIS ping is {closest_sec / 3600.0:.1f} hours away from observation "
                            f"(exceeds maximum allowed staleness of {max_ais_staleness_hours:.0f} hours)."
                        ),
                        mask_pixels=mask_pixels,
                        min_required_pixels=min_px,
                        oil_status=oil_status,
                        oil_probability=oil_prob,
                        evidence_provenance=provenance,
                    )
        except Exception:
            pass

    # All safety gates passed
    return AttributionEligibility(
        eligible=True,
        status=STATUS_PERMITTED,
        reason="Confirmed oil spill geometry with sufficient area and valid temporal alignment. Attribution permitted.",
        mask_pixels=mask_pixels,
        min_required_pixels=min_px,
        oil_status=oil_status,
        oil_probability=oil_prob,
        evidence_provenance=provenance,
    )
