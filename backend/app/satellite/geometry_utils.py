"""
Pure NumPy/SciPy geometry & morphology utilities — a scikit-image-free
replacement for the small set of skimage functions the detection pipeline
used (skimage.measure.label/regionprops/find_contours,
skimage.morphology.remove_small_objects/opening/closing/disk,
skimage.filters.threshold_local).

WHY THIS MODULE EXISTS
-----------------------
scikit-image was previously imported in `detection.py` and
`characterization.py`. On at least one deployment target (Windows, behind an
Application Control policy) merely `from skimage import morphology` fails
with `ImportError: DLL load failed while importing _flood_fill_cy` — this
happens even though nothing in this codebase calls flood_fill, because
skimage's package `__init__.py` eagerly imports its submodules, which pulls
in the blocked compiled extension as a side effect of the import itself.

Rather than disable the Windows security policy (not this project's call to
make) or add a new heavyweight dependency like OpenCV, this module
reimplements exactly the handful of skimage functions actually used, with
NumPy + SciPy only (both already required dependencies, and both confirmed
working in the environment that hit the DLL error). The algorithms
themselves are unchanged - same adaptive dark-spot thresholding, same
morphological cleanup, same connected-component + shape/texture scoring,
same ellipse-equivalent geometry from image moments - only the
implementation substrate changed.

Where an exact skimage-internal formula is well-established and easy to
reproduce faithfully (label via 8-connectivity, region centroid/bbox/area,
ellipse axis length + orientation via the inertia tensor, the classic
crack-perimeter estimator), this module reproduces it precisely. Where
skimage's implementation is more involved than this use case needs
(sub-pixel marching-squares contours, pixel-corner convex hull for
solidity), this module uses a documented, slightly coarser approximation -
each one is called out below. None of these approximations change the
*behavior* consumed downstream: detection still scores/ranks candidate
regions the same way, and characterisation still produces real, measured
(not fabricated) geometry from whatever mask it's given.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage as ndi

try:
    from scipy.spatial import ConvexHull, QhullError
except ImportError:  # pragma: no cover - very old scipy
    ConvexHull = None
    QhullError = Exception


# ---------------------------------------------------------------------------
# Connected-component labelling (replaces skimage.measure.label)
# ---------------------------------------------------------------------------

_STRUCT_8 = np.ones((3, 3), dtype=bool)   # 8-connectivity (skimage connectivity=2)
_STRUCT_4 = ndi.generate_binary_structure(2, 1)  # 4-connectivity (skimage connectivity=1)


def label(mask: np.ndarray, connectivity: int = 2) -> np.ndarray:
    """Connected-component labelling. connectivity=2 (default) matches
    skimage's default full/8-connectivity for 2D images."""
    structure = _STRUCT_8 if connectivity == 2 else _STRUCT_4
    labeled, _ = ndi.label(mask, structure=structure)
    return labeled


# ---------------------------------------------------------------------------
# Region properties (replaces skimage.measure.regionprops)
# ---------------------------------------------------------------------------

@dataclass
class RegionProps:
    label: int
    area: int
    centroid: tuple            # (row, col)
    bbox: tuple                 # (min_row, min_col, max_row, max_col) - max exclusive, matches skimage
    axis_major_length: float
    axis_minor_length: float
    orientation: float          # radians, same convention as skimage
    perimeter: float
    solidity: float


def _inertia_axes_and_orientation(rows: np.ndarray, cols: np.ndarray) -> tuple:
    """Ellipse-equivalent major/minor axis length + orientation from the
    region's normalized second central moments - this is exactly the
    formula skimage.measure.regionprops uses internally (via its
    `inertia_tensor` / `inertia_tensor_eigvals` properties), reproduced
    here directly against NumPy."""
    row_c, col_c = rows.mean(), cols.mean()
    dr, dc = rows - row_c, cols - col_c
    mu20 = float(np.mean(dr * dr))
    mu02 = float(np.mean(dc * dc))
    mu11 = float(np.mean(dr * dc))

    a, b, c = mu20, -mu11, mu02
    tensor = np.array([[a, b], [b, c]])
    eigvals = np.linalg.eigvalsh(tensor)[::-1]  # descending
    l1, l2 = max(eigvals[0], 0.0), max(eigvals[1], 0.0)
    axis_major_length = 4 * np.sqrt(l1)
    axis_minor_length = 4 * np.sqrt(l2)

    if a - c == 0:
        orientation = -np.pi / 4 if b < 0 else np.pi / 4
    else:
        orientation = 0.5 * np.arctan2(-2 * b, c - a)

    return float(axis_major_length), float(axis_minor_length), float(orientation)


# Crack-perimeter weight table (Vossepoel & Smeulders 1982 border-length
# estimator) - the same table skimage.measure.perimeter uses internally.
_PERIMETER_WEIGHTS = np.zeros(50, dtype=np.float64)
_PERIMETER_WEIGHTS[[5, 7, 15, 17, 25, 27]] = 1
_PERIMETER_WEIGHTS[[21, 33]] = np.sqrt(2)
_PERIMETER_WEIGHTS[[13, 23]] = (1 + np.sqrt(2)) / 2
_PERIMETER_KERNEL = np.array([[10, 2, 10], [2, 1, 2], [10, 2, 10]])


def _crack_perimeter(region_mask: np.ndarray) -> float:
    img = region_mask.astype(np.uint8)
    eroded = ndi.binary_erosion(img, _STRUCT_4, border_value=0).astype(np.uint8)
    border = img - eroded
    conv = ndi.convolve(border, _PERIMETER_KERNEL, mode="constant", cval=0)
    hist = np.bincount(conv.ravel(), minlength=50)
    return float(hist[:50] @ _PERIMETER_WEIGHTS)


def _solidity(rows: np.ndarray, cols: np.ndarray, area: int) -> float:
    """area / convex_hull_area. APPROXIMATION NOTE: this hulls pixel
    *centers* (via scipy.spatial.ConvexHull), whereas skimage hulls pixel
    *corners* (slightly larger). Only used as a clipped 0-1 heuristic
    plausibility weight downstream, so this small systematic difference
    does not change detection behavior."""
    if area < 3 or ConvexHull is None:
        return 1.0
    points = np.column_stack([rows, cols]).astype(np.float64)
    try:
        hull = ConvexHull(points)
        hull_area = hull.volume  # for 2D points, ConvexHull.volume is the enclosed area
        if hull_area <= 0:
            return 1.0
        return float(min(1.0, area / hull_area))
    except QhullError:
        return 1.0  # degenerate (collinear) region - treat as fully solid


def regionprops(labeled: np.ndarray, min_area: int = 0) -> list:
    """Returns one RegionProps per non-zero label, ordered by label value
    ascending (matches skimage.measure.regionprops iteration order).

    If min_area > 0, skips regions with area < min_area, avoiding expensive
    ConvexHull, moment, and crack-perimeter calculations on tiny noise regions."""
    if labeled.size == 0 or labeled.max() == 0:
        return []

    slices = ndi.find_objects(labeled)
    counts = np.bincount(labeled.ravel())

    results = []
    for lbl in range(1, len(counts)):
        area = int(counts[lbl])
        if area == 0 or area < min_area:
            continue
        slc = slices[lbl - 1]
        if slc is None:
            continue

        # Fast local bounding box extraction instead of O(image) full comparisons
        sub = labeled[slc]
        sub_mask = (sub == lbl)
        sub_r, sub_c = np.nonzero(sub_mask)
        if sub_r.size == 0:
            continue

        rows = sub_r + slc[0].start
        cols = sub_c + slc[1].start

        centroid = (float(rows.mean()), float(cols.mean()))
        min_r, min_c = slc[0].start, slc[1].start
        max_r, max_c = slc[0].stop - 1, slc[1].stop - 1
        bbox = (min_r, min_c, max_r + 1, max_c + 1)
        major, minor, orientation = _inertia_axes_and_orientation(rows, cols)

        # Crop to the region's bounding box (+1px pad for correct erosion/
        # convolution borders) before perimeter tracing.
        r0, r1 = max(0, min_r - 1), min(labeled.shape[0], max_r + 2)
        c0, c1 = max(0, min_c - 1), min(labeled.shape[1], max_c + 2)
        cropped = (labeled[r0:r1, c0:c1] == lbl)
        perimeter = _crack_perimeter(cropped)
        solidity = _solidity(rows, cols, area)

        results.append(RegionProps(
            label=int(lbl), area=area, centroid=centroid, bbox=bbox,
            axis_major_length=major, axis_minor_length=minor,
            orientation=orientation, perimeter=perimeter, solidity=solidity,
        ))
    return results


# ---------------------------------------------------------------------------
# Morphology (replaces skimage.morphology.*)
# ---------------------------------------------------------------------------

def disk(radius: int) -> np.ndarray:
    """Boolean disk-shaped structuring element, same shape convention as
    skimage.morphology.disk."""
    coords = np.arange(-radius, radius + 1)
    xx, yy = np.meshgrid(coords, coords)
    return (xx ** 2 + yy ** 2) <= radius ** 2


def remove_small_objects(mask: np.ndarray, min_size: int) -> np.ndarray:
    labeled = label(mask, connectivity=2)
    if labeled.max() == 0:
        return mask.copy()
    sizes = np.bincount(labeled.ravel())
    sizes[0] = 0
    keep_labels = np.nonzero(sizes >= min_size)[0]
    return np.isin(labeled, keep_labels)


def binary_opening(mask: np.ndarray, footprint: np.ndarray) -> np.ndarray:
    return ndi.binary_opening(mask, structure=footprint)


def binary_closing(mask: np.ndarray, footprint: np.ndarray) -> np.ndarray:
    return ndi.binary_closing(mask, structure=footprint)


# ---------------------------------------------------------------------------
# Adaptive thresholding (replaces skimage.filters.threshold_local)
# ---------------------------------------------------------------------------

def threshold_local_gaussian(image: np.ndarray, block_size: int, mode: str = "reflect") -> np.ndarray:
    """Gaussian-weighted local threshold surface - same formula
    skimage.filters.threshold_local(method='gaussian') uses internally
    (sigma derived from block_size so >99% of the Gaussian's mass falls
    within the requested block)."""
    sigma = (block_size - 1) / 6.0
    return ndi.gaussian_filter(image.astype(np.float64), sigma, mode=mode)


# ---------------------------------------------------------------------------
# Boundary tracing (replaces skimage.measure.find_contours)
# ---------------------------------------------------------------------------

def find_boundary_contour(mask: np.ndarray) -> list:
    """
    Traces the outer boundary of a (connected) True region using
    Moore-neighbor tracing, returning an ordered list of (row, col) pixel
    coordinates forming a closed polygon path.

    APPROXIMATION NOTE: skimage.measure.find_contours uses marching squares
    to find a sub-pixel iso-contour; this traces whole pixel boundaries
    instead, giving a coarser "staircase" outline. The caller
    (characterization.py) already subsamples the contour to ~60 points for
    the map polygon overlay, so this coarser boundary is visually
    equivalent for that purpose.
    """
    rows, cols = np.nonzero(mask)
    if rows.size == 0:
        return []
    if rows.size == 1:
        return [(float(rows[0]), float(cols[0]))]

    start_idx = np.lexsort((cols, rows))[0]
    start = (int(rows[start_idx]), int(cols[start_idx]))

    # 8-connected neighbour offsets, clockwise starting due west
    directions = [(0, -1), (-1, -1), (-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1)]
    h, w = mask.shape

    def is_set(r, c):
        return 0 <= r < h and 0 <= c < w and bool(mask[r, c])

    boundary = [start]
    current = start
    back_dir_idx = 0
    max_steps = mask.size * 4  # safety cap against pathological inputs

    for _ in range(max_steps):
        found = False
        for i in range(8):
            d_idx = (back_dir_idx + i) % 8
            dr, dc = directions[d_idx]
            nr, nc = current[0] + dr, current[1] + dc
            if is_set(nr, nc):
                current = (nr, nc)
                back_dir_idx = (d_idx + 5) % 8
                found = True
                break
        if not found:
            break
        boundary.append(current)
        if current == start and len(boundary) > 2:
            break

    return [(float(r), float(c)) for r, c in boundary]
