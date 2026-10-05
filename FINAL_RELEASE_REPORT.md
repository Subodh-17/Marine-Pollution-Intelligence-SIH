# Marine Pollution Intelligence - Final Release Report

## 1. What was changed
* Performed a complete repository audit to identify stale artifacts and duplicate structures.
* Removed the redundant `backend/backend/` directory tree after preserving necessary ML metadata/evaluation reports.
* Cleaned up runtime artifacts (`__pycache__`, `app.db`, `New Text Document.txt`, and temporary node binaries).
* Updated the classifier metadata and evaluation reports to represent the V2 classifier natively as the single authoritative model.
* Re-exported the ONNX artifacts to correctly map to the new V2 architecture.
* Fixed `verify_onnx.py` to correctly check parity on the dynamically loaded V2 architecture instead of hardcoding V1.
* Evaluated full backend testing (pytest) and frontend build/tests to confirm no functionality regressions.

## 2. Production ML architecture
* **Classifier**: `SceneClassifierV2`
* **Segmenter**: `SpillUNet`
* Both models have been verified to execute via ONNX Runtime CPU. 

## 3. Actual independent test metrics (Part III)
* **Accuracy:** 78.44%
* **Macro Precision:** 80.34%
* **Macro Recall:** 78.44%
* **Macro F1:** 77.79%
* **Oil Precision:** 95.59%
* **Oil Recall:** 86.67%
* **Oil F1:** 90.91%

## 4. Validation metrics
* **Best validation accuracy (V2):** 92.49%

## 5. Checkpoint filenames and SHA256
* **classifier_real_promoted.pth:** `0f587cb7604047247424bf7a79a9f07ae0ab66b9e4a3070cf32c2c1957fed1ee`
* **spill_unet_real_promoted.pt:** `af725b2587baed621b6ee264598a44dea008dc7214ccf454ccadc2fdd50e4361`

## 6. ONNX status
* **classifier_real_best.onnx:** Successfully exported as opset 18 from SceneClassifierV2.
* **spill_unet_real_best.onnx:** Successfully exported as opset 18.
* **ONNX parity check:** Verified exact parity between PyTorch and ONNX models (max abs diff < `1e-7`, exact argmax/mask agreement).

## 7. Backend test result
* All `pytest` tests run and passed successfully.

## 8. Frontend test result
* All `vitest` unit tests run and passed successfully.

## 9. Build result
* Vite frontend build completed successfully in ~8s.

## 10. Known limitations
* The classifier V2 (78.44% test accuracy) still generalizes imperfectly to out-of-distribution incidence angles compared to the training set validation metrics (92.49%).
* PyTorch `torch.onnx.export` fallback to Opset 18 due to limitations mapping `Identity` down to Opset 13 in modern versions.

## 11. Final ZIP path
`E:\Projects\Marine_Pollution_Intelligence_SIH_FINAL\SIH-26143-Final-Release.zip`
