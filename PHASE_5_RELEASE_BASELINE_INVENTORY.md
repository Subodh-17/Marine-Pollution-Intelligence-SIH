# PHASE 5 — RELEASE BASELINE AND WORKING TREE INVENTORY
**Project:** Marine Pollution Intelligence (SIH-26143)
**Date:** 2026-10-30 | **Status:** Baseline Frozen & Protected
**Repository:** `E:\Projects\Marine_Pollution_Intelligence_SIH_FINAL`
**Branch:** `main` (synced with `origin/main`)
**HEAD Commit:** `d9df2bcab171e8d6ed66de264acd011dd38847dc`
**Audited Baseline Commit:** `9774ca00d9166f585725651adb6e59bae2efa0ac`

---

## 1. Baseline Integrity & Commit Lineage

The current working tree is derived from baseline commit `9774ca0` via a documented linear progression of feature additions, emergency stability fixes, scientific audits, and demonstration preparations:

```
d9df2bc docs: finalize SIH competition demonstration package and trigger CI verification (HEAD -> main, origin/main)
531803f feat(sih): complete Phase 4 SIH demonstration materials, live runbook, judge defense, and route planner state sync
77eeb37 feat(audit): complete Phase 3 independent scientific audit, spill data integrity, and SIH readiness deliverables
6810652 feat(routing): harden real spill-to-route integration, input bounds validation, and add Phase 2 acceptance reports
ab71b14 docs: add emergency recovery root cause, stability audit, and production verification reports
64d91d1 fix(routing): use self-contained pure-python graph engine without mandatory networkx
245171a feat(routing): add spill-aware maritime routing and fuel intelligence system
f925f4c feat: add classifier calibration and job restart resilience
2d5d7ce chore(branding): finalize professional design system, typography, and repository rename
ff9edea feat(satellite): enable real dual-pol ML inference with deterministic auto-routing
9774ca0 perf(satellite): optimize real image detection and resilient async uploads (AUDITED BASELINE)
```

---

## 2. Working Tree Modifications Register

The following 7 files contain verified, tested improvements awaiting final release staging:

| File Path | Component | Scope of Change | Status |
|---|---|---|---|
| `backend/app/routing/avoidance_engine.py` | Avoidance Engine | Lines 251–288: Sets `recommended_route_type="baseline_safe"`, `spill_avoiding_route=None`, and comparison zero delta when baseline is clear. | Verified & Tested |
| `backend/data/ml_checkpoints/classifier_v2_part3_evaluation.json` | ML Metrics Store | Reconciled accuracy to 79.33% (ONNX runtime), macro F1 0.7882, FAR 0.0167, and added runtime note. | Verified & Tested |
| `backend/app/ml/detector.py` | Inference Engine | Exposes runtime explanation note, dynamically computes `oil_false_alarm_rate`, and updates calibrated ECE. | Verified & Tested |
| `backend/app/ml/tiling.py` | Tiled Inference | Added `try...except` wrapper in coarse multiscale pass for fail-closed fallback to base segmentation. | Verified & Tested |
| `frontend/src/components/tabs/RoutePlannerTab.jsx` | Route Planner UI | Added route recommendation status banners (`--safe`, `--detour`, `--neutral`). | Verified & Tested |
| `frontend/src/components/tabs/RoutePlannerTab.css` | Styling | Added styles for recommendation banners across light and dark themes. | Verified & Tested |
| `frontend/src/test/RoutePlannerTab.test.jsx` | Frontend Tests | Added unit test asserting `baseline_safe` recommendation banner rendering. | Verified & Tested |

---

## 3. Audit & Verification Reports Inventory

All previous audit deliverables and verification reports are confirmed present in the repository root:

1. `FULL_APPLICATION_AUDIT_REPORT.md` (Full 9-page stack audit, verified defects, resolution register)
2. `FINAL_MODEL_PERFORMANCE_AND_CLASSICAL_DETECTOR_BENCHMARK.md` (Defensible ML benchmark, confusion matrix, 95% CI)
3. `SEGMENTATION_FAILURE_AND_GENERALIZATION_REPORT.md` (Quartile-by-quartile analysis, Q4 failure, multi-scale trade-off)
4. `DATA_PROVENANCE_AND_DEMO_TRANSPARENCY_AUDIT.md` (Zenodo DOIs, historical AIS replays, metocean fallbacks)
5. `ROUTE_PLANNER_AND_FUEL_VALIDATION_REPORT.md` (Avoidance geometry, Admiralty fuel formula, multi-vessel matrix)
6. `UI_UX_CORRECTION_REPORT.md` (Accessibility, contrast, recommendation banners, layout checks)
7. `FINAL_REGRESSION_AND_ACCEPTANCE_REPORT.md` (Regression logs, modified files register, acceptance checklist)
8. `FINAL_INDEPENDENT_RELEASE_VERIFICATION.md` (Independent empirical proof of PyTorch vs ONNX downsampling root cause)

Additional supporting artifacts:
- `SIH_FINAL_FIVE_MINUTE_PRESENTATION_SCRIPT.md`
- `SIH_JUDGE_QUESTIONS_AND_TECHNICAL_ANSWERS.md`
- `SIH_LIVE_DEMO_RUNBOOK.md`
- `SIH_DEMO_DATA_MANIFEST.md`
- `SIH_FINAL_COMPETITION_READINESS_REPORT.md`

---

## 4. Untracked Artifacts & Data Files Inventory

The following untracked files are intentionally preserved and protected:
- `backend/app/ml/fit_classifier_calibration.py` (Independent calibration fitting and verification script)
- `backend/data/ml_checkpoints/classifier_logits_cache.npz` (Cached validation and test logits from genuine Zenodo runs)
- `backend/tests/test_multiscale_segmentation_safety.py` (Safety regression suite for multiscale tiling fail-closed fallback)
- Previous audit reports (`ADVANCED_ML_RELIABILITY_REPORT.md`, `PRODUCTION_DEPLOYMENT_READINESS.md`, `RELEASE_STAGING_REVIEW.md`, `REMOTE_READINESS_AUDIT.md`)

---

## 5. Working Tree Protection & Safety Guarantees

- **No resets or cleans executed:** No `git reset --hard`, `git clean -fd`, or `git checkout .` operations will be run.
- **No data deletions:** SQLite database (`backend/data/app.db`), checkpoint weights (`.onnx`, `.pth`, `.pt`), and test manifests remain untouched.
- **Strict Read-Only Mode:** All subsequent Phase 5 checks (production smoke tests, demonstration rehearsals, quality reviews) will operate strictly in read-only mode without destructive side effects.
