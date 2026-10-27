# Marine Pollution Intelligence — SIH-26143: Release-Gate Audit

**Generated:** 2026-10-27T20:17:27Z
**Release Verdict:** `EXPERIMENTAL_CANDIDATE_FROZEN`
**Production Default:** `SINGLE_SCALE_BASELINE` (`ML_MULTISCALE_SEGMENTATION=false`)
**Independent Validation Status:** `BLOCKED_DATA_UNAVAILABLE`

---

## 1. Executive Summary

This release-gate audit confirms that the single-scale baseline model remains the **production default**. The experimental multiscale segmentation candidate `size_gated_closure` has been rigorously frozen and verified, but cannot be promoted to production default because **no unconsulted, independent labeled SAR dataset is currently available locally**.

The held-out Part III test set (450 scenes) from Zenodo SAR was previously consulted during Phase 4.3A for the initial promotion gate decision, and is therefore classified as `PREVIOUSLY_CONSULTED_TEST`. Reusing it for promotion would introduce bias.

---

## 2. Checkpoint SHA-256 Hashes (Frozen)

| File | Status | Expected SHA-256 | Match |
| :--- | :--- | :--- | :--- |
| `spill_unet_real_best.pt` | VERIFIED | `af725b2587baed621b6ee264598a44dea008dc7214ccf454ccadc2fdd50e4361` | True |
| `classifier_real_promoted.pth` | VERIFIED | `0f587cb7604047247424bf7a79a9f07ae0ab66b9e4a3070cf32c2c1957fed1ee` | True |
| `spill_unet_real_best.onnx` | VERIFIED | `da94d5af9b68155a883365b8a2eb615cc296f12df1926c4f612ea1bcb7656d8c` | True |
| `classifier_real_best.onnx` | VERIFIED | `dc68400ca1b54116e75dc900afb63ac5f0cb48db452892c0ac8e401c44fdf6b7` | True |

---

## 3. Configuration & Runtime Defaults

- **Production Default:** `ML_MULTISCALE_SEGMENTATION=false` (Single-Scale Baseline)
- **Experimental Strategy:** `ML_MULTISCALE_FUSION_STRATEGY=size_gated_closure`
- **Zero-Risk Rollback Path:** `ML_MULTISCALE_FUSION_STRATEGY=base_only` produces bitwise baseline equivalent output.
- **Detector Default Multiscale Flag:** `False` (Confirmed False)

---

## 4. Frozen Experimental Candidate Specification

- **Strategy Name:** `size_gated_closure`
- **Implementation Version:** `size_gated_closure_v1`
- **Component Area Threshold:** 100 pixels
- **Morphological Footprint:** 25x25 square footprint
- **Hole Filling:** Active (`binary_fill_holes`)
- **Coarse Weight Cap:** 0.95
- **Probability Seed Threshold:** 0.45
- **Operational Binarization:** 0.50

---

## 5. Dataset Provenance & Split Separation

- **Training Split:** 2,184 scenes (`TRAINING`)
- **Validation Split:** 386 scenes (`VALIDATION`)
- **Consulted Test Split:** 450 scenes (`PREVIOUSLY_CONSULTED_TEST`)
- **Split Leakage:** Zero overlap detected across all splits.
- **Independent Test Status:** `BLOCKED_DATA_UNAVAILABLE`

---

## 6. Next Steps for Promotion

To promote `size_gated_closure` to production default:
1. Complete dataset intake per `docs/INDEPENDENT_DATASET_INTAKE.md`.
2. Execute independent validation using `scripts/verify_release_gate.py --independent-dataset-dir <PATH>`.
3. Verify zero leakage, Q4 large-slick recovery gain $\ge +2.0\%$, and Lookalike false-alarm inflation $\le +5.0\%$.
