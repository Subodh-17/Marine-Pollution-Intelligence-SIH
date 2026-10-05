# Documentation index

Most of this project's documentation is kept at the **repository root**
rather than physically inside this `docs/` folder. That's a deliberate
choice, not an oversight: the docs below are extensively cross-referenced
by relative path from inside `backend/app/**/*.py` and
`frontend/src/**/*.jsx` source comments (e.g. "see ../../MODEL_CARD.md"),
and moving the files would mean rewriting every one of those in-code
references at real risk of silently breaking or misdirecting one - for a
purely cosmetic folder-location change. This `docs/` folder exists as the
documented, discoverable entry point the project structure calls for; the
links below are the real, current documents.

**Exception:** the real-Zenodo-dataset documentation genuinely lives inside
this folder (`DATASET_SETUP.md`, `DATASET_LICENSE.md`,
`DATASET_STRUCTURE.md`) - these are new, and nothing in the existing
codebase's relative-path references depends on them living at the root, so
there was no reason to break the "put new dataset docs under `docs/`"
convention here.

| Document | What it covers |
|---|---|
| [`README.md`](../README.md) | Quick start, architecture summary, configuration, reproducibility |
| [`DATASET_SETUP.md`](DATASET_SETUP.md) | Download, extraction, disk space, and setup for the real Zenodo Sentinel-1 SAR Oil Spill Dataset (external, not bundled) |
| [`DATASET_LICENSE.md`](DATASET_LICENSE.md) | Dataset license (CC BY 4.0), citation, and attribution requirements |
| [`DATASET_STRUCTURE.md`](DATASET_STRUCTURE.md) | Exact expected folder layout, file formats, and train/val/test splitting policy |
| [`FINAL_HANDOFF.md`](../FINAL_HANDOFF.md) | Production-readiness + ML hardening summary, test results, limitations |
| [`ARCHITECTURE.md`](../ARCHITECTURE.md) | System/pipeline architecture |
| [`DATA_PIPELINE.md`](../DATA_PIPELINE.md) | Data flow: satellite -> detection -> drift -> AIS -> attribution |
| [`MODEL_CARD.md`](../MODEL_CARD.md) | Detection/drift/scoring model card: training data (synthetic and real), limitations, intended (non-)use |
| [`backend/app/ml/README_ML_STATUS.md`](../backend/app/ml/README_ML_STATUS.md) | Current ML availability/validation status |
| [`AUDIT_LOG.md`](../AUDIT_LOG.md) | Chronological record of hardening/audit passes |
| [`MERGE_MATRIX.md`](../MERGE_MATRIX.md) | Feature/requirement merge tracking |
| [`SIH_REQUIREMENT_MATRIX.md`](../SIH_REQUIREMENT_MATRIX.md) | Requirement-by-requirement coverage matrix |
| [`DEMO_GUIDE.md`](../DEMO_GUIDE.md) | Walkthrough script for demoing the platform |
