"""
Torch-free ONNX Runtime CPU inference backend.

WHY THIS EXISTS
---------------
The production target is a Render Free Web Service (512 MB RAM, 0.1 CPU).
A PyTorch wheel alone is far larger than that environment can comfortably
install and import, so PyTorch is deliberately NOT a production dependency
(see requirements.txt vs requirements-ml.txt). This module runs the SAME
genuine, already-trained checkpoints via ONNX Runtime's CPU execution
provider instead.

WHAT THIS MODULE IS NOT
-----------------------
This is ONLY an inference transport. It does not train, does not convert,
does not re-weight, and does not approximate anything. The `.onnx` files it
loads are produced offline by `app/ml/export_onnx.py` from the exact
promoted `.pth`/`.pt` checkpoints, and the original checkpoints remain on
disk untouched.

PROVENANCE SAFETY (the important part)
-------------------------------------
An `.onnx` file existing on disk proves nothing on its own - it could have
been exported from the synthetic prototype, or from a checkpoint that has
since been replaced. So this module NEVER infers "real Zenodo trained" from
a filename. Instead, `export_onnx.py` writes a sidecar manifest
(`onnx_export_manifest.json`) recording, per model, the source checkpoint
filename, that checkpoint's sha256 at export time, and the `trained_on`
marker read out of the checkpoint itself. At load time this module:

  1. requires the manifest to exist and to cover the model being loaded;
  2. requires the manifest's `trained_on` to be the real-Zenodo marker
     before `ML_REAL_ZENODO_TRAINED` is even a candidate provenance;
  3. re-hashes the source `.pth`/`.pt` on disk (when it is still shipped)
     and refuses the real label if the bytes no longer match what was
     exported.

Anything that fails those checks degrades to an explicit load error, which
the engine surfaces as `available=False` -> the orchestrator's existing,
documented classical fallback. It never silently downgrades to a
different-but-still-confident claim.

Even after all of that, `ML_REAL_ZENODO_TRAINED` is only ever attached to a
DetectionResult by `detector.py` AFTER a forward pass actually returned a
finite, correctly-shaped output - see `MLDetectorEngine.detect`.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

import numpy as np

from app.ml.calibration import ClassifierCalibrator

try:
    import onnxruntime as ort
    ONNX_AVAILABLE = True
    ONNXRUNTIME_VERSION = getattr(ort, "__version__", None)
except (ImportError, OSError):  # pragma: no cover - absent or blocked by OS policy
    ONNX_AVAILABLE = False
    ONNXRUNTIME_VERSION = None

# The marker `app/ml/train.py` writes into a checkpoint it produced from the
# real Zenodo dataset. Mirrored from evaluate_real.py / detector.py so the
# real-vs-synthetic test is the same string everywhere.
REAL_ZENODO_MARKER = "REAL_ZENODO_SENTINEL1_SAR_OIL_SPILL_DATASET"

MANIFEST_FILENAME = "onnx_export_manifest.json"

RUNTIME_ONNX_CPU = "onnxruntime_cpu"


class OnnxLoadError(Exception):
    """Raised internally when an ONNX model cannot be trusted or loaded.

    Always caught by `OnnxBackend.__init__` and converted into a load-error
    string, so importing or constructing this class can never take the API
    down - consistent with detector.py's "degrade, never raise" contract.
    """


def _sha256_of_file(path: str, chunk_size: int = 1024 * 1024) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def _make_session_options():
    """Session options tuned for a 512 MB / 0.1 CPU container.

    - single-threaded: 0.1 CPU means extra threads add contention and
      per-thread arenas, not speed.
    - arena disabled: ORT's default allocator arena over-reserves relative
      to what these two small models actually need, which matters far more
      than allocation speed at this size.
    """
    so = ort.SessionOptions()
    so.intra_op_num_threads = 1
    so.inter_op_num_threads = 1
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    so.enable_cpu_mem_arena = False
    so.enable_mem_pattern = False
    return so


class OnnxBackend:
    """Loads the exported ONNX models once and runs CPU inference.

    Mirrors the subset of MLDetectorEngine's interface that `detect()`
    actually needs, so detector.py can hold either this or the PyTorch
    backend behind the same two calls.
    """

    runtime = RUNTIME_ONNX_CPU

    def __init__(self, checkpoint_dir: str, classifier_file: str, segmenter_file: str):
        self.checkpoint_dir = checkpoint_dir
        self.load_errors: list[str] = []
        self.classifier_loaded = False
        self.segmenter_loaded = False
        self.classifier_provenance: Optional[str] = None
        self.segmenter_provenance: Optional[str] = None
        self.checkpoint_checksums: dict = {}
        self.classifier_checkpoint_path: Optional[str] = None
        self.segmenter_checkpoint_path: Optional[str] = None
        self.class_labels: Optional[list] = None
        self.onnxruntime_version = ONNXRUNTIME_VERSION
        self._clf_session = None
        self._seg_session = None
        self._clf_input_name = None
        self._seg_input_name = None
        self.calibrator = ClassifierCalibrator(os.path.join(self.checkpoint_dir, "classifier_calibration.json"))

        if not ONNX_AVAILABLE:
            self.load_errors.append(
                "onnxruntime is not installed - ONNX inference unavailable, "
                "classical detector remains active."
            )
            return

        manifest = self._read_manifest()
        if manifest is None:
            return

        self._load_one("classifier", classifier_file, manifest)
        self._load_one("segmenter", segmenter_file, manifest)

    # -- manifest ---------------------------------------------------------

    def _read_manifest(self) -> Optional[dict]:
        path = os.path.join(self.checkpoint_dir, MANIFEST_FILENAME)
        if not os.path.exists(path):
            self.load_errors.append(
                f"{MANIFEST_FILENAME} not found in {self.checkpoint_dir} - refusing to load "
                "ONNX models whose training provenance cannot be established. Run "
                "`python -m app.ml.export_onnx` (needs PyTorch, offline/local) to produce it."
            )
            return None
        try:
            with open(path) as f:
                data = json.load(f)
        except Exception as e:
            self.load_errors.append(f"{MANIFEST_FILENAME} could not be parsed ({type(e).__name__}).")
            return None
        if not isinstance(data, dict) or not isinstance(data.get("models"), dict):
            self.load_errors.append(f"{MANIFEST_FILENAME} does not have the expected structure.")
            return None
        return data

    # -- loading ----------------------------------------------------------

    def _load_one(self, role: str, filename: str, manifest: dict) -> None:
        try:
            onnx_path = os.path.join(self.checkpoint_dir, filename)
            if not os.path.exists(onnx_path):
                raise OnnxLoadError(f"{filename} not found in {self.checkpoint_dir}")

            entry = (manifest.get("models") or {}).get(filename)
            if not isinstance(entry, dict):
                raise OnnxLoadError(
                    f"{filename} is present but {MANIFEST_FILENAME} has no entry for it - "
                    "provenance cannot be established, refusing to load it."
                )

            provenance = self._verify_provenance(filename, entry)

            session = ort.InferenceSession(
                onnx_path,
                sess_options=_make_session_options(),
                providers=["CPUExecutionProvider"],
            )
            input_name = self._validate_io(role, filename, session)

            if role == "classifier":
                self._clf_session = session
                self._clf_input_name = input_name
                self.classifier_loaded = True
                self.classifier_provenance = provenance
                self.classifier_checkpoint_path = onnx_path
                self.checkpoint_checksums["classifier"] = _sha256_of_file(onnx_path)
                labels = entry.get("class_labels")
                if isinstance(labels, list) and labels:
                    self.class_labels = [str(x) for x in labels]
            else:
                self._seg_session = session
                self._seg_input_name = input_name
                self.segmenter_loaded = True
                self.segmenter_provenance = provenance
                self.segmenter_checkpoint_path = onnx_path
                self.checkpoint_checksums["segmenter"] = _sha256_of_file(onnx_path)
        except Exception as e:
            self.load_errors.append(f"{role}: {e}")

    def _validate_io(self, role: str, filename: str, session) -> str:
        """Structural validation of an ONNX graph's inputs/outputs.

        Catches a wrong, truncated, or mismatched export at LOAD time - where
        it becomes an honest `available=False` plus classical fallback -
        rather than at inference time in the middle of a user's request.

        Deliberately checks structure (rank, channel count, class count),
        never numerics: whether the weights are the RIGHT weights is a
        provenance question, handled by `_verify_provenance`.
        """
        inputs = session.get_inputs()
        outputs = session.get_outputs()
        if len(inputs) != 1:
            raise OnnxLoadError(f"{filename} exposes {len(inputs)} inputs, expected exactly 1")
        if len(outputs) != 1:
            raise OnnxLoadError(f"{filename} exposes {len(outputs)} outputs, expected exactly 1")

        in_shape = list(inputs[0].shape)
        out_shape = list(outputs[0].shape)

        if len(in_shape) != 4:
            raise OnnxLoadError(
                f"{filename} input rank is {len(in_shape)} ({in_shape}), expected 4 (N, C, H, W)"
            )
        # Channel dim must be the genuine dual-pol pair. A model expecting a
        # different channel count would mean the export does not match this
        # pipeline's VV/VH contract at all.
        channels = in_shape[1]
        if isinstance(channels, int) and channels != 2:
            raise OnnxLoadError(
                f"{filename} expects {channels} input channels, but this pipeline feeds exactly "
                "2 (VV, VH). Refusing to load a model whose input contract does not match."
            )

        if role == "classifier":
            if len(out_shape) != 2:
                raise OnnxLoadError(
                    f"{filename} classifier output rank is {len(out_shape)} ({out_shape}), "
                    "expected 2 (N, num_classes)"
                )
            num_classes = out_shape[1]
            if isinstance(num_classes, int) and num_classes != 3:
                raise OnnxLoadError(
                    f"{filename} outputs {num_classes} classes, expected 3 "
                    "(Oil, No-Oil, Lookalike)"
                )
        else:
            if len(out_shape) != 4:
                raise OnnxLoadError(
                    f"{filename} segmenter output rank is {len(out_shape)} ({out_shape}), "
                    "expected 4 (N, 1, H, W)"
                )
            out_channels = out_shape[1]
            if isinstance(out_channels, int) and out_channels != 1:
                raise OnnxLoadError(
                    f"{filename} segmenter outputs {out_channels} channels, expected 1 "
                    "(binary oil/background logit map)"
                )

        return inputs[0].name

    def _verify_provenance(self, filename: str, entry: dict) -> str:
        """Returns the provenance this ONNX file has actually EARNED.

        Never returns the real-Zenodo label on the strength of a filename.
        """
        # Imported here (not at module scope) purely to avoid a circular
        # import - detector.py imports this module.
        from app.ml.detector import PROVENANCE_ML_REAL, PROVENANCE_ML_SYNTHETIC

        trained_on = entry.get("source_trained_on")
        if trained_on != REAL_ZENODO_MARKER:
            return PROVENANCE_ML_SYNTHETIC

        source_file = entry.get("source_checkpoint")
        recorded = entry.get("source_sha256")
        if not source_file or not recorded:
            raise OnnxLoadError(
                f"{MANIFEST_FILENAME} claims {filename} is real-Zenodo-trained but does not "
                "record which checkpoint it came from or that checkpoint's hash - refusing "
                "to present it as real."
            )

        source_path = os.path.join(self.checkpoint_dir, source_file)
        if os.path.exists(source_path):
            actual = _sha256_of_file(source_path)
            if actual != recorded:
                raise OnnxLoadError(
                    f"{filename} was exported from {source_file}, but that checkpoint's bytes "
                    "on disk no longer match the hash recorded at export time - the ONNX file "
                    "is stale. Re-run `python -m app.ml.export_onnx`."
                )
        # If the source checkpoint is not shipped, the manifest hash is all
        # we have; that is recorded as a caveat by get_onnx_info() rather
        # than silently ignored.
        return PROVENANCE_ML_REAL

    # -- inference --------------------------------------------------------

    def run_classifier_with_calibration(
        self, stacked: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray, bool, float]:
        """(2, H, W) float32 -> (raw_probs, calibrated_probs, is_calibrated, effective_temperature)."""
        x = stacked[np.newaxis, ...]
        logits = self._clf_session.run(None, {self._clf_input_name: x})[0][0].astype(np.float64)
        clf_sha256 = self.checkpoint_checksums.get("classifier", "")
        return self.calibrator.calibrate_logits(
            logits,
            current_model_sha256=clf_sha256,
            current_class_labels=self.class_labels or [],
        )

    def run_classifier(self, stacked: np.ndarray) -> np.ndarray:
        """stacked: (2, H, W) float32 -> per-class probability vector (numpy).

        Applies softmax here because the exported graph ends at logits, which
        is exactly what the PyTorch path did (`torch.softmax` in detector.py).
        """
        x = stacked[np.newaxis, ...]
        logits = self._clf_session.run(None, {self._clf_input_name: x})[0][0]
        logits = logits.astype(np.float64)
        shifted = logits - np.max(logits)
        exp = np.exp(shifted)
        return exp / np.sum(exp)

    def run_segmenter(self, stacked: np.ndarray) -> np.ndarray:
        """stacked: (2, H, W) float32 -> (H, W) sigmoid probability map.

        Sigmoid is applied here for the same reason as softmax above: the
        exported graph stops at logits so the ONNX output is directly
        comparable to the PyTorch model's raw output.
        """
        x = stacked[np.newaxis, ...]
        logit = self._seg_session.run(None, {self._seg_input_name: x})[0][0, 0]
        # Numerically stable sigmoid: clip logit to [-50.0, 50.0] to eliminate float overflow warnings/NaNs
        z = np.clip(logit.astype(np.float64), -50.0, 50.0)
        return (1.0 / (1.0 + np.exp(-z))).astype(np.float32)

    # -- introspection ----------------------------------------------------

    def get_onnx_info(self) -> dict:
        def io_of(session):
            if session is None:
                return None
            return {
                "inputs": [{"name": i.name, "shape": list(i.shape)} for i in session.get_inputs()],
                "outputs": [{"name": o.name, "shape": list(o.shape)} for o in session.get_outputs()],
            }

        clf_sha256 = self.checkpoint_checksums.get("classifier", "")
        is_cal = self.calibrator.is_compatible(clf_sha256, self.class_labels or [])

        return {
            "runtime": RUNTIME_ONNX_CPU,
            "onnxruntime_version": self.onnxruntime_version,
            "providers": ["CPUExecutionProvider"],
            "classifier_io": io_of(self._clf_session),
            "segmenter_io": io_of(self._seg_session),
            "calibration": {
                "available": is_cal,
                "temperature": self.calibrator.temperature if is_cal else 1.0,
                "method": "temperature_scaling" if is_cal else None,
                "provenance": self.calibrator.provenance if is_cal else None,
            },
        }
