"""

Dataset preparation for the real Zenodo Sentinel-1 SAR Oil Spill Dataset.



WHAT THIS MODULE IS FOR

\------------------------

Everything else in this codebase (the classical detector, the synthetic demo,

the shipped ML prototype) works fully offline with zero dataset dependency.

This module is the one deliberate exception: it is how a developer who has

manually downloaded and extracted the real, published Zenodo Sentinel-1 SAR

Oil Spill Dataset turns it into train/val/test manifests that

`app/ml/train.py` and `app/ml/evaluate_real.py` can consume.



It is an OFFLINE, EXPLICIT, OPT-IN CLI tool:

    python -m app.ml.dataset --dataset-root ./data/zenodo_sar inspect

    python -m app.ml.dataset --dataset-root ./data/zenodo_sar build-manifests



It is never imported by `app.main` (the FastAPI app) and never runs at

server startup - a machine with no dataset downloaded is completely

unaffected. See docs/DATASET_SETUP.md for the download/extraction steps this

module assumes have already happened, and docs/DATASET_STRUCTURE.md for the

exact published folder layout this module's discovery logic is built against.



THE PUBLISHED DATASET (three separate Zenodo records - see docs/DATASET_SETUP.md)

\------------------------------------------------------------------------------

  Part I   (10.5281/zenodo.8346860)  - 1200 train/val Oil Spill images+masks

  Part II  (10.5281/zenodo.8253899)  -  685 train/val No-Oil images+masks

                                          685 train/val Lookalike images+masks

  Part III (10.5281/zenodo.13761290) -  150 test Oil images+masks

                                          150 test No-Oil images+masks

                                          150 test Lookalike images+masks



All images are Sentinel-1 Sigma0 SAR, in decibels, 2048x2048 with 2

polarization channels (VV, VH), TIFF format. All masks are single-band TIFF,

foreground(oil)=1 / background=0, same 2048x2048 spatial size, NOT

georeferenced. Parts I/II and Part III use two different folder-naming

conventions (see `_classify_directory` below) - this module recognizes both

without requiring the user to rename anything after extraction.



LEAKAGE PREVENTION

\-------------------

Part III is the dataset's own published test split. The default split

strategy ("dataset_native") NEVER mixes Part III into train/val - it treats

Parts I+II as the only train/val pool (further split by this module into

train/val) and Part III as the held-out test set, matching how the dataset

was actually designed and documented. A cheap content-fingerprint check (see

`_quick_fingerprint`) additionally guards against an accidental duplicate

file appearing in both a train/val part directory and the test part

directory (e.g. a botched re-extraction) - `build_manifests()` raises rather

than silently producing a leaky split if that happens.



DOES NOT MODIFY THE ORIGINAL DOWNLOADED FILES

\-----------------------------------------------

This module only ever reads image/mask files under `dataset_root` and writes

NEW manifest/metadata JSON files under `<dataset_root>/manifests/` (or an

explicit `--output-dir`). It never writes into, renames, or deletes anything

under the user's extracted Part I/II/III directories.

"""

from __future__ import annotations



import argparse

import hashlib

import json

import os

import re

import sys

import threading

import time

from dataclasses import dataclass, field, asdict

from datetime import datetime, timezone

from typing import Optional



import numpy as np



try:

    import tifffile

    TIFFFILE_AVAILABLE = True

except ImportError:  # pragma: no cover - tifffile is a core (non-ML) dependency, this is defensive only

    TIFFFILE_AVAILABLE = False



from app.config import get_settings



# ---------------------------------------------------------------------------

# Categories - deliberately the exact 3 labels app/ml/architectures.py's

# SceneClassifier and app/ml/detector.py::DEFAULT_CLASS_LABELS already use,

# so a manifest built here trains that exact architecture with zero

# relabeling / remapping required.

# ---------------------------------------------------------------------------

CATEGORY_OIL = "Oil"

CATEGORY_NO_OIL = "No-Oil"

CATEGORY_LOOKALIKE = "Lookalike"

VALID_CATEGORIES = (CATEGORY_OIL, CATEGORY_NO_OIL, CATEGORY_LOOKALIKE)



# Dataset DOIs (see docs/DATASET_SETUP.md / DATASET_LICENSE.md for full

# citation, license, and attribution text - CC-BY-4.0, Trujillo-Acatitla et

# al. 2024). Keyed by the conventional subdirectory name this module expects

# under DATASET_ROOT (see docs/DATASET_STRUCTURE.md) - purely a label, not

# something this module enforces the user name their folders as (discovery

# works by content/structure, not by requiring "part1"/"part2"/"part3" names

# specifically - see `_infer_part_label`).

DATASET_DOIS = {

    "part1": "10.5281/zenodo.8346860",

    "part2": "10.5281/zenodo.8253899",

    "part3": "10.5281/zenodo.13761290",

}

DATASET_CITATION = (

    "Trujillo-Acatitla, R., Tuxpan-Vargas, J., Ovando-Vazquez, C., & Monterrubio-Martinez, E. (2024). "

    "Sentinel-1 SAR Oil spill image dataset for train, validate, and test deep learning models. "

    "Parts I-III. Zenodo. https://doi.org/10.5281/zenodo.8346860"

)



_IMAGE_EXTENSIONS = (".tif", ".tiff")

_MASK_EXTENSIONS = (".tif", ".tiff", ".png")



# Test-split (Part III) directories are recognized by containing "test" in

# their path, in addition to the part3 DOI mapping below - both are used as

# corroborating signals, not a hard requirement, so a user who extracted

# Part III into a differently-named folder is still discovered correctly by

# the images/mask + category structure alone.





class DatasetNotFoundError(RuntimeError):

    """Raised whenever DATASET_ROOT does not exist, is empty, or contains no

    recognizable image/mask pairs. Every CLI entry point in this module and

    in app/ml/train.py / app/ml/evaluate_real.py catches this and exits with

    a clear, actionable setup message - callers must never catch this and

    silently substitute synthetic data instead (see module docstring / the

    project rule this enforces)."""





# =============================================================================

# Discovery

# =============================================================================



@dataclass

class PairCandidate:

    """One discovered (image, mask) file pair, not yet validated for

    readability/shape - see `validate_pair`."""

    category: str

    image_path: str   # absolute path on disk

    mask_path: str     # absolute path on disk

    part_label: str    # e.g. "part1", "part3", or "unknown_part" if not inferable

    stem: str           # filename stem shared by image and mask (e.g. "0001")





@dataclass

class ValidatedRecord:

    """One (image, mask) pair that passed shape/readability validation and is

    ready to be written into a manifest."""

    id: str

    category: str

    image_path: str    # POSIX-style, RELATIVE to dataset_root

    mask_path: str      # POSIX-style, RELATIVE to dataset_root

    part_label: str

    height: int

    width: int

    channels: int

    quick_fingerprint: str    # cheap content signature - see `_quick_fingerprint`

    image_checksum: Optional[str] = None  # full sha256, only when --full-checksum requested





@dataclass

class DiscoveryIssue:

    kind: str        # "corrupt" | "shape_mismatch" | "missing_counterpart" | "unreadable" | "wrong_channels"

    path: str

    detail: str





@dataclass

class DiscoveryReport:

    dataset_root: str

    pairs_found: int

    records: list  # list[ValidatedRecord]

    issues: list   # list[DiscoveryIssue]

    category_counts: dict

    part_counts: dict



    def to_dict(self) -> dict:

        return {

            "dataset_root": self.dataset_root,

            "pairs_found": self.pairs_found,

            "category_counts": self.category_counts,

            "part_counts": self.part_counts,

            "issues": [asdict(i) for i in self.issues],

        }





def _normalize_token(name: str) -> str:

    return name.strip().lower().replace(" ", "_").replace("-", "_")





def _strip_kind_tokens(component: str) -> str:

    """Removes 'images'/'image'/'masks'/'mask' substrings from a single path

    component, case-insensitively, collapsing any underscores/whitespace left

    behind. Used to compute a `context_id` that is IDENTICAL for a matching

    images-dir/mask-dir pair regardless of which of the two published naming

    conventions produced it (see module docstring and `_context_id`)."""

    out = component

    for tok in ("images", "image", "masks", "mask"):

        out = re.sub(tok, "", out, flags=re.IGNORECASE)

    out = re.sub(r"[_\s]+", "_", out).strip("_").lower()

    return out





def _context_id(rel_dir: str) -> str:

    """Path with every component's images/mask token stripped - two leaf

    directories (one images, one mask) that are meant to pair with each

    other reduce to the same context_id. See module docstring "THE PUBLISHED

    DATASET" for why Part I/II and Part III need genuinely different

    directory-matching logic, and why this single stripping rule handles

    both without special-casing either."""

    parts = [p for p in rel_dir.split(os.sep) if p not in (".", "")]

    stripped = [_strip_kind_tokens(p) for p in parts]

    return "/".join(p for p in stripped if p)





def _classify_directory(rel_dir: str) -> tuple[Optional[str], Optional[str]]:

    """Returns (kind, category) for a directory, where kind is "images" or

    "mask" and category is one of VALID_CATEGORIES, or None for either if it

    can't be determined. Scans path components from the LEAF upward (most

    specific first) so an ancestor directory whose name happens to contain

    "images" (e.g. Part III's extracted `02_Test_images_and_ground_truth`)

    never overrides the real, more specific `Images`/`Mask` child directory

    - see module docstring for why Part III needs this (its kind is encoded

    in a middle path component, not the leaf, unlike Part I/II)."""

    parts = [p for p in rel_dir.split(os.sep) if p not in (".", "")]

    kind = None

    category = None

    for component in reversed(parts):

        normed = _normalize_token(component)

        if kind is None:

            if "mask" in normed:

                kind = "mask"

            elif "image" in normed:

                kind = "images"

        if category is None:

            if "lookalike" in normed:

                category = CATEGORY_LOOKALIKE

            elif "no_oil" in normed or "nooil" in normed or "no oil" in component.lower():

                category = CATEGORY_NO_OIL

            elif "oil" in normed:

                category = CATEGORY_OIL

        if kind is not None and category is not None:

            break

    return kind, category





def _infer_part_label(rel_dir: str) -> str:

    """Best-effort label for which of the three Zenodo parts a discovered

    directory belongs to - purely cosmetic/reporting (which DOI to cite in

    the manifest), never used to decide train/val/test membership (that is

    decided structurally - see `build_manifests`: Part III is identified by

    living under a directory recognized as the test split, not by this

    label)."""

    lowered = rel_dir.lower().replace(os.sep, "/")

    if "part1" in lowered or "oil_spill" in lowered.replace(" ", "_"):

        return "part1"

    if "part3" in lowered or "test" in lowered:

        return "part3"

    if "part2" in lowered or "lookalike" in lowered or "no_oil" in lowered.replace(" ", "_"):

        return "part2"

    return "unknown_part"





def _is_test_split_dir(rel_dir: str) -> bool:

    lowered = rel_dir.lower().replace(os.sep, "/")

    return "part3" in lowered or "test" in lowered





def _find_leaf_collections(dataset_root: str) -> dict:

    """Walks dataset_root and returns {(context_id, category): {"images": abs_dir|None, "mask": abs_dir|None}}

    for every directory that directly contains at least one image-extension

    file and can be classified via `_classify_directory`."""

    collections: dict = {}

    for current_dir, _subdirs, files in os.walk(dataset_root):

        rel_dir = os.path.relpath(current_dir, dataset_root)

        if rel_dir == ".":

            continue

        has_image_file = any(f.lower().endswith(_IMAGE_EXTENSIONS + tuple(_MASK_EXTENSIONS)) for f in files)

        if not has_image_file:

            continue

        kind, category = _classify_directory(rel_dir)

        if kind is None or category is None:

            continue

        ctx = _context_id(rel_dir)

        key = (ctx, category)

        collections.setdefault(key, {"images": None, "mask": None, "rel_dirs": {}})

        collections[key][kind] = current_dir

        collections[key]["rel_dirs"][kind] = rel_dir

    return collections





def discover_pairs(dataset_root: str) -> tuple[list, list]:
    """Discover image/mask pairs under ``dataset_root``.

    The published Zenodo dataset uses two filename conventions:

    Parts I/II:
        image: <stem>.tif
        mask:  <stem>.tif

    Part III:
        image: <stem>.tif
        mask:  <stem>_segmentation.tif

    The ``_segmentation`` suffix is removed only from the in-memory pairing
    key. The original filenames and dataset files are never modified.

    Returns:
        A tuple ``(candidates, issues)`` where candidates are discovered
        image/mask pairs and issues contains missing/duplicate counterparts.
    """
    if not os.path.isdir(dataset_root):
        raise DatasetNotFoundError(
            f"DATASET_ROOT does not exist: {dataset_root!r}. See docs/DATASET_SETUP.md "
            "to download and extract the Zenodo Sentinel-1 SAR Oil Spill Dataset "
            "before running this command."
        )

    collections = _find_leaf_collections(dataset_root)
    candidates: list[PairCandidate] = []
    issues: list[DiscoveryIssue] = []

    for (ctx, category), sides in collections.items():
        images_dir = sides.get("images")
        mask_dir = sides.get("mask")

        if images_dir is None or mask_dir is None:
            present = images_dir or mask_dir
            issues.append(
                DiscoveryIssue(
                    kind="missing_counterpart",
                    path=present or ctx,
                    detail=(
                        f"Found a {'images' if images_dir else 'mask'} directory "
                        f"for category={category} (context={ctx!r}) with no matching "
                        f"{'mask' if images_dir else 'images'} directory alongside it - "
                        "excluded from the manifest."
                    ),
                )
            )
            continue

        image_files: dict[str, str] = {}
        for filename in os.listdir(images_dir):
            if not filename.lower().endswith(_IMAGE_EXTENSIONS):
                continue

            stem = os.path.splitext(filename)[0]

            if stem in image_files:
                issues.append(
                    DiscoveryIssue(
                        kind="duplicate_image",
                        path=os.path.join(images_dir, filename),
                        detail=(
                            f"Multiple image files resolve to the same stem {stem!r}: "
                            f"{image_files[stem]!r} and {filename!r}. "
                            "The duplicate image is excluded."
                        ),
                    )
                )
                continue

            image_files[stem] = filename

        # Part III masks are named <image_stem>_segmentation.tif.
        # Normalize only the in-memory key so that, for example:
        #
        #     00000.tif
        #     00000_segmentation.tif
        #
        # correctly form one pair.
        mask_files: dict[str, str] = {}

        for filename in os.listdir(mask_dir):
            if not filename.lower().endswith(tuple(_MASK_EXTENSIONS)):
                continue

            stem = os.path.splitext(filename)[0]

            if stem.lower().endswith("_segmentation"):
                stem = stem[:-len("_segmentation")]

            if stem in mask_files:
                issues.append(
                    DiscoveryIssue(
                        kind="duplicate_mask",
                        path=os.path.join(mask_dir, filename),
                        detail=(
                            f"Multiple mask files resolve to the same image stem "
                            f"{stem!r}: {mask_files[stem]!r} and {filename!r}. "
                            "The duplicate mask is excluded."
                        ),
                    )
                )
                continue

            mask_files[stem] = filename

        rel_images_dir = os.path.relpath(images_dir, dataset_root)
        part_label = _infer_part_label(rel_images_dir)

        stems = sorted(set(image_files) | set(mask_files))

        for stem in stems:
            image_filename = image_files.get(stem)
            mask_filename = mask_files.get(stem)

            if image_filename is None:
                issues.append(
                    DiscoveryIssue(
                        kind="missing_counterpart",
                        path=os.path.join(mask_dir, mask_filename),
                        detail=(
                            f"Mask file has no matching image file with stem "
                            f"{stem!r} in {images_dir}."
                        ),
                    )
                )
                continue

            if mask_filename is None:
                issues.append(
                    DiscoveryIssue(
                        kind="missing_counterpart",
                        path=os.path.join(images_dir, image_filename),
                        detail=(
                            f"Image file has no matching mask file with stem "
                            f"{stem!r} in {mask_dir}."
                        ),
                    )
                )
                continue

            candidates.append(
                PairCandidate(
                    category=category,
                    image_path=os.path.join(images_dir, image_filename),
                    mask_path=os.path.join(mask_dir, mask_filename),
                    part_label=part_label,
                    stem=stem,
                )
            )

    return candidates, issues


def _tiff_shape(path: str) -> tuple:

    """Reads just the shape/dtype from a TIFF's header - never decodes full

    pixel data, so this stays fast even against a multi-GB dataset (mirrors

    the same fast-shape-check technique app/main.py::_check_image_dimensions

    already uses for uploads).



    Uses `TiffFile.series[0]` rather than `pages[0]` deliberately: a 2-band

    (H, W, 2) array written by common TIFF writers (including tifffile's own

    default `imwrite`) is frequently stored as multiple single-band PAGES

    rather than one interleaved page - `pages[0].shape` would then report

    only the first band's 2D shape (silently dropping the channel axis).

    `series[0]` is tifffile's own reconstruction of the full logical array

    shape across however many pages it's actually split across, still

    without decoding pixel data.

    """

    with tifffile.TiffFile(path) as tf:

        series = tf.series[0]

        return tuple(series.shape), series.dtype





def _quick_fingerprint(path: str, sample_bytes: int = 65536) -> str:

    """Cheap content signature: file size + sha256 of the first/last

    `sample_bytes`. Used only for the train/test leakage guard (detecting an

    accidentally duplicated file across splits) - deliberately NOT a full

    file hash, since hashing every byte of a \~90GB dataset on every manifest

    build would be prohibitively slow. Two different large scientific TIFFs

    matching on this fingerprint by coincidence is astronomically unlikely;

    an exact duplicate (the actual failure mode this guards against) always

    matches. Pass --full-checksum to additionally compute real sha256 sums

    for the final manifest (slower, optional, see `_full_sha256`)."""

    size = os.path.getsize(path)

    h = hashlib.sha256()

    h.update(str(size).encode())

    with open(path, "rb") as f:

        head = f.read(sample_bytes)

        h.update(head)

        if size > sample_bytes:

            f.seek(max(0, size - sample_bytes))

            h.update(f.read(sample_bytes))

    return h.hexdigest()





def _full_sha256(path: str, chunk_size: int = 4 * 1024 * 1024) -> str:

    h = hashlib.sha256()

    with open(path, "rb") as f:

        for chunk in iter(lambda: f.read(chunk_size), b""):

            h.update(chunk)

    return h.hexdigest()





def _channel_count(shape: tuple) -> tuple:

    """Normalizes a TIFF page shape to (height, width, channels), handling

    both plausible on-disk layouts for a "2048x2048x2" dual-pol image

    (channel-last (H,W,2), matching app/satellite/real_image.py's existing

    convention) and a channel-first / multi-page layout ((2,H,W)) some TIFF

    writers use instead - never assumes which one without checking."""

    if len(shape) == 2:

        return shape[0], shape[1], 1

    if len(shape) == 3:

        if shape[-1] <= 4:

            return shape[0], shape[1], shape[-1]

        if shape[0] <= 4:

            return shape[1], shape[2], shape[0]

    raise ValueError(f"Unrecognized image shape {shape} - expected 2D or a small channel axis in a 3D array.")





def validate_pairs(

    candidates: list,

    compute_full_checksum: bool = False,

) -> tuple[list, list]:

    """Validates each candidate pair: both files must be openable, image

    must have exactly 2 channels (VV, VH) for training the existing dual-pol

    architectures, and image/mask spatial dimensions must match. Corrupt or

    mismatched files are recorded as issues and excluded - a single bad file

    never aborts the whole build."""

    if not TIFFFILE_AVAILABLE:

        raise RuntimeError("tifffile is required to validate the dataset but is not installed.")



    records: list[ValidatedRecord] = []

    issues: list[DiscoveryIssue] = []



    for cand in candidates:

        try:

            img_shape, _img_dtype = _tiff_shape(cand.image_path)

        except Exception as e:

            issues.append(DiscoveryIssue(kind="corrupt", path=cand.image_path, detail=f"Could not read TIFF header: {e}"))

            continue

        try:

            mask_shape, _mask_dtype = _tiff_shape(cand.mask_path) if cand.mask_path.lower().endswith((".tif", ".tiff")) else (None, None)

        except Exception as e:

            issues.append(DiscoveryIssue(kind="corrupt", path=cand.mask_path, detail=f"Could not read TIFF header: {e}"))

            continue



        try:

            h, w, c = _channel_count(img_shape)

        except ValueError as e:

            issues.append(DiscoveryIssue(kind="shape_mismatch", path=cand.image_path, detail=str(e)))

            continue



        if c != 2:

            issues.append(DiscoveryIssue(

                kind="wrong_channels", path=cand.image_path,

                detail=f"Expected 2 channels (VV, VH), found {c} - excluded from the dual-pol training manifest.",

            ))

            continue



        if mask_shape is not None:

            mh, mw = mask_shape[0], mask_shape[1]

            if (mh, mw) != (h, w):

                issues.append(DiscoveryIssue(

                    kind="shape_mismatch", path=cand.mask_path,

                    detail=f"Mask spatial size {(mh, mw)} does not match image size {(h, w)}.",

                ))

                continue



        try:

            fingerprint = _quick_fingerprint(cand.image_path)

        except Exception as e:

            issues.append(DiscoveryIssue(kind="unreadable", path=cand.image_path, detail=f"Could not read file for fingerprinting: {e}"))

            continue



        checksum = _full_sha256(cand.image_path) if compute_full_checksum else None



        records.append(ValidatedRecord(

            id=f"{cand.part_label}_{cand.category}_{cand.stem}".replace(" ", "_"),

            category=cand.category,

            image_path=cand.image_path,

            mask_path=cand.mask_path,

            part_label=cand.part_label,

            height=h, width=w, channels=c,

            quick_fingerprint=fingerprint,

            image_checksum=checksum,

        ))



    return records, issues





# =============================================================================

# Manifest creation (train/val/test split, leakage guard, persistence)

# =============================================================================



@dataclass

class ManifestBuildReport:

    dataset_root: str

    output_dir: str

    seed: int

    split_strategy: str

    val_fraction: float

    generated_at: str

    train_count: int

    val_count: int

    test_count: int

    category_counts: dict

    issues: list

    leakage_checked: bool

    leakage_conflicts: list

    manifest_paths: dict





def _relpath_posix(path: str, root: str) -> str:

    return os.path.relpath(path, root).replace(os.sep, "/")





def _stratified_split(records: list, val_fraction: float, seed: int) -> tuple[list, list]:

    """Deterministic, seeded, per-category shuffle-split - keeps train/val

    class balance close to the pool's own balance rather than an unlucky

    global shuffle skewing one split toward a single category."""

    import random

    rng = random.Random(seed)

    train, val = [], []

    by_category: dict = {}

    for r in records:

        by_category.setdefault(r.category, []).append(r)

    for category in sorted(by_category):

        items = list(by_category[category])

        rng.shuffle(items)

        n_val = max(1, round(len(items) * val_fraction)) if items else 0

        val.extend(items[:n_val])

        train.extend(items[n_val:])

    # Re-shuffle the combined lists so records aren't grouped by category on

    # disk in the manifest (purely cosmetic; DataLoader shuffling in

    # app/ml/train.py does not depend on this).

    rng.shuffle(train)

    rng.shuffle(val)

    return train, val





def _check_leakage(train_val: list, test: list) -> list:

    """Compares quick_fingerprint values across the train/val pool and the

    test set; returns a list of conflicting (train_or_val_path, test_path)

    detail strings. An empty list means no leakage was detected - this does

    NOT prove there is none (fingerprints are a cheap sample-based check,

    see `_quick_fingerprint`), only that the cheap check found nothing."""

    test_by_fp = {r.quick_fingerprint: r for r in test}

    conflicts = []

    for r in train_val:

        hit = test_by_fp.get(r.quick_fingerprint)

        if hit is not None:

            conflicts.append(f"{r.image_path}  <->  {hit.image_path} (matching content fingerprint)")

    return conflicts





def build_manifests(

    dataset_root: str,

    output_dir: Optional[str] = None,

    val_fraction: float = 0.15,

    seed: int = 42,

    compute_full_checksum: bool = False,

    split_strategy: str = "dataset_native",

) -> ManifestBuildReport:

    """Discovers, validates, splits, and persists train/val/test manifests.



    split_strategy="dataset_native" (default, recommended): Parts I+II are

    the train/val pool (split by `val_fraction`); Part III is the held-out

    test set, exactly as the dataset was published and documented - see

    module docstring "LEAKAGE PREVENTION".



    Raises DatasetNotFoundError if dataset_root doesn't exist or zero valid

    pairs are found anywhere under it (never silently produces an empty

    manifest that a training run would then silently "succeed" against).

    """

    candidates, discovery_issues = discover_pairs(dataset_root)

    if not candidates:

        raise DatasetNotFoundError(

            f"No valid image/mask pairs were discovered under {dataset_root!r}. See "

            "docs/DATASET_STRUCTURE.md for the expected folder layout, and re-run "

            "`python -m app.ml.dataset --dataset-root <path> inspect` for details on what was found."

        )



    records, validation_issues = validate_pairs(candidates, compute_full_checksum=compute_full_checksum)

    issues = discovery_issues + validation_issues

    if not records:

        raise DatasetNotFoundError(

            f"{len(candidates)} candidate pairs were found under {dataset_root!r}, but none passed "

            "validation (see issues list) - check docs/DATASET_STRUCTURE.md and re-run `inspect`."

        )



    if split_strategy != "dataset_native":

        raise ValueError(f"Unsupported split_strategy {split_strategy!r} - only 'dataset_native' is implemented.")



    test_records = [r for r in records if _is_test_split_dir(os.path.relpath(r.image_path, dataset_root))]

    train_val_records = [r for r in records if r not in test_records]



    if not test_records:

        raise DatasetNotFoundError(

            "No Part III (test-split) images were discovered under DATASET_ROOT - a held-out test set "

            "is required (see docs/DATASET_STRUCTURE.md). If you have only downloaded Parts I/II so far, "

            "download Part III (10.5281/zenodo.13761290) before building manifests, or explicitly accept "

            "an empty test set is out of scope for this pipeline (evaluation requires real held-out data, "

            "per project policy - see app/ml/evaluate_real.py)."

        )



    leakage_conflicts = _check_leakage(train_val_records, test_records)

    if leakage_conflicts:

        raise RuntimeError(

            "Potential train/test leakage detected - the following files appear in BOTH the train/val "

            "pool and the held-out test set (matching content fingerprint):\n"

            + "\n".join(f"  - {c}" for c in leakage_conflicts)

            + "\nRefusing to build manifests. Check your extraction for duplicated/misplaced files."

        )



    train_records, val_records = _stratified_split(train_val_records, val_fraction=val_fraction, seed=seed)



    output_dir = output_dir or os.path.join(dataset_root, "manifests")

    os.makedirs(output_dir, exist_ok=True)



    def _manifest_dict(name: str, recs: list) -> dict:

        return {

            "split": name,

            "dataset_root": os.path.abspath(dataset_root),

            "generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),

            "seed": seed,

            "split_strategy": split_strategy,

            "count": len(recs),

            "records": [

                {

                    "id": r.id,

                    "category": r.category,

                    "image_path": _relpath_posix(r.image_path, dataset_root),

                    "mask_path": _relpath_posix(r.mask_path, dataset_root),

                    "part_label": r.part_label,

                    "height": r.height,

                    "width": r.width,

                    "channels": r.channels,

                    "quick_fingerprint": r.quick_fingerprint,

                    "image_checksum_sha256": r.image_checksum,

                }

                for r in recs

            ],

        }



    manifest_paths = {}

    for name, recs in (("train", train_records), ("val", val_records), ("test", test_records)):

        path = os.path.join(output_dir, f"{name}_manifest.json")

        with open(path, "w") as f:

            json.dump(_manifest_dict(name, recs), f, indent=2)

        manifest_paths[name] = path



    def _category_counts(recs: list) -> dict:

        counts: dict = {c: 0 for c in VALID_CATEGORIES}

        for r in recs:

            counts[r.category] = counts.get(r.category, 0) + 1

        return counts



    metadata = {

        "dataset_root": os.path.abspath(dataset_root),

        "generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),

        "seed": seed,

        "split_strategy": split_strategy,

        "val_fraction": val_fraction,

        "dataset_dois": DATASET_DOIS,

        "dataset_citation": DATASET_CITATION,

        "counts": {

            "train": len(train_records), "val": len(val_records), "test": len(test_records),

            "train_by_category": _category_counts(train_records),

            "val_by_category": _category_counts(val_records),

            "test_by_category": _category_counts(test_records),

        },

        "issues": [asdict(i) for i in issues],

        "leakage_check": {"performed": True, "conflicts_found": 0},

        "manifest_files": {k: os.path.basename(v) for k, v in manifest_paths.items()},

    }

    metadata_path = os.path.join(output_dir, "dataset_metadata.json")

    with open(metadata_path, "w") as f:

        json.dump(metadata, f, indent=2)

    manifest_paths["metadata"] = metadata_path



    return ManifestBuildReport(

        dataset_root=os.path.abspath(dataset_root),

        output_dir=output_dir,

        seed=seed,

        split_strategy=split_strategy,

        val_fraction=val_fraction,

        generated_at=metadata["generated_at"],

        train_count=len(train_records),

        val_count=len(val_records),

        test_count=len(test_records),

        category_counts=metadata["counts"],

        issues=issues,

        leakage_checked=True,

        leakage_conflicts=leakage_conflicts,

        manifest_paths=manifest_paths,

    )





def load_manifest(path: str) -> dict:

    if not os.path.exists(path):

        raise DatasetNotFoundError(

            f"Manifest not found at {path!r}. Run `python -m app.ml.dataset build-manifests` first "

            "(see docs/DATASET_SETUP.md)."

        )

    with open(path) as f:

        return json.load(f)





# =============================================================================

# Dataset status (read-only, local-filesystem-only - backs GET

# /api/ml/dataset-status on the ML Training page, see app/main.py)

# =============================================================================



def _empty_parts_status() -> dict:

    """Zero-count shape for every part label this module knows about - used

    whenever discovery couldn't even start (not configured / missing), so

    the response shape is identical regardless of state."""

    return {

        part_label: {

            "doi": doi, "detected": False, "validated_pairs": 0,

            "category_counts": {c: 0 for c in VALID_CATEGORIES},

        }

        for part_label, doi in DATASET_DOIS.items()

    }





def get_dataset_status(dataset_root: Optional[str] = None) -> dict:

    """Real, local-filesystem-only status of the (optional, opt-in, never

    auto-downloaded) Zenodo Sentinel-1 SAR Oil Spill Dataset.



    Reuses the EXACT SAME `discover_pairs()` / `validate_pairs()` functions

    `build_manifests()` and the `inspect`/`build-manifests` CLI commands

    already use (see bottom of this module) - never a second, possibly

    inconsistent counting path. Only ever reads files already present under

    `dataset_root`; never fetches, extracts, or writes anything (matching

    this whole module's "OFFLINE, EXPLICIT, OPT-IN" contract - see module

    docstring). Every count returned comes from files genuinely found and

    validated on this call - nothing here is a hardcoded or remembered

    number.



    Deliberately says NOTHING about whether a model has been TRAINED - that

    is an entirely separate question answered by `/api/ml/status` and

    `/api/ml/training-history` (see app/ml/detector.py). A `"validated"`

    dataset and a synthetic-trained model can both be true at once: the

    dataset being present on disk doesn't mean `app.ml.train` has ever been

    run against it.



    Returns one of four states, driven entirely by what's genuinely found:

      - "not_configured" - no DATASET_ROOT path is configured at all.

      - "missing" - a path is configured but nothing usable exists there

        (the directory doesn't exist, or exists but contains no

        recognizable image/mask pairs at all).

      - "incomplete" - some genuine image/mask pairs were discovered and

        validated, but not all three published parts (Part I/II/III) have

        at least one validated pair yet.

      - "validated" - all three parts have at least one genuinely validated

        (readable, correctly-shaped, dual-pol VV/VH) image/mask pair.

    """

    if dataset_root is None:

        dataset_root = get_settings().dataset_root



    base = {

        "dataset_root": os.path.abspath(dataset_root) if dataset_root else None,

        "dataset_root_configured": bool(dataset_root),

        "dataset_citation": DATASET_CITATION,

        "note": (

            "This reflects whether the real Zenodo dataset is present and structurally valid on "

            "disk - it says nothing about whether a model has actually been trained on it. See "

            "/api/ml/status and /api/ml/training-history for training/model state."

        ),

    }

    empty_counts = {

        "parts": _empty_parts_status(),

        "category_counts": {c: 0 for c in VALID_CATEGORIES},

        "total_validated_pairs": 0,

        "issue_count": 0,

        "issues": [],

        "issues_truncated": False,

    }



    if not dataset_root:

        return {

            **base, **empty_counts,

            "status": "not_configured",

            "reason": "No DATASET_ROOT is configured (see .env.example / app/config.py).",

        }



    if not os.path.isdir(dataset_root):

        return {

            **base, **empty_counts,

            "status": "missing",

            "reason": (

                f"DATASET_ROOT is configured as {os.path.abspath(dataset_root)!r} but that "

                "directory does not exist. See docs/DATASET_SETUP.md to download and extract "

                "the dataset yourself - this is never done automatically."

            ),

        }



    try:

        candidates, discovery_issues = discover_pairs(dataset_root)

    except DatasetNotFoundError as e:

        return {**base, **empty_counts, "status": "missing", "reason": str(e)}



    try:

        records, validation_issues = validate_pairs(candidates)

    except RuntimeError as e:

        # tifffile not installed - report unavailable rather than crashing

        # the endpoint (tifffile is a core, non-ML dependency, so this is a

        # defensive-only path - see TIFFFILE_AVAILABLE above).

        return {

            **base,

            "status": "missing",

            "reason": f"Could not validate discovered files: {e}",

            "parts": _empty_parts_status(),

            "category_counts": {c: 0 for c in VALID_CATEGORIES},

            "total_validated_pairs": 0,

            "issue_count": len(discovery_issues),

            "issues": [asdict(i) for i in discovery_issues[:50]],

            "issues_truncated": len(discovery_issues) > 50,

        }



    all_issues = discovery_issues + validation_issues

    category_counts = {c: sum(1 for r in records if r.category == c) for c in VALID_CATEGORIES}



    parts = {}

    for part_label, doi in DATASET_DOIS.items():

        part_records = [r for r in records if r.part_label == part_label]

        parts[part_label] = {

            "doi": doi,

            "detected": len(part_records) > 0,

            "validated_pairs": len(part_records),

            "category_counts": {c: sum(1 for r in part_records if r.category == c) for c in VALID_CATEGORIES},

        }



    if len(records) == 0 and not all_issues:

        status = "missing"

        reason = (

            f"DATASET_ROOT ({base['dataset_root']}) exists but contains no recognizable "

            "Sentinel-1 image/mask pairs. See docs/DATASET_STRUCTURE.md for the expected "

            "folder layout."

        )

    elif all(parts[p]["detected"] for p in ("part1", "part2", "part3")):

        status = "validated"

        reason = None

    else:

        missing_parts = [p for p in ("part1", "part2", "part3") if not parts[p]["detected"]]

        status = "incomplete"

        reason = f"Part(s) not yet detected/validated: {', '.join(missing_parts)}."



    return {

        **base,

        "status": status,

        "reason": reason,

        "parts": parts,

        "category_counts": category_counts,

        "total_validated_pairs": len(records),

        "issue_count": len(all_issues),

        "issues": [asdict(i) for i in all_issues[:50]],

        "issues_truncated": len(all_issues) > 50,

    }





# =============================================================================

# In-memory cache for the HTTP endpoint (`GET /api/ml/dataset-status`)

# =============================================================================

#

# `get_dataset_status()` above calls `discover_pairs()` + `validate_pairs()`,

# which walk the entire DATASET_ROOT tree and open/shape-check every single

# TIFF pair. That is deliberately thorough (see the module docstring's

# "OFFLINE, EXPLICIT, OPT-IN" contract) but expensive on the real, \~90GB

# published dataset - slow enough that re-running it on every single ML

# Training page load is both unnecessary (the files on disk only change

# when an operator re-downloads/re-extracts the dataset, not between two

# page loads a few seconds apart) and actively harmful to the page's

# usability (see app/main.py's `/api/ml/dataset-status` docstring and the

# frontend's MlTrainingTab.jsx for the user-facing symptom this caused).

#

# This wrapper is intentionally kept SEPARATE from `get_dataset_status()`

# itself (which stays a pure, always-fresh function - see

# test_ml_dataset_status.py, which calls it directly and expects a fresh

# scan every time) so nothing about the honesty contract of the underlying

# scan changes; only the HTTP-facing wrapper adds a short-lived cache.

_dataset_status_cache_lock = threading.Lock()

_dataset_status_cache: dict[str, dict] = {}



# Default cache lifetime - overridable via DATASET_STATUS_CACHE_TTL_SECONDS

# (see app/config.py / .env.example). A stale-but-not-yet-expired cached

# result is still served automatically; an operator (or the ML Training

# page's "Refresh dataset status" button, via `?refresh=true`) can always

# force an immediate rescan regardless of this TTL.

DEFAULT_DATASET_STATUS_CACHE_TTL_SECONDS = 300.0





def get_dataset_status_cached(

    dataset_root: Optional[str] = None,

    force_refresh: bool = False,

    ttl_seconds: Optional[float] = None,

) -> dict:

    """Cached wrapper around `get_dataset_status()` for the HTTP endpoint.



    Returns the exact same shape as `get_dataset_status()`, plus one added

    `"cache"` object: `{"cached": bool, "cached_at": iso8601, "ttl_seconds": float}`

    so the frontend/operator can see whether a response is fresh or served

    from cache and how old it is - never hidden or silently stale.



    Caching is keyed by the resolved `dataset_root` path itself, so pointing

    DATASET_ROOT at a different directory always triggers a fresh scan

    rather than serving a stale result computed for the old path.



    `force_refresh=True` (used by `GET /api/ml/dataset-status?refresh=true`,

    i.e. the ML Training page's "Refresh dataset status" button) always

    bypasses the cache and performs a real rescan, then re-populates the

    cache with the fresh result - it never "revalidates the entire dataset"

    on its own; it only runs when explicitly asked to.

    """

    if dataset_root is None:

        dataset_root = get_settings().dataset_root

    if ttl_seconds is None:

        ttl_seconds = DEFAULT_DATASET_STATUS_CACHE_TTL_SECONDS

    cache_key = dataset_root or ""

    now = time.time()



    if not force_refresh:

        with _dataset_status_cache_lock:

            entry = _dataset_status_cache.get(cache_key)

        if entry is not None and (now - entry["cached_at_epoch"]) < ttl_seconds:

            body = dict(entry["body"])

            body["cache"] = {

                "cached": True,

                "cached_at": entry["cached_at_iso"],

                "ttl_seconds": ttl_seconds,

            }

            return body



    body = get_dataset_status(dataset_root)

    cached_at_iso = datetime.now(timezone.utc).isoformat()

    with _dataset_status_cache_lock:

        _dataset_status_cache[cache_key] = {

            "body": body,

            "cached_at_epoch": now,

            "cached_at_iso": cached_at_iso,

        }

    out = dict(body)

    out["cache"] = {"cached": False, "cached_at": cached_at_iso, "ttl_seconds": ttl_seconds}

    return out





def clear_dataset_status_cache() -> None:

    """Reset the in-memory dataset-status cache. Used by tests, and safe to

    call at any time - it never touches disk, it only forces the next

    `get_dataset_status_cached()` call to rescan."""

    with _dataset_status_cache_lock:

        _dataset_status_cache.clear()





# =============================================================================

# CLI

# =============================================================================



def _cmd_inspect(args) -> int:

    try:

        candidates, issues = discover_pairs(args.dataset_root)

    except DatasetNotFoundError as e:

        print(f"ERROR: {e}", file=sys.stderr)

        return 1

    by_category: dict = {}

    for c in candidates:

        by_category[c.category] = by_category.get(c.category, 0) + 1

    print(f"Dataset root: {os.path.abspath(args.dataset_root)}")

    print(f"Candidate pairs discovered: {len(candidates)}")

    for cat in VALID_CATEGORIES:

        print(f"  {cat}: {by_category.get(cat, 0)}")

    if issues:

        print(f"\n{len(issues)} issue(s) found (missing counterparts, etc.):")

        for issue in issues[:50]:

            print(f"  [{issue.kind}] {issue.path}: {issue.detail}")

        if len(issues) > 50:

            print(f"  ... and {len(issues) - 50} more.")

    if not candidates:

        print(

            "\nNo pairs discovered. Check docs/DATASET_STRUCTURE.md for the expected folder layout "

            "and confirm you extracted the .7z archives (not just downloaded them).",

            file=sys.stderr,

        )

        return 1

    return 0





def _cmd_build(args) -> int:

    try:

        report = build_manifests(

            dataset_root=args.dataset_root,

            output_dir=args.output_dir,

            val_fraction=args.val_fraction,

            seed=args.seed,

            compute_full_checksum=args.full_checksum,

        )

    except (DatasetNotFoundError, RuntimeError, ValueError) as e:

        print(f"ERROR: {e}", file=sys.stderr)

        return 1

    print(f"Manifests written to: {report.output_dir}")

    print(f"  train: {report.train_count}")

    print(f"  val:   {report.val_count}")

    print(f"  test:  {report.test_count}")

    print(f"  seed:  {report.seed}  split_strategy: {report.split_strategy}")

    if report.issues:

        print(f"\n{len(report.issues)} issue(s) recorded in dataset_metadata.json (excluded from manifests).")

    return 0





def main(argv=None) -> int:

    parser = argparse.ArgumentParser(description="Zenodo Sentinel-1 SAR Oil Spill Dataset preparation.")

    parser.add_argument(

        "--dataset-root", default=None,

        help="Root directory of the extracted Zenodo dataset (default: DATASET_ROOT env var / app config).",

    )

    sub = parser.add_subparsers(dest="command", required=True)



    p_inspect = sub.add_parser("inspect", help="Report what images/masks are discoverable under --dataset-root.")

    p_inspect.set_defaults(func=_cmd_inspect)



    p_build = sub.add_parser("build-manifests", help="Validate, split, and write train/val/test manifests.")

    p_build.add_argument("--output-dir", default=None, help="Where to write manifests (default: <dataset_root>/manifests).")

    p_build.add_argument("--val-fraction", type=float, default=0.15)

    p_build.add_argument("--seed", type=int, default=42)

    p_build.add_argument("--full-checksum", action="store_true", help="Compute a full sha256 per image (slow).")

    p_build.set_defaults(func=_cmd_build)



    args = parser.parse_args(argv)

    if args.dataset_root is None:

        args.dataset_root = get_settings().dataset_root

    return args.func(args)





if __name__ == "__main__":

    raise SystemExit(main())
