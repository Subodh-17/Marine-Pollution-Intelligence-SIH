# Dataset Setup — Real Zenodo Sentinel-1 SAR Oil Spill Dataset

> **This dataset is external and is NOT included in this repository or in
> any distributed ZIP of this project.** It is ~96.5 GB of compressed
> downloads across three separate Zenodo records, published under
> CC-BY-4.0 by Trujillo-Acatitla, Tuxpan-Vargas, Ovando-Vazquez &
> Monterrubio-Martinez. You must download and extract it yourself before
> any of the commands in this document will work. Nothing in this
> application downloads it automatically, and nothing in the application
> requires it to be present — the classical detector, the synthetic demo,
> and the shipped ML prototype all work fully offline with zero dataset
> dependency (see `MODEL_CARD.md`).

## 1. What you're downloading

The dataset is published in three parts, each a separate Zenodo record and
DOI:

| Part | DOI | Content | Compressed size |
|---|---|---|---|
| I | [10.5281/zenodo.8346860](https://doi.org/10.5281/zenodo.8346860) | 1200 train/val **Oil Spill** images + masks | ~40.7 GB |
| II | [10.5281/zenodo.8253899](https://doi.org/10.5281/zenodo.8253899) | 685 train/val **Lookalike** + 685 train/val **No-Oil** images + masks | ~45.9 GB |
| III | [10.5281/zenodo.13761290](https://doi.org/10.5281/zenodo.13761290) | 150 **test** Oil + 150 test Lookalike + 150 test No-Oil images + masks (the dataset's own held-out test split) | ~9.9 GB |

**Total compressed download: ~96.5 GB.** Each part is distributed as one or
more `.7z` archives. All images are Sentinel-1 Sigma0 SAR, in decibels,
2048×2048 pixels, with 2 polarization channels (VV, VH), stored as TIFF.
All masks are single-band TIFF at the same 2048×2048 size (foreground/oil
= 1, background = 0), not georeferenced.

## 2. Disk space estimate

Plan for **at least 150–200 GB of free disk space**: ~96.5 GB for the
downloaded `.7z` archives, plus roughly the same order of magnitude again
once extracted (TIFF is far less compressed than 7z for this kind of
imagery), plus working room for manifests and any checkpoints you train.
You do not need to keep the `.7z` files after extracting — deleting them
once extraction is verified will roughly halve your steady-state usage.

## 3. Citation & attribution

If you use this dataset (directly or through this pipeline), cite it:

> Trujillo-Acatitla, R., Tuxpan-Vargas, J., Ovando-Vazquez, C., &
> Monterrubio-Martinez, E. (2024). *Sentinel-1 SAR Oil spill image dataset
> for train, validate, and test deep learning models* (Parts I–III).
> Zenodo. https://doi.org/10.5281/zenodo.8346860

See `DATASET_LICENSE.md` for the full CC-BY-4.0 license text and what it
requires of you.

## 4. Download

1. Visit each of the three Zenodo record pages linked above.
2. Download every file listed on each record page (each part may be split
   across multiple `.7z` volumes — download all of them for a part before
   extracting).
3. Zenodo does not require an account to download, but downloads are large
   — a resumable download manager (e.g. `aria2c`, or your browser's native
   resume support) is strongly recommended over a single `curl`/`wget`
   call for the multi-GB files.

## 5. Extraction

Each part is a `.7z` archive (or set of them). On Windows, use
[7-Zip](https://www.7-zip.org/) (right-click → Extract Here). On
Linux/macOS, use `7z x <file>.7z` (install via `p7zip`) or Python's
`py7zr` (`pip install py7zr && python -m py7zr x <file>.7z <dest>`).

Extract each part into its own subfolder under a single dataset root, for
example:

```
DATASET_ROOT=./data/zenodo_sar
```

```
data/zenodo_sar/
├── part1/                                   # from 10.5281/zenodo.8346860
│   ├── 01_Train_Val_Oil_Spill_images/
│   └── 01_Train_Val_Oil_Spill_mask/
├── part2/                                   # from 10.5281/zenodo.8253899
│   ├── 01_Train_Val_Lookalike_images/
│   ├── 01_Train_Val_Lookalike_mask/
│   ├── 01_Train_Val_No_Oil_Images/
│   └── 01_Train_Val_No_Oil_mask/
└── part3/                                   # from 10.5281/zenodo.13761290
    └── 02_Test_images_and_ground_truth/
        ├── Images/
        │   ├── Oil/
        │   ├── Lookalike/
        │   └── No_oil/
        └── Mask/
            ├── Oil/
            ├── Lookalike/
            └── No_oil/
```

The exact subfolder names inside each part come straight from the
published archives — see `docs/DATASET_STRUCTURE.md` for the full layout
reference and for how this project's dataset-discovery code recognizes
both of the two different naming conventions used above (Part I/II encode
category+type in the leaf folder name; Part III nests category folders
under `Images`/`Mask`). You do **not** need to rename anything after
extraction — `app/ml/dataset.py` discovers files by structure, not by
requiring you to name your folders `part1`/`part2`/`part3` specifically
(those names above are just a clear convention for this doc).

**Do not point `DATASET_ROOT` at the folder containing the `.7z` files
themselves — it must point at the folder containing the *extracted* TIFF
images and masks.**

## 6. Verify the download/extraction

From the `backend/` directory (with the project's Python environment
active):

```bash
python -m app.ml.dataset --dataset-root ./data/zenodo_sar inspect
```

This reports how many image/mask pairs were discovered per category, and
flags anything missing, mismatched, or unreadable — without needing to
build manifests yet. If it reports zero pairs, re-check the folder layout
against `docs/DATASET_STRUCTURE.md` and confirm you extracted (not just
downloaded) every archive.

## 7. Build train/val/test manifests

```bash
python -m app.ml.dataset --dataset-root ./data/zenodo_sar build-manifests
```

This validates every discovered pair, splits Parts I+II into train/val
(Part III is always kept as the held-out test set — see
`docs/DATASET_STRUCTURE.md` "Splitting policy"), and writes
`train_manifest.json` / `val_manifest.json` / `test_manifest.json` /
`dataset_metadata.json` under `./data/zenodo_sar/manifests/`. This step
does not modify anything under your extracted Part I/II/III folders.

## 8. Train and evaluate

See the main `README.md` / `MODEL_CARD.md` for the full training,
evaluation, and promotion commands once manifests exist (`python -m
app.ml.train`, `python -m app.ml.evaluate_real`, `python -m app.ml.promote`).

## 9. Limitations to be aware of

- The dataset was published for research use; performance on Sentinel-1
  scenes acquired with different modes/incidence angles/sea states than it
  covers is not something this pipeline validates for you.
- The published test split (Part III) is what this pipeline treats as
  "held out" — it is the dataset authors' own test set, not an
  independently collected one.
- Masks are binary (oil vs. not-oil) — there is no severity/thickness
  label, so a trained model's output is a coverage mask, not a spill
  volume or severity estimate.
