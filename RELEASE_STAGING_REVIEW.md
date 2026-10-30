# Marine Pollution Intelligence — Controlled Release Staging Review

**Target Repository:** `E:/Projects/Marine_Pollution_Intelligence_SIH_FINAL`  
**GitHub Repository:** `https://github.com/Subodh-17/marine-pollution-intelligence`  
**Baseline Commit:** [`2d5d7cee6d7792b5aa0a55a7fa2981bf34c24f47`](https://github.com/Subodh-17/marine-pollution-intelligence/commit/2d5d7cee6d7792b5aa0a55a7fa2981bf34c24f47) (`origin/main`)  
**Staging Status:** 9 approved files safely staged in local index (0 commits created, 0 pushes initiated)  
**Execution Timestamp:** October 28, 2026, 22:01 IST  

---

## 1. Baseline State & Working-Tree Reconciliation

### 1.1 Git Tracking Baseline
- Current Branch: `main` tracking `origin/main` (up to date).
- Local `HEAD` SHA: `2d5d7cee6d7792b5aa0a55a7fa2981bf34c24f47`
- Remote `origin/main` SHA: `2d5d7cee6d7792b5aa0a55a7fa2981bf34c24f47`
- Initial Working Tree State: 9 tracked modified files, 10 untracked files.

### 1.2 Resolution of the 8 vs. 10 Untracked Files Discrepancy
The discrepancy between previously cited counts of 8 versus 10 untracked files is completely resolved by separating codebase sprint deliverables from ephemeral SQLite runtime sidecars:
- **8 Sprint Deliverable Files:**
  1. `ADVANCED_ML_RELIABILITY_REPORT.md` (documentation)
  2. `backend/app/ml/calibration.py` (implementation)
  3. `backend/app/ml/fit_classifier_calibration.py` (offline fitting script)
  4. `backend/data/ml_checkpoints/classifier_calibration.json` (runtime calibration parameters)
  5. `backend/data/ml_checkpoints/classifier_logits_cache.npz` (offline validation/test logit cache)
  6. `backend/tests/test_classifier_calibration.py` (test suite)
  7. `backend/tests/test_jobs_restart_resilience.py` (test suite)
  8. `backend/tests/test_multiscale_segmentation_safety.py` (test suite)
- **2 Ephemeral SQLite Sidecar Files:**
  9. `backend/data/app.db-shm` (32,768 bytes, SQLite WAL shared-memory index)
  10. `backend/data/app.db-wal` (0 bytes, SQLite write-ahead log)
When ephemeral database sidecars are excluded, exactly 8 sprint files exist. When `git ls-files --others` reports all unignored files, exactly 10 untracked files exist.

---

## 2. Minimal Safe Release Set & Staged File Inventory

Exactly **9 files** have been staged individually via explicit file paths. Every staged file belongs strictly to the approved **Classifier Confidence Calibration** or **Background-Job Restart Resilience** objectives:

| Staged File Path | Git Status | Objective | Necessity & Justification |
| :--- | :---: | :--- | :--- |
| `backend/app/ml/calibration.py` | `A` (New) | Calibration | Core mathematical implementation of Platt temperature scaling ($T=1.0879$), ECE / Brier / NLL scoring rules, model checksum validation, and fail-closed fallback. |
| `backend/data/ml_checkpoints/classifier_calibration.json` | `A` (New) | Calibration | Required runtime calibration parameters (temperature, class ordering, model SHA-256 binding, validation ECE coordinates). |
| `backend/app/ml/onnx_engine.py` | `M` (Modified) | Calibration | Loads `ClassifierCalibrator`, provides `run_classifier_with_calibration()`, surfaces calibration metadata in `get_onnx_info()`. Contains zero multiscale changes. |
| `backend/app/ml/detector.py` | `M` (Modified) | Calibration | Integrates calibrator with `detect()`, provides fail-closed fallback to raw scores, outputs calibrated confidence while preserving backwards-compatible score fields. Contains zero multiscale changes. |
| `backend/tests/test_classifier_calibration.py` | `A` (New) | Calibration | Automated test suite for temperature invariance, calibration scoring rules, fail-closed security, and detector contract integration. |
| `backend/app/persistence/db.py` | `M` (Modified) | Job Resilience | Safe forward migrations for SQLite and PostgreSQL: adds `updated_at`, `retry_count`, `max_retries`, `payload_json`, and creates performance indexes. Contains zero multiscale changes. |
| `backend/app/persistence/jobs.py` | `M` (Modified) | Job Resilience | Implements durable job state lifecycle (`interrupted` status), error message path and credential sanitization, startup recovery, and `shutdown_executor()` thread pool management. Contains zero multiscale changes. |
| `backend/app/main.py` | `M` (Modified) | Job Resilience | FastAPI `_lifespan()` startup hook invoking `recover_interrupted_jobs()`, and investigation job payload registration. Contains zero multiscale changes. |
| `backend/tests/test_jobs_restart_resilience.py` | `A` (New) | Job Resilience | Automated test suite for restart recovery, retry exhaustion, path/credential redaction, concurrent submission bounds, and executor shutdown. |

### Staged Diff Summary (`git diff --cached --stat`)
```
 backend/app/main.py                                |  36 ++-
 backend/app/ml/calibration.py                      | 269 ++++++++++++++++++++
 backend/app/ml/detector.py                         |  61 ++++-
 backend/app/ml/onnx_engine.py                      |  25 ++
 backend/app/persistence/db.py                      |  29 ++-
 backend/app/persistence/jobs.py                    | 251 ++++++++++++++++---
 .../ml_checkpoints/classifier_calibration.json     | 278 +++++++++++++++++++++
 backend/tests/test_classifier_calibration.py       | 167 +++++++++++++
 backend/tests/test_jobs_restart_resilience.py      | 161 ++++++++++++
 9 files changed, 1235 insertions(+), 42 deletions(-)
```

---

## 3. Unstaged & Excluded Files Inventory

All experimental multiscale segmentation code, fitting caches, ephemeral database files, and documentation reports were intentionally **excluded from staging**:

### 3.1 Unstaged Tracked Modifications (4 files)
- `backend/app/ml/tiling.py`: Multiscale 3-tier tiling and fallback implementation.
- `backend/app/ml/evaluate_multiscale_comparison.py`: Multiscale evaluation script.
- `backend/data/ml_checkpoints/multiscale_comparison_report.json`: Multiscale candidate evaluation report.
- `backend/data/ml_checkpoints/multiscale_comparison_report.md`: Multiscale candidate evaluation documentation.

### 3.2 Unstaged Untracked Files (7 files)
- `backend/tests/test_multiscale_segmentation_safety.py`: Multiscale safety test suite (excluded with multiscale implementation).
- `backend/app/ml/fit_classifier_calibration.py`: Offline fitting script (excluded; not required at runtime).
- `backend/data/ml_checkpoints/classifier_logits_cache.npz`: Offline fitting cache (12.6 KB; excluded per instructions).
- `ADVANCED_ML_RELIABILITY_REPORT.md`: Sprint documentation report (excluded).
- `RELEASE_STAGING_REVIEW.md`: This staging audit report (untracked, not staged).
- `backend/data/app.db-shm`: Ephemeral SQLite shared-memory file.
- `backend/data/app.db-wal`: Ephemeral SQLite write-ahead log file.

---

## 4. Multiscale Safety Gate & Default-Disabled Confirmation

- **Safety Gate Result:** In `real_multiscale_evaluation_report.json`, the multiscale candidate failed safety gate `REAL-06` due to a $+0.1631\%$ increase in false-alarm pixel rate on negative clean-sea scenes (exceeding the strict $\le +0.05\%$ safety threshold).
- **Default-Disabled Status:** In `backend/app/config.py`, `ml_multiscale_segmentation` is explicitly set to `False`. In `backend/app/ml/detector.py`, `_resolve_multiscale_enabled()` defaults to `False`. The multiscale segmentation code is completely excluded from the staged release candidate and cannot be triggered unless `ML_MULTISCALE_SEGMENTATION=true` is set.

---

## 5. Model Checksum & Calibration Verification

- **Model File:** `backend/data/ml_checkpoints/classifier_real_best.onnx`
- **Computed SHA-256:** `sha256:dc68400ca1b54116e75dc900afb63ac5f0cb48db452892c0ac8e401c44fdf6b7`
- **Recorded SHA-256:** `sha256:dc68400ca1b54116e75dc900afb63ac5f0cb48db452892c0ac8e401c44fdf6b7`
- **Checksum Match:** **TRUE**
- **Optimal Temperature:** $T = 1.0879$
- **Class Ordering:** `["Oil", "No-Oil", "Lookalike"]` (strictly validated)
- **Generalization:** Evaluated on held-out test split (450 scenes): ECE improved by 12.6% ($0.0866 \to 0.0757$), with zero change in classification accuracy (invariant at 79.33%).

---

## 6. Database Preservation & Test Evidence

- **Database File:** `backend/data/app.db`
- **Size:** 156,643,328 bytes (unchanged)
- **Last Modified Timestamp:** `2026-10-28T15:16:09.450880+00:00` (completely unmodified)
- **Integrity Status:** `PRAGMA integrity_check` $\to$ `ok`
- **Record Counts:** 152 investigations, 0 users, 2 historical demo jobs. Zero test rows leaked.

### Automated Test Execution Results (Pre- and Post-Staging)
1. **Pre-Staging Focused Pytest:**
   - Command: `$env:PYTHONPATH="backend"; pytest backend/tests/test_classifier_calibration.py backend/tests/test_classical_classification.py backend/tests/test_lookalike_safety.py backend/tests/test_jobs_restart_resilience.py backend/tests/test_persistence.py -v`
   - Result: **59 passed in 23.68s (exit code 0)**.
2. **Post-Staging Re-Verification Pytest:**
   - Command: `$env:PYTHONPATH="backend"; pytest backend/tests/test_classifier_calibration.py backend/tests/test_classical_classification.py backend/tests/test_lookalike_safety.py backend/tests/test_jobs_restart_resilience.py backend/tests/test_persistence.py -v`
   - Result: **59 passed in 26.71s (exit code 0)**.
3. **Full Backend Suite:** **687 passed, 10 skipped, 4 warnings in 279.43s (exit code 0)**.
4. **Frontend Vitest Suite:** **79 passed across 15 test files in 43.06s (exit code 0)**.
5. **Frontend Lint & Build:** **0 lint errors, 726 modules built in 0.43s (exit code 0)**.
6. **Staged Whitespace & Syntax:** `git diff --cached --check` $\to$ **0 errors (clean)**.

---

## 7. Remaining Risks & Next Actions

- **Unresolved Blockers:** **NONE**. All 9 staged files are cleanly decoupled from the multiscale experiment, tested against temporary SQLite databases, verified against model checksums, and pass all regression suites.
- **Next Commands for Inspection:**
  - View staged diff: `git diff --cached`
  - View staging summary: `git diff --cached --stat`
  - Check working tree: `git status --short`
- **Release Hold:** No commits, tags, pushes, or deployments will occur without explicit authorization.
