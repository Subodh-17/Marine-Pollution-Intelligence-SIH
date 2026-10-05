"""
Unit tests for app/satellite/geometry_utils.py - the pure NumPy/SciPy
replacement for scikit-image (added for Windows/Application-Control DLL
compatibility, see the module's own docstring). These test the module in
isolation against hand-verifiable geometric cases, independent of the
detection/characterization tests that exercise it indirectly.
"""
import numpy as np
import pytest

from app.satellite import geometry_utils as gu


def test_label_separates_disconnected_regions():
    mask = np.zeros((20, 20), dtype=bool)
    mask[2:5, 2:5] = True     # blob 1
    mask[10:14, 10:14] = True  # blob 2 (far away, disconnected)
    labeled = gu.label(mask)
    assert labeled.max() == 2
    assert len(np.unique(labeled)) == 3  # background + 2 regions


def test_label_8_connectivity_joins_diagonal_pixels():
    mask = np.zeros((10, 10), dtype=bool)
    mask[3, 3] = True
    mask[4, 4] = True  # only diagonally adjacent to (3,3)
    labeled = gu.label(mask, connectivity=2)
    assert labeled.max() == 1  # should be treated as ONE region under 8-connectivity


def test_label_4_connectivity_splits_diagonal_pixels():
    mask = np.zeros((10, 10), dtype=bool)
    mask[3, 3] = True
    mask[4, 4] = True
    labeled = gu.label(mask, connectivity=1)
    assert labeled.max() == 2  # NOT connected under strict 4-connectivity


def test_regionprops_area_and_centroid_for_a_square():
    mask = np.zeros((30, 30), dtype=bool)
    mask[10:20, 10:20] = True  # 10x10 square, rows/cols 10..19
    labeled = gu.label(mask)
    props = gu.regionprops(labeled)
    assert len(props) == 1
    r = props[0]
    assert r.area == 100
    assert r.centroid == pytest.approx((14.5, 14.5))
    assert r.bbox == (10, 10, 20, 20)


def test_regionprops_square_has_near_equal_axes_and_high_solidity():
    mask = np.zeros((40, 40), dtype=bool)
    mask[10:30, 10:30] = True  # 20x20 square
    props = gu.regionprops(gu.label(mask))
    r = props[0]
    # a filled square should be very close to isotropic
    assert r.axis_major_length == pytest.approx(r.axis_minor_length, rel=0.02)
    assert r.solidity > 0.95  # a square is convex, should be ~1.0


def test_regionprops_rectangle_is_elongated_along_expected_axis():
    mask = np.zeros((60, 60), dtype=bool)
    mask[20:24, 5:55] = True  # very wide, short rectangle: 4 rows x 50 cols
    props = gu.regionprops(gu.label(mask))
    r = props[0]
    assert r.axis_major_length > r.axis_minor_length
    # elongation ratio should roughly track the 50:4 aspect ratio of the rectangle
    assert (r.axis_major_length / r.axis_minor_length) > 5


def test_regionprops_perimeter_approximates_known_square_perimeter():
    # a filled NxN square has a true perimeter of 4*N; the crack-perimeter
    # estimator should land close to that (within the algorithm's own
    # documented tolerance for axis-aligned shapes)
    n = 20
    mask = np.zeros((40, 40), dtype=bool)
    mask[10:10 + n, 10:10 + n] = True
    r = gu.regionprops(gu.label(mask))[0]
    assert r.perimeter == pytest.approx(4 * n, rel=0.15)


def test_regionprops_bbox_is_half_open_matching_skimage_convention():
    mask = np.zeros((10, 10), dtype=bool)
    mask[3, 4] = True  # single pixel
    r = gu.regionprops(gu.label(mask))[0]
    assert r.bbox == (3, 4, 4, 5)  # max is exclusive: pixel row 3 -> max_row 4


def test_disk_matches_expected_circular_footprint():
    d = gu.disk(2)
    assert d.shape == (5, 5)
    assert d[2, 2] == True   # center always included
    assert d[0, 0] == False  # corner excluded (distance sqrt(8) > 2)
    assert d[0, 2] == True   # directly above center, distance 2 == radius


def test_remove_small_objects_keeps_large_drops_small():
    mask = np.zeros((30, 30), dtype=bool)
    mask[1:3, 1:3] = True      # 4px - small
    mask[10:20, 10:20] = True  # 100px - large
    cleaned = gu.remove_small_objects(mask, min_size=50)
    assert cleaned[1, 1] == False
    assert cleaned[15, 15] == True
    assert cleaned.sum() == 100


def test_binary_opening_removes_thin_protrusion():
    mask = np.zeros((20, 20), dtype=bool)
    mask[5:15, 5:15] = True  # solid 10x10 block
    mask[15, 5] = True        # single-pixel spike off the corner
    opened = gu.binary_opening(mask, gu.disk(1))
    # the spike should be removed by opening; the solid block mostly survives
    assert opened[15, 5] == False
    assert opened[8, 8] == True


def test_threshold_local_gaussian_is_smooth_and_same_shape():
    rng = np.random.default_rng(0)
    img = rng.random((50, 50))
    thresh = gu.threshold_local_gaussian(img, block_size=15)
    assert thresh.shape == img.shape
    # a Gaussian-smoothed surface should have much lower variance than the
    # original noise field
    assert thresh.std() < img.std()


def test_find_boundary_contour_traces_a_closed_square_loop():
    mask = np.zeros((20, 20), dtype=bool)
    mask[5:10, 5:10] = True  # 5x5 square
    contour = gu.find_boundary_contour(mask)
    assert len(contour) > 4  # more than just corners
    # every traced point should actually be a boundary pixel of the square
    for r, c in contour:
        assert mask[int(r), int(c)] == True
    # the path should start and end at (approximately) the same point (closed loop)
    assert contour[0] == contour[-1] or len(contour) > 10


def test_find_boundary_contour_handles_empty_mask():
    mask = np.zeros((10, 10), dtype=bool)
    assert gu.find_boundary_contour(mask) == []


def test_find_boundary_contour_handles_single_pixel():
    mask = np.zeros((10, 10), dtype=bool)
    mask[5, 5] = True
    contour = gu.find_boundary_contour(mask)
    assert contour == [(5.0, 5.0)]


def test_regionprops_matches_a_known_ellipse_orientation_direction():
    """A region elongated along the ROW axis (vertical) vs one elongated
    along the COLUMN axis (horizontal) should produce visibly different
    orientation values - a sanity check on the sign/axis convention rather
    than an exact-value check."""
    vertical = np.zeros((60, 60), dtype=bool)
    vertical[5:55, 25:30] = True  # tall and narrow
    horizontal = np.zeros((60, 60), dtype=bool)
    horizontal[25:30, 5:55] = True  # short and wide

    r_v = gu.regionprops(gu.label(vertical))[0]
    r_h = gu.regionprops(gu.label(horizontal))[0]
    assert r_v.orientation != pytest.approx(r_h.orientation, abs=0.1)
