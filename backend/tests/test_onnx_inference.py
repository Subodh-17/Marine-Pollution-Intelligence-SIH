"""
Tests for the production ONNX Runtime CPU inference path
(app/ml/onnx_engine.py, app/ml/export_onnx.py, app/ml/detector.py).

DESIGN RULE FOR THIS FILE
-------------------------
The whole point of the ONNX path is that production does NOT have PyTorch.
So the tests that cover the production path must not require torch either.
Only the explicit torch-vs-onnx parity test is allowed to need it, and it
skips (never silently passes) when torch is unavailable.

Tests that need the exported `.onnx` artifacts skip when those are absent,
because the artifacts are produced by an offline step
(`python -m app.ml.export_onnx`) that needs torch and the real checkpoints.
A skip here means "not verified in this environment", never "fine".
"""
import json
import os
import shutil

import numpy as np
import pytest

from app.ml import detector as detector_mod
from app.ml.detector import (
    MLDetectorEngine, PROVENANCE_ML_REAL, RUNTIME_ONNX_CPU,
    _CLASSIFIER_ONNX_FILE, _SEGMENTER_ONNX_FILE,
)
from app.ml.onnx_engine import (
    ONNX_AVAILABLE, MANIFEST_FILENAME, OnnxBackend, REAL_ZENODO_MARKER,
)

CKPT_DIR = os.path.abspath(detector_mod.CHECKPOINT_DIR)
CLF_ONNX = os.path.join(CKPT_DIR, _CLASSIFIER_ONNX_FILE)
SEG_ONNX = os.path.join(CKPT_DIR, _SEGMENTER_ONNX_FILE)
MANIFEST = os.path.join(CKPT_DIR, MANIFEST_FILENAME)


def _onnx_artifacts_present() -> bool:
    return all(os.path.exists(p) for p in (CLF_ONNX, SEG_ONNX, MANIFEST))


requires_onnxruntime = pytest.mark.skipif(
    not ONNX_AVAILABLE,
    reason="onnxruntime not installed - production inference path cannot be exercised",
)
requires_artifacts = pytest.mark.skipif(
    not _onnx_artifacts_present(),
    reason=(
        "exported .onnx artifacts + manifest not present - run "
        "`python -m app.ml.export_onnx` (needs PyTorch, offline) to produce them"
    ),
)


def _dual_pol(h=64, w=64, seed=0):
    rng = np.random.default_rng(seed)
    return (rng.random((h, w)).astype(np.float32),
            rng.random((h, w)).astype(np.float32))


# ---------------------------------------------------------------------------
# Production path must not need PyTorch
# ---------------------------------------------------------------------------

class TestNoTorchRequired:
    def test_onnx_engine_module_does_not_import_torch(self):
        """Guards the core deployment constraint: importing the production
        inference module must never pull torch in."""
        import app.ml.onnx_engine as mod
        source = open(mod.__file__).read()
        assert "import torch" not in source
        assert "from torch" not in source

    def test_detector_imports_without_torch(self):
        """detector.py is imported by app.main at request time; it must load
        regardless of whether torch is installed."""
        assert hasattr(detector_mod, "TORCH_AVAILABLE")
        assert isinstance(detector_mod.TORCH_AVAILABLE, bool)

    def test_engine_constructs_and_never_raises(self):
        engine = MLDetectorEngine()
        assert isinstance(engine.available, bool)
        assert isinstance(engine.load_errors, list)
        assert engine.runtime in (None, RUNTIME_ONNX_CPU, detector_mod.RUNTIME_PYTORCH)


# ---------------------------------------------------------------------------
# ONNX Runtime CPU loading + inference
# ---------------------------------------------------------------------------

@requires_onnxruntime
@requires_artifacts
class TestOnnxInference:
    def test_backend_loads_both_models_on_cpu_provider(self):
        backend = OnnxBackend(CKPT_DIR, _CLASSIFIER_ONNX_FILE, _SEGMENTER_ONNX_FILE)
        assert backend.classifier_loaded, backend.load_errors
        assert backend.segmenter_loaded, backend.load_errors
        info = backend.get_onnx_info()
        assert info["providers"] == ["CPUExecutionProvider"]
        assert info["runtime"] == RUNTIME_ONNX_CPU

    def test_classifier_accepts_dual_pol_and_returns_three_finite_probs(self):
        backend = OnnxBackend(CKPT_DIR, _CLASSIFIER_ONNX_FILE, _SEGMENTER_ONNX_FILE)
        vv, vh = _dual_pol()
        probs = backend.run_classifier(np.stack([vv, vh], axis=0))
        assert probs.shape == (3,)
        assert np.all(np.isfinite(probs))
        assert probs.min() >= 0.0
        assert abs(float(probs.sum()) - 1.0) < 1e-5

    @pytest.mark.parametrize("hw", [(64, 64), (128, 128), (96, 128)])
    def test_segmenter_output_matches_input_shape_and_is_finite(self, hw):
        """detector.py rejects any segmenter output whose shape differs from
        the input scene, so this is a hard contract, not a nicety."""
        backend = OnnxBackend(CKPT_DIR, _CLASSIFIER_ONNX_FILE, _SEGMENTER_ONNX_FILE)
        vv, vh = _dual_pol(*hw)
        seg = backend.run_segmenter(np.stack([vv, vh], axis=0))
        assert seg.shape == hw
        assert np.all(np.isfinite(seg))
        assert seg.min() >= 0.0 and seg.max() <= 1.0

    def test_segmentation_threshold_produces_boolean_mask(self):
        backend = OnnxBackend(CKPT_DIR, _CLASSIFIER_ONNX_FILE, _SEGMENTER_ONNX_FILE)
        vv, vh = _dual_pol()
        seg = backend.run_segmenter(np.stack([vv, vh], axis=0))
        mask = seg > 0.5
        assert mask.dtype == np.bool_
        assert mask.shape == vv.shape


# ---------------------------------------------------------------------------
# Provenance must be earned, not assumed from a filename
# ---------------------------------------------------------------------------

@requires_onnxruntime
@requires_artifacts
class TestProvenanceSafeguards:
    def test_real_provenance_requires_matching_source_checksum(self, tmp_path):
        """An .onnx whose recorded source hash no longer matches the
        checkpoint on disk is stale and must NOT be served as real."""
        staging = tmp_path / "ckpt"
        shutil.copytree(CKPT_DIR, staging)
        manifest = json.loads((staging / MANIFEST_FILENAME).read_text())
        entry = manifest["models"][_SEGMENTER_ONNX_FILE]
        source = entry.get("source_checkpoint")
        if not source or not (staging / source).exists():
            pytest.skip("source checkpoint not shipped alongside the ONNX; hash re-check n/a")
        entry["source_sha256"] = "sha256:" + "0" * 64
        (staging / MANIFEST_FILENAME).write_text(json.dumps(manifest))

        backend = OnnxBackend(str(staging), _CLASSIFIER_ONNX_FILE, _SEGMENTER_ONNX_FILE)
        assert not backend.segmenter_loaded
        assert any("no longer match" in e for e in backend.load_errors), backend.load_errors

    def test_missing_manifest_refuses_to_load_onnx(self, tmp_path):
        """An .onnx file existing on disk proves nothing about how it was
        trained - without the manifest it must not be loaded at all."""
        staging = tmp_path / "ckpt"
        shutil.copytree(CKPT_DIR, staging)
        (staging / MANIFEST_FILENAME).unlink()
        backend = OnnxBackend(str(staging), _CLASSIFIER_ONNX_FILE, _SEGMENTER_ONNX_FILE)
        assert not backend.classifier_loaded
        assert not backend.segmenter_loaded
        assert any(MANIFEST_FILENAME in e for e in backend.load_errors)

    def test_non_real_trained_marker_does_not_get_real_label(self, tmp_path):
        staging = tmp_path / "ckpt"
        shutil.copytree(CKPT_DIR, staging)
        manifest = json.loads((staging / MANIFEST_FILENAME).read_text())
        for entry in manifest["models"].values():
            entry["source_trained_on"] = "SOMETHING_ELSE"
        (staging / MANIFEST_FILENAME).write_text(json.dumps(manifest))

        backend = OnnxBackend(str(staging), _CLASSIFIER_ONNX_FILE, _SEGMENTER_ONNX_FILE)
        assert backend.segmenter_provenance != PROVENANCE_ML_REAL
        assert backend.classifier_provenance != PROVENANCE_ML_REAL

    def test_real_label_only_when_marker_and_hash_agree(self):
        backend = OnnxBackend(CKPT_DIR, _CLASSIFIER_ONNX_FILE, _SEGMENTER_ONNX_FILE)
        manifest = json.loads(open(MANIFEST).read())
        entry = manifest["models"][_SEGMENTER_ONNX_FILE]
        if entry.get("source_trained_on") == REAL_ZENODO_MARKER:
            assert backend.segmenter_provenance == PROVENANCE_ML_REAL
        else:
            assert backend.segmenter_provenance != PROVENANCE_ML_REAL


# ---------------------------------------------------------------------------
# Routing: dual-pol runs ML, oversized scenes fall back, failures fall back
# ---------------------------------------------------------------------------

@requires_onnxruntime
@requires_artifacts
class TestDetectorRouting:
    def test_dual_pol_runs_genuine_onnx_inference(self):
        engine = MLDetectorEngine()
        if engine.runtime != RUNTIME_ONNX_CPU:
            pytest.skip(f"engine selected {engine.runtime}, not the ONNX path")
        vv, vh = _dual_pol(128, 128)
        result = engine.detect(vv, vh)
        assert result.success
        assert result.mask.shape == vv.shape
        assert np.isfinite(result.confidence)
        assert "onnx" in result.method
        assert any("inference_runtime=onnxruntime_cpu" in n for n in result.notes)

    def test_provenance_only_real_after_successful_inference(self):
        engine = MLDetectorEngine()
        if engine.runtime != RUNTIME_ONNX_CPU:
            pytest.skip(f"engine selected {engine.runtime}, not the ONNX path")
        vv, vh = _dual_pol(128, 128)
        result = engine.detect(vv, vh)
        if engine.provenance == PROVENANCE_ML_REAL:
            assert result.success
            assert result.provenance == PROVENANCE_ML_REAL
        else:
            assert result.provenance != PROVENANCE_ML_REAL

    def test_oversized_scene_with_tiling_disabled_falls_back_instead_of_risking_oom(self, monkeypatch):
        """With tiling off, a scene above the direct-inference budget must
        produce an honest failed result the orchestrator can fall back
        from - never a fabricated ML detection. (With tiling on - the
        default - this same scene now runs genuine tiled ML instead; see
        test_scene_above_direct_budget_runs_tiled_ml below and
        tests/test_tiling.py.)"""
        monkeypatch.setenv("ML_MAX_INFERENCE_PIXELS", "1024")  # 32x32
        monkeypatch.setenv("ML_TILING_ENABLED", "false")
        engine = MLDetectorEngine()
        if engine.runtime is None:
            pytest.skip("no ML runtime available")
        vv, vh = _dual_pol(128, 128)
        result = engine.detect(vv, vh)
        assert not result.success
        assert result.provenance != PROVENANCE_ML_REAL

    def test_scene_above_direct_budget_runs_tiled_ml(self, monkeypatch):
        """Phase 3: a scene too large for one direct forward pass now runs
        genuine tiled ML at full resolution instead of refusing outright -
        see app/ml/tiling.py."""
        monkeypatch.setenv("ML_MAX_INFERENCE_PIXELS", "1024")  # 32x32
        engine = MLDetectorEngine()
        if engine.runtime != RUNTIME_ONNX_CPU:
            pytest.skip(f"engine selected {engine.runtime}, not the ONNX path")
        vv, vh = _dual_pol(128, 128)
        result = engine.detect(vv, vh)
        assert result.success
        assert result.mask.shape == (128, 128)
        assert any("tiled" in n for n in result.notes)

    def test_scene_beyond_even_the_tiled_ceiling_still_falls_back(self, monkeypatch):
        """The outer ceiling (ML_MAX_TILED_INFERENCE_PIXELS) still exists -
        tiling bounds per-tile memory, not total request time, so a
        pathologically large scene must still fail safely."""
        monkeypatch.setenv("ML_MAX_INFERENCE_PIXELS", "1024")  # 32x32
        monkeypatch.setenv("ML_MAX_TILED_INFERENCE_PIXELS", "16384")  # 128x128
        engine = MLDetectorEngine()
        if engine.runtime is None:
            pytest.skip("no ML runtime available")
        vv, vh = _dual_pol(256, 256)
        result = engine.detect(vv, vh)
        assert not result.success
        assert result.provenance != PROVENANCE_ML_REAL
        assert "budget" in (result.fallback_reason or "")

    def test_shape_mismatch_is_a_failed_result_not_a_crash(self):
        engine = MLDetectorEngine()
        vv = np.random.rand(64, 64).astype(np.float32)
        vh = np.random.rand(32, 32).astype(np.float32)
        result = engine.detect(vv, vh)
        assert not result.success
        assert result.provenance != PROVENANCE_ML_REAL

    def test_non_finite_input_is_rejected(self):
        engine = MLDetectorEngine()
        vv, vh = _dual_pol()
        vv = vv.copy()
        vv[0, 0] = np.nan
        result = engine.detect(vv, vh)
        assert not result.success
        assert result.provenance != PROVENANCE_ML_REAL

    def test_inference_failure_falls_back_without_fabricating(self, monkeypatch):
        """If the ONNX session itself blows up mid-request, the engine must
        return an honest failure, not a made-up mask."""
        engine = MLDetectorEngine()
        if engine.runtime != RUNTIME_ONNX_CPU:
            pytest.skip(f"engine selected {engine.runtime}, not the ONNX path")

        def boom(*_a, **_k):
            raise RuntimeError("simulated onnxruntime failure")

        monkeypatch.setattr(engine, "_forward_segmenter", boom)
        vv, vh = _dual_pol(64, 64)
        result = engine.detect(vv, vh)
        assert not result.success
        assert result.provenance == "ML_FAILED"
        assert not result.mask.any()


# ---------------------------------------------------------------------------
# Single-band input must never be upgraded into fake dual-pol
# ---------------------------------------------------------------------------

class TestSingleBandFallback:
    def test_single_band_scene_is_not_dual_pol(self):
        """read_dual_pol/ is_dual_pol is the gate that keeps single-band
        imagery away from a model that genuinely needs VV+VH. A single-band
        file must not be silently channel-duplicated."""
        from app.satellite.real_image import load_real_scene
        sample = os.path.join(
            os.path.dirname(__file__), "..", "data", "sample_sar", "demo_oil_scene_01.tif"
        )
        if not os.path.exists(sample):
            pytest.skip("bundled sample SAR image not present")
        result = load_real_scene(sample)
        if result.is_dual_pol:
            assert result.vv is not None and result.vh is not None
            assert not np.array_equal(result.vv, result.vh), (
                "VV and VH are identical - that would indicate a duplicated/fabricated channel"
            )
        else:
            assert result.vv is None and result.vh is None


# ---------------------------------------------------------------------------
# torch <-> onnx parity (skipped, never silently passed, when torch is absent)
# ---------------------------------------------------------------------------

@requires_onnxruntime
@requires_artifacts
class TestTorchOnnxParity:
    @pytest.mark.parametrize("hw", [(64, 64), (128, 128)])
    def test_parity_within_tolerance(self, hw):
        torch = pytest.importorskip(
            "torch", reason="PyTorch not installed - parity NOT verified in this environment"
        )
        from app.ml.architectures import SceneClassifier, SpillUNet
        from app.ml.detector import (
            _CLASSIFIER_CANDIDATES, _SEGMENTER_CANDIDATES, _extract_state_dict,
        )

        from app.ml.architectures import SceneClassifierV2
        _CLASSIFIER_ARCHITECTURE_CLASSES = {"SceneClassifier": SceneClassifier, "SceneClassifierV2": SceneClassifierV2}

        def load(candidates, out_name):
            for filename, _prov in candidates:
                path = os.path.join(CKPT_DIR, filename)
                if os.path.exists(path):
                    raw = torch.load(path, map_location="cpu", weights_only=False)
                    if out_name == "classifier":
                        arch = raw.get("architecture", "SceneClassifier") if isinstance(raw, dict) else "SceneClassifier"
                        model = _CLASSIFIER_ARCHITECTURE_CLASSES.get(arch, SceneClassifier)(in_channels=2, num_classes=3)
                    else:
                        model = SpillUNet(2, 1)
                    model.load_state_dict(_extract_state_dict(raw), strict=True)
                    model.eval()
                    return model
            return None

        clf = load(_CLASSIFIER_CANDIDATES, "classifier")
        seg = load(_SEGMENTER_CANDIDATES, "segmenter")
        if clf is None or seg is None:
            pytest.skip("source checkpoints not present for parity comparison")

        backend = OnnxBackend(CKPT_DIR, _CLASSIFIER_ONNX_FILE, _SEGMENTER_ONNX_FILE)
        vv, vh = _dual_pol(*hw, seed=7)
        stacked = np.stack([vv, vh], axis=0)
        tensor = torch.from_numpy(stacked).unsqueeze(0)

        with torch.no_grad():
            t_probs = torch.softmax(clf(tensor), dim=1).numpy()[0]
            t_seg = torch.sigmoid(seg(tensor)).numpy()[0, 0]
        o_probs = backend.run_classifier(stacked)
        o_seg = backend.run_segmenter(stacked)

        assert np.abs(t_probs - o_probs).max() < 1e-4
        assert np.abs(t_seg - o_seg).max() < 1e-4
        assert int(np.argmax(t_probs)) == int(np.argmax(o_probs))
        assert ((t_seg > 0.5) == (o_seg > 0.5)).all(), "masks diverge after the production threshold"

# ---------------------------------------------------------------------------
# Structural validation of the ONNX graphs (Phase 7)
# ---------------------------------------------------------------------------

@requires_onnxruntime
@requires_artifacts
class TestOnnxFileValidation:
    def test_expected_input_and_output_names_exist(self):
        backend = OnnxBackend(CKPT_DIR, _CLASSIFIER_ONNX_FILE, _SEGMENTER_ONNX_FILE)
        info = backend.get_onnx_info()
        for io in (info["classifier_io"], info["segmenter_io"]):
            assert len(io["inputs"]) == 1
            assert len(io["outputs"]) == 1
            assert io["inputs"][0]["name"]
            assert io["outputs"][0]["name"]

    def test_input_is_four_dimensional_dual_pol(self):
        backend = OnnxBackend(CKPT_DIR, _CLASSIFIER_ONNX_FILE, _SEGMENTER_ONNX_FILE)
        info = backend.get_onnx_info()
        for io in (info["classifier_io"], info["segmenter_io"]):
            shape = io["inputs"][0]["shape"]
            assert len(shape) == 4, shape
            assert shape[1] == 2, f"expected 2 (VV, VH) channels, got {shape[1]}"

    def test_classifier_outputs_three_classes(self):
        backend = OnnxBackend(CKPT_DIR, _CLASSIFIER_ONNX_FILE, _SEGMENTER_ONNX_FILE)
        shape = backend.get_onnx_info()["classifier_io"]["outputs"][0]["shape"]
        assert len(shape) == 2 and shape[1] == 3, shape

    def test_segmenter_outputs_single_channel_map(self):
        backend = OnnxBackend(CKPT_DIR, _CLASSIFIER_ONNX_FILE, _SEGMENTER_ONNX_FILE)
        shape = backend.get_onnx_info()["segmenter_io"]["outputs"][0]["shape"]
        assert len(shape) == 4 and shape[1] == 1, shape

    def test_class_ordering_is_read_from_checkpoint_not_hardcoded(self):
        """Class ordering must come from the checkpoint's own class_mapping.
        Getting this wrong silently swaps Oil and No-Oil."""
        backend = OnnxBackend(CKPT_DIR, _CLASSIFIER_ONNX_FILE, _SEGMENTER_ONNX_FILE)
        manifest = json.loads(open(MANIFEST).read())
        expected = manifest["models"][_CLASSIFIER_ONNX_FILE].get("class_labels")
        if not expected:
            pytest.skip("manifest records no class_labels for this export")
        assert backend.class_labels == expected
        assert expected[0] == "Oil", f"class 0 must be Oil, got {expected[0]}"

    def test_engine_surfaces_class_labels(self):
        engine = MLDetectorEngine()
        if engine.runtime != RUNTIME_ONNX_CPU:
            pytest.skip(f"engine selected {engine.runtime}, not the ONNX path")
        assert len(engine.class_labels) == 3
        assert engine.class_labels[0] == "Oil"

# ---------------------------------------------------------------------------
# Phase 21 (investigation provenance): runtime, threshold, and sidecar
# metadata must be present regardless of which runtime actually loaded the
# weights - a caller reading model_info should never see less detail just
# because production served ONNX instead of PyTorch.
# ---------------------------------------------------------------------------

@requires_onnxruntime
@requires_artifacts
class TestProvenanceCompleteness:
    def test_onnx_path_reports_inference_runtime(self):
        engine = MLDetectorEngine()
        if engine.runtime != RUNTIME_ONNX_CPU:
            pytest.skip(f"engine selected {engine.runtime}, not the ONNX path")
        info = engine.get_model_info()
        assert info["inference_runtime"] == RUNTIME_ONNX_CPU

    def test_onnx_path_reports_segmentation_threshold(self):
        engine = MLDetectorEngine()
        info = engine.get_model_info()
        assert info["segmentation_threshold"] == 0.5

    def test_segmentation_threshold_is_configurable(self, monkeypatch):
        monkeypatch.setenv("ML_SEGMENTATION_THRESHOLD", "0.7")
        engine = MLDetectorEngine()
        assert engine.segmentation_threshold == 0.7

    def test_invalid_threshold_env_var_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("ML_SEGMENTATION_THRESHOLD", "not-a-number")
        engine = MLDetectorEngine()
        assert engine.segmentation_threshold == 0.5

    def test_onnx_path_reports_model_version_from_sidecar(self):
        """The ONNX path never torch.loads the raw checkpoint dict, so
        model_version must fall back to the sidecar JSON's best_epoch
        rather than silently reporting None - this was a real gap found
        and fixed this round."""
        engine = MLDetectorEngine()
        if engine.runtime != RUNTIME_ONNX_CPU:
            pytest.skip(f"engine selected {engine.runtime}, not the ONNX path")
        info = engine.get_model_info()
        if engine.provenance == PROVENANCE_ML_REAL:
            assert info["model_version"] is not None

    def test_onnx_path_reports_preprocessing_version_and_dataset_dois(self):
        engine = MLDetectorEngine()
        if engine.runtime != RUNTIME_ONNX_CPU or engine.provenance != PROVENANCE_ML_REAL:
            pytest.skip("requires the ONNX path serving the real checkpoint")
        info = engine.get_model_info()
        assert info["preprocessing_version"] == "sar_percentile_1_99_minmax_v1"
        assert info["dataset_dois"]
