"""
Real PyTorch training for the existing `SpillUNet` (segmentation) and
`SceneClassifier` (scene classification) architectures (app/ml/architectures.py)
against the real, locally-downloaded Zenodo Sentinel-1 SAR Oil Spill Dataset
manifests produced by `app/ml/dataset.py`.

This is an OFFLINE, EXPLICIT CLI TOOL - never imported by `app.main` (the
FastAPI app) and never run at server startup. See docs/DATASET_SETUP.md.

    python -m app.ml.train --dataset-root ./data/zenodo_sar --epochs 20

WHAT THIS DOES NOT DO
-----------------------
It never overwrites the shipped, synthetic-trained checkpoints
(`data/ml_checkpoints/spill_unet.pt`, `classifier_checkpoint.pth`) that
`app/ml/detector.py` loads by default - new checkpoints are written under
new, clearly-labelled filenames (`spill_unet_real_*.pt`,
`classifier_real_*.pth`). Promoting a newly-trained, evaluated checkpoint to
be the one `app/ml/detector.py` actually loads is a deliberate, separate,
explicit step - see `app/ml/promote.py` - never automatic.

HONESTY CONTRACT
------------------
This script does not print or save a training-completed / accuracy claim
unless the training loop actually ran to completion and computed those
numbers from real forward/backward passes on real (locally downloaded)
image data. If `--dataset-root` has no valid manifests, this exits non-zero
with a clear setup message (see app/ml/dataset.py::DatasetNotFoundError) -
it never silently substitutes synthetic data to "make training succeed".
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional

import numpy as np

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    from torch.utils.data import Dataset, DataLoader
    TORCH_AVAILABLE = True
except ImportError:  # pragma: no cover
    TORCH_AVAILABLE = False

try:
    from tqdm import tqdm as _tqdm
except ImportError:  # pragma: no cover - purely a progress-bar convenience, never required
    def _tqdm(iterable, **kwargs):
        return iterable

from app.config import get_settings
from app.ml import dataset as ds_mod
from app.ml.dataset import DatasetNotFoundError

# CLASS_LABELS order MUST match app/ml/detector.py::DEFAULT_CLASS_LABELS
# exactly - the classifier checkpoint's `class_mapping` (index -> label) is
# how app/ml/detector.py knows what each output logit means, and a mismatch
# here would silently mislabel every classifier prediction at inference time.
CLASS_LABELS = ["Oil", "No-Oil", "Lookalike"]
CLASS_TO_IDX = {label: i for i, label in enumerate(CLASS_LABELS)}


def set_seed(seed: int) -> None:
    """Best-effort determinism. Full bit-for-bit reproducibility across
    different machines/PyTorch builds/CPU-vs-CUDA is not guaranteed by
    PyTorch itself even with every seed fixed (see PyTorch's own
    reproducibility docs) - this sets every seed this codebase controls and
    disables the one major source of nondeterminism it can safely disable
    without breaking correctness (cudnn benchmark mode)."""
    random.seed(seed)
    np.random.seed(seed)
    if TORCH_AVAILABLE:
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        torch.backends.cudnn.benchmark = False


# =============================================================================
# Dataset
# =============================================================================

if TORCH_AVAILABLE:

    def _augment_sar_pair(image_t: "torch.Tensor", mask_t: "torch.Tensor"):
        """SAR-appropriate augmentation only - see SarPairDataset's
        `augment` docstring for why brightness/contrast/intensity jitter is
        deliberately excluded. `image_t` is (2, H, W) - VV and VH - and
        `mask_t` is (1, H, W); every transform is applied identically to
        both so pixel-to-mask alignment is never broken. Uses `torch.rand`/
        `torch.randint` (not the stdlib `random` module) so this respects
        whatever seed `torch.manual_seed()` set (see `set_seed` above),
        keeping a given training run reproducible end to end."""
        if torch.rand(()).item() < 0.5:
            image_t = torch.flip(image_t, dims=[-1])  # horizontal
            mask_t = torch.flip(mask_t, dims=[-1])
        if torch.rand(()).item() < 0.5:
            image_t = torch.flip(image_t, dims=[-2])  # vertical
            mask_t = torch.flip(mask_t, dims=[-2])
        k = int(torch.randint(0, 4, ()).item())  # 0, 90, 180, or 270 degrees
        if k:
            image_t = torch.rot90(image_t, k=k, dims=[-2, -1])
            mask_t = torch.rot90(mask_t, k=k, dims=[-2, -1])
        return image_t, mask_t

    class SarPairDataset(Dataset):
        """Loads (VV, VH) SAR image + binary oil mask pairs from a manifest
        produced by `app/ml/dataset.py::build_manifests`.

        Normalization is IDENTICAL to what `app/ml/detector.py` uses at
        inference time (`app.satellite.real_image.normalize_sar_band`,
        per-image 1st/99th-percentile min-max to [0,1]) - imported directly
        from that module rather than reimplemented, specifically so
        training-time and inference-time preprocessing can never silently
        drift apart. `preprocessing_version` (also imported from there) is
        what `app/ml/detector.py` reports in every inference response.
        """

        def __init__(self, manifest_path: str, dataset_root: str, image_size: int = 256,
                     limit: Optional[int] = None, augment: bool = False):
            import tifffile
            self._tifffile = tifffile
            manifest = ds_mod.load_manifest(manifest_path)
            self.dataset_root = dataset_root
            self.image_size = image_size
            self.records = manifest["records"]
            if limit is not None:
                self.records = self.records[:limit]
            # Only ever True for the TRAIN split (see train_ds/val_ds
            # construction below) - augmenting validation or (needless to
            # say) Part III test data would invalidate exactly the
            # metrics this whole pipeline exists to measure honestly.
            # Limited to horizontal flip, vertical flip, and 90-degree
            # rotations - transformations a real SAR acquisition could
            # itself have produced (a different satellite heading/orbit
            # pass), so they never invent backscatter values that
            # couldn't physically occur. No brightness/contrast/intensity
            # jitter: SAR pixel values are physically meaningful (radar
            # backscatter, already normalized per-image by
            # normalize_sar_band), not photographic pixels where that kind
            # of jitter is a widely-used, harmless augmentation - here it
            # would corrupt exactly the physical signal the model needs to
            # learn from.
            self.augment = augment

        def __len__(self):
            return len(self.records)

        def __getitem__(self, idx):
            from app.satellite.real_image import normalize_sar_band

            rec = self.records[idx]
            img_path = os.path.join(self.dataset_root, rec["image_path"])
            mask_path = os.path.join(self.dataset_root, rec["mask_path"])

            arr = self._tifffile.imread(img_path).astype(np.float64)
            if arr.ndim == 3 and arr.shape[-1] >= 2:
                vv_raw, vh_raw = arr[..., 0], arr[..., 1]
            elif arr.ndim == 3 and arr.shape[0] >= 2:
                vv_raw, vh_raw = arr[0], arr[1]
            else:
                raise ValueError(f"Expected a dual-channel image at {img_path}, got shape {arr.shape}")
            vv = normalize_sar_band(vv_raw)
            vh = normalize_sar_band(vh_raw)
            image = np.stack([vv, vh], axis=0)  # (2, H, W)

            mask_arr = self._tifffile.imread(mask_path)
            mask = (mask_arr > 0).astype(np.float32)[None, ...]  # (1, H, W)

            image_t = torch.from_numpy(image.astype(np.float32))
            mask_t = torch.from_numpy(mask)
            if image_t.shape[-2:] != (self.image_size, self.image_size):
                image_t = F.interpolate(image_t.unsqueeze(0), size=(self.image_size, self.image_size),
                                         mode="bilinear", align_corners=False).squeeze(0)
                mask_t = F.interpolate(mask_t.unsqueeze(0), size=(self.image_size, self.image_size),
                                        mode="nearest").squeeze(0)

            if self.augment:
                image_t, mask_t = _augment_sar_pair(image_t, mask_t)

            label_idx = CLASS_TO_IDX[rec["category"]]
            return image_t, mask_t, label_idx


# =============================================================================
# Losses
# =============================================================================

if TORCH_AVAILABLE:

    def dice_loss(logits: "torch.Tensor", target: "torch.Tensor", eps: float = 1e-6) -> "torch.Tensor":
        probs = torch.sigmoid(logits)
        probs_flat = probs.flatten(1)
        target_flat = target.flatten(1)
        intersection = (probs_flat * target_flat).sum(dim=1)
        union = probs_flat.sum(dim=1) + target_flat.sum(dim=1)
        dice = (2 * intersection + eps) / (union + eps)
        return 1.0 - dice.mean()

    def bce_dice_loss(logits: "torch.Tensor", target: "torch.Tensor", bce_weight: float = 0.5) -> "torch.Tensor":
        bce = F.binary_cross_entropy_with_logits(logits, target)
        dl = dice_loss(logits, target)
        return bce_weight * bce + (1 - bce_weight) * dl


# =============================================================================
# Training config / history
# =============================================================================

@dataclass
class TrainingConfig:
    dataset_root: str
    manifest_dir: str
    model: str  # "segmenter" | "classifier"
    architecture: str  # "SpillUNet" | "SceneClassifier" | "SceneClassifierV2"
    epochs: int
    batch_size: int
    lr: float
    image_size: int
    seed: int
    device: str
    checkpoint_dir: str
    bce_weight: float
    num_workers: int
    limit_train_samples: Optional[int]
    started_at: str


def _dataset_metadata_snapshot(manifest_dir: str) -> dict:
    meta_path = os.path.join(manifest_dir, "dataset_metadata.json")
    if not os.path.exists(meta_path):
        return {"available": False, "reason": f"dataset_metadata.json not found in {manifest_dir}"}
    with open(meta_path) as f:
        return json.load(f)


def _model_param_count(model) -> int:
    return sum(p.numel() for p in model.parameters())


def _epoch_segmenter(model, loader, optimizer, device, bce_weight: float, train: bool) -> dict:
    from app.evaluation.metrics import binary_mask_metrics

    model.train(mode=train)
    total_loss = 0.0
    n_batches = 0
    all_metrics = []
    context = torch.enable_grad() if train else torch.no_grad()
    with context:
        for images, masks, _labels in _tqdm(loader, desc="segmenter " + ("train" if train else "val"), leave=False):
            images = images.to(device)
            masks = masks.to(device)
            logits = model(images)
            loss = bce_dice_loss(logits, masks, bce_weight=bce_weight)
            if train:
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
            total_loss += float(loss.item())
            n_batches += 1
            with torch.no_grad():
                preds = (torch.sigmoid(logits) > 0.5).cpu().numpy().astype(bool)
                gts = masks.cpu().numpy().astype(bool)
                for i in range(preds.shape[0]):
                    m = binary_mask_metrics(preds[i, 0], gts[i, 0])
                    all_metrics.append(m)
    mean_loss = total_loss / max(1, n_batches)
    if all_metrics:
        mean_iou = float(np.mean([m.iou for m in all_metrics]))
        mean_f1 = float(np.mean([m.f1 for m in all_metrics]))
        mean_dice = float(np.mean([m.dice for m in all_metrics]))
        mean_px_acc = float(np.mean([m.pixel_accuracy for m in all_metrics]))
    else:
        mean_iou = mean_f1 = mean_dice = mean_px_acc = 0.0
    return {"loss": round(mean_loss, 6), "iou": round(mean_iou, 4), "f1": round(mean_f1, 4),
            "dice": round(mean_dice, 4), "pixel_accuracy": round(mean_px_acc, 4)}


def _compute_class_weights(records, device) -> "torch.Tensor":
    """Inverse-frequency class weights for `F.cross_entropy`, computed from
    the ACTUAL training-split record counts (never hardcoded, never from
    val/test) - so this correctly reflects whatever real class balance the
    dataset genuinely has, rather than an assumption baked into the code.
    Standard `n_samples / (n_classes * count_c)` weighting: a class with
    fewer training examples gets a proportionally larger loss weight, so
    the optimizer is not free to minimize loss by just favoring whichever
    class happens to be most numerous. Returns a `(num_classes,)` tensor in
    `CLASS_LABELS` order."""
    counts = [0] * len(CLASS_LABELS)
    for rec in records:
        counts[CLASS_TO_IDX[rec["category"]]] += 1
    n = sum(counts)
    k = len(CLASS_LABELS)
    weights = [n / (k * c) if c > 0 else 0.0 for c in counts]
    return torch.tensor(weights, dtype=torch.float32, device=device)


def _epoch_classifier(model, loader, optimizer, device, train: bool, class_weights=None) -> dict:
    model.train(mode=train)
    total_loss = 0.0
    n_batches = 0
    correct = 0
    total = 0
    context = torch.enable_grad() if train else torch.no_grad()
    with context:
        for images, _masks, labels in _tqdm(loader, desc="classifier " + ("train" if train else "val"), leave=False):
            images = images.to(device)
            labels = torch.as_tensor(labels, dtype=torch.long, device=device)
            logits = model(images)
            # Weighted on train (counteracts class imbalance in what the
            # optimizer is pushed to do); UNWEIGHTED on val - validation
            # loss/accuracy should reflect genuine, unweighted performance,
            # not be flattered or penalized by a weighting choice that only
            # makes sense as a training-time optimization nudge.
            loss = F.cross_entropy(logits, labels, weight=class_weights if train else None)
            if train:
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
            total_loss += float(loss.item())
            n_batches += 1
            preds = torch.argmax(logits, dim=1)
            correct += int((preds == labels).sum().item())
            total += labels.shape[0]
    mean_loss = total_loss / max(1, n_batches)
    acc = correct / total if total else 0.0
    return {"loss": round(mean_loss, 6), "accuracy": round(acc, 4)}


def train_model(
    dataset_root: str,
    model_kind: str = "segmenter",
    architecture: Optional[str] = None,
    manifest_dir: Optional[str] = None,
    epochs: int = 20,
    batch_size: int = 4,
    lr: float = 1e-3,
    image_size: int = 256,
    seed: int = 42,
    device: str = "cpu",
    checkpoint_dir: Optional[str] = None,
    bce_weight: float = 0.5,
    num_workers: int = 0,
    limit_train_samples: Optional[int] = None,
) -> dict:
    """Trains either the segmenter ("segmenter" -> SpillUNet) or the
    classifier ("classifier" -> SceneClassifier) against the real dataset
    manifests, and writes checkpoints/history/config/metadata to
    `checkpoint_dir` under `<model_kind>_real_*` filenames.

    Raises DatasetNotFoundError (never falls back to synthetic data) if the
    manifests are missing - run `python -m app.ml.dataset build-manifests`
    first (see docs/DATASET_SETUP.md).
    """
    if not TORCH_AVAILABLE:
        raise RuntimeError("PyTorch is not installed. Install it via `pip install -r requirements-ml.txt`.")
    if model_kind not in ("segmenter", "classifier"):
        raise ValueError(f"Unknown model_kind {model_kind!r} - expected 'segmenter' or 'classifier'.")
    # `architecture` only applies to model_kind="classifier" - the segmenter
    # has one architecture (SpillUNet). Default stays the ORIGINAL
    # SceneClassifier for full backward compatibility (an existing script/
    # CI invocation with no --architecture flag trains exactly what it
    # always did); "scene_classifier_v2" opts into the redesigned
    # architecture - see architectures.py::SceneClassifierV2 docstring for
    # why it exists (root-caused against a reported 37.56% independent
    # Part III accuracy from the original architecture - see README_ML_
    # STATUS.md "SceneClassifierV2: root-cause and redesign").
    architecture = architecture or "scene_classifier"
    if model_kind == "classifier" and architecture not in ("scene_classifier", "scene_classifier_v2"):
        raise ValueError(f"Unknown architecture {architecture!r} for model_kind='classifier' - "
                          "expected 'scene_classifier' or 'scene_classifier_v2'.")

    settings = get_settings()
    manifest_dir = manifest_dir or os.path.join(dataset_root, "manifests")
    checkpoint_dir = checkpoint_dir or settings.ml_checkpoint_dir
    os.makedirs(checkpoint_dir, exist_ok=True)

    train_manifest_path = os.path.join(manifest_dir, "train_manifest.json")
    val_manifest_path = os.path.join(manifest_dir, "val_manifest.json")
    if not (os.path.exists(train_manifest_path) and os.path.exists(val_manifest_path)):
        raise DatasetNotFoundError(
            f"train/val manifests not found in {manifest_dir!r}. Run "
            "`python -m app.ml.dataset --dataset-root <path> build-manifests` first - see docs/DATASET_SETUP.md."
        )

    set_seed(seed)
    torch_device = torch.device(device)

    train_ds = SarPairDataset(train_manifest_path, dataset_root, image_size=image_size,
                               limit=limit_train_samples, augment=True)
    val_ds = SarPairDataset(val_manifest_path, dataset_root, image_size=image_size, augment=False)
    if len(train_ds) == 0 or len(val_ds) == 0:
        raise DatasetNotFoundError(
            f"train_manifest.json / val_manifest.json in {manifest_dir!r} contain zero records - "
            "rebuild manifests (see docs/DATASET_SETUP.md)."
        )

    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True, num_workers=num_workers)
    val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False, num_workers=num_workers)

    from app.ml.architectures import SceneClassifier, SceneClassifierV2, SpillUNet

    if model_kind == "segmenter":
        model = SpillUNet(in_channels=2, out_channels=1).to(torch_device)
    elif architecture == "scene_classifier_v2":
        model = SceneClassifierV2(in_channels=2, num_classes=len(CLASS_LABELS)).to(torch_device)
    else:
        model = SceneClassifier(in_channels=2, num_classes=len(CLASS_LABELS)).to(torch_device)

    optimizer = torch.optim.Adam(model.parameters(), lr=lr)

    from app.ml.paths import portable_path

    config = TrainingConfig(
        dataset_root=portable_path(dataset_root), manifest_dir=portable_path(manifest_dir),
        model=model_kind, architecture=type(model).__name__,
        epochs=epochs, batch_size=batch_size, lr=lr, image_size=image_size,
        seed=seed, device=str(torch_device), checkpoint_dir=portable_path(checkpoint_dir),
        bce_weight=bce_weight, num_workers=num_workers, limit_train_samples=limit_train_samples,
        started_at=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
    )

    # Computed once, from the train split's own real record counts - see
    # _compute_class_weights' docstring. None for the segmenter (not
    # applicable to a per-pixel BCE loss here; that one already has its own
    # separate `bce_weight` knob).
    classifier_class_weights = _compute_class_weights(train_ds.records, torch_device) if model_kind == "classifier" else None
    if classifier_class_weights is not None:
        print(f"[classifier] class weights (train-split inverse-frequency, "
              f"{CLASS_LABELS} order): {[round(w, 3) for w in classifier_class_weights.tolist()]}")

    history = []
    best_metric = -1.0
    best_epoch = -1
    t0 = time.perf_counter()

    for epoch in range(1, epochs + 1):
        epoch_t0 = time.perf_counter()
        if model_kind == "segmenter":
            train_stats = _epoch_segmenter(model, train_loader, optimizer, torch_device, bce_weight, train=True)
            val_stats = _epoch_segmenter(model, val_loader, optimizer, torch_device, bce_weight, train=False)
            val_score = val_stats["iou"]
        else:
            train_stats = _epoch_classifier(model, train_loader, optimizer, torch_device, train=True,
                                             class_weights=classifier_class_weights)
            val_stats = _epoch_classifier(model, val_loader, optimizer, torch_device, train=False,
                                           class_weights=classifier_class_weights)
            val_score = val_stats["accuracy"]

        epoch_record = {
            "epoch": epoch, "train": train_stats, "val": val_stats,
            "duration_seconds": round(time.perf_counter() - epoch_t0, 2),
        }
        history.append(epoch_record)

        is_best = val_score > best_metric
        if is_best:
            best_metric = val_score
            best_epoch = epoch

        checkpoint_payload = {
            "model_state_dict": model.state_dict(),
            "epoch": epoch,
            "val_metrics": val_stats,
            "seed": seed,
            "architecture": model.__class__.__name__,
            "input_channels": 2,
            "preprocessing_version": _preprocessing_version(),
            "class_mapping": {i: label for i, label in enumerate(CLASS_LABELS)} if model_kind == "classifier" else None,
            "trained_on": "REAL_ZENODO_SENTINEL1_SAR_OIL_SPILL_DATASET",
            "dataset_dois": ds_mod.DATASET_DOIS,
        }
        last_path = os.path.join(checkpoint_dir, f"{_ckpt_stub(model_kind)}_real_last.{_ckpt_ext(model_kind)}")
        torch.save(checkpoint_payload, last_path)
        if is_best:
            best_path = os.path.join(checkpoint_dir, f"{_ckpt_stub(model_kind)}_real_best.{_ckpt_ext(model_kind)}")
            torch.save(checkpoint_payload, best_path)

        print(f"[{model_kind}] epoch {epoch}/{epochs}  train={train_stats}  val={val_stats}"
              f"{'  <- best' if is_best else ''}")

    total_seconds = time.perf_counter() - t0

    training_summary = {
        "config": asdict(config),
        "history": history,
        "best_epoch": best_epoch,
        "best_val_metric": best_metric,
        "total_training_seconds": round(total_seconds, 2),
        "completed": True,
        "completed_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        "num_train_samples": len(train_ds),
        "num_val_samples": len(val_ds),
        "model_param_count": _model_param_count(model),
    }

    stub = _ckpt_stub(model_kind)
    with open(os.path.join(checkpoint_dir, f"{stub}_real_training_config.json"), "w") as f:
        json.dump(asdict(config), f, indent=2)
    with open(os.path.join(checkpoint_dir, f"{stub}_real_training_history.json"), "w") as f:
        json.dump(history, f, indent=2)
    with open(os.path.join(checkpoint_dir, f"{stub}_real_dataset_manifest.json"), "w") as f:
        json.dump(_dataset_metadata_snapshot(manifest_dir), f, indent=2)
    with open(os.path.join(checkpoint_dir, f"{stub}_real_model_metadata.json"), "w") as f:
        json.dump({
            "architecture": model.__class__.__name__,
            "input_channels": 2,
            "num_classes": len(CLASS_LABELS) if model_kind == "classifier" else None,
            "output": "binary_segmentation_mask" if model_kind == "segmenter" else "3_class_scene_label",
            "param_count": _model_param_count(model),
            "preprocessing_version": _preprocessing_version(),
            "framework": f"torch=={torch.__version__}",
            "trained_on": "REAL_ZENODO_SENTINEL1_SAR_OIL_SPILL_DATASET",
            "dataset_dois": ds_mod.DATASET_DOIS,
            "dataset_citation": ds_mod.DATASET_CITATION,
            "training_date": training_summary["completed_at"],
            "best_epoch": best_epoch,
            "best_val_metric": best_metric,
            "num_train_samples": len(train_ds),
            "num_val_samples": len(val_ds),
            "seed": seed,
        }, f, indent=2)

    return training_summary


def _preprocessing_version() -> str:
    from app.satellite.real_image import PREPROCESSING_VERSION
    return PREPROCESSING_VERSION


def _ckpt_stub(model_kind: str) -> str:
    return "spill_unet" if model_kind == "segmenter" else "classifier"


def _ckpt_ext(model_kind: str) -> str:
    return "pt" if model_kind == "segmenter" else "pth"


# =============================================================================
# CLI
# =============================================================================

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Train the SpillUNet segmenter and/or SceneClassifier on the real Zenodo dataset.")
    settings = get_settings()
    parser.add_argument("--dataset-root", default=settings.dataset_root)
    parser.add_argument("--manifest-dir", default=None, help="Default: <dataset-root>/manifests")
    parser.add_argument("--models", default="segmenter,classifier",
                         help="Comma-separated: segmenter, classifier, or both (default: both).")
    parser.add_argument("--epochs", type=int, default=settings.ml_train_epochs)
    parser.add_argument("--batch-size", type=int, default=settings.ml_train_batch_size)
    parser.add_argument("--lr", type=float, default=settings.ml_train_lr)
    parser.add_argument("--image-size", type=int, default=settings.ml_train_image_size)
    parser.add_argument("--seed", type=int, default=settings.ml_train_seed)
    parser.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
    parser.add_argument("--checkpoint-dir", default=settings.ml_checkpoint_dir)
    parser.add_argument("--bce-weight", type=float, default=0.5)
    parser.add_argument("--num-workers", type=int, default=0,
                         help="DataLoader workers. Keep at 0 on Windows unless you guard the entry "
                              "point with `if __name__ == '__main__':` (this script already does).")
    parser.add_argument("--limit-train-samples", type=int, default=None,
                         help="Debug/smoke-test only: cap the number of training samples used.")
    parser.add_argument("--architecture", default="scene_classifier",
                         choices=["scene_classifier", "scene_classifier_v2"],
                         help="Only applies to --models classifier. 'scene_classifier' is the "
                              "original, currently-shipped architecture (default, backward "
                              "compatible). 'scene_classifier_v2' is the redesigned, deeper "
                              "architecture - see architectures.py::SceneClassifierV2 docstring "
                              "and README_ML_STATUS.md for why it exists.")
    args = parser.parse_args(argv)

    if args.device == "cuda" and not (TORCH_AVAILABLE and torch.cuda.is_available()):
        print("ERROR: --device cuda requested but CUDA is not available in this environment.", file=sys.stderr)
        return 1

    models_to_train = [m.strip() for m in args.models.split(",") if m.strip()]
    for m in models_to_train:
        if m not in ("segmenter", "classifier"):
            print(f"ERROR: unknown model {m!r} in --models (expected 'segmenter' and/or 'classifier').", file=sys.stderr)
            return 1

    for model_kind in models_to_train:
        try:
            summary = train_model(
                dataset_root=args.dataset_root, model_kind=model_kind,
                architecture=args.architecture if model_kind == "classifier" else None,
                manifest_dir=args.manifest_dir, epochs=args.epochs, batch_size=args.batch_size,
                lr=args.lr, image_size=args.image_size, seed=args.seed, device=args.device,
                checkpoint_dir=args.checkpoint_dir, bce_weight=args.bce_weight,
                num_workers=args.num_workers, limit_train_samples=args.limit_train_samples,
            )
        except DatasetNotFoundError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        print(f"\n{model_kind}: training complete. Best epoch {summary['best_epoch']} "
              f"(val metric {summary['best_val_metric']}). Checkpoints written to {args.checkpoint_dir}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
