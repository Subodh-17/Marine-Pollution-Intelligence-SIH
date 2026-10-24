"""
Spill age estimation.

Real oil-slick "age" estimation from a single SAR scene is a genuinely hard,
low-confidence problem in the remote-sensing literature - it normally
requires multi-temporal imagery (repeat passes) or calibrated
weathering/spreading models. With a SINGLE scene (as in this prototype) we
can only produce a coarse heuristic bucket from spreading/thinning proxies
(area-to-perimeter ratio, aspect ratio elongation under assumed wind/current
stretching), and we must say so honestly.

Per the SIH spec (Section 9): "If reliable age estimation is not possible
from the available data, explicitly show 'Age estimate unavailable / low
confidence.' Do not invent an age."
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AgeEstimate:
    hours: float | None
    hours_low: float | None
    hours_high: float | None
    confidence: str  # "low" | "unavailable"
    method: str
    caveat: str


def estimate_age(aspect_ratio: float, area_km2: float, wind_speed_ms: float | None) -> AgeEstimate:
    """
    Heuristic single-scene age proxy:

    Freshly released oil tends to be more compact/rounder; as it weathers it
    is stretched and thinned by wind/current shear, increasing elongation
    and area for a given released volume. We use aspect_ratio and area as
    weak proxies to bucket a *very rough* age range, explicitly flagged as
    low-confidence, single-scene, non-calibrated.

    This is NOT a validated model - it has not been fit or tested against
    ground truth because no ground-truth spill-age dataset was available in
    this environment. It exists to satisfy the pipeline stage with an
    honestly-labelled placeholder that can be replaced with a real
    multi-temporal or physically calibrated model later.
    """
    import math
    if (
        aspect_ratio is None
        or not math.isfinite(aspect_ratio)
        or aspect_ratio <= 0
        or area_km2 is None
        or not math.isfinite(area_km2)
        or area_km2 <= 0
    ):
        return AgeEstimate(
            hours=None, hours_low=None, hours_high=None,
            confidence="unavailable", method="none",
            caveat="Age estimate unavailable / low confidence: insufficient geometry.",
        )

    wind = wind_speed_ms if wind_speed_ms is not None else 6.0
    stretch_factor = aspect_ratio * (1 + 0.05 * wind)
    rough_hours = float(min(72.0, max(1.0, stretch_factor * 2.4)))

    return AgeEstimate(
        hours=round(rough_hours, 1),
        hours_low=round(max(0.5, rough_hours * 0.55), 1),
        hours_high=round(min(96.0, rough_hours * 1.7), 1),
        confidence="low",
        method="single_scene_spreading_heuristic (aspect-ratio + wind stretch proxy)",
        caveat=(
            "Single-scene age estimate. This is a low-confidence heuristic "
            "based on slick elongation, not a calibrated physical or "
            "multi-temporal model. Treat as an order-of-magnitude indication "
            "only."
        ),
    )
