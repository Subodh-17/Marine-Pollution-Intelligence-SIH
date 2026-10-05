# Dataset Structure — Real Zenodo Sentinel-1 SAR Oil Spill Dataset

This is the technical reference for exactly what `app/ml/dataset.py`
expects to find under `DATASET_ROOT`, and how it discovers/pairs files. See
`docs/DATASET_SETUP.md` for the download/extraction walkthrough this
assumes you've already done.

## Expected folder layout

The published dataset uses **two different folder-naming conventions**
across its three parts — this is a fact about the published archives, not
a choice this project made, and `app/ml/dataset.py` recognizes both
without requiring you to rename anything.

```
<DATASET_ROOT>/
├── part1/                                    # Part I — 10.5281/zenodo.8346860
│   ├── 01_Train_Val_Oil_Spill_images/
│   │   ├── 0001.tif
│   │   ├── 0002.tif
│   │   └── ...                               # 1200 images total
│   └── 01_Train_Val_Oil_Spill_mask/
│       ├── 0001.tif
│       └── ...                               # 1200 masks, matching stems
├── part2/                                    # Part II — 10.5281/zenodo.8253899
│   ├── 01_Train_Val_Lookalike_images/        # 685 images
│   ├── 01_Train_Val_Lookalike_mask/          # 685 masks
│   ├── 01_Train_Val_No_Oil_Images/           # 685 images
│   └── 01_Train_Val_No_Oil_mask/             # 685 masks
└── part3/                                    # Part III — 10.5281/zenodo.13761290
    └── 02_Test_images_and_ground_truth/
        ├── Images/
        │   ├── Oil/                          # 150 images
        │   ├── Lookalike/                    # 150 images
        │   └── No_oil/                       # 150 images
        └── Mask/
            ├── Oil/                          # 150 masks
            ├── Lookalike/                    # 150 masks
            └── No_oil/                       # 150 masks
```

**Part I/II convention:** category AND images-vs-mask are both encoded in
one leaf folder name (`<prefix>_images` / `<prefix>_mask`).

**Part III convention:** images-vs-mask is encoded in a parent folder
(`Images`/`Mask`), and category is the leaf folder name alone
(`Oil`/`Lookalike`/`No_oil`).

The `part1`/`part2`/`part3` top-level names are a naming convention for
this document only — `app/ml/dataset.py` does not require those specific
names; it discovers collections by walking every directory under
`DATASET_ROOT` and classifying each one by its own name / its ancestors'
names (see `_classify_directory` in that module), so extracting each `.7z`
directly under `DATASET_ROOT` with whatever top-level name it comes with
also works.

## File format

- **Images:** TIFF, Sentinel-1 Sigma0 SAR in decibels, 2048×2048 pixels,
  **2 channels** (VV, VH — dual polarization). `app/ml/dataset.py` rejects
  any image that isn't exactly 2 channels (`wrong_channels` issue) rather
  than silently guessing which band is which.
- **Masks:** TIFF, single-band, same 2048×2048 spatial size as their
  matching image, values `{0, 1}` — `1` = oil-covered pixel, `0` =
  background. For No-Oil and Lookalike scenes, the mask is all-zero (there
  is no oil to segment) — this is intentional, not a data error, and
  `app/ml/train.py` trains against it as such (correctly predicting an
  empty mask on these scenes is part of the segmentation task).
- **Filenames:** images and masks are paired by matching filename stem
  (e.g. `0001.tif` in an images folder pairs with `0001.tif` in the
  corresponding mask folder). Stems are **only unique within their own
  collection** (Part I's oil image `0001.tif` and Part III's test oil image
  `0001.tif` are unrelated files) — `app/ml/dataset.py` namespaces every
  record's `id` by part + category + stem specifically to avoid treating
  same-named files from different collections as duplicates.

## Splitting policy (train / val / test)

`app/ml/dataset.py build_manifests()` uses the `"dataset_native"` split
strategy by default and exclusively:

- **Part III is always the test set.** It is the dataset's own published,
  held-out test split and is never mixed into train/val, regardless of
  `--val-fraction`.
- **Parts I+II are the only train/val pool**, split by category
  (stratified) using a deterministic, seeded shuffle (`--seed`,
  `--val-fraction`, default 15% val).
- A cheap content-fingerprint check additionally guards against an
  accidentally duplicated file appearing in both the train/val pool and
  the test set (e.g. a botched re-extraction) — `build_manifests()` raises
  rather than silently producing a leaky split if that happens.

## What discovery does NOT do

- It never reads full pixel data during discovery/manifest-building
  (only TIFF header/shape metadata) — so `inspect` and `build-manifests`
  stay fast even against the full ~96.5 GB dataset.
- It never modifies, renames, or deletes anything under your extracted
  Part I/II/III folders — only new files under `<DATASET_ROOT>/manifests/`
  are written.
- It never assumes a file is fine just because it has a `.tif` extension —
  every candidate pair's header is opened and its shape/channel count
  checked before it's accepted into a manifest; anything that fails is
  recorded as an `issue` in `dataset_metadata.json`, not silently dropped
  or silently included.
