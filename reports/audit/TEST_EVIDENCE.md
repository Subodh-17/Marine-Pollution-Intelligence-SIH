# Test Evidence & Verification Matrix

**Date**: 2026-10-24
**Target Commit**: `f98a4167cf7ea3900ca8e52dbbfa50fbc184083a`
**Execution Environment**: Local Windows x64 (Python 3.11.0 / Node 22.18.0)

---

## 1. Test Runs Summary

| Suite / Command | Execution Context | Passed | Failed | Skipped | Exit Code | Status | Notes |
|---|---|---|---|---|---|---|---|
| `pytest backend/tests/test_audit_remediation_regressions.py -v` | Local (Python 3.11) | 9 | 0 | 0 | 0 | PASSED | Targeted regression suite covering SEC-01..03, DB-01, PERF-01, DATA-01, concurrency |
| `pytest backend/tests -q` | Local (Python 3.11) | 450 | 0 | 28 | 0 | PASSED | Full backend suite; 21 skipped require offline PyTorch training, 7 require live PostgreSQL instance |
| `python -m app.ml.verify_onnx` | Local (Python 3.11 / ONNX) | 4 resolutions | 0 | 1 (parity) | 0 | PASSED | Production ONNX Runtime CPU inference verified on 64x64, 128x128, 256x256, 128x192 |
| `npm --prefix frontend run lint` | Local (Node 22 / Oxlint) | 40 files | 0 | 0 | 0 | PASSED | 0 warnings, 0 errors across 40 files |
| `npm --prefix frontend test` | Local (Node 22 / Vitest) | 52 | 0 | 0 | 0 | PASSED | 11 test suites passing in 26.54s |
| `npm --prefix frontend run build` | Local (Node 22 / Vite) | Built | 0 | 0 | 0 | PASSED | Production assets compiled cleanly to `frontend/dist` in 333ms |

---

## 2. Skipped Tests Audit (Backend)

All 28 skipped tests in the backend suite were audited for validity:
1. **PyTorch / Offline Training Pipeline (21 tests)**:
   - `test_evaluate_classifier.py` (1)
   - `test_evaluate_real.py` (1)
   - `test_lookalike_study.py` (1)
   - `test_ml_training_pipeline.py` (1)
   - `test_promote_and_precedence.py` (1)
   - `test_scene_classifier_v2.py` (1)
   - `test_train_augmentation_and_class_weights.py` (1)
   - `test_ml_and_real_satellite.py` (1)
   - `test_ml_training_page.py` (10)
   - `test_onnx_inference.py:335` (2) - PyTorch vs ONNX parity checks
   *Rationale*: PyTorch is deliberately excluded from `requirements.txt` to maintain a lightweight (~512MB RAM) production container on Render, which runs on ONNX Runtime CPU. PyTorch is included in `requirements-ml.txt` for offline training.
2. **PostgreSQL Multi-Store Integration (7 tests)**:
   - `test_persistence_postgres.py` (7 tests)
   *Rationale*: Intentionally guarded by `pytest.mark.skipif(not TEST_POSTGRES_URL)` for environments where a dedicated PostgreSQL daemon is running. SQLite provides full persistence by default.

---

## 3. Production Endpoint Health Probes

| Endpoint | Method | Expected Status | Received Status | Payload / Output |
|---|---|---|---|---|
| `https://marine-pollution-intelligence-api.onrender.com/health` | GET | 200 | 200 | `{"status":"ok","version":"0.1.0","env":"production"}` |
| `https://marine-pollution-intelligence-api.onrender.com/ready` | GET | 200 | 200 | `{"status":"ready","ml_available":true,"checks":{"synthetic_pipeline":"ok","ml_runtime":"onnxruntime_cpu","ml_available":true,"database":"ok"}}` |
| `https://marine-pollution-intelligence-api.onrender.com/api/config` | GET | 200 | 200 | Non-secret config: `app_version`, `max_upload_size_mb: 50.0`, etc. |
| `https://marine-pollution-intelligence-api.onrender.com/api/satellite/samples` | GET | 200 | 200 | 4 bundled SAR scenes listed (`demo_oil_scene_01.tif`, `02`, `lookalike`, `clean`) |
| `https://marine-pollution-intelligence-sih.vercel.app/` | GET | 200 | 200 | Single Page Application HTML shell with theme initialization |
