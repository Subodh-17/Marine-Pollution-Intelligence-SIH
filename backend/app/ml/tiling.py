"""
Tiled inference for the SpillUNet segmenter on scenes larger than fits in
one forward pass.

WHY TILE RATHER THAN RESIZE
----------------------------
A real Sentinel-1 scene is typically far larger than the model's training
resolution (256x256 patches - see ML_TRAIN_IMAGE_SIZE / dataset.py). Resizing
the whole scene down to fit in memory would destroy the spatial detail an oil
slick's shape and extent are judged by, and would silently run the model far
outside its trained input distribution. Tiling instead runs the *exact same*
per-pixel model the checkpoint was evaluated with, just in pieces, and
stitches the pieces back into a full-resolution result.

WHY THIS IS ONLY FOR THE SEGMENTER
-----------------------------------
The classifier makes one whole-scene decision (Oil / No-Oil / Lookalike).
There is no principled way to average per-tile classifier logits into a
single scene-level answer without inventing an aggregation policy this
codebase has no training-time equivalent for - doing so would be exactly the
kind of "invent a new preprocessing/behavior" this project's instructions
prohibit. For scenes too large to classify directly, detector.py instead
classifies a block-mean-downsampled representation of the whole scene, which
is a scene-level view (just coarser), not a fabricated aggregation - and
labels the result accordingly.
"""
from __future__ import annotations

import logging
from typing import Callable, List, Tuple

import numpy as np

logger = logging.getLogger(__name__)


def compute_tile_bounds(h: int, w: int, tile_size: int, overlap: int) -> List[Tuple[int, int, int, int]]:
    """Returns (y0, y1, x0, x1) covering the full (h, w) image.

    Every tile is exactly `tile_size` (except when the image itself is
    smaller than one tile, in which case there is exactly one tile spanning
    the whole image) - this is what handles "dimensions not an exact
    multiple of tile size": rather than emitting a small ragged final tile
    (which could violate the U-Net's own divisible-by-4 requirement), the
    last tile in each axis is pulled back so it still ends exactly at the
    image edge and is still full-size, overlapping its neighbour by however
    much extra that takes.
    """
    if tile_size <= 0:
        raise ValueError(f"tile_size must be positive, got {tile_size}")
    if overlap < 0 or overlap >= tile_size:
        raise ValueError(f"overlap must be in [0, tile_size), got {overlap} for tile_size={tile_size}")

    def axis_starts(length: int) -> List[int]:
        if length <= tile_size:
            return [0]
        stride = tile_size - overlap
        starts = list(range(0, length - tile_size + 1, stride))
        if starts[-1] + tile_size < length:
            starts.append(length - tile_size)  # pull the final tile back to the edge
        return starts

    y_starts = axis_starts(h)
    x_starts = axis_starts(w)
    bounds = []
    for y0 in y_starts:
        y1 = min(y0 + tile_size, h)
        for x0 in x_starts:
            x1 = min(x0 + tile_size, w)
            bounds.append((y0, y1, x0, x1))
    return bounds


def _generate_tile_weights(
    tile_h: int, tile_w: int, overlap: int,
    is_top: bool, is_bottom: bool, is_left: bool, is_right: bool,
) -> np.ndarray:
    """Computes a 2D cosine-taper blending window for interior overlapping seams.
    Exterior scene boundaries retain full 1.0 weight to prevent edge attenuation."""
    wy = np.ones(tile_h, dtype=np.float64)
    wx = np.ones(tile_w, dtype=np.float64)

    if overlap > 0:
        ov_y = min(overlap, tile_h // 2)
        if ov_y > 0:
            ramp_y = 0.5 * (1.0 - np.cos(np.linspace(0, np.pi, ov_y)))
            if not is_top:
                wy[:ov_y] = ramp_y
            if not is_bottom:
                wy[-ov_y:] = ramp_y[::-1]

        ov_x = min(overlap, tile_w // 2)
        if ov_x > 0:
            ramp_x = 0.5 * (1.0 - np.cos(np.linspace(0, np.pi, ov_x)))
            if not is_left:
                wx[:ov_x] = ramp_x
            if not is_right:
                wx[-ov_x:] = ramp_x[::-1]

    return np.outer(wy, wx)


def stitch_tiled_segmentation(
    run_fn: Callable[[np.ndarray], np.ndarray],
    stacked: np.ndarray,
    tile_size: int,
    overlap: int,
) -> np.ndarray:
    """Runs `run_fn` (a (2, h, w) -> (h, w) sigmoid-probability forward pass,
    i.e. MLDetectorEngine._forward_segmenter) over tiles of `stacked`
    (2, H, W) and stitches them into one (H, W) probability map.

    SIH-26143 AUDIT REVISION (§2):
    Applies cosine distance-to-edge feathering across overlapping tile seams
    while preserving true exterior image borders, eliminating visible seam
    artifacts without attenuating valid boundary signals.
    """
    if stacked.ndim != 3 or stacked.shape[0] != 2:
        raise ValueError(f"expected (2, H, W), got {stacked.shape}")
    _, h, w = stacked.shape

    bounds = compute_tile_bounds(h, w, tile_size, overlap)
    acc = np.zeros((h, w), dtype=np.float64)
    count = np.zeros((h, w), dtype=np.float64)

    for (y0, y1, x0, x1) in bounds:
        tile = stacked[:, y0:y1, x0:x1]
        prob = run_fn(tile)
        if prob.shape != (y1 - y0, x1 - x0):
            raise ValueError(
                f"tile forward pass returned shape {prob.shape}, expected {(y1 - y0, x1 - x0)}"
            )
        weights = _generate_tile_weights(
            tile_h=y1 - y0, tile_w=x1 - x0, overlap=overlap,
            is_top=(y0 == 0), is_bottom=(y1 == h),
            is_left=(x0 == 0), is_right=(x1 == w),
        )
        acc[y0:y1, x0:x1] += prob * weights
        count[y0:y1, x0:x1] += weights

    # count is >= 1 everywhere by construction (bounds cover the full image)
    return (acc / np.maximum(count, 1e-8)).astype(np.float32)


def downsample_mean(arr: np.ndarray, max_dim: int) -> np.ndarray:
    """Block-mean downsample a 2D array so neither dimension exceeds
    `max_dim`. Used ONLY for the classifier's whole-scene view of a scene too
    large to classify directly in one pass.

    SIH-26143 AUDIT REVISION (§2):
    Uses edge-padded block averaging so no boundary pixels are cropped or discarded
    when dimensions are not exact multiples of the downsampling factor.
    """
    h, w = arr.shape
    if h <= max_dim and w <= max_dim:
        return arr
    factor = max(1, int(np.ceil(max(h, w) / max_dim)))
    pad_h = (factor - (h % factor)) % factor
    pad_w = (factor - (w % factor)) % factor
    if pad_h > 0 or pad_w > 0:
        padded = np.pad(arr, ((0, pad_h), (0, pad_w)), mode="edge")
    else:
        padded = arr
    new_h, new_w = padded.shape
    reshaped = padded.reshape(new_h // factor, factor, new_w // factor, factor)
    out = reshaped.mean(axis=(1, 3)).astype(arr.dtype)
    if out.shape[0] > max_dim or out.shape[1] > max_dim:
        out = out[:max_dim, :max_dim]
    return out


def stitch_multiscale_segmentation(
    run_fn: Callable[[np.ndarray], np.ndarray],
    stacked: np.ndarray,
    tile_size: int,
    overlap: int,
    fits_direct: bool = False,
    scale_factor: float = 0.5,
    coarse_weight: float = 0.95,
    fusion_strategy: str = "boundary_preserved",
) -> Tuple[np.ndarray, bool]:
    """Runs multiscale segmentation to recover large-slick receptive-field loss.

    WHY MULTISCALE IS NEEDED (SIH-26143 AUDIT REVISION §2):
    SpillUNet has two downsampling stages with an effective receptive field of
    ~35-45 pixels. In the interior of large oil slicks (Q4 slicks >11% frame
    coverage), every pixel within the receptive field is uniform dark water with
    no visible boundary, causing severe under-segmentation (historical Q4 recall ~0.335,
    predicted-to-true area ratio ~0.262).

    By executing a coarse-scale forward pass at half resolution (0.5x), the effective
    receptive field doubles to ~70-90 pixels of original image context. The coarse pass
    sees slick boundaries from deep within the interior.

    FUSION STRATEGIES (SIH-26143 AUDIT REVISION §4):
    1. 'boundary_preserved' (default & promoted):
       Applies morphological minimum filter (erosion, size=3) in coarse probability
       space prior to upsampling. This retracts coarse support away from the exterior
       boundary of slicks, ensuring base resolution strictly controls fine perimeter
       boundaries (eliminating boundary blooming / false-positive expansion on small slicks),
       while fully preserving interior probability for large slicks (receptive-field infill).
    2. 'direct_max':
       Direct soft-max fusion: max(prob_base, prob_coarse_up * coarse_weight).
    3. 'base_only':
       Bypasses coarse-scale fusion entirely, returning base-scale probabilities
       (zero-risk rollback path).

    Returns (fused_probability_map, multiscale_applied_flag).
    """
    if stacked.ndim != 3 or stacked.shape[0] != 2:
        raise ValueError(f"expected (2, H, W), got {stacked.shape}")
    _, h, w = stacked.shape

    # 1. Base scale segmentation
    if fits_direct:
        prob_base = run_fn(stacked)
    else:
        prob_base = stitch_tiled_segmentation(run_fn, stacked, tile_size, overlap)

    prob_base = np.nan_to_num(np.clip(prob_base, 0.0, 1.0), nan=0.0).astype(np.float32)

    if fusion_strategy == "base_only":
        return prob_base, False

    # 2. Check if multiscale coarse pass is feasible
    if min(h, w) < 64:
        return prob_base, False

    coarse_h = max(32, int(round(h * scale_factor)))
    coarse_w = max(32, int(round(w * scale_factor)))
    # SpillUNet requires dimensions divisible by 4
    coarse_h = (coarse_h // 4) * 4
    coarse_w = (coarse_w // 4) * 4

    if coarse_h < 32 or coarse_w < 32:
        return prob_base, False

    # 3. Downsample SAR bands to coarse scale
    import scipy.ndimage as ndi
    zoom_y = coarse_h / h
    zoom_x = coarse_w / w
    coarse_vv = ndi.zoom(stacked[0], (zoom_y, zoom_x), order=1).astype(np.float32)
    coarse_vh = ndi.zoom(stacked[1], (zoom_y, zoom_x), order=1).astype(np.float32)
    stacked_coarse = np.stack([coarse_vv, coarse_vh], axis=0)

    # 4. Run segmenter on coarse representation
    try:
        if fits_direct or (coarse_h <= tile_size and coarse_w <= tile_size):
            prob_coarse = run_fn(stacked_coarse)
        else:
            prob_coarse = stitch_tiled_segmentation(run_fn, stacked_coarse, tile_size, overlap)
        prob_coarse = np.nan_to_num(np.clip(prob_coarse, 0.0, 1.0), nan=0.0).astype(np.float32)
    except Exception as exc:
        logger.warning('Multiscale coarse pass failed, falling back to base segmentation: %s', exc)
        return prob_base, False

    # 5. Apply fusion strategy
    if fusion_strategy in ("boundary_preserved", "size_gated_closure"):
        # Morphological erosion in coarse space: retracts coarse mask by 1 coarse pixel (2 full-res pixels).
        # Prevents blooming at edges of small slicks while retaining interior of large slicks.
        prob_coarse_to_fuse = ndi.minimum_filter(prob_coarse, size=3)
    else:
        prob_coarse_to_fuse = prob_coarse

    up_y = h / coarse_h
    up_x = w / coarse_w
    prob_coarse_up = ndi.zoom(prob_coarse_to_fuse, (up_y, up_x), order=1).astype(np.float32)

    # Dimensional safeguard for sub-pixel interpolation rounding
    if prob_coarse_up.shape != (h, w):
        if prob_coarse_up.shape[0] < h or prob_coarse_up.shape[1] < w:
            prob_coarse_up = np.pad(
                prob_coarse_up,
                ((0, max(0, h - prob_coarse_up.shape[0])), (0, max(0, w - prob_coarse_up.shape[1]))),
                mode="edge",
            )
        prob_coarse_up = prob_coarse_up[:h, :w]

    coarse_eff = np.clip(prob_coarse_up * coarse_weight, 0.0, 1.0)

    # 6. Strategy-specific fusion
    if fusion_strategy == "size_gated_closure":
        # SIH-26143 AUDIT REVISION (Phase 4.3B):
        # Size-Gated Morphological Infill: Coarse-scale infill is applied ONLY within the
        # morphological closure of confirmed base slick components having area >= 100 px.
        # This prevents coarse false positives in isolated lookalikes and clean sea,
        # while fully recovering interior hollows of large slicks.
        base_mask = prob_base > 0.45
        if np.any(base_mask):
            lbl, n = ndi.label(base_mask)
            if n > 0:
                sizes = ndi.sum(base_mask, lbl, range(1, n + 1))
                valid_lbls = [i + 1 for i, s in enumerate(sizes) if s >= 100]
                if valid_lbls:
                    large_seed = np.isin(lbl, valid_lbls)
                    struct = np.ones((25, 25), dtype=bool)
                    closed = ndi.binary_closing(large_seed, structure=struct) | ndi.binary_fill_holes(large_seed)
                    prob_fused = np.where(closed, np.maximum(prob_base, coarse_eff), prob_base).astype(np.float32)
                    return prob_fused, True
        return prob_base, True

    # Default / boundary_preserved: soft max fusion
    prob_fused = np.maximum(prob_base, coarse_eff).astype(np.float32)
    return prob_fused, True
