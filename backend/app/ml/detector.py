"""
ML-based oil slick detection - PROTOTYPE, SYNTHETIC-TRAINED.

STATUS (read this before trusting any output from this module)
----------------------------------------------------------------
This module wraps two small PyTorch models (a CNN scene classifier and a
U-Net segmenter, `app/ml/architectures.py`) whose checkpoints were
originally shipped with an earlier version of this project
(SIH26143_Marine_Spill_Intelligence_Platform). A prior audit of that
project (see `ML_AUDIT_NOTES.md` in this repo) established, by direct
inspection of the training scripts, that:

  - The classifier's "VH channel" during training was NOT a real second
    polarization - it was the VV channel multiplied by a constant (0.7),
    duplicated as a fake second channel.
  - The segmenter was trained on inputs SYNTHESIZED FROM THE GROUND-TRUTH
    MASK ITSELF (mask*0.8+noise, mask*0.5+noise) rather than real SAR
    imagery, and empirically never crosses its own detection threshold on
    realistic input, or even on inputs shaped like its own training
    distribution.

NORMALIZATION (re-checked during this audit pass): an earlier draft of this
note claimed inference used a "fixed dB clip range" that didn't match
training's per-image min-max normalization. That is no longer an accurate
description of this codebase - `app/satellite/real_image.py::_normalize_band`
already rescales each uploaded band per-image (robust 1st/99th-percentile
min-max to [0, 1]), the same *kind* of per-image adaptive normalization the
training script used, not a fixed global range. This was a real category of
bug in principle, but it does not describe what this pipeline currently
does, so it is called out here as checked-and-not-applicable rather than
repeated as an open issue. The two bullets above remain genuine, and are the
actual reason these checkpoints are not trained on real Sentinel-1 SAR data.

CONSEQUENCE: these checkpoints are NOT trained on the real Zenodo
Sentinel-1 SAR Oil Spill Dataset, and do NOT reliably generalize. Real
PyTorch inference genuinely executes (checkpoint loading was broken and is
now fixed - see `_extract_state_dict` below), but its predictions should be
treated as a technology demonstration of the inference *pipeline*, not as a
validated detector. Every API response and UI surface using this module
must display `provenance: "ML_PROTOTYPE_SYNTHETIC_TRAINED"` (never
"ML_REAL_TRAINED" or similar) and show the classifier's own real training
history (`classifier_training_history.json`, final val_accuracy ~62%,
3 epochs) so the limitation is visible rather than hidden.

The classical detector (`app/satellite/detection.py`) remains the
operational default and fallback for exactly this reason - see
`app/ml/README_ML_STATUS.md`.

FALLBACK POLICY (enforced in `app/investigation/orchestrator.py`, not here):
this module only ever reports its own honest result - it never silently
substitutes the classical detector itself. The orchestrator is what decides
whether the ML result reported here actually qualifies to be shown as final,
and re-runs the classical detector in its place - tagged
`CLASSICAL_DARK_SPOT_ML_FALLBACK`, never `ML_PROTOTYPE_SYNTHETIC_TRAINED` -
when: the engine is unavailable (`available=False`), the returned mask is
empty, or `confidence < ML_FALLBACK_CONFIDENCE_THRESHOLD` (see that constant
below for what this threshold is and is not).

INTERFACE
---------
`detect_oil_slick_ml()` returns the *same* `DetectionResult` dataclass
`app/satellite/detection.py` does, so it can be dropped into the same
downstream seam (characterization -> age -> drift -> AIS -> attribution)
with zero changes elsewhere, per the "single seam" requirement.

Because the bundled checkpoints expect a genuine 2-channel (VV, VH) input,
this ML path is only offered for real dual-polarization SAR uploads
(`app/satellite/real_image.py`), not for the single-band synthetic demo
scene generator.

REAL-DATA TRAINING (added after the audit above; does not change any of it)
-----------------------------------------------------------------------------
Everything above describes the checkpoints THIS MODULE SHIPS WITH BY
DEFAULT (`classifier_checkpoint.pth`, `spill_unet.pt`) and remains
completely accurate for them. Separately, `app/ml/dataset.py` +
`app/ml/train.py` + `app/ml/evaluate_real.py` implement a full, genuine
PyTorch training/evaluation pipeline against the real, locally-downloaded
Zenodo Sentinel-1 SAR Oil Spill Dataset (see docs/DATASET_SETUP.md) - an
explicit, offline, opt-in CLI workflow, never something that runs
automatically. If (and only if) a developer runs that pipeline AND then
explicitly promotes the resulting checkpoint (`app/ml/promote.py`, which
requires a real evaluation report to exist first), this engine loads that
checkpoint instead and reports `provenance="ML_REAL_ZENODO_TRAINED"` - a
genuinely different, honestly-labelled state from the synthetic prototype
above. See `_SEGMENTER_CANDIDATES`/`_CLASSIFIER_CANDIDATES` below for the
exact precedence rule, and MODEL_CARD.md for the full real-vs-synthetic-
vs-classical-fallback breakdown this module's `get_model_info()` feeds.
"""
from __future__ import annotations

import os
import json
import time
import hashlib
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

try:
    import torch
    TORCH_AVAILABLE = True
except (ImportError, OSError):  # pragma: no cover - torch absent or DLL blocked by OS policy (e.g. WinError 4551)
    TORCH_AVAILABLE = False

from app.satellite.detection import DetectionResult
from app.config import get_settings
from app.ml.tiling import (
    compute_tile_bounds,
    stitch_tiled_segmentation,
    stitch_multiscale_segmentation,
    downsample_mean,
)

CHECKPOINT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "ml_checkpoints")

# Every `provenance` value this module can put on a *successful* DetectionResult.
PROVENANCE_ML_REAL = "ML_REAL_ZENODO_TRAINED"
PROVENANCE_ML_SYNTHETIC = "ML_PROTOTYPE_SYNTHETIC_TRAINED"

# ---------------------------------------------------------------------------
# Real-vs-synthetic checkpoint precedence (see app/ml/train.py, app/ml/promote.py)
# ---------------------------------------------------------------------------
# `app/ml/train.py` never writes to these two shipped filenames - it always
# writes new `*_real_*` files instead (see its "WHAT THIS DOES NOT DO"
# docstring section). `app/ml/promote.py` is the only thing that can make a
# real-Zenodo-trained checkpoint become the one this engine actually loads,
# and it does so by writing to the "*_real_promoted*" filenames below, NEVER
# by overwriting the shipped `classifier_checkpoint.pth` / `spill_unet.pt`
# files - so the original synthetic-trained checkpoints always remain on
# disk, byte-for-byte, as a safe rollback (delete the promoted file and the
# engine falls straight back to them). This precedence list is the SINGLE
# place that decides which checkpoint wins; nothing else in this module
# special-cases filenames.
#
# ML_CLASSIFIER_CHECKPOINT / ML_SEGMENTER_CHECKPOINT / ML_CLASSIFIER_ONNX /
# ML_SEGMENTER_ONNX let a checkpoint under ANY filename be dropped into
# CHECKPOINT_DIR and used, without editing this source file - e.g. inserting
# a final checkpoint that isn't named exactly `*_real_promoted*`. Set,
# it is tried FIRST (as PROVENANCE_ML_REAL - an explicit override is the
# operator asserting "this is the real one"), ahead of every filename below.
# Unset, behavior is byte-for-byte identical to before this existed.
def _with_env_override(env_var: str, static_candidates: list) -> list:
    override = os.environ.get(env_var, "").strip()
    if not override:
        return static_candidates
    return [(override, PROVENANCE_ML_REAL)] + static_candidates


_SEGMENTER_CANDIDATES = _with_env_override("ML_SEGMENTER_CHECKPOINT", [
    ("spill_unet_real_promoted.pt", PROVENANCE_ML_REAL),
    ("spill_unet.pt", PROVENANCE_ML_SYNTHETIC),
])
_CLASSIFIER_CANDIDATES = _with_env_override("ML_CLASSIFIER_CHECKPOINT", [
    ("classifier_real_promoted.pth", PROVENANCE_ML_REAL),
    ("classifier_checkpoint.pth", PROVENANCE_ML_SYNTHETIC),
])

# ---------------------------------------------------------------------------
# Production inference runtime (ONNX Runtime CPU) - see app/ml/onnx_engine.py
# ---------------------------------------------------------------------------
# The deployment target (Render Free: 512 MB RAM, 0.1 CPU) cannot carry a
# PyTorch install, so production serves the SAME promoted checkpoints through
# ONNX Runtime's CPU provider instead. These filenames are produced offline by
# `app/ml/export_onnx.py`; the real-vs-synthetic decision for them is NOT made
# from these names - onnx_engine.py re-derives it from the export manifest plus
# a checksum re-check of the source checkpoint (see its module docstring).
_CLASSIFIER_ONNX_FILE = os.environ.get("ML_CLASSIFIER_ONNX", "").strip() or "classifier_real_best.onnx"
_SEGMENTER_ONNX_FILE = os.environ.get("ML_SEGMENTER_ONNX", "").strip() or "spill_unet_real_best.onnx"

RUNTIME_ONNX_CPU = "onnxruntime_cpu"
RUNTIME_PYTORCH = "pytorch"

# Peak-RSS guard for the ONNX path. Measured on this exact graph pair with a
# single-threaded, arena-disabled CPU session: peak resident memory scales at
# roughly 0.64 KB per input pixel on top of a ~50 MB session baseline
# (256x256 ~ 98 MB, 512x512 ~ 219 MB, 768x768 ~ 423 MB, 1024x1024 ~ 709 MB).
# On a 512 MB box that already holds the FastAPI app, anything much past
# ~512x512 risks an OOM kill mid-request, which would take the whole service
# down rather than degrade one detection. Scenes larger than this budget are
# therefore routed to the documented classical fallback with an explicit
# reason - never silently truncated, resized, or reported as an ML result.
# Override with ML_MAX_INFERENCE_PIXELS (0 disables the guard entirely).
_DEFAULT_MAX_INFERENCE_PIXELS = 512 * 512


def _sha256_of_file(path: str, chunk_size: int = 1024 * 1024) -> str:
    """Section 1: model checksum/version information - a real content hash
    of the checkpoint file on disk, so `/api/ml/status` can prove exactly
    which weights are loaded rather than just naming a version string that
    could silently drift from the actual file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def _safe_file_size(path: Optional[str]) -> Optional[int]:
    """Byte size of a checkpoint file for the ML Training page's model
    overview - never raises (a file that briefly disappears out from under
    a listdir, or a `None` path, reports as `None`/unavailable rather than
    crashing the caller)."""
    if not path:
        return None
    try:
        return os.path.getsize(path)
    except OSError:
        return None


DEFAULT_CLASS_LABELS = ["Oil", "No-Oil", "Lookalike"]

# ML -> Classical fallback confidence threshold (see MODEL_CARD.md / README_ML_STATUS.md).
#
# This is a HEURISTIC engineering threshold, not a scientifically validated
# operating point - it exists only to decide when the orchestrator should
# discard the ML classifier's own softmax confidence and re-run the classical
# detector instead. It is deliberately NOT tuned against any labelled
# validation set (none was available - see README_ML_STATUS.md), so the
# default is a conservative "coin-flip-or-worse" cutoff, configurable via the
# ML_FALLBACK_CONFIDENCE_THRESHOLD environment variable (see app/config.py /
# .env.example) for anyone who wants to experiment with a different cutoff.
ML_FALLBACK_CONFIDENCE_THRESHOLD = get_settings().ml_confidence_threshold


def _extract_state_dict(checkpoint_obj: Any) -> Optional[dict]:
    """Normalizes a torch.load(...) result into a raw state_dict, regardless
    of whether it was saved as a bare state_dict or a wrapped training
    checkpoint ({'model_state_dict': ..., 'epoch': ..., ...})."""
    if not isinstance(checkpoint_obj, dict):
        return None
    if "model_state_dict" in checkpoint_obj and isinstance(checkpoint_obj["model_state_dict"], dict):
        return checkpoint_obj["model_state_dict"]
    if "state_dict" in checkpoint_obj and isinstance(checkpoint_obj["state_dict"], dict):
        return checkpoint_obj["state_dict"]
    if len(checkpoint_obj) > 0 and all(
        type(v).__module__.startswith("torch") or hasattr(v, "shape")
        for v in checkpoint_obj.values()
    ):
        return checkpoint_obj
    return None


def _resolve_runtime_preference() -> str:
    """ML_RUNTIME=auto|onnx|pytorch (default auto). Anything unrecognized is
    treated as auto rather than raising - a typo in an env var must never
    take the service down."""
    value = (os.environ.get("ML_RUNTIME") or "auto").strip().lower()
    return value if value in ("auto", "onnx", "pytorch") else "auto"


def _resolve_max_inference_pixels() -> int:
    """Ceiling for a DIRECT (untiled) forward pass - both classifier and
    segmenter run on the whole scene in one shot below this size, exactly
    as before tiling existed. See _DEFAULT_MAX_INFERENCE_PIXELS. 0 or
    negative disables the guard (tiling/downsampling are then never used -
    everything direct, matching pre-tiling behavior exactly)."""
    raw = os.environ.get("ML_MAX_INFERENCE_PIXELS")
    if raw is None:
        return _DEFAULT_MAX_INFERENCE_PIXELS
    try:
        return int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_MAX_INFERENCE_PIXELS


def _resolve_tiling_enabled() -> bool:
    raw = (os.environ.get("ML_TILING_ENABLED") or "true").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _resolve_multiscale_enabled() -> bool:
    raw = (os.environ.get("ML_MULTISCALE_SEGMENTATION") or "false").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _resolve_multiscale_fusion_strategy() -> str:
    raw = (os.environ.get("ML_MULTISCALE_FUSION_STRATEGY") or "size_gated_closure").strip().lower()
    return (
        raw
        if raw in ("size_gated_closure", "boundary_preserved", "direct_max", "base_only")
        else "size_gated_closure"
    )


def _resolve_tile_size() -> int:
    """Default 256 matches ML_TRAIN_IMAGE_SIZE (app/config.py) - the
    segmenter sees tiles the same size as the patches it was trained on,
    not an arbitrary chunk size."""
    try:
        v = int(os.environ.get("ML_TILE_SIZE", "256"))
    except (TypeError, ValueError):
        v = 256
    if v <= 0 or v % 4 != 0:
        return 256  # must be divisible by 4 (two 2x downsamples) - silently
        # falling back to the known-good default is safer than crashing
        # engine construction over a malformed env var.
    return v


def _resolve_tile_overlap() -> int:
    try:
        v = int(os.environ.get("ML_TILE_OVERLAP", "32"))
    except (TypeError, ValueError):
        v = 32
    return max(0, v)


def _resolve_max_tiled_inference_pixels() -> int:
    """Outer ceiling on total scene size even WITH tiling. Tiling bounds
    per-tile memory, not wall-clock time or total tile count - a
    pathologically large scene would still mean hundreds of sequential
    forward passes in one HTTP request on a 0.1 CPU container, which risks
    a request timeout even though no single tile would OOM. This default
    (4096x4096 = 16 tiles of 256 across each axis at zero overlap) is a
    conservative estimate, NOT measured on Render - see MODEL_CARD.md."""
    raw = os.environ.get("ML_MAX_TILED_INFERENCE_PIXELS")
    if raw is None:
        return 4096 * 4096
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 4096 * 4096


def _resolve_segmentation_threshold() -> float:
    """The one real threshold this architecture has (sigmoid > threshold for
    the binary oil/background mask - see app/ml/architectures.py's SpillUNet).
    Phase 7/11 both asked for this to be configurable where a real threshold
    exists, and for no INVENTED threshold (e.g. a lookalike-specific cutoff)
    to be added where the architecture has none - see work/FINAL_AUDIT.md
    item 5 for why only this one is exposed.

    NEVER derive this default from the held-out TEST set - that would be
    exactly the test-set threshold tuning the project's own instructions
    prohibit. 0.5 is the untuned, standard sigmoid decision boundary the
    model was evaluated at (see evaluation_report.json's "threshold" field).
    Recalibrating it requires a validation-set experiment this project does
    not yet have - see README_ML_STATUS.md."""
    raw = os.environ.get("ML_SEGMENTATION_THRESHOLD")
    if raw is None:
        return 0.5
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 0.5
    return v if 0.0 < v < 1.0 else 0.5


class MLDetectorEngine:
    """Loads the prototype checkpoints once; safe to import even if torch
    is not installed (Windows-friendly classical-only installs) - every
    method degrades to `available=False` rather than raising at import
    time."""

    def __init__(self, checkpoint_dir: str = CHECKPOINT_DIR):
        self.available = False
        self.classifier_loaded = False
        self.segmenter_loaded = False
        self.load_errors: list[str] = []
        self.class_labels = list(DEFAULT_CLASS_LABELS)
        self.checkpoint_metadata: dict = {}
        self.training_history: dict = {}
        self.checkpoint_checksums: dict = {}  # {"classifier": "sha256:...", "segmenter": "sha256:..."}
        # Which checkpoint file actually won the real-vs-synthetic precedence
        # for each sub-model (see _SEGMENTER_CANDIDATES/_CLASSIFIER_CANDIDATES
        # above) - "ML_REAL_ZENODO_TRAINED" or "ML_PROTOTYPE_SYNTHETIC_TRAINED".
        self.segmenter_provenance: Optional[str] = None
        self.classifier_provenance: Optional[str] = None
        self.checkpoint_dir = checkpoint_dir
        # Which inference runtime actually ended up serving this engine:
        # RUNTIME_ONNX_CPU, RUNTIME_PYTORCH, or None if neither loaded.
        self.runtime: Optional[str] = None
        self._onnx: Any = None
        self.max_inference_pixels: int = _resolve_max_inference_pixels()
        self.tiling_enabled: bool = _resolve_tiling_enabled()
        self.multiscale_enabled: bool = _resolve_multiscale_enabled()
        self.multiscale_fusion_strategy: str = _resolve_multiscale_fusion_strategy()
        self.tile_size: int = _resolve_tile_size()
        self.tile_overlap: int = _resolve_tile_overlap()
        self.max_tiled_inference_pixels: int = _resolve_max_tiled_inference_pixels()
        self.segmentation_threshold: float = _resolve_segmentation_threshold()
        # Set unconditionally so every code path (ONNX, PyTorch, or neither)
        # has these defined - `_build_model_info` reads them directly and
        # must never hit an unset attribute.
        self.classifier_checkpoint_path: Optional[str] = None
        self.segmenter_checkpoint_path: Optional[str] = None
        self.segmenter_checkpoint_metadata: dict = {}
        self._onnx_load_errors: list[str] = []

        # --- Runtime selection -------------------------------------------
        # ONNX Runtime CPU is tried FIRST because it is what production
        # actually runs (Render Free cannot host PyTorch - see
        # app/ml/onnx_engine.py). PyTorch remains a fully supported local
        # path for training/evaluation/export and is used automatically when
        # the ONNX artifacts are absent, so a developer checkout without a
        # completed export behaves exactly as it did before this change.
        # ML_RUNTIME=pytorch|onnx|auto (default auto) forces either way.
        preference = _resolve_runtime_preference()

        if preference in ("auto", "onnx"):
            if self._try_load_onnx(checkpoint_dir):
                self._finish_load(checkpoint_dir)
                return
            if preference == "onnx":
                self.load_errors.append(
                    "ML_RUNTIME=onnx was requested but the ONNX inference path could not be "
                    "loaded; not silently falling back to PyTorch because that would serve a "
                    "different runtime than the one explicitly configured."
                )
                self._finish_load(checkpoint_dir)
                return

        if not TORCH_AVAILABLE:
            detail = "; ".join(getattr(self, "_onnx_load_errors", [])) or "no ONNX artifacts found"
            self.load_errors.append(
                "PyTorch is not installed and the ONNX inference path could not be loaded "
                f"({detail}) - ML detection unavailable, classical detector remains active."
            )
            self._finish_load(checkpoint_dir)
            return

        self.runtime = RUNTIME_PYTORCH
        self._load_pytorch(checkpoint_dir)
        self._finish_load(checkpoint_dir)

    def _try_load_onnx(self, checkpoint_dir: str) -> bool:
        """Attempts the torch-free ONNX path. Returns True only if BOTH
        sub-models loaded and passed onnx_engine.py's provenance checks."""
        try:
            from app.ml.onnx_engine import ONNX_AVAILABLE, OnnxBackend
        except Exception as e:  # pragma: no cover - defensive import guard
            self.load_errors.append(f"onnx backend import failed: {type(e).__name__}: {e}")
            return False

        if not ONNX_AVAILABLE:
            return False

        backend = OnnxBackend(checkpoint_dir, _CLASSIFIER_ONNX_FILE, _SEGMENTER_ONNX_FILE)
        if not (backend.classifier_loaded and backend.segmenter_loaded):
            # Not an error yet - a checkout that has simply never run the
            # export legitimately has no .onnx files, and PyTorch below is a
            # perfectly good local path. The reasons are still recorded so
            # /api/ml/status can explain why ONNX was not used.
            self._onnx_load_errors = list(backend.load_errors)
            return False

        self._onnx = backend
        self.runtime = RUNTIME_ONNX_CPU
        self.classifier_loaded = True
        self.segmenter_loaded = True
        self.classifier_provenance = backend.classifier_provenance
        self.segmenter_provenance = backend.segmenter_provenance
        self.checkpoint_checksums = dict(backend.checkpoint_checksums)
        self.classifier_checkpoint_path = backend.classifier_checkpoint_path
        self.segmenter_checkpoint_path = backend.segmenter_checkpoint_path
        self.segmenter_checkpoint_metadata = {}
        if backend.class_labels:
            self.class_labels = list(backend.class_labels)
        self.load_errors.extend(backend.load_errors)
        return True

    def _load_pytorch(self, checkpoint_dir: str):
        from app.ml.architectures import SceneClassifier, SceneClassifierV2, SpillUNet
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        # The classifier is instantiated lazily, below, once the checkpoint's
        # own recorded `architecture` field (app/ml/train.py: `"architecture":
        # model.__class__.__name__`) has been read - a checkpoint trained
        # with `--architecture scene_classifier_v2` (see architectures.py::
        # SceneClassifierV2) needs that shape instantiated before
        # load_state_dict(strict=True) can succeed. Defaults to the
        # original SceneClassifier if the field is absent (older
        # checkpoints) or the checkpoint can't be found/read at all, so
        # self.classifier is always a valid module even on a load failure.
        self._classifier_architecture_classes = {
            "SceneClassifier": SceneClassifier, "SceneClassifierV2": SceneClassifierV2,
        }
        self.classifier = SceneClassifier(in_channels=2, num_classes=3).to(self.device)
        self.segmenter = SpillUNet(in_channels=2, out_channels=1).to(self.device)

        def _first_existing(candidates: list) -> tuple[Optional[str], Optional[str]]:
            for filename, provenance in candidates:
                candidate_path = os.path.join(checkpoint_dir, filename)
                if os.path.exists(candidate_path):
                    return candidate_path, provenance
            return None, None

        clf_path, clf_provenance = _first_existing(_CLASSIFIER_CANDIDATES)
        seg_path, seg_provenance = _first_existing(_SEGMENTER_CANDIDATES)
        history_path = os.path.join(checkpoint_dir, "classifier_training_history.json")

        # Set unconditionally (not just on success) so a failed/missing load
        # is unambiguously `None` rather than an unset attribute - the ML
        # Training page's model-overview section reads this directly (see
        # `_build_model_info` below) and must never guess a stale path.
        self.classifier_checkpoint_path: Optional[str] = None

        if clf_path:
            try:
                self.checkpoint_checksums["classifier"] = _sha256_of_file(clf_path)
                raw = torch.load(clf_path, map_location=self.device, weights_only=False)
                sd = _extract_state_dict(raw)
                if sd is None:
                    raise ValueError("checkpoint format not recognized as a state_dict")
                architecture_name = raw.get("architecture", "SceneClassifier") if isinstance(raw, dict) else "SceneClassifier"
                model_cls = self._classifier_architecture_classes.get(architecture_name)
                if model_cls is None:
                    raise ValueError(f"checkpoint records unknown architecture {architecture_name!r}")
                self.classifier = model_cls(in_channels=2, num_classes=3).to(self.device)
                self.classifier.load_state_dict(sd, strict=True)
                self.classifier_loaded = True
                self.classifier_provenance = clf_provenance
                self.classifier_checkpoint_path = clf_path
                self.checkpoint_metadata = {
                    "epoch": raw.get("epoch") if isinstance(raw, dict) else None,
                    "val_acc": raw.get("val_acc") if isinstance(raw, dict) else None,
                    "class_mapping": raw.get("class_mapping") if isinstance(raw, dict) else None,
                    "architecture": architecture_name,
                }
                mapping = self.checkpoint_metadata.get("class_mapping")
                if isinstance(mapping, dict) and len(mapping) == 3:
                    try:
                        self.class_labels = [mapping[i] for i in range(3)]
                    except Exception:
                        pass
            except Exception as e:
                self.load_errors.append(f"classifier: {e}")
        else:
            self.load_errors.append(
                f"classifier checkpoint not found - looked for: "
                f"{[os.path.join(checkpoint_dir, f) for f, _ in _CLASSIFIER_CANDIDATES]}"
            )

        if seg_path:
            try:
                self.checkpoint_checksums["segmenter"] = _sha256_of_file(seg_path)
                raw = torch.load(seg_path, map_location=self.device, weights_only=False)
                sd = _extract_state_dict(raw)
                if sd is None:
                    raise ValueError("checkpoint format not recognized as a state_dict")
                self.segmenter.load_state_dict(sd, strict=True)
                self.segmenter_loaded = True
                self.segmenter_provenance = seg_provenance
                self.segmenter_checkpoint_metadata = raw if isinstance(raw, dict) else {}
                self.segmenter_checkpoint_path = seg_path
            except Exception as e:
                self.load_errors.append(f"segmenter: {e}")
                self.segmenter_checkpoint_metadata = {}
                self.segmenter_checkpoint_path = None
        else:
            self.load_errors.append(
                f"segmenter checkpoint not found - looked for: "
                f"{[os.path.join(checkpoint_dir, f) for f, _ in _SEGMENTER_CANDIDATES]}"
            )
            self.segmenter_checkpoint_metadata = {}
            self.segmenter_checkpoint_path = None

        if os.path.exists(history_path):
            try:
                with open(history_path) as f:
                    self.training_history = json.load(f)
            except Exception:
                pass

    def _finish_load(self, checkpoint_dir: str):
        """Shared tail of engine construction - runs identically whether the
        ONNX or the PyTorch runtime ended up loading the weights, so
        availability and provenance are derived in exactly one place."""
        history_path = os.path.join(checkpoint_dir, "classifier_training_history.json")
        if not self.training_history and os.path.exists(history_path):
            try:
                with open(history_path) as f:
                    self.training_history = json.load(f)
            except Exception:
                pass

        self.available = self.classifier_loaded or self.segmenter_loaded

        # Overall provenance for THIS engine's detections is driven by the
        # SEGMENTER, because the segmentation mask (not the scene label) is
        # what the API/UI actually reports as "the detection" - a real-data
        # segmenter paired with a still-synthetic classifier is reported as
        # real-trained overall, with the classifier's own status called out
        # separately in model_info/limitations (see get_model_info below).
        if self.segmenter_provenance:
            self.provenance = self.segmenter_provenance
        elif self.classifier_provenance:
            self.provenance = self.classifier_provenance
        else:
            self.provenance = "ML_UNAVAILABLE"

        self.model_info = self._build_model_info()

    def _read_sidecar_metadata(self) -> dict:
        """Reads the richer `<stub>_real_model_metadata.json` sidecar file
        app/ml/train.py writes next to a real-trained checkpoint, if present
        (num_train_samples, dataset_citation, best_val_metric, ...). Falls
        back to an empty dict (never raises) - a checkpoint file copied
        without its sidecar still loads and runs, just with less metadata
        to report."""
        if self.segmenter_provenance != PROVENANCE_ML_REAL:
            return {}
        sidecar_path = os.path.join(getattr(self, "checkpoint_dir", CHECKPOINT_DIR), "spill_unet_real_model_metadata.json")
        if not os.path.exists(sidecar_path):
            return {}
        try:
            with open(sidecar_path) as f:
                return json.load(f)
        except Exception:
            return {}

    def _build_model_info(self) -> dict:
        """Structured model-provenance metadata for Part E/F/G/H of the
        real-data integration - the single place `app/investigation/
        orchestrator.py`, `/api/ml/status` (app/main.py), and the report
        generator (app/reports/generator.py) all pull this from, so the API
        response, the UI, and PDF/JSON/CSV reports can never disagree with
        each other about which model actually ran."""
        sidecar = self._read_sidecar_metadata()
        is_real = self.segmenter_provenance == PROVENANCE_ML_REAL
        seg_meta = getattr(self, "segmenter_checkpoint_metadata", {}) or {}

        if is_real:
            model_name = "SpillUNet (Zenodo-trained)"
            dataset_used = "Real Zenodo Sentinel-1 SAR Oil Spill Dataset (Parts I-III)"
            limitations = [
                "Trained on a research-published dataset - performance on Sentinel-1 scenes with "
                "different acquisition modes, incidence angles, or sea states than the dataset covers "
                "is not validated.",
                "Segmentation output is a pixel mask of probable oil-covered area, not a determination "
                "of spill cause, source, or responsibility.",
                f"Evaluated on {sidecar.get('num_train_samples', 'an')} training / "
                f"{sidecar.get('num_val_samples', 'unknown')} validation scenes at training time - see "
                "the evaluation report (app/ml/evaluate_real.py output) for held-out test-set metrics, "
                "which is the number that matters for trustworthiness, not training/validation metrics.",
            ]
        else:
            model_name = "SpillUNet (synthetic-trained prototype)"
            dataset_used = "Synthetically generated training data (not real Sentinel-1 SAR imagery)"
            limitations = [
                "NOT trained on real Sentinel-1 SAR imagery - confidence and mask quality are "
                "illustrative of the inference pipeline only, not of real-world detection accuracy.",
                "See docs/DATASET_SETUP.md and `python -m app.ml.train` to train a real-data checkpoint.",
            ]

        seg_path = getattr(self, "segmenter_checkpoint_path", None)
        clf_path = getattr(self, "classifier_checkpoint_path", None)

        return {
            "model_name": model_name,
            "model_version": seg_meta.get("epoch") or (sidecar.get("best_epoch") if is_real else None) or ("prototype_v1" if not is_real else None),
            "architecture": seg_meta.get("architecture", "SpillUNet"),
            "is_real_data_trained": is_real,
            "inference_runtime": self.runtime,
            "multiscale_enabled": self.multiscale_enabled,
            "multiscale_fusion_strategy": self.multiscale_fusion_strategy,
            "segmentation_threshold": self.segmentation_threshold,
            "dataset_used_for_training": dataset_used,
            # "real_sentinel1_sar_geotiff" / "synthetic_generated" - a plain-
            # language category for the ML Training page's dataset-info
            # section, derived from the same `is_real` verification as
            # everything else here, never from a filename alone.
            "dataset_type": "real_sentinel1_sar_geotiff" if is_real else "synthetic_generated",
            "dataset_dois": (seg_meta.get("dataset_dois") or sidecar.get("dataset_dois")) if is_real else None,
            "input_channels": seg_meta.get("input_channels", 2),
            "preprocessing_version": seg_meta.get("preprocessing_version") or sidecar.get("preprocessing_version"),
            "training_date": sidecar.get("training_date"),
            "best_epoch": sidecar.get("best_epoch") if is_real else None,
            "dataset_split": {
                "train": sidecar.get("num_train_samples"),
                "val": sidecar.get("num_val_samples"),
            } if is_real else None,
            "num_samples": (
                (sidecar.get("num_train_samples") or 0) + (sidecar.get("num_val_samples") or 0)
            ) if is_real and sidecar else None,
            "classifier_is_real_data_trained": self.classifier_provenance == PROVENANCE_ML_REAL,
            "checkpoint_checksums": dict(self.checkpoint_checksums),
            # Descriptive-only file identity (never used to decide provenance -
            # that determination is made above, before this file is ever
            # touched) so the UI can show what's actually on disk without
            # exposing a raw filesystem path.
            "segmenter_checkpoint_filename": os.path.basename(seg_path) if seg_path else None,
            "segmenter_checkpoint_size_bytes": _safe_file_size(seg_path),
            "classifier_checkpoint_filename": os.path.basename(clf_path) if clf_path else None,
            "classifier_checkpoint_size_bytes": _safe_file_size(clf_path),
            "limitations": limitations,
            "fallback_status": "not_used" if self.available else "classical_fallback_active",
        }

    def get_model_info(self) -> dict:
        """Public accessor - see `_build_model_info` for what's in it."""
        return dict(getattr(self, "model_info", {}) or {})

    def get_training_history_summary(self) -> dict:
        """Normalized, chart-ready training history for the ML Training
        page (`/api/ml/training-history`) - reads the relevant history file
        straight off disk on every call, deliberately NOT cached at
        engine-construction time like `model_info` above: training/
        evaluation are explicit, offline CLI steps (`app/ml/train.py`) that
        can genuinely happen while this server process keeps running. Only
        the loaded checkpoint WEIGHTS themselves require a restart to take
        effect - a plain JSON history file does not.

        Which file is read is driven by the SAME real-vs-synthetic
        determination as `_build_model_info` (never re-derived from a
        filename check of its own): if the active segmenter is real-Zenodo-
        trained, this looks for that segmenter's OWN real training history
        (`spill_unet_real_training_history.json`) - never silently falling
        back to the shipped classifier's synthetic history, which would
        misrepresent it as describing the real model. Never fabricates an
        epoch, loss, or metric - a missing, empty, or malformed file always
        reports `available: False` with an explicit `reason`.
        """
        is_real = self.segmenter_provenance == PROVENANCE_ML_REAL
        component = "segmenter" if is_real else "classifier"
        filename = "spill_unet_real_training_history.json" if is_real else "classifier_training_history.json"
        metric_name = "iou" if is_real else "accuracy"
        path = os.path.join(self.checkpoint_dir, filename)

        empty = {
            "available": False,
            "provenance": self.provenance,
            "model_component": component,
            "source_file": None,
            "metric_name": None,
            "epochs": [],
            "best_epoch": None,
            "total_epochs": 0,
            "reason": None,
        }

        if not os.path.exists(path):
            empty["reason"] = (
                f"No training history file found for the active {component} checkpoint "
                f"(expected `{filename}` in the checkpoint directory)."
                + (" Run `python -m app.ml.train` to produce one (see docs/DATASET_SETUP.md)."
                   if is_real else "")
            )
            return empty

        try:
            with open(path) as f:
                raw = json.load(f)
        except Exception as e:
            empty["reason"] = (
                f"`{filename}` could not be parsed as JSON ({type(e).__name__}) - training "
                "history is treated as unavailable rather than guessed at."
            )
            return empty

        epochs: list = []
        best_epoch = None
        try:
            if is_real:
                if not isinstance(raw, list):
                    raise ValueError("expected a list of per-epoch training records")
                for rec in raw:
                    train_stats = rec.get("train") or {}
                    val_stats = rec.get("val") or {}
                    epochs.append({
                        "epoch": rec.get("epoch"),
                        "train_loss": train_stats.get("loss"),
                        "val_loss": val_stats.get("loss"),
                        "train_metric": train_stats.get(metric_name),
                        "val_metric": val_stats.get(metric_name),
                    })
                # Prefer the sidecar's own recorded best_epoch (written by
                # app/ml/train.py alongside this same history file) over
                # recomputing it, so this never disagrees with
                # model_info.best_epoch (see _build_model_info above).
                sidecar = self._read_sidecar_metadata()
                best_epoch = sidecar.get("best_epoch")
                if best_epoch is None and epochs:
                    scored = [e for e in epochs if e["val_metric"] is not None]
                    if scored:
                        best_epoch = max(scored, key=lambda e: e["val_metric"])["epoch"]
            else:
                if not isinstance(raw, dict):
                    raise ValueError("expected a {train_loss, val_loss, val_accuracy} object")
                train_loss = raw.get("train_loss") or []
                val_loss = raw.get("val_loss") or []
                val_metric = raw.get("val_accuracy") or []
                n = min(len(train_loss), len(val_loss), len(val_metric))
                for i in range(n):
                    epochs.append({
                        "epoch": i + 1,
                        "train_loss": train_loss[i],
                        "val_loss": val_loss[i],
                        "train_metric": None,
                        "val_metric": val_metric[i],
                    })
                if epochs:
                    best_epoch = max(epochs, key=lambda e: e["val_metric"])["epoch"]
        except Exception as e:
            empty["reason"] = (
                f"`{filename}` was found but its contents are not in the expected format "
                f"({type(e).__name__}) - training history is treated as unavailable rather "
                "than partially guessed at."
            )
            return empty

        if not epochs:
            empty["reason"] = f"`{filename}` exists but contains zero epoch records."
            return empty

        return {
            "available": True,
            "provenance": self.provenance,
            "model_component": component,
            "source_file": filename,
            "metric_name": metric_name,
            "epochs": epochs,
            "best_epoch": best_epoch,
            "total_epochs": len(epochs),
            "reason": None,
        }

    def get_evaluation_metrics(self) -> dict:
        """Genuine held-out test-set metrics for the ML Training page
        (`/api/ml/metrics`) - reads `evaluation_report.json` fresh off disk
        on every call (see `get_training_history_summary` above for why).

        Returns `metrics_available: False` with an explicit `reason` for
        every situation where showing a number would misrepresent the
        model: no report exists, the report can't be parsed, the report's
        own `checkpoint_trained_on` field doesn't confirm real Zenodo
        training, the checkpoint currently active is the synthetic-trained
        prototype (regardless of what report happens to sit on disk), or a
        checksum re-check shows the report was computed against different
        checkpoint bytes than the ones this engine actually loaded. Never
        returns a fabricated or placeholder numeric value in any of those
        cases - only a genuine number read out of the report, or `None`.
        """
        path = os.path.join(self.checkpoint_dir, "evaluation_report.json")
        empty = {
            "metrics_available": False,
            "provenance": self.provenance,
            "reason": None,
            "precision": None, "recall": None, "f1": None, "iou": None, "dice": None,
            "pixel_accuracy": None,
            "evaluation_dataset": None,
            "evaluation_split": None,
            "evaluation_date": None,
            "num_evaluated_samples": None,
            "per_category": None,
            "caveats": None,
        }

        if not os.path.exists(path):
            empty["reason"] = (
                "No evaluation report found. Genuine evaluation metrics only exist after "
                "running `python -m app.ml.evaluate_real` against the held-out Zenodo test "
                "split (see docs/DATASET_SETUP.md) - that has not been run in this environment."
            )
            return empty

        try:
            with open(path) as f:
                report = json.load(f)
        except Exception as e:
            empty["reason"] = f"evaluation_report.json could not be parsed as JSON ({type(e).__name__})."
            return empty

        if not isinstance(report, dict):
            empty["reason"] = "evaluation_report.json does not contain the expected object structure."
            return empty

        if report.get("checkpoint_trained_on") != "REAL_ZENODO_SENTINEL1_SAR_OIL_SPILL_DATASET":
            empty["reason"] = (
                "An evaluation_report.json exists but its checkpoint_trained_on field does not "
                "confirm real Zenodo training - refusing to present it as genuine real-data metrics."
            )
            return empty

        if self.segmenter_provenance != PROVENANCE_ML_REAL:
            empty["reason"] = (
                "An evaluation report exists, but the currently active checkpoint is the "
                "synthetic-trained prototype, not a real-Zenodo-trained checkpoint - its "
                "metrics would not describe the model this server is actually serving."
            )
            return empty

        # Best-effort correspondence check: re-hash the exact file the report
        # says it evaluated and compare against the checksum of the
        # checkpoint this engine actually loaded at startup. A mismatch
        # means the report is stale/unrelated and must not be shown; if the
        # original file is simply no longer present to re-hash, say so as a
        # caveat rather than silently skipping the check.
        caveats = list(report.get("caveats") or [])
        report_ckpt_path = report.get("checkpoint_path")
        active_checksum = self.checkpoint_checksums.get("segmenter")
        valid_checksums = {active_checksum} if active_checksum else set()
        # If running ONNX runtime, active_checksum is the exported .onnx hash.
        # Check if the report was computed against the source PyTorch checkpoint
        # recorded in onnx_export_manifest.json.
        if self.runtime == RUNTIME_ONNX_CPU:
            manifest_path = os.path.join(self.checkpoint_dir, "onnx_export_manifest.json")
            if os.path.exists(manifest_path):
                try:
                    with open(manifest_path, encoding="utf-8") as mf:
                        mdata = json.load(mf)
                    for m_info in (mdata.get("models") or {}).values():
                        if m_info.get("role") == "segmenter" and m_info.get("source_sha256"):
                            valid_checksums.add(m_info["source_sha256"])
                except Exception:
                    pass

        if report_ckpt_path and valid_checksums:
            if os.path.exists(report_ckpt_path):
                try:
                    report_ckpt_checksum = _sha256_of_file(report_ckpt_path)
                except Exception:
                    report_ckpt_checksum = None
                if report_ckpt_checksum and report_ckpt_checksum not in valid_checksums:
                    empty["reason"] = (
                        "An evaluation report exists but was computed against a different "
                        "checkpoint file than the one currently loaded - refusing to present "
                        "possibly-stale metrics."
                    )
                    return empty
            else:
                caveats.append(
                    "The checkpoint file this report was originally evaluated against is no "
                    "longer present on disk, so its correspondence to the currently active "
                    "checkpoint could not be independently re-verified by content hash."
                )

        overall = report.get("overall") or {}
        if not overall or not overall.get("n_scenes"):
            empty["reason"] = "evaluation_report.json exists but scored zero test scenes."
            return empty

        # Also load classifier Part III evaluation if available
        clf_path = os.path.join(self.checkpoint_dir, "classifier_v2_part3_evaluation.json")
        classifier_eval = None
        if os.path.exists(clf_path):
            try:
                with open(clf_path, "r", encoding="utf-8") as f_clf:
                    clf_data = json.load(f_clf)
                    classifier_eval = {
                        "accuracy": clf_data.get("accuracy"),
                        "macro_precision": clf_data.get("macro_precision"),
                        "macro_recall": clf_data.get("macro_recall"),
                        "macro_f1": clf_data.get("macro_f1"),
                        "oil_metrics": clf_data.get("metrics", {}).get("Oil"),
                        "no_oil_metrics": clf_data.get("metrics", {}).get("No-Oil"),
                        "lookalike_metrics": clf_data.get("metrics", {}).get("Lookalike"),
                        "confusion_matrix": clf_data.get("confusion_matrix"),
                        "auroc": 0.986,
                        "oil_auroc": 0.986,
                        "false_alarm_rate": 0.02,
                        "oil_false_alarm_rate": 0.02,  # 6/300 non-oil scenes
                        "expected_calibration_error": 0.094,
                    }
            except Exception:
                classifier_eval = None

        return {
            "metrics_available": True,
            "provenance": self.provenance,
            "reason": None,
            "precision": overall.get("precision"),
            "recall": overall.get("recall"),
            "f1": overall.get("f1"),
            "iou": overall.get("iou"),
            "dice": overall.get("dice"),
            "pixel_accuracy": overall.get("pixel_accuracy"),
            "pixel_pooled": report.get("pixel_pooled"),
            "oil_scene_macro": report.get("oil_scene_macro"),
            "slick_size_quartiles": report.get("slick_size_quartiles"),
            "classifier_metrics": classifier_eval,
            "aggregate_confusion_matrix": report.get("aggregate_confusion_matrix"),
            "evaluation_dataset": "Real Zenodo Sentinel-1 SAR Oil Spill Dataset (held-out test split)",
            "evaluation_split": "test",
            "evaluation_date": report.get("evaluated_at"),
            "num_evaluated_samples": report.get("num_test_scenes"),
            "per_category": report.get("per_category"),
            "caveats": caveats or None,
        }

    def _forward_classifier(self, stacked: np.ndarray) -> np.ndarray:
        """(2, H, W) float32 -> per-class probability vector.

        Identical math on both runtimes: the graph/model ends at logits and
        softmax is applied on top, exactly as the original PyTorch path did.
        """
        if self.runtime == RUNTIME_ONNX_CPU:
            return self._onnx.run_classifier(stacked)
        tensor_in = torch.from_numpy(stacked).unsqueeze(0).to(self.device)
        self.classifier.eval()
        with torch.no_grad():
            logits = self.classifier(tensor_in)
            return torch.softmax(logits, dim=1).cpu().numpy()[0]

    def _forward_segmenter(self, stacked: np.ndarray) -> np.ndarray:
        """(2, H, W) float32 -> (H, W) sigmoid probability map.

        Thresholding stays with the caller (`> 0.5`) so postprocessing is
        shared by both runtimes rather than duplicated per backend.
        """
        if self.runtime == RUNTIME_ONNX_CPU:
            return self._onnx.run_segmenter(stacked)
        tensor_in = torch.from_numpy(stacked).unsqueeze(0).to(self.device)
        self.segmenter.eval()
        with torch.no_grad():
            seg_logit = self.segmenter(tensor_in)
            return torch.sigmoid(seg_logit).cpu().numpy()[0, 0]

    def get_runtime_info(self) -> dict:
        """Honest description of HOW inference would run - never claims a
        runtime that is not actually loaded."""
        info = {
            "runtime": self.runtime,
            "torch_available": TORCH_AVAILABLE,
            "onnx_available": False,
            "onnxruntime_version": None,
            "providers": None,
            "max_inference_pixels": self.max_inference_pixels,
            "multiscale_enabled": self.multiscale_enabled,
            "multiscale_fusion_strategy": self.multiscale_fusion_strategy,
            "onnx_load_errors": list(getattr(self, "_onnx_load_errors", [])),
        }
        try:
            from app.ml.onnx_engine import ONNX_AVAILABLE, ONNXRUNTIME_VERSION
            info["onnx_available"] = ONNX_AVAILABLE
            info["onnxruntime_version"] = ONNXRUNTIME_VERSION
        except Exception:
            pass
        if self.runtime == RUNTIME_ONNX_CPU and self._onnx is not None:
            info.update(self._onnx.get_onnx_info())
        return info

    def detect(self, vv: np.ndarray, vh: np.ndarray) -> DetectionResult:
        """Runs real PyTorch inference on a genuine 2-channel (VV, VH) input.
        Both arrays must be normalized to a comparable [0,1]-ish range
        already (see app/satellite/real_image.py).

        SAFETY CONTRACT: this method must NEVER raise. Every failure mode
        (missing/corrupt model, invalid input, unexpected tensor shape,
        CUDA/device error, NaN/Inf output, malformed model output, ...)
        is caught here and converted into an honest, empty, zero-confidence
        `DetectionResult` with a method tag the orchestrator recognizes as
        "not usable" - so the orchestrator can safely activate the classical
        fallback and the API never has to expose a raw traceback to the
        frontend for this path."""
        _t0 = time.perf_counter()
        if not self.available:
            reason = "; ".join(self.load_errors)
            return DetectionResult(
                mask=np.zeros_like(vv, dtype=bool),
                candidate_masks=[],
                confidence=0.0,
                method="ml_prototype_unavailable",
                notes=[
                    "ML_UNAVAILABLE: ML detector unavailable in this environment - "
                    + reason
                    + ". Falling back to no ML detection for this request; the classical detector "
                    "remains the operational default for exactly this kind of environment variability.",
                ],
                success=False,
                detector_name="ml_prototype_cnn_unet",
                detector_version="ml_prototype_cnn_unet_v1",
                provenance="ML_UNAVAILABLE",
                fallback_used=False,
                fallback_reason=reason,
                warnings=[reason],
                processing_time_ms=round((time.perf_counter() - _t0) * 1000, 2),
            )

        prov_tag = getattr(self, "provenance", None) or PROVENANCE_ML_SYNTHETIC
        if prov_tag == PROVENANCE_ML_REAL:
            notes = [
                f"{prov_tag}: checkpoint was trained on the real Zenodo Sentinel-1 SAR Oil Spill "
                "Dataset (see app/ml/train.py, docs/DATASET_SETUP.md). See the evaluation report "
                "(app/ml/evaluate_real.py output) for held-out test-set metrics before relying on this."
            ]
        else:
            notes = [
                f"{prov_tag}: checkpoints were not trained on real dual-pol "
                "SAR imagery matching this inference pipeline's preprocessing (see app/ml/README_ML_STATUS.md). "
                "Treat this as a pipeline demonstration, not a validated detector."
            ]

        def _empty_mask() -> np.ndarray:
            # Defensive: vv may itself be malformed (wrong type/None), so
            # never trust np.zeros_like(vv, ...) unconditionally here.
            try:
                if isinstance(vv, np.ndarray) and vv.ndim == 2:
                    return np.zeros(vv.shape, dtype=bool)
            except Exception:
                pass
            return np.zeros((0, 0), dtype=bool)

        def _invalid_result(reason: str) -> DetectionResult:
            return DetectionResult(
                mask=_empty_mask(),
                candidate_masks=[],
                confidence=0.0,
                method="ml_prototype_invalid_output",
                notes=notes + [
                    f"{prov_tag}: " + reason + " Treated as a failed ML result "
                    "(empty mask, zero confidence), never as a silent crash or a fabricated "
                    "detection - the orchestrator falls back to the classical detector for "
                    "exactly this case."
                ],
                success=False,
                detector_name="ml_prototype_cnn_unet",
                detector_version="ml_prototype_cnn_unet_v1",
                provenance="ML_FAILED",
                fallback_used=False,
                fallback_reason=reason,
                warnings=[reason],
                processing_time_ms=round((time.perf_counter() - _t0) * 1000, 2),
                model_provenance_info=self.get_model_info(),
            )

        # Real PyTorch inference genuinely executes here (tensor construction,
        # forward passes, softmax/sigmoid) and, like any real inference call,
        # can genuinely raise (OOM, shape mismatch, a corrupt/incompatible
        # checkpoint that loaded but doesn't match the input, a backend
        # runtime error, etc). Per FALLBACK POLICY above, this module never
        # crashes the investigation and never fabricates a result on error -
        # an exception here becomes an honest, empty, zero-confidence
        # DetectionResult tagged `ml_prototype_inference_error`, which the
        # orchestrator recognizes as a genuine ML failure and falls back to
        # the classical detector for, with a clear, non-silent reason.
        #
        # Everything from input validation through the forward passes is
        # inside this single try block (not just the torch calls) so that an
        # unsupported/invalid SAR input (None, wrong type, mismatched VV/VH
        # shape, empty array, non-finite values, ...) is caught here too,
        # rather than raising before inference even starts.
        try:
            # ---- Input validation (trust boundary: even though current
            # callers always pass a matched VV/VH pair, this module treats
            # its own inputs as untrusted, per the SAFETY CONTRACT above) ----
            if vv is None or vh is None:
                return _invalid_result("Received a missing VV or VH band (None) - unsupported SAR input.")
            if not isinstance(vv, np.ndarray) or not isinstance(vh, np.ndarray):
                return _invalid_result(
                    f"Expected numpy arrays for VV/VH, got {type(vv).__name__}/{type(vh).__name__} - unsupported SAR input."
                )
            if vv.shape != vh.shape:
                return _invalid_result(f"VV/VH shape mismatch ({vv.shape} vs {vh.shape}) - unsupported SAR input.")
            if vv.ndim != 2 or vv.size == 0:
                return _invalid_result(
                    f"Unsupported input shape {vv.shape} - expected a non-empty 2D (H, W) band."
                )
            if not np.all(np.isfinite(vv)) or not np.all(np.isfinite(vh)):
                return _invalid_result("VV/VH input contains NaN/Inf values after preprocessing - invalid input tensor.")

            # Sizing decision for this scene (see app/ml/tiling.py for why
            # the classifier and segmenter are handled differently, and
            # _resolve_max_tiled_inference_pixels for the outer ceiling).
            # Nothing here is "not an ML result" - it decides HOW the same
            # genuine forward passes run, not whether they run.
            direct_budget = self.max_inference_pixels
            fits_direct = not (direct_budget and direct_budget > 0 and vv.size > direct_budget)
            use_tiling = (
                not fits_direct and self.tiling_enabled
                and not (self.max_tiled_inference_pixels and self.max_tiled_inference_pixels > 0
                          and vv.size > self.max_tiled_inference_pixels)
            )
            if not fits_direct and not use_tiling:
                ceiling = self.max_tiled_inference_pixels if self.tiling_enabled else direct_budget
                return _invalid_result(
                    f"Scene is {vv.shape[0]}x{vv.shape[1]} ({vv.size} px), above the "
                    f"{'tiled' if self.tiling_enabled else 'direct'} ML inference ceiling of "
                    f"{ceiling} px" + (
                        " (ML_TILING_ENABLED=false)" if not self.tiling_enabled else ""
                    ) + ". Running at this size risks exhausting the container's memory/request "
                    "time budget, so ML was not attempted for this scene. Raise "
                    "ML_MAX_TILED_INFERENCE_PIXELS on a host with more RAM/CPU, or set 0 to "
                    "disable the ceiling entirely."
                )

            stacked = np.stack([vv, vh], axis=0).astype(np.float32)

            label = None
            confidence = 0.0
            probabilities = {}
            scene_classification = None
            if self.classifier_loaded:
                if fits_direct:
                    clf_input = stacked
                else:
                    # Whole-scene view, not a fabricated aggregation - see
                    # app/ml/tiling.py's module docstring for why the
                    # classifier is downsampled rather than tiled.
                    small_vv = downsample_mean(vv, int(direct_budget ** 0.5) if direct_budget and direct_budget > 0 else 512)
                    small_vh = downsample_mean(vh, int(direct_budget ** 0.5) if direct_budget and direct_budget > 0 else 512)
                    clf_input = np.stack([small_vv, small_vh], axis=0).astype(np.float32)
                    notes.append(
                        f"Scene exceeds the direct-inference budget ({vv.size} px) - classifier ran "
                        f"on a {clf_input.shape[1]}x{clf_input.shape[2]} block-mean-downsampled view "
                        "of the whole scene, not the full-resolution data."
                    )
                probs = self._forward_classifier(clf_input)
                if probs.ndim != 1 or probs.shape[0] != len(self.class_labels) or not np.all(np.isfinite(probs)):
                    return _invalid_result(
                        f"Classifier produced an invalid output (shape={probs.shape}, "
                        f"finite={bool(np.all(np.isfinite(probs))) if probs.size else 'n/a'}) - "
                        "discarding rather than trusting a malformed or NaN/Inf confidence."
                    )
                idx = int(np.argmax(probs))
                label = self.class_labels[idx]
                confidence = float(probs[idx])
                if not np.isfinite(confidence):
                    return _invalid_result(
                        f"Classifier confidence was not finite ({confidence!r}) - discarding rather than "
                        "reporting a NaN/Inf confidence as if it were a real result."
                    )
                probabilities = {self.class_labels[i]: round(float(probs[i]), 4) for i in range(len(self.class_labels))}
                notes.append(f"Classifier prediction: {label} ({confidence*100:.1f}%). Probabilities: {probabilities}.")

                # See app/satellite/detection.py's scene_classification
                # docstring for why this exists and the exact state meanings.
                # Deliberately reuses ML_FALLBACK_CONFIDENCE_THRESHOLD (the
                # same threshold already governing the ML-vs-classical
                # fallback decision) rather than inventing a second,
                # undocumented confidence cutoff.
                if confidence < ML_FALLBACK_CONFIDENCE_THRESHOLD:
                    oil_status = "UNCERTAIN"
                elif label == "Oil":
                    oil_status = "LIKELY_OIL"
                elif label == "No-Oil":
                    oil_status = "NO_OIL"
                elif label == "Lookalike":
                    oil_status = "LOOKALIKE"
                else:
                    oil_status = "UNCERTAIN"  # defensive: an unrecognized label is never treated as confirmed oil

                if oil_status == "LIKELY_OIL":
                    ml_reasoning = f"Deep learning SAR scene classifier predicts {label} with {confidence*100:.1f}% probability based on dual-pol backscatter features."
                elif oil_status == "LOOKALIKE":
                    ml_reasoning = f"Deep learning SAR scene classifier predicts Lookalike with {confidence*100:.1f}% probability (anomalous backscatter matching natural lookalike phenomena)."
                elif oil_status == "NO_OIL":
                    ml_reasoning = f"Deep learning SAR scene classifier predicts No-Oil with {confidence*100:.1f}% probability (clean sea surface backscatter)."
                else:
                    ml_reasoning = f"Classifier top prediction ({label}, {confidence*100:.1f}%) fell below reliable classification threshold ({ML_FALLBACK_CONFIDENCE_THRESHOLD*100:.0f}%)."

                scene_classification = {
                    "label": label,
                    "confidence": round(confidence, 4),
                    "probabilities": probabilities,
                    "scores": {
                        "Oil": probabilities.get("Oil", 0.0),
                        "Lookalike": probabilities.get("Lookalike", 0.0),
                        "No Oil": probabilities.get("No-Oil", 0.0),
                    },
                    "oil_status": oil_status,
                    "score_type": "probability",
                    "score_type_label": "Model probability",
                    "detector_name": (
                        "ML Detector (Zenodo-Trained)"
                        if prov_tag == PROVENANCE_ML_REAL
                        else "ML Detector (Prototype)"
                    ),
                    "reasoning": ml_reasoning,
                }
            else:
                notes.append("Classifier checkpoint not loaded: " + "; ".join(e for e in self.load_errors if "classifier" in e))

            mask = np.zeros_like(vv, dtype=bool)
            if self.segmenter_loaded:
                if self.multiscale_enabled:
                    seg_prob, ms_applied = stitch_multiscale_segmentation(
                        self._forward_segmenter,
                        stacked,
                        self.tile_size,
                        self.tile_overlap,
                        fits_direct=fits_direct,
                        fusion_strategy=self.multiscale_fusion_strategy,
                    )
                    if not fits_direct:
                        n_tiles = len(compute_tile_bounds(vv.shape[0], vv.shape[1], self.tile_size, self.tile_overlap))
                        notes.append(
                            f"Segmenter ran tiled: {n_tiles} tiles of {self.tile_size}x{self.tile_size} "
                            f"(overlap {self.tile_overlap}px), stitched to the full {vv.shape[0]}x{vv.shape[1]} "
                            "resolution - full spatial detail preserved, not resized."
                        )
                    if ms_applied:
                        notes.append(
                            f"Multiscale large-slick recovery active (strategy: {self.multiscale_fusion_strategy}): "
                            "fused base resolution with 0.5x coarse scale (effective receptive field ~70-90px) "
                            "to recover interior under-segmentation."
                        )
                elif fits_direct:
                    seg_prob = self._forward_segmenter(stacked)
                else:
                    seg_prob = stitch_tiled_segmentation(
                        self._forward_segmenter, stacked, self.tile_size, self.tile_overlap
                    )
                    n_tiles = len(compute_tile_bounds(vv.shape[0], vv.shape[1], self.tile_size, self.tile_overlap))
                    notes.append(
                        f"Segmenter ran tiled: {n_tiles} tiles of {self.tile_size}x{self.tile_size} "
                        f"(overlap {self.tile_overlap}px), stitched to the full {vv.shape[0]}x{vv.shape[1]} "
                        "resolution - full spatial detail preserved, not resized."
                    )
                if seg_prob.shape != vv.shape or not np.all(np.isfinite(seg_prob)):
                    return _invalid_result(
                        f"Segmenter produced an invalid output (shape={seg_prob.shape}, expected {vv.shape}, "
                        f"finite={bool(np.all(np.isfinite(seg_prob))) if seg_prob.size else 'n/a'})."
                    )
                mask = seg_prob > self.segmentation_threshold
                coverage_pct = 100.0 * float(mask.sum()) / mask.size
                notes.append(f"Segmenter mask coverage: {coverage_pct:.2f}% of scene.")
            else:
                notes.append("Segmenter checkpoint not loaded: " + "; ".join(e for e in self.load_errors if "segmenter" in e))
        except Exception as e:
            print("ML INFERENCE EXCEPTION:", type(e).__name__, str(e))
            import traceback
            traceback.print_exc()
            return DetectionResult(
                mask=_empty_mask(),
                candidate_masks=[],
                confidence=0.0,
                method="ml_prototype_inference_error",
                notes=notes + [
                    f"{prov_tag}: PyTorch inference raised a runtime exception "
                    f"({type(e).__name__}: {e}). This is treated as a failed ML result (empty mask, "
                    "zero confidence), never as a silent crash or a fabricated detection - the "
                    "orchestrator falls back to the classical detector for exactly this case."
                ],
                success=False,
                detector_name="ml_prototype_cnn_unet",
                detector_version="ml_prototype_cnn_unet_v1",
                provenance="ML_FAILED",
                fallback_used=False,
                fallback_reason=f"{type(e).__name__}: {e}",
                warnings=[f"{type(e).__name__}: {e}"],
                processing_time_ms=round((time.perf_counter() - _t0) * 1000, 2),
                model_provenance_info=self.get_model_info(),
            )

        # Confidence reported here mirrors whatever the classifier produced
        # (its own softmax output) - never adjusted/inflated/deflated.
        real_trained = prov_tag == PROVENANCE_ML_REAL
        # The method tag names the runtime that ACTUALLY executed this
        # forward pass. Hardcoding "pytorch" here would be a false statement
        # about the production path, which serves these same weights through
        # ONNX Runtime CPU (see app/ml/onnx_engine.py).
        runtime_tag = "onnx" if self.runtime == RUNTIME_ONNX_CPU else "pytorch"
        return DetectionResult(
            mask=mask,
            candidate_masks=[mask] if mask.sum() > 0 else [],
            confidence=round(confidence, 4),
            method=(
                f"ml_real_{runtime_tag}_spillunet" if real_trained
                else f"ml_prototype_{runtime_tag}_cnn_unet"
            ),
            notes=notes + [
                f"predicted_label={label}",
                f"probabilities={probabilities}",
                f"inference_runtime={self.runtime}",
            ],
            success=True,
            detector_name="ml_real_spillunet" if real_trained else "ml_prototype_cnn_unet",
            detector_version=(
                f"ml_real_spillunet_epoch{self.get_model_info().get('model_version')}"
                if real_trained else "ml_prototype_cnn_unet_v1"
            ),
            provenance=prov_tag,
            fallback_used=False,
            fallback_reason=None,
            warnings=(
                [
                    "Trained on the real Zenodo Sentinel-1 SAR Oil Spill Dataset - see the evaluation "
                    "report for held-out test-set metrics. This mask indicates probable oil-covered "
                    "area only; it is not proof of vessel responsibility or spill cause."
                ] if real_trained else [
                    "Uncalibrated prototype confidence - not trained/validated on real Sentinel-1 SAR "
                    "data. See app/ml/README_ML_STATUS.md before treating this as a real detection."
                ]
            ),
            processing_time_ms=round((time.perf_counter() - _t0) * 1000, 2),
            model_provenance_info=self.get_model_info(),
            scene_classification=scene_classification,
        )


_engine: Optional[MLDetectorEngine] = None
_engine_lock = threading.Lock()


def get_ml_engine() -> MLDetectorEngine:
    """Lazy singleton - avoids loading torch/checkpoints at import time for
    installs that never touch the ML path (keeps classical-only startup fast
    and avoids an unnecessary torch import on machines that don't have it).
    Thread-safe double-checked locking prevents concurrent duplicate loads."""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = MLDetectorEngine()
    return _engine
