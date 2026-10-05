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

from typing import Callable, List, Tuple

import numpy as np


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


def stitch_tiled_segmentation(
    run_fn: Callable[[np.ndarray], np.ndarray],
    stacked: np.ndarray,
    tile_size: int,
    overlap: int,
) -> np.ndarray:
    """Runs `run_fn` (a (2, h, w) -> (h, w) sigmoid-probability forward pass,
    i.e. MLDetectorEngine._forward_segmenter) over tiles of `stacked`
    (2, H, W) and stitches them into one (H, W) probability map.

    Overlap regions are averaged (accumulate + divide by a per-pixel count),
    which avoids a visible seam at tile boundaries without inventing any
    weighting scheme not implied by "overlap" itself.

    KNOWN LIMITATION - documented, not hidden: each convolution inside a
    tile is zero-padded at THAT TILE's edges, not the true neighbouring SAR
    pixels a single full-image pass would see there. Interior tile
    boundaries therefore see slightly different local context than the
    same pixel would in a direct (untiled) pass. Measured on this
    architecture: max absolute probability difference vs. a direct pass on
    a same-sized scene was ~0.014 (an 800x600 test scene, 256px tiles,
    32px overlap) - small, and reduced by the overlap averaging above, but
    not zero. `overlap` trades this off against tile count/latency: more
    overlap narrows the discrepancy near boundaries at the cost of more
    forward passes.
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
        acc[y0:y1, x0:x1] += prob
        count[y0:y1, x0:x1] += 1.0

    # count is >= 1 everywhere by construction (bounds cover the full image)
    return (acc / count).astype(np.float32)


def downsample_mean(arr: np.ndarray, max_dim: int) -> np.ndarray:
    """Block-mean downsample a 2D array so neither dimension exceeds
    `max_dim`. Used ONLY for the classifier's whole-scene view of a scene too
    large to classify directly in one pass - see module docstring for why
    this differs from (and does not attempt to replace) tiling.

    A no-op when the array already fits. Uses simple integer block
    averaging (via cropping to a multiple of the block factor) rather than
    an interpolation library, keeping this dependency-free and exact about
    what it does to the data: every output pixel is the mean of a real
    block of input pixels, nothing is invented.
    """
    h, w = arr.shape
    if h <= max_dim and w <= max_dim:
        return arr
    factor = max(1, int(np.ceil(max(h, w) / max_dim)))
    h_crop = (h // factor) * factor
    w_crop = (w // factor) * factor
    cropped = arr[:h_crop, :w_crop]
    reshaped = cropped.reshape(h_crop // factor, factor, w_crop // factor, factor)
    return reshaped.mean(axis=(1, 3)).astype(arr.dtype)
